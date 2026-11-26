"""Источник «был на эфире»: отчёты зрителей Bizon365 → кэш в своих таблицах.

API (как `common/launch_engine/services/bizon.py` bot-engine):
  GET {api_base}/webinars/reports/getlist?skip=&limit=   (≤100 за раз, свежие вперёд;
      комната – хвост поля `name` после «:», начало – `created`)
  GET {api_base}/webinars/reports/getviewers?webinarId=&skip=&limit=
  заголовок X-Token.

§27: пока эфир идёт, отчёта ещё нет (getviewers отдаёт 403, в getlist сессии
нет). Поэтому «нет отчёта за окно» – это «не знаем» (None), а не «не был»:
положительный гейт «был» такой шаг пропустит, дожим «не был» – отправит.
Гейты «был / не был» ставить ПОСЛЕ окончания эфира.

Отчёт закрытой сессии не меняется – зрителей забираем один раз и храним;
список отчётов перечитываем не чаще `list_ttl_seconds`.
Зритель находится по `id=` в адресе, с которым он пришёл (его дописывает
наша прокси /go при клике на ссылку Bizon), или по телефону.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol
from urllib.parse import parse_qs, urlparse

from ..clock import MSK, UTC
from ..config import resolve_secret
from .base import as_int, phone10

if TYPE_CHECKING:
    from ..registry import Tenant
    from ..service import Runtime
    from .config import BizonSettings

log = logging.getLogger("runtime.gates.bizon")

PAGE = 100
VIEWERS_PAGE = 1000
MAX_VIEWERS = 20000


class BizonNotReady(Exception):
    """Отчёт ещё не готов (эфир идёт, §27) – 403 на getviewers."""


class BizonClient(Protocol):
    async def list_reports(self, skip: int, limit: int) -> list[dict]: ...

    async def viewers(self, webinar_id: str, skip: int, limit: int) -> list[dict]: ...


class HttpBizonClient:
    def __init__(self, s: "BizonSettings"):
        self.base = s.api_base.rstrip("/")
        self.token = resolve_secret(s.token)

    async def _get(self, path: str, params: dict) -> dict:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as http:
            async with http.get(f"{self.base}{path}", params=params,
                                headers={"X-Token": self.token}) as r:
                if r.status == 403:
                    raise BizonNotReady(path)
                r.raise_for_status()
                return await r.json(content_type=None)

    async def list_reports(self, skip: int, limit: int) -> list[dict]:
        return (await self._get("/webinars/reports/getlist", {"skip": skip, "limit": limit})).get("list") or []

    async def viewers(self, webinar_id: str, skip: int, limit: int) -> list[dict]:
        d = await self._get("/webinars/reports/getviewers",
                            {"webinarId": webinar_id, "skip": skip, "limit": limit})
        return d.get("viewers") or []


# ── разбор ───────────────────────────────────────────────────────────────────

_WID_DT = re.compile(r"\*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?)")


def report_room(r: dict) -> str:
    return str(r.get("name") or "").rsplit(":", 1)[-1].strip()


def report_start(r: dict) -> datetime | None:
    for k in ("created", "startsAt", "startDate"):
        v = r.get(k)
        if v:
            try:
                dt = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
                return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
            except ValueError:
                pass
    m = _WID_DT.search(str(r.get("webinarId") or ""))
    if m:
        return MSK.localize(datetime.fromisoformat(m.group(1))).astimezone(UTC)
    return None


def viewer_keys(v: dict) -> tuple[int | None, str | None]:
    uid = None
    url = str(v.get("url") or v.get("referer") or "")
    if "id=" in url:
        try:
            uid = as_int((parse_qs(urlparse(url).query).get("id") or [""])[0])
        except Exception:  # noqa: BLE001
            uid = None
    return uid, phone10(v.get("phone"))


# ── синк ─────────────────────────────────────────────────────────────────────

class BizonSource:
    """Состояние источника в процессе: клиенты по аккаунтам и замки синка."""

    def __init__(self, rt: "Runtime"):
        self.rt = rt
        # фабрика клиента – тесты подменяют на фальшивый Bizon
        self.client_factory = HttpBizonClient
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, account: str) -> asyncio.Lock:
        return self._locks.setdefault(account, asyncio.Lock())

    async def _ensure_listed(self, client: BizonClient, s: "BizonSettings") -> None:
        account, now = s.token, self.rt.clock.now()
        async with self.rt.pool.acquire() as conn:
            last = await conn.fetchval("SELECT listed_at FROM gate_bizon_sync WHERE account=$1", account)
        if last is not None and (now - last).total_seconds() < s.list_ttl_seconds:
            return
        rows: list[dict] = []
        skip = 0
        while skip < s.list_limit:
            batch = await client.list_reports(skip, min(PAGE, s.list_limit - skip))
            if not batch:
                break
            rows += batch
            skip += len(batch)
            if len(batch) < PAGE:
                break
        async with self.rt.pool.acquire() as conn:
            async with conn.transaction():
                for r in rows:
                    wid = str(r.get("webinarId") or "").strip()
                    if not wid:
                        continue
                    await conn.execute(
                        """INSERT INTO gate_bizon_reports (account, webinar_id, room, started_at, seen_at)
                           VALUES ($1,$2,$3,$4,$5)
                           ON CONFLICT (account, webinar_id) DO UPDATE
                             SET seen_at=EXCLUDED.seen_at,
                                 started_at=COALESCE(gate_bizon_reports.started_at, EXCLUDED.started_at)""",
                        account, wid, report_room(r), report_start(r), now)
                await conn.execute(
                    """INSERT INTO gate_bizon_sync (account, listed_at) VALUES ($1,$2)
                       ON CONFLICT (account) DO UPDATE SET listed_at=EXCLUDED.listed_at""", account, now)
        log.info("Bizon %s: список отчётов перечитан (%s)", account, len(rows))

    async def _fetch_viewers(self, client: BizonClient, account: str, wid: str) -> bool:
        out: list[dict] = []
        skip = 0
        try:
            while len(out) < MAX_VIEWERS:
                batch = await client.viewers(wid, skip, VIEWERS_PAGE)
                if not batch:
                    break
                out += batch
                skip += len(batch)
        except BizonNotReady:
            log.info("Bizon %s: отчёт %s ещё не готов (эфир идёт? §27)", account, wid)
            return False
        now = self.rt.clock.now()
        async with self.rt.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM gate_bizon_viewers WHERE account=$1 AND webinar_id=$2",
                                   account, wid)
                await conn.executemany(
                    "INSERT INTO gate_bizon_viewers (account, webinar_id, n, uid, phone10) VALUES ($1,$2,$3,$4,$5)",
                    [(account, wid, i, *viewer_keys(v)) for i, v in enumerate(out)])
                await conn.execute(
                    """UPDATE gate_bizon_reports SET viewers_fetched_at=$3, viewers_count=$4
                       WHERE account=$1 AND webinar_id=$2""", account, wid, now, len(out))
        log.info("Bizon %s: зрители отчёта %s забраны (%s)", account, wid, len(out))
        return True

    async def attended(self, t: "Tenant", room: str, window: tuple[datetime, datetime] | None,
                       uid: int, phone: str | None) -> tuple[bool | None, str]:
        """Был ли человек в зале `room` в окне. → (факт, пояснение); None – не знаем."""
        s = t.settings.bizon
        if not s.configured():
            return None, "Bizon у запуска не настроен"
        if not room:
            return None, "у шага и запуска не задан зал Bizon"
        account = s.token
        try:
            client = self.client_factory(s)
            async with self._lock(account):
                await self._ensure_listed(client, s)
                async with self.rt.pool.acquire() as conn:
                    sessions = await conn.fetch(
                        """SELECT webinar_id, viewers_fetched_at FROM gate_bizon_reports
                           WHERE account=$1 AND room=$2
                             AND ($3::timestamptz IS NULL OR (started_at >= $3 AND started_at < $4))
                           ORDER BY started_at""",
                        account, room, window[0] if window else None, window[1] if window else None)
                pending = [r["webinar_id"] for r in sessions if r["viewers_fetched_at"] is None]
                done = [r["webinar_id"] for r in sessions if r["viewers_fetched_at"] is not None]
                for wid in pending:
                    if await self._fetch_viewers(client, account, wid):
                        done.append(wid)
        except Exception as e:  # noqa: BLE001 – источник упал: «не знаем», решает политика
            log.warning("[%s] Bizon недоступен (%s): %s", t.slug, room, e)
            return None, f"Bizon недоступен: {type(e).__name__}"
        if not sessions:
            return None, f"отчёта зала {room} за окно ещё нет (§27)"
        ph = phone10(phone)
        async with self.rt.pool.acquire() as conn:
            hit = await conn.fetchval(
                """SELECT EXISTS (SELECT 1 FROM gate_bizon_viewers WHERE account=$1
                     AND webinar_id = ANY($2::text[]) AND (uid = $3 OR ($4::text IS NOT NULL AND phone10 = $4)))""",
                account, done, uid, ph)
        if hit:
            return True, f"в отчёте Bizon зала {room}"
        if len(done) < len(sessions):
            return None, f"часть отчётов зала {room} ещё не готова (§27)"
        return False, f"нет в отчёте Bizon зала {room}"


def step_window(webinar_at: datetime | None, step, branch, *, hours: int,
                lead_minutes: int) -> tuple[datetime, datetime] | None:
    """Окно «был на эфире» – на дне САМОГО ШАГА (§32а bot-engine), с повтором ветки для anchor=repeat.

    Без эфира человека окна нет – проверяются все сессии зала.
    """
    if webinar_at is None:
        return None
    sc = step.schedule
    day = sc.day_offset
    if sc.day_offset_by_branch and branch is not None and branch.id in sc.day_offset_by_branch:
        day = sc.day_offset_by_branch[branch.id]
    base = webinar_at + timedelta(days=int(day or 0))
    if sc.anchor == "repeat" and branch is not None:
        base += timedelta(minutes=int(branch.repeat_offset_minutes or 0))
    return base - timedelta(minutes=lead_minutes), base + timedelta(hours=hours)
