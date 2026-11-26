"""Служебный вход «Проверить в боте» из Студии.

Кнопка Студии открывает `https://t.me/<бот>?start=studio_test_<id воронки>`. Такой
/start запускает человека в ЭТУ воронку с начала, даже если /start бота ведёт в
другую (стартовая воронка запуска – `entry_funnel`):

  * ожидающие задачи человека в этой воронке отменяются;
  * анти-дубль 90 с для нового прохода не действует (отметка `start_fresh` в
    `actions` – исполнитель считает дубли только после неё);
  * UTM и метка входа из служебного payload не берутся – это не рекламный заход.

Безопасно: человек перезапускает только себя. Работает у любого запуска, но только
для воронки, опубликованной в этом запуске (иначе – обычный /start).

Telegram пускает в payload только `A-Za-z0-9_-` и не длиннее 64 символов. Id
воронки, который туда не влезает, заменяется коротким хэшем (`studio_th_<12 hex>`).
"""
from __future__ import annotations

import hashlib
import re

PREFIX = "studio_test_"
HASH_PREFIX = "studio_th_"
_OK = re.compile(r"^[A-Za-z0-9_-]+$")
MAX_LEN = 64


def _hash(funnel_id: str) -> str:
    return hashlib.sha1(funnel_id.encode("utf-8")).hexdigest()[:12]


def payload_for(funnel_id: str) -> str:
    """Payload /start для «Проверить в боте» этой воронки."""
    if _OK.match(funnel_id) and len(PREFIX) + len(funnel_id) <= MAX_LEN:
        return PREFIX + funnel_id
    return HASH_PREFIX + _hash(funnel_id)


def is_test_payload(payload: str) -> bool:
    return payload.startswith(PREFIX) or payload.startswith(HASH_PREFIX)


def match(payload: str, funnel_ids) -> str | None:
    """Служебный payload → id воронки из `funnel_ids` (или None)."""
    ids = list(funnel_ids)
    if payload.startswith(PREFIX):
        want = payload[len(PREFIX):]
        return want if want in ids else None
    if payload.startswith(HASH_PREFIX):
        want = payload[len(HASH_PREFIX):]
        return next((f for f in ids if _hash(f) == want), None)
    return None


def link(username: str, funnel_id: str) -> str:
    return f"https://t.me/{username.lstrip('@')}?start={payload_for(funnel_id)}"
