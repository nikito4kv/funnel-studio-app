# VENDORED из bot-engine: common/launch_engine/funnel/reg_window.py – только чистая часть
# Коммит bot-engine: dd105332b3ddf41a2edab574d6605ea381d55283
# Функции скопированы как есть (вырезаны по AST: parse_windows, in_windows, entry_step_keys).
# Чтение времени регистрации из базы бота (sqlalchemy) не копируется: у движка
# Студии своя база – runtime/gates/evaluate.py. Обновлять: scripts/vendor_engine.py.
"""Гейт «не слать тем, кто зарегистрировался в эти часы» (skip_if_registered_msk).

Поле шага – список окон МСК вида "HH:MM-HH:MM": левая граница включается,
правая нет. Попал час регистрации человека в окно – шаг пропускается, а
цепочка идёт дальше (раннер планирует on_done как у любого гейта).

Что считаем временем регистрации. Отдельной колонки нет и не нужно:
регистрация – это отправка ВХОДНОГО шага воронки. Берём ПОСЛЕДНЮЮ такую
отправку этому человеку из `<slug>_sent_messages`: повторный /start
перезапускает воронку, и отсчёт должен идти от него, а не от первого входа
полугодовой давности. Входные шаги – `entry_step` всех веток `branch_router`
и всех `entry_variants`; у воронки без роутера – её первый шаг.

TG и MAX пишут отправки в одну таблицу и различаются `kind`: 'funnel' у
телеграма, 'funnel_max' у MAX (там max_id лежит в колонке telegram_id).

Fail-open: время не нашлось (шаг входа не записался, человек пришёл до
появления таблицы) или упала база – шаг ШЛЁМ. Лишнее сообщение безопаснее
молчания, так устроены все гейты движка.

Зачем появилось: evergreen-воронка, окна регистрации.
"""

from __future__ import annotations
import re
from datetime import datetime
import pytz

_MSK = pytz.timezone("Europe/Moscow")
_WINDOW = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")


def parse_windows(windows: list[str]) -> list[tuple[int, int]]:
    """["11:00-12:00"] → [(660, 720)] – минуты от полуночи, [от, до).

    ValueError на любом кривом окне: формат, час > 23 (справа допустимо
    ровно 24:00 – «до конца суток»), минуты > 59, пустое или вывернутое окно.
    Окна через полночь («23:00-01:00») не поддерживаются – задайте двумя.
    """
    out: list[tuple[int, int]] = []
    for w in windows or []:
        m = _WINDOW.match((w or "").strip())
        if not m:
            raise ValueError(f"окно {w!r}: нужен формат HH:MM-HH:MM")
        h1, m1, h2, m2 = (int(x) for x in m.groups())
        if h1 > 23 or m1 > 59 or m2 > 59 or h2 > 24 or (h2 == 24 and m2):
            raise ValueError(f"окно {w!r}: такого времени нет")
        a, b = h1 * 60 + m1, h2 * 60 + m2
        if a >= b:
            raise ValueError(f"окно {w!r}: начало должно быть раньше конца "
                             "(через полночь – двумя окнами)")
        out.append((a, b))
    return out


def in_windows(dt_msk: datetime, windows: list[str]) -> bool:
    """Попадает ли момент (МСК) в одно из окон. Правая граница не включается."""
    t = dt_msk.hour * 60 + dt_msk.minute
    return any(a <= t < b for a, b in parse_windows(windows))


def entry_step_keys(funnel) -> set[str]:
    """Ключи входных шагов воронки – их отправка и есть «регистрация»."""
    keys: set[str] = set()
    router = getattr(funnel, "branch_router", None)
    for br in (getattr(router, "branches", None) or []):
        if br.entry_step:
            keys.add(br.entry_step)
    for v in (getattr(funnel, "entry_variants", None) or []):
        if v.entry_step:
            keys.add(v.entry_step)
    if not keys:
        first = getattr(funnel, "first_step", None)
        if first is not None:
            keys.add(first.key)
    return keys
