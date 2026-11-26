"""Опрос внутри бота – перенос services/survey.py + handlers/survey.py bot-engine.

Вопрос опроса – обычный шаг с `survey.qid`. Его кнопки – варианты ответа:
движок сам делает из них callback `f:<slug>:sv:<id шага>:<индекс>` (слаг
запуска в данных обязателен – общий MAX-бот раскладывает нажатия по
запускам по префиксу `f:<slug>:`, CLAUDE.md bot-engine §36). У multi-вопроса
последней строкой – кнопка «готово» (`…:d`), варианты отмечаются ✅.
`survey.free` – ответ приходит обычным сообщением (кнопок-вариантов нет).

Как в движке:
  * на вопросе цепочка стоит: on_done / also_fire вопроса не планируются,
    дальше – только ответом по стрелке «следующий вопрос» (survey_next),
    сразу, без пересчёта времени (движок зовёт fire_step напрямую);
  * ответ – в survey_responses.answers[qid]; single перезаписывается,
    multi копит список;
  * `final` – опрос пройден: submitted_at + отмена дожимов
    z_survey_followup / s_survey_followup;
  * `edit` – следующий шаг заменяет сообщение вопроса (текстовый шаг);
  * свободный ответ ждём 48 часов, дальше сообщение – обычное.

Отличия (намеренные):
  * повторное нажатие на уже отвеченный вопрос игнорируется (защита от
    двойного перехода, §14 bot-engine): вопрос «открыт» от отправки до ответа,
    перезапуск воронки шлёт вопрос заново и открывает его снова – поэтому
    «залипания» из-за ответа в прошлом прогоне (ради которого движок не
    игнорировал повторы) здесь нет;
  * вопрос не ушёл (ошибка Telegram) – идём по survey_next сразу: человек
    вопроса не видел, ждать его ответ = остановить воронку навсегда.
"""
from __future__ import annotations

import base64
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from studio.model.funnel import Button, Content, EdgeKind, Step, TgContent

from .. import repo
from ..repo import JobSpec, Outcome
from .common import deliver

if TYPE_CHECKING:
    from ..executor import Executor, UserCtx
    from ..graph import CompiledFunnel

log = logging.getLogger("runtime.steps.survey")

FREE_TTL = timedelta(hours=48)          # AWAIT_TTL_HOURS движка
FOLLOWUP_KEYS = ("z_survey_followup", "s_survey_followup")
DONE = "d"
REPEAT_ACK = "Ответ уже принят"


# ── callback ─────────────────────────────────────────────────────────────────

def _sid(step_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(step_id.bytes).rstrip(b"=").decode()


def cb_data(slug: str, step_id: uuid.UUID, choice) -> str:
    data = f"f:{slug}:sv:{_sid(step_id)}:{choice}"
    if len(data.encode()) > 64:
        # Telegram режет callback_data длиннее 64 байт; слаг такой длины – редкость
        log.warning("слаг «%s» слишком длинный для кнопок опроса – без слага", slug)
        data = f"sv:{_sid(step_id)}:{choice}"
    return data


@dataclass
class Parsed:
    slug: str | None
    step_id: uuid.UUID
    choice: str


def parse(data: str) -> Parsed | None:
    parts = (data or "").split(":")
    if len(parts) == 5 and parts[0] == "f" and parts[2] == "sv":
        slug, raw, choice = parts[1], parts[3], parts[4]
    elif len(parts) == 3 and parts[0] == "sv":
        slug, raw, choice = None, parts[1], parts[2]
    else:
        return None
    try:
        sid = uuid.UUID(bytes=base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except Exception:  # noqa: BLE001
        return None
    return Parsed(slug, sid, choice)


def is_survey_callback(data: str) -> bool:
    return (data or "").startswith(("f:", "sv:")) and parse(data) is not None


# ── вопрос ───────────────────────────────────────────────────────────────────

def question_step(ex: "Executor", u: "UserCtx", step: Step, selected=()) -> Step:
    """Копия шага, где кнопки – варианты ответа (с отметками для multi)."""
    text, buttons, _claim = ex._content(u, step)
    sel = set(selected or ())
    sv = step.survey
    rows: list[Button] = []
    if not sv.free:
        for i, b in enumerate(buttons):
            label = (b.text or "").strip()
            rows.append(Button(text=("✅ " + label) if label in sel else label,
                               callback_data=cb_data(u.slug, step.id, i)))
        if sv.multi:
            rows.append(Button(text=sv.done_button, callback_data=cb_data(u.slug, step.id, DONE)))
    else:
        rows = [b for b in buttons if b.url]    # у свободного ответа – только ссылки
    # MAX: callback-кнопки там берутся только из content.max.buttons (§36 – телеграмный
    # payload без слага нажимался бы в пустоту), поэтому варианты ответа кладутся туда
    # явно. У свободного ответа – ссылки MAX-шага (None – «как в TG»).
    mx_buttons = rows
    if sv.free:
        mb = step.content.max.buttons
        mx_buttons = None if mb is None else [b for b in mb if b.url]
    content = Content(tg=TgContent(text=text, buttons=rows),
                      max=step.content.max.model_copy(update={"buttons": mx_buttons,
                                                              "claim_button_text": None}))
    return step.model_copy(update={"content": content})


def answer_labels(ex: "Executor", u: "UserCtx", step: Step) -> list[str]:
    _t, buttons, _c = ex._content(u, step)
    return [(b.text or "").strip() for b in buttons]


async def fire_question(ex: "Executor", u: "UserCtx", g: "CompiledFunnel", step: Step, job, ctx,
                        out: Outcome) -> None:
    sv = step.survey
    d = await deliver(ex, u, g, question_step(ex, u, step), job, out)
    if d.blocked:
        return
    if not d.ok:
        # вопроса человек не видел – не ждём ответа, которого не будет
        log.warning("[%s] %s: вопрос %s не ушёл – идём дальше по опросу", u.slug, u.uid, step.key)
        out.act("survey_skipped", step, payload="вопрос не отправлен")
        nxt = g.edge(step.id, EdgeKind.survey_next)
        if nxt is not None:
            out.specs.append(JobSpec(step_id=nxt.to_step, step_key=g.key_of(nxt.to_step), kind="fire",
                                     run_at=u.now, reason=f"вопрос {step.key} не ушёл"))
        return
    async with ex.rt.pool.acquire() as conn:
        # сообщение с кнопками вопроса: Telegram – число, MAX – строка «mid.…»
        args = (u.tenant_id, u.platform, u.uid, g.funnel_id, g.version, step.id, step.key, sv.qid,
                sv.free, d.message_id, u.now, d.ext_id)
        if d.rerun:
            # задача повторно: человек мог уже ответить – открытый вопрос не трогаем
            await conn.execute(
                """INSERT INTO survey_prompts (tenant_id, platform, uid, funnel_id, version, step_id,
                        step_key, qid, free, message_id, opened_at, ext_message_id)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) ON CONFLICT DO NOTHING""", *args)
        else:
            await conn.execute(
                """INSERT INTO survey_prompts (tenant_id, platform, uid, funnel_id, version, step_id,
                        step_key, qid, free, message_id, opened_at, ext_message_id)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                   ON CONFLICT (tenant_id, platform, uid, step_id) DO UPDATE SET
                        funnel_id=EXCLUDED.funnel_id, version=EXCLUDED.version,
                        step_key=EXCLUDED.step_key, qid=EXCLUDED.qid, free=EXCLUDED.free,
                        message_id=EXCLUDED.message_id, ext_message_id=EXCLUDED.ext_message_id,
                        opened_at=EXCLUDED.opened_at,
                        status='open', selected='[]'::jsonb, answered_at=NULL""", *args)
    if u.position.get("awaiting") is None:
        # позиция «ждёт ответа» (не «дошла»); чужое ожидание (телефон, почта) не трогаем
        out.awaiting = {"kind": "survey", "step_id": str(step.id), "version": g.version}
    out.act("survey_asked", step, payload=sv.qid)
    log.info("[%s] %s: задан вопрос %s (%s)%s", u.slug, u.uid, step.key, sv.qid,
             " – ждём ответ текстом" if sv.free else "")


# ── ответ ────────────────────────────────────────────────────────────────────

async def _save_answer(conn, tenant_id, platform, uid, qid: str, value, now) -> None:
    await conn.execute(
        """INSERT INTO survey_responses (tenant_id, platform, uid, answers, created_at, updated_at)
           VALUES ($1,$2,$3, jsonb_build_object($4::text, $5::jsonb), $6, $6)
           ON CONFLICT (tenant_id, platform, uid) DO UPDATE SET
                answers = survey_responses.answers || EXCLUDED.answers, updated_at = EXCLUDED.updated_at""",
        tenant_id, platform, uid, qid, value, now)


async def _advance(rt, conn, tenant, uid: int, platform: str, g, step: Step, prompt, pos,
                   answer, update_key: str, *, edit_ok: bool) -> Outcome:
    """Ответ принят: закрыть вопрос, записать, поставить следующий шаг."""
    now = rt.clock.now()
    sv = step.survey
    await _save_answer(conn, tenant.id, platform, uid, sv.qid, answer, now)
    await conn.execute(
        """UPDATE survey_prompts SET status='answered', answered_at=$5, selected=$6
           WHERE tenant_id=$1 AND platform=$2 AND uid=$3 AND step_id=$4""",
        tenant.id, platform, uid, step.id, now, answer if isinstance(answer, list) else [])
    out = Outcome()
    out.act("survey_answer", step, payload=str(answer)[:500], qid=sv.qid)
    aw = (pos["awaiting"] if pos else None) or {}
    if aw.get("kind") == "survey" and aw.get("step_id") == str(step.id):
        out.awaiting = None
    nxt = g.edge(step.id, EdgeKind.survey_next)
    if nxt is not None and g.step(nxt.to_step) is not None:
        if edit_ok and sv.edit and (prompt["message_id"] or prompt["ext_message_id"]):
            await conn.execute(
                """INSERT INTO survey_edits (tenant_id, platform, uid, funnel_id, step_id, message_id,
                        created_at, ext_message_id) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                   ON CONFLICT (tenant_id, platform, uid, funnel_id, step_id) DO UPDATE SET
                        message_id=EXCLUDED.message_id, ext_message_id=EXCLUDED.ext_message_id,
                        created_at=EXCLUDED.created_at""",
                tenant.id, platform, uid, g.funnel_id, nxt.to_step, prompt["message_id"], now,
                prompt["ext_message_id"])
        out.specs.append(JobSpec(step_id=nxt.to_step, step_key=g.key_of(nxt.to_step), kind="fire",
                                 run_at=now, reason=f"ответ на {step.key}"))
    if sv.final:
        await conn.execute(
            "UPDATE survey_responses SET submitted_at=$4 WHERE tenant_id=$1 AND platform=$2 AND uid=$3",
            tenant.id, platform, uid, now)
        out.act("survey_done", step)
        for key in FOLLOWUP_KEYS:
            s = g.by_key.get(key)
            if s is not None:
                out.cancel_slots.append((s.id, "fire"))
        log.info("[%s] %s: опрос пройден (%s)", tenant.slug, uid, step.key)
    await repo.apply_outcome(conn, tenant_id=tenant.id, platform=platform, uid=uid,
                             funnel_id=g.funnel_id, version=g.version, out=out, now=now,
                             idem_prefix=f"ev:{update_key}")
    return out


async def _load(rt, conn, tenant, platform: str, uid: int, prompt):
    pos = await conn.fetchrow(
        """SELECT * FROM positions WHERE tenant_id=$1 AND platform=$2 AND uid=$3 AND funnel_id=$4
           FOR UPDATE""", tenant.id, platform, uid, prompt["funnel_id"])
    g = await rt.registry.get(tenant.id, prompt["funnel_id"], prompt["version"])
    step = g.step(prompt["step_id"]) if g is not None else None
    return pos, g, step


async def on_callback(rt, entry, uid: int, data: str, update_key: str, *,
                      platform: str = "tg") -> str:
    """Нажатие варианта ответа. → текст всплывающей подсказки."""
    p = parse(data)
    if p is None:
        return ""
    tenant = rt.registry.tenant(entry.tenant_id)
    if p.slug is not None and p.slug != tenant.slug:
        log.warning("[%s] кнопка опроса чужого запуска «%s» от %s – пропускаем", tenant.slug, p.slug, uid)
        return ""
    post = None
    async with rt.pool.acquire() as conn:
        async with conn.transaction():
            # порядок блокировок как везде: человек → позиция → задачи
            part = await conn.fetchrow(
                "SELECT * FROM participants WHERE tenant_id=$1 AND platform=$2 AND uid=$3 FOR UPDATE",
                tenant.id, platform, uid)
            if part is None:
                return ""
            prompt = await conn.fetchrow(
                """SELECT * FROM survey_prompts WHERE tenant_id=$1 AND platform=$2 AND uid=$3
                     AND step_id=$4 FOR UPDATE""", tenant.id, platform, uid, p.step_id)
            if prompt is None:
                return ""
            pos, g, step = await _load(rt, conn, tenant, platform, uid, prompt)
            if step is None or not step.survey.qid or pos is None:
                return "Этот вопрос устарел"
            if prompt["status"] != "open":
                log.info("[%s] %s: повторное нажатие на вопрос %s – ответ уже принят",
                         tenant.slug, uid, step.key)
                await repo.insert_actions(conn, tenant_id=tenant.id, platform=platform, uid=uid,
                                          funnel_id=g.funnel_id, version=g.version,
                                          actions=[repo.ActionRec("survey_repeat", step.id, step.key,
                                                                  p.choice)], now=rt.clock.now())
                return REPEAT_ACK
            from ..executor import UserCtx
            u = UserCtx(tenant_id=tenant.id, slug=tenant.slug, platform=platform, uid=uid,
                        participant=dict(part), position=dict(pos), now=rt.clock.now())
            labels = answer_labels(rt.executor, u, step)
            sv = step.survey
            if p.choice == DONE:
                if not sv.multi:
                    return ""
                sel = list(prompt["selected"] or [])
                await _advance(rt, conn, tenant, uid, platform, g, step, prompt, pos, sel, update_key,
                               edit_ok=True)
                ack = "Готово"
                post = ("final", u, g, step, prompt) if sv.final and not sv.edit else None
            else:
                try:
                    label = labels[int(p.choice)]
                except (ValueError, IndexError):
                    log.warning("[%s] опрос: непонятный выбор %r на %s от %s", tenant.slug, p.choice,
                                step.key, uid)
                    return ""
                if sv.multi:
                    sel = list(prompt["selected"] or [])
                    if label in sel:
                        sel.remove(label)
                        ack = f"– {label}"
                    else:
                        sel.append(label)
                        ack = f"✓ {label}"
                    await conn.execute(
                        """UPDATE survey_prompts SET selected=$5 WHERE tenant_id=$1 AND platform=$2
                             AND uid=$3 AND step_id=$4""", tenant.id, platform, uid, step.id, sel)
                    await _save_answer(conn, tenant.id, platform, uid, sv.qid, sel, rt.clock.now())
                    await repo.insert_actions(conn, tenant_id=tenant.id, platform=platform, uid=uid,
                                              funnel_id=g.funnel_id, version=g.version,
                                              actions=[repo.ActionRec("survey_toggle", step.id,
                                                                      step.key, label)],
                                              now=rt.clock.now())
                    post = ("reedit", u, g, step, prompt, sel)
                else:
                    await _advance(rt, conn, tenant, uid, platform, g, step, prompt, pos, label,
                                   update_key, edit_ok=True)
                    ack = f"✓ {label}"
                    post = ("final", u, g, step, prompt) if sv.final and not sv.edit else None
    rt.wake()
    if post is not None:
        await _after(rt, entry, post)
    return ack


async def _after(rt, entry, post) -> None:
    """Правки клавиатуры после коммита (сбой тут ничего не ломает)."""
    kind, u, g, step, prompt = post[:5]
    if u.platform == "max":
        await _after_max(rt, post)
        return
    mid = prompt["message_id"]
    if not mid:
        return
    try:
        if kind == "reedit":
            _text, kb = rt.executor.render(u, g, question_step(rt.executor, u, step, post[5]))
            await entry.bot.edit_message_reply_markup(chat_id=u.uid, message_id=mid, reply_markup=kb)
        elif kind == "final":
            # финальный вопрос: гасим кнопки (как движок), чтобы не было второго круга
            await entry.bot.edit_message_reply_markup(chat_id=u.uid, message_id=mid, reply_markup=None)
    except Exception as e:  # noqa: BLE001
        log.debug("правка клавиатуры вопроса не удалась: %s", e)


async def _after_max(rt, post) -> None:
    """MAX: отметки multi и «гасим кнопки» у финального вопроса – правкой всего
    сообщения (`PUT /messages`: клавиатура – одно из вложений). У вопроса с
    картинкой/видео вложения заменились бы целиком и картинка пропала бы – его
    не трогаем (повторное нажатие всё равно игнорируется, отметка видна во
    всплывающей подсказке)."""
    from ..platforms import max_steps
    from .common import text_only
    kind, u, g, step, prompt = post[:5]
    mid = prompt["ext_message_id"]
    if not mid or not text_only(step):
        return
    ex = rt.executor
    try:
        if kind == "reedit":
            text, rows = max_steps.render(ex, u, g, question_step(ex, u, step, post[5]))
        else:
            text, _rows = max_steps.render(ex, u, g, question_step(ex, u, step))
            rows = None
        await max_steps.edit(ex, u, mid, text, rows)
    except Exception as e:  # noqa: BLE001
        log.debug("MAX: правка клавиатуры вопроса не удалась: %s", e)


async def on_free_text(rt, conn, tenant, uid: int, text: str, update_key: str, *,
                       platform: str = "tg") -> bool:
    """Свободный ответ (вызывается из gateway.on_text внутри его транзакции,
    человек уже заблокирован FOR UPDATE). → True, если текст – ответ."""
    answer = (text or "").strip()[:1000]
    if not answer or answer.startswith("/"):
        return False
    now = rt.clock.now()
    prompt = await conn.fetchrow(
        """SELECT * FROM survey_prompts WHERE tenant_id=$1 AND platform=$2 AND uid=$3
             AND status='open' AND free AND opened_at > $4
           ORDER BY opened_at DESC LIMIT 1 FOR UPDATE""", tenant.id, platform, uid, now - FREE_TTL)
    if prompt is None:
        return False
    pos, g, step = await _load(rt, conn, tenant, platform, uid, prompt)
    if step is None or not step.survey.qid or pos is None:
        return False
    await _advance(rt, conn, tenant, uid, platform, g, step, prompt, pos, answer, update_key,
                   edit_ok=False)
    log.info("[%s] %s: свободный ответ на %s записан", tenant.slug, uid, step.key)
    return True


async def is_done(conn, tenant_id: int, platform: str, uid: int) -> bool:
    """Источник для гейта `survey_done` (не слать прошедшим опрос)."""
    return bool(await conn.fetchval(
        """SELECT 1 FROM survey_responses WHERE tenant_id=$1 AND platform=$2 AND uid=$3
             AND submitted_at IS NOT NULL""", tenant_id, platform, uid))
