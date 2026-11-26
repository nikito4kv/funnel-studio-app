# VENDORED из bot-engine: common/launch_engine/funnel/dnd.py
# Коммит bot-engine: dd105332b3ddf41a2edab574d6605ea381d55283
# Логика не менялась. Правка только в импортах (scripts/vendor_sim.py):
#   loader движка -> studio.engine_ref.loader;
#   datetime -> studio.sim.clock.datetime (now() = время симуляции).
# Обновлять: uv run python scripts/vendor_sim.py
"""DND (Do-Not-Disturb) — перенос отложенных таймеров из ночного окна.

Если step запланирован на время в [from..to] МСК и шаг НЕ помечен
`dnd_exempt: true`, переносим на `reschedule_to` следующего дня
(обычно 09:01).

Используется и TG (`runner._schedule_or_fire`), и VK
(`vk_runner._schedule_or_fire`).

Окно может пересекать полночь (from > to, например 21:00 → 09:00).
В этом случае «в DND» = `(time >= from) OR (time < to)`.
"""

from __future__ import annotations

from datetime import time, timedelta

from studio.sim.clock import datetime

import pytz

_MSK = pytz.timezone("Europe/Moscow")


def _hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def apply_dnd_to_delay(
    delay_seconds: int,
    dnd_cfg,
    step_dnd_exempt: bool,
) -> int:
    """Возвращает скорректированный delay_seconds с учётом DND.

    Args:
        delay_seconds: исходная задержка перед fire_step.
        dnd_cfg: cfg.features.dnd (DndConfig).
        step_dnd_exempt: step.dnd_exempt флаг.

    Returns:
        Новый delay_seconds. Если шаг выпадает на DND и не exempt —
        перенесён на reschedule_to следующего дня. Иначе возвращает
        исходное значение.
    """
    if not dnd_cfg.enabled or step_dnd_exempt:
        return delay_seconds
    if delay_seconds <= 0:
        # Immediate fires пропускаем — это либо ручной trigger (review),
        # либо chained step из event loop; не задеваем.
        return delay_seconds

    try:
        dnd_from = _hhmm(dnd_cfg.from_)
        dnd_to = _hhmm(dnd_cfg.to)
        resched = _hhmm(dnd_cfg.reschedule_to)
    except Exception:
        return delay_seconds

    now_msk = datetime.now(_MSK)
    fire_at_msk = now_msk + timedelta(seconds=delay_seconds)
    fire_t = fire_at_msk.time()

    # Окно может пересекать полночь (from > to, как 21:00 → 09:00).
    if dnd_from > dnd_to:
        in_window = (fire_t >= dnd_from) or (fire_t < dnd_to)
    else:
        in_window = dnd_from <= fire_t < dnd_to

    if not in_window:
        return delay_seconds

    # Целевое время — reschedule_to. Если fire_at в вечернем сегменте
    # (>= from), переносим на след. день; если в утреннем (< to), на тот же день.
    target = fire_at_msk.replace(
        hour=resched.hour, minute=resched.minute, second=0, microsecond=0,
    )
    if dnd_from > dnd_to and fire_t >= dnd_from:
        target = target + timedelta(days=1)

    new_delay = int((target - now_msk).total_seconds())
    return max(0, new_delay)
