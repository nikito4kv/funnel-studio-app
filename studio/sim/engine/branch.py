# VENDORED из bot-engine: common/launch_engine/funnel/branch.py
# Коммит bot-engine: dd105332b3ddf41a2edab574d6605ea381d55283
# Логика не менялась. Правка только в импортах (scripts/vendor_sim.py):
#   loader движка -> studio.engine_ref.loader;
#   datetime -> studio.sim.clock.datetime (now() = время симуляции).
# Обновлять: uv run python scripts/vendor_sim.py
"""Branch-routing хелперы для воронок с несколькими entry-step.

Используется при /start (TG и MAX): по локальному времени МСК выбираем
ветку (например L=13:00 / R=18:00) и сохраняем у participant
branch + webinar_date + webinar_time. Потом FunnelRunner стартует с
branch.entry_step, а absolute_time_msk шаги вычисляют offset от
webinar_date юзера.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import time, timedelta

from studio.sim.clock import datetime

import pytz

from studio.engine_ref.loader import BranchRouter, FunnelBranch, BranchWindow

_MSK = pytz.timezone("Europe/Moscow")


def now_msk() -> datetime:
    return datetime.now(_MSK)


def pick_branch_for_now(
    router: BranchRouter, now: datetime | None = None,
) -> tuple[FunnelBranch, BranchWindow, _date] | None:
    """Возвращает (branch, window, webinar_date) для now-MSK.

    webinar_date вычисляется по window.day: today/tomorrow относительно
    локальной даты МСК.
    """
    n = (now or now_msk()).astimezone(_MSK)
    hhmm = n.strftime("%H:%M")
    picked = router.pick(hhmm)
    if not picked:
        return None
    branch, window = picked
    today = n.date()
    if window.day == "fixed":
        # Фикс-дата эфира (одна для всех регистраций). Парсим window.date
        # "YYYY-MM-DD"; при ошибке/отсутствии — fallback на today.
        try:
            webinar_date = _date.fromisoformat(window.date or "")
        except (ValueError, TypeError):
            webinar_date = today
    elif window.day == "tomorrow":
        webinar_date = today + timedelta(days=1)
    else:
        webinar_date = today
    return branch, window, webinar_date


def compose_webinar_dt(
    webinar_date: _date | None, webinar_time: str | None,
) -> datetime | None:
    """Возвращает MSK-aware datetime эфира юзера или None если нет данных."""
    if not webinar_date or not webinar_time:
        return None
    try:
        h, m = webinar_time.split(":")
        return _MSK.localize(datetime.combine(webinar_date, time(int(h), int(m))))
    except Exception:
        return None


def branch_by_id(router: BranchRouter | None, branch_id: str | None) -> FunnelBranch | None:
    """Ветка воронки по сохранённому у участника id. None – если не найдена."""
    if not router or not branch_id:
        return None
    for b in router.branches:
        if b.id == branch_id:
            return b
    return None


def resolve_step_target(
    step, anchor_dt: datetime, branch: FunnelBranch | None,
) -> datetime | None:
    """МСК-момент, в который должен уйти шаг. None – время задать нечем.

    Два способа задать время шага, взаимоисключающие:

    1. `webinar_offset_minutes` – СМЕЩЕНИЕ ОТ ЭФИРА участника. «За час до»
       это -60, «через 17 минут эфира» это +17. Час по стенным часам при
       этом не важен: у одного человека эфир в 13:00, у другого в 18:00,
       и оба получат сообщение ровно за час до СВОЕГО.
       `anchor: repeat` берёт за точку отсчёта не сам эфир, а его повтор
       (`branch.repeat_offset_minutes` – у дневной ветки повтор в тот же
       день вечером, у вечерней – назавтра днём).
       `webinar_offset_by_branch: {DAY: -190, EVE: -130}` – когда расстояние
       до эфира у веток разное (утреннее «в 9:50 / в 15:50»); ветка не
       найдена – берётся общий webinar_offset_minutes.
       `day_offset_by_branch: {DAY: 5, EVE: 6}` – то же для ДНЯ у шагов с
       часом по стенным часам: у вечерней ветки повтор последнего дня
       уезжает на следующие сутки, и общий день оказывается в прошлом.

    2. `absolute_time_msk` – час по стенным часам («в 9:00 по МСК»),
       прежнее поведение. Для сообщений, не привязанных к эфиру: утренний
       прогрев, вечерний дожим.

    В обоих случаях `day_offset` двигает на N суток от дня эфира – так
    пятидневка укладывается в одну цепочку шагов.

    ⚠️ Способ 1 существует ради воронок, где ветвей больше одной, а тексты
    у них общие. Без него каждую ветку приходится копировать целиком
    (так сделан автовеб Жени: 3100 строк на две ветки), и дальше они
    расходятся на первой же правке текста – маркетолог правит одну копию
    из двух и не догадывается о второй.
    """
    off = getattr(step, "webinar_offset_minutes", None)
    by_branch = getattr(step, "webinar_offset_by_branch", None) or {}
    if branch is not None and branch.id in by_branch:
        off = by_branch[branch.id]
    if off is not None:
        base = anchor_dt
        if getattr(step, "anchor", "webinar") == "repeat":
            base = base + timedelta(minutes=int(branch.repeat_offset_minutes) if branch else 0)
        return base + timedelta(days=int(step.day_offset or 0), minutes=int(off))
    if step.absolute_time_msk:
        try:
            h, m = step.absolute_time_msk.split(":")
        except Exception:
            return None
        day = int(step.day_offset or 0)
        by_day = getattr(step, "day_offset_by_branch", None) or {}
        if branch is not None and branch.id in by_day:
            day = int(by_day[branch.id])
        return anchor_dt.replace(
            hour=int(h), minute=int(m), second=0, microsecond=0,
        ) + timedelta(days=day)
    return None
