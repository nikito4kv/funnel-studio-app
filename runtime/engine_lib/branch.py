# VENDORED из bot-engine: common/launch_engine/funnel/branch.py
# Коммит bot-engine: dd105332b3ddf41a2edab574d6605ea381d55283 (файл-источник не менялся с f171a355)
# Правки против оригинала (логика расчёта времени НЕ менялась):
#   1. импорт моделей движка убран – функции принимают модель Студии
#      (studio.model.funnel: BranchRouter / Branch / BranchWindow / Schedule),
#      у неё те же имена полей (`from_`, `to`, `day`, `date`, `id`,
#      `repeat_offset_minutes`, `webinar_offset_minutes`, …);
#   2. `BranchRouter.pick` движка перенесён сюда функцией `pick_window`
#      (у модели Студии этого метода нет) – тело то же;
#   3. `pick_branch_for_now` берёт `now` обязательным параметром: время в
#      движке Студии идёт от управляемых часов (runtime/clock.py), иначе
#      тесты не могли бы перематывать время.
"""Branch-routing хелперы для воронок с несколькими entry-step.

Используется при /start (TG и MAX): по локальному времени МСК выбираем
ветку (например L=13:00 / R=18:00) и сохраняем у participant
branch + webinar_date + webinar_time. Потом FunnelRunner стартует с
branch.entry_step, а absolute_time_msk шаги вычисляют offset от
webinar_date юзера.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime, time, timedelta

import pytz

_MSK = pytz.timezone("Europe/Moscow")


def pick_window(router, now_msk_hhmm: str):
    """(branch, window) для времени МСК "HH:MM" – `BranchRouter.pick` движка."""
    def hm_to_min(s: str) -> int:
        h, m = s.split(":")
        return int(h) * 60 + int(m)

    now_min = hm_to_min(now_msk_hhmm)
    for br in router.branches:
        for w in br.windows:
            fr = hm_to_min(w.from_)
            to = hm_to_min(w.to)
            if fr <= now_min <= to:
                return br, w
    return None


def pick_branch_for_now(router, now: datetime):
    """Возвращает (branch, window, webinar_date) для now-MSK.

    webinar_date вычисляется по window.day: today/tomorrow относительно
    локальной даты МСК.
    """
    n = now.astimezone(_MSK)
    hhmm = n.strftime("%H:%M")
    picked = pick_window(router, hhmm)
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


def branch_by_id(router, branch_id: str | None):
    """Ветка воронки по сохранённому у участника id. None – если не найдена."""
    if not router or not branch_id:
        return None
    for b in router.branches:
        if b.id == branch_id:
            return b
    return None


def resolve_step_target(step, anchor_dt: datetime, branch) -> datetime | None:
    """МСК-момент, в который должен уйти шаг. None – время задать нечем.

    `step` – любой объект с полями расписания движка; в Студии это
    `Step.schedule` (имена полей те же).

    Два способа задать время шага, взаимоисключающие:

    1. `webinar_offset_minutes` – СМЕЩЕНИЕ ОТ ЭФИРА участника. «За час до»
       это -60, «через 17 минут эфира» это +17. `anchor: repeat` берёт за
       точку отсчёта повтор (`branch.repeat_offset_minutes`).
       `webinar_offset_by_branch` / `day_offset_by_branch` – своё для ветки.

    2. `absolute_time_msk` – час по стенным часам («в 9:00 по МСК»).

    В обоих случаях `day_offset` двигает на N суток от дня эфира.
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


def shift_for_step(dt: datetime | None, step_schedule, branch, *, main: bool = False):
    """Эфир, О КОТОРОМ ГОВОРИТ ЭТОТ ШАГ (перенесено из runner._shift_for_repeat).

    Плюс `day_offset` суток и, для `anchor: repeat`, сдвиг на повтор ветки.
    От этого зависят {webinar.time}/{webinar.date}/{webinar.unix} в текстах.
    main=True – «основное время дня»: день шага учитываем, повтор – нет.
    """
    if dt is None or step_schedule is None:
        return dt
    dt = dt + timedelta(days=int(getattr(step_schedule, "day_offset", 0) or 0))
    if main or getattr(step_schedule, "anchor", "webinar") != "repeat":
        return dt
    if branch is None:
        return dt
    return dt + timedelta(minutes=int(branch.repeat_offset_minutes or 0))
