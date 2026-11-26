"""Детерминированные id объектов Студии.

У шага два имени: неизменяемый `id` (UUID) и читаемый `key` (как в yaml).
Пока воронки живут в yaml, id выводится из (запуск, воронка, key) через uuid5:
повторный импорт того же файла даёт те же id, и заметки/раскладка/правки,
привязанные к id, не теряются. Когда Студия станет источником правды, id
будет выдаваться один раз при создании шага и больше не пересчитываться –
тогда переименование key ничего не ломает.
"""

from __future__ import annotations

import uuid

# Корневое пространство имён Студии. Менять нельзя: поменяются все id.
STUDIO_NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://funnel-studio.local/ns/v1")


def step_id(launch: str, funnel_id: str, key: str, occurrence: int = 1) -> uuid.UUID:
    """id шага. occurrence > 1 – повтор того же key в одной воронке (движок
    берёт первый, но в файле сохраняем все)."""
    suffix = "" if occurrence == 1 else f"#{occurrence}"
    return uuid.uuid5(STUDIO_NS, f"step:{launch}:{funnel_id}:{key}{suffix}")


def edge_id(from_step: uuid.UUID, kind: str, target: str, index: int) -> uuid.UUID:
    return uuid.uuid5(STUDIO_NS, f"edge:{from_step}:{kind}:{target}:{index}")
