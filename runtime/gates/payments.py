"""Источник «оплатил»: вебхук кассы → своя таблица оплат → гейты paid / course_paid.

Касса (pay.example.com, Laravel) шлёт POST с JSON и заголовком
`X-Payment-Signature` = HMAC-SHA256(тело, общий секрет) – ровно как
`common/payments/security.py` в bot-engine. Формат тела – как у приёмника bot-engine
(`common/payments/router.py`): event, external_payment_id, tariff_id, amount,
status, customer{telegram_id, phone, email}, utm{content…}, meta{…},
occurred_at.

Привязка оплаты к человеку запуска (как `vip_sync`, но без опроса кассы):
  1. по id человека – его кладёт в ссылку наша прокси /govip (`id=<uid>`,
     касса сохраняет в bot_meta и возвращает в `meta`); запасные ключи –
     customer.telegram_id и utm_content цифрами (старые ссылки);
  2. по телефону – ТОЛЬКО если у запуска `payments.match_by_phone` (§34: тот
     же тариф могут продавать два бота, матч по телефону ловит чужих);
  3. `payments.paid_from` – оплаты раньше этой даты не считаются.

Идемпотентность: одна строка на внешний id платежа. Повтор того же статуса –
ничего не делает («duplicate»); смена статуса (paid → refunded) – обновляет,
и гейт сразу видит возврат.

Гейт читает таблицу оплат при каждой проверке, поэтому оплату, пришедшую
раньше, чем человек поделился телефоном, он тоже увидит. Событие `paid` в
actions (и шаг `payments.on_paid`) – в момент привязки при приёме вебхука.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from ..clock import MSK
from ..config import resolve_secret
from ..repo import ActionRec, JobSpec, Outcome, apply_outcome, insert_actions
from .base import as_int, phone10

if TYPE_CHECKING:
    from ..registry import Tenant
    from ..service import Runtime

log = logging.getLogger("runtime.gates.payments")

SIGNATURE_HEADER = "X-Payment-Signature"
PRODUCTS = ("vip", "course")


# ── подпись ──────────────────────────────────────────────────────────────────

def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify(body: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False
    return hmac.compare_digest(sign(body, secret), signature.strip().lower())


# ── разбор ───────────────────────────────────────────────────────────────────

def parse_paid_from(s: str) -> datetime | None:
    """`payments.paid_from` (МСК) → tz-aware. Пусто/мусор → None (без ограничения)."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        log.warning("payments.paid_from=%r не разобран – ограничения по дате нет", s)
        return None
    return MSK.localize(dt) if dt.tzinfo is None else dt


def _parse_dt(s) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return MSK.localize(dt) if dt.tzinfo is None else dt


def client_of(payload: dict) -> tuple[int | None, str | None]:
    """(id человека, платформа) из платежа. Порядок – от точного к запасному."""
    meta = payload.get("meta") or payload.get("bot_meta") or {}
    cust = payload.get("customer") or {}
    utm = payload.get("utm") or {}
    pl = (meta.get("pl") or meta.get("platform") or "").strip().lower() or None
    if meta.get("max_id") or cust.get("max_id"):
        pl = pl or "max"
    for v in (meta.get("id"), meta.get("client_id"), meta.get("uid"), meta.get("telegram_id"),
              meta.get("tg_id"), meta.get("max_id"), cust.get("telegram_id"), cust.get("max_id")):
        uid = as_int(v)
        if uid:
            return uid, pl
    uid = as_int(utm.get("content") or payload.get("utm_content"))
    return (uid, pl) if uid else (None, pl)


def status_of(payload: dict) -> str:
    st = (payload.get("status") or "").strip().lower()
    if st:
        return st
    ev = (payload.get("event") or "").strip().lower()
    return ev.split(".", 1)[1] if ev.startswith("payment.") else (ev or "unknown")


# ── приём вебхука ────────────────────────────────────────────────────────────

def _secret_of(t: "Tenant") -> str | None:
    try:
        return resolve_secret(t.settings.payments.secret)
    except Exception as e:  # noqa: BLE001
        log.error("[%s] секрет кассы %s не найден: %s", t.slug, t.settings.payments.secret, e)
        return None


async def receive(rt: "Runtime", body: bytes, signature: str,
                  tenant_slug: str | None = None) -> tuple[int, dict]:
    """Приём вебхука кассы. → (HTTP-код, ответ).

    Подпись проверяется секретами всех запусков с включённой кассой (или
    одного – для адреса /t/<запуск>/payments/callback). Платёж «видят» только
    запуски, чьим секретом подпись сошлась.
    """
    cands = [t for t in rt.registry.tenants.values()
             if t.settings.payments.enabled and t.settings.payments.secret
             and (tenant_slug is None or t.slug == tenant_slug)]
    verified: list["Tenant"] = []
    for t in cands:
        sec = _secret_of(t)
        if sec and verify(body, signature, sec):
            verified.append(t)
    if not verified:
        log.warning("вебхук кассы отклонён: подпись не сошлась ни с одним секретом (%s кандидатов)",
                    len(cands))
        return 401, {"ok": False, "error": "bad signature"}
    try:
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("не объект")
    except Exception:  # noqa: BLE001
        return 400, {"ok": False, "error": "invalid JSON"}
    ext = str(payload.get("external_payment_id") or payload.get("payment_id") or "").strip()
    if not ext:
        return 400, {"ok": False, "error": "missing external_payment_id"}

    now = rt.clock.now()
    status = status_of(payload)
    uid, pl = client_of(payload)
    cust = payload.get("customer") or {}
    ph = phone10(cust.get("phone") or payload.get("phone"))
    tariff = str(payload.get("tariff_id") or "").strip()
    paid_at = _parse_dt(payload.get("paid_at") or payload.get("occurred_at")) or now
    refs = sorted({t.settings.payments.secret for t in verified})

    async with rt.pool.acquire() as conn:
        async with conn.transaction():
            prev = await conn.fetchrow(
                "SELECT id, status FROM gate_payments WHERE external_id=$1 FOR UPDATE", ext)
            if prev is None:
                pid = await conn.fetchval(
                    """INSERT INTO gate_payments (external_id, event, status, tariff_id, tariff_name,
                           amount, currency, client_uid, client_platform, phone10, email,
                           verified_refs, payload, paid_at, received_at, updated_at)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$15)
                       RETURNING id""",
                    ext, str(payload.get("event") or ""), status, tariff, payload.get("tariff_name"),
                    _num(payload.get("amount")), payload.get("currency"), uid, pl, ph,
                    (cust.get("email") or None), refs, payload,
                    paid_at if status == "paid" else None, now)
                changed = True
            else:
                pid = prev["id"]
                changed = prev["status"] != status
                await conn.execute(
                    """UPDATE gate_payments SET status=$2, event=$3, payload=$4, updated_at=$5,
                           verified_refs=ARRAY(SELECT DISTINCT unnest(verified_refs || $6::text[])),
                           paid_at=COALESCE(paid_at, $7)
                       WHERE id=$1""",
                    pid, status, str(payload.get("event") or ""), payload, now, refs,
                    paid_at if status == "paid" else None)
    if not changed:
        log.info("вебхук кассы %s (%s) – повтор, ничего не делаем", ext, status)
        return 200, {"ok": True, "duplicate": True}

    matched = 0
    if status == "paid":
        for t in verified:
            matched += await _link(rt, t, pid, ext, tariff, uid, pl, ph, paid_at, now)
    elif prev is not None and prev["status"] == "paid":
        await _refund_actions(rt, pid, ext, status, now)
    log.info("вебхук кассы %s: %s, тариф %s, id %s, привязано людей: %s", ext, status, tariff,
             uid or "-", matched)
    return 200, {"ok": True, "status": status, "matched": matched}


def _num(v):
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


async def _link(rt: "Runtime", t: "Tenant", pid: int, ext: str, tariff: str, uid, pl, ph,
                paid_at: datetime, now: datetime) -> int:
    ps = t.settings.payments
    since = parse_paid_from(ps.paid_from)
    if since is not None and paid_at < since:
        log.info("[%s] оплата %s от %s раньше paid_from – не считаем", t.slug, ext, paid_at)
        return 0
    n = 0
    for product in PRODUCTS:
        if tariff not in ps.tariffs(product):
            continue
        async with rt.pool.acquire() as conn:
            people = []
            if uid:
                people += [(r["platform"], r["uid"], "id") for r in await conn.fetch(
                    """SELECT platform, uid FROM participants WHERE tenant_id=$1 AND uid=$2
                         AND ($3::text IS NULL OR platform=$3)""", t.id, uid, pl)]
            if ps.match_by_phone and ph:
                people += [(r["platform"], r["uid"], "phone") for r in await conn.fetch(
                    """SELECT platform, uid FROM participants WHERE tenant_id=$1 AND phone IS NOT NULL
                         AND right(regexp_replace(phone, '\\D', '', 'g'), 10) = $2""", t.id, ph)]
        seen = set()
        for platform, puid, how in people:
            if (platform, puid) in seen:
                continue
            seen.add((platform, puid))
            if await _link_one(rt, t, pid, ext, tariff, product, platform, puid, how, now):
                n += 1
    return n


async def _link_one(rt, t, pid, ext, tariff, product, platform, uid, how, now) -> bool:
    step_key = (t.settings.payments.on_paid or {}).get(product, "").strip()
    target = await _on_paid_target(rt, t, platform, uid, step_key) if step_key else None
    async with rt.pool.acquire() as conn:
        async with conn.transaction():
            # порядок блокировок как везде: человек → позиция → задачи
            await conn.execute(
                "SELECT 1 FROM participants WHERE tenant_id=$1 AND platform=$2 AND uid=$3 FOR UPDATE",
                t.id, platform, uid)
            ins = await conn.fetchval(
                """INSERT INTO gate_payment_links (tenant_id, payment_id, platform, uid, product,
                                                   matched_by, created_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING RETURNING 1""",
                t.id, pid, platform, uid, product, how, now)
            if not ins:
                return False
            act = ActionRec(action="paid", payload=f"{product}: тариф {tariff}, платёж {ext}, по {how}",
                            data={"product": product, "payment": ext, "matched_by": how})
            if target is None:
                if step_key:
                    act.data["on_paid"] = f"шаг «{step_key}» не найден в воронках человека"
                    log.warning("[%s] %s: on_paid.%s = «%s» – шага нет в воронках человека",
                                t.slug, uid, product, step_key)
                await insert_actions(conn, tenant_id=t.id, platform=platform, uid=uid, funnel_id=None,
                                     version=None, actions=[act], now=now)
                return True
            funnel_id, version, step = target
            await conn.execute(
                """SELECT 1 FROM positions WHERE tenant_id=$1 AND platform=$2 AND uid=$3
                   AND funnel_id=$4 FOR UPDATE""", t.id, platform, uid, funnel_id)
            out = Outcome(actions=[act])
            out.specs.append(JobSpec(step_id=step.id, step_key=step.key, kind="fire", run_at=now,
                                     reason=f"оплата {product} ({ext})", version=version))
            await apply_outcome(conn, tenant_id=t.id, platform=platform, uid=uid, funnel_id=funnel_id,
                                version=version, out=out, now=now, idem_prefix=f"paid{pid}:{t.id}:{uid}")
    log.info("[%s] %s: оплата %s привязана (%s)%s", t.slug, uid, product, how,
             f", запущен шаг {step_key}" if target else "")
    return True


async def _on_paid_target(rt, t, platform, uid, step_key):
    """(воронка, версия, шаг) для шага on_paid: в версии позиции человека, иначе в текущей."""
    async with rt.pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT funnel_id, version FROM positions WHERE tenant_id=$1 AND platform=$2 AND uid=$3
                 AND status <> 'stopped' ORDER BY updated_at DESC""", t.id, platform, uid)
    for r in rows:
        for v in (r["version"], rt.registry.current_version(t.id, r["funnel_id"])):
            if v is None:
                continue
            g = await rt.registry.get(t.id, r["funnel_id"], v)
            if g is not None and step_key in g.by_key:
                return r["funnel_id"], v, g.by_key[step_key]
    return None


async def _refund_actions(rt, pid, ext, status, now) -> None:
    async with rt.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT tenant_id, platform, uid, product FROM gate_payment_links WHERE payment_id=$1", pid)
        for r in rows:
            await insert_actions(conn, tenant_id=r["tenant_id"], platform=r["platform"], uid=r["uid"],
                                 funnel_id=None, version=None, now=now,
                                 actions=[ActionRec(action="payment_" + status[:20],
                                                    payload=f"{r['product']}: платёж {ext} → {status}")])


# ── гейт ─────────────────────────────────────────────────────────────────────

async def is_paid(conn, t: "Tenant", platform: str, uid: int, phone: str | None,
                  product: str) -> bool | None:
    """Оплатил ли человек продукт запуска. None – касса у запуска не настроена."""
    ps = t.settings.payments
    if not ps.configured() or not ps.tariffs(product):
        return None
    ph = phone10(phone) if ps.match_by_phone else None
    return bool(await conn.fetchval(
        """SELECT EXISTS (SELECT 1 FROM gate_payments p
             WHERE p.status = 'paid' AND p.tariff_id = ANY($1::text[]) AND $2 = ANY(p.verified_refs)
               AND ($3::timestamptz IS NULL OR COALESCE(p.paid_at, p.received_at) >= $3)
               AND ((p.client_uid = $4 AND (p.client_platform IS NULL OR p.client_platform = $5))
                    OR ($6::text IS NOT NULL AND p.phone10 = $6)))""",
        ps.tariffs(product), ps.secret, parse_paid_from(ps.paid_from), uid, platform, ph))
