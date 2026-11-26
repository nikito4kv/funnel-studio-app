# VENDORED из bot-engine: common/launch_engine/services/video_meta.py
# Коммит bot-engine: dd105332b3ddf41a2edab574d6605ea381d55283 (файл-источник не менялся с f171a355)
# Скопировано как есть, логика не менялась (CLAUDE.md bot-engine §35: видео без
# width/height телеграм рисует квадратом).
"""Размеры и длительность mp4 — читаем сами, без ffprobe.

Зачем. `bot.send_video(...)` без `width`/`height` отдаёт телеграму файл «как
есть», и клиент рисует его в квадратной рамке: горизонтальный ролик выглядит
сплющенным. Тот же файл, залитый человеком из приложения, приходит нормально –
приложение размеры передаёт. Так 10.09.2026 в лидмагнитном боте «сплющило»
бонусный урок: в одном запуске у шага стоял file_id (заливал человек), а в другом
тот же mp4 уходил с диска ботом.

Читаем контейнер напрямую: ffprobe в образе нет, а нужны всего три числа.
Разбираем только заголовки (`moov`), сами кадры (`mdat`) не трогаем –
файл на 19 МБ разбирается за миллисекунды.

⚠️ Матрица поворота учитывается: у ролика с телефона в `tkhd` лежат размеры
ДО поворота, и без обмена сторон вертикальное видео уехало бы в горизонталь.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path

logger = logging.getLogger(__name__)

# Контейнеры, которые умеет разобрать этот модуль. Прочее (webm, gif) отдаём
# как раньше – без размеров.
_MP4_SUFFIXES = {".mp4", ".m4v", ".mov"}

_CONTAINERS = {b"moov", b"trak", b"mdia"}

_cache: dict[str, dict | None] = {}


def probe_mp4(path: str | Path) -> dict | None:
    """{'width', 'height', 'duration'} для mp4 либо None, если не разобрали.

    Ошибки не поднимаются: размеры – украшение, из-за них сообщение теряться
    не должно.
    """
    key = str(path)
    if key in _cache:
        return _cache[key]
    out = None
    try:
        if Path(key).suffix.lower() in _MP4_SUFFIXES:
            out = _probe(key)
    except Exception:
        logger.debug("probe_mp4 failed: %s", key, exc_info=True)
        out = None
    _cache[key] = out
    return out


def _probe(path: str) -> dict | None:
    width = height = 0
    duration = 0
    with open(path, "rb") as f:
        for typ, body, end in _walk(f, 0, Path(path).stat().st_size, 0):
            if typ == b"mvhd":
                duration = _mvhd_duration(f, body, end)
            elif typ == b"tkhd":
                w, h = _tkhd_size(f, body, end)
                # Берём самую крупную дорожку: у звуковой размеры нулевые,
                # а у файлов с несколькими видеодорожками картинку задаёт
                # основная.
                if w * h > width * height:
                    width, height = w, h
    if width <= 0 or height <= 0:
        return None
    return {"width": width, "height": height, "duration": duration}


def _walk(f, start: int, end: int, depth: int):
    """Обход дерева атомов. Заходим только в контейнеры, где лежат нужные
    заголовки, — иначе разбирали бы весь файл."""
    if depth > 6:
        return
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        head = f.read(8)
        if len(head) < 8:
            return
        size = struct.unpack(">I", head[:4])[0]
        typ = head[4:8]
        body = pos + 8
        if size == 1:
            ext = f.read(8)
            if len(ext) < 8:
                return
            size = struct.unpack(">Q", ext)[0]
            body = pos + 16
        elif size == 0:
            size = end - pos
        if size < 8 or pos + size > end:
            return
        yield typ, body, pos + size
        if typ in _CONTAINERS:
            yield from _walk(f, body, pos + size, depth + 1)
        pos += size


def _mvhd_duration(f, body: int, end: int) -> int:
    f.seek(body)
    d = f.read(min(end - body, 120))
    if not d:
        return 0
    ver = d[0]
    if ver == 1:
        scale = struct.unpack(">I", d[20:24])[0]
        dur = struct.unpack(">Q", d[24:32])[0]
    else:
        scale = struct.unpack(">I", d[12:16])[0]
        dur = struct.unpack(">I", d[16:20])[0]
    return int(dur / scale) if scale else 0


def _tkhd_size(f, body: int, end: int) -> tuple[int, int]:
    f.seek(body)
    d = f.read(min(end - body, 200))
    if not d:
        return 0, 0
    ver = d[0]
    # version+flags, времена, id, резерв, длительность
    off = 4 + (32 if ver == 1 else 20)
    off += 8       # reserved
    off += 8       # layer + alternate_group + volume + reserved
    matrix = struct.unpack(">9i", d[off:off + 36])
    off += 36
    w = struct.unpack(">I", d[off:off + 4])[0] / 65536
    h = struct.unpack(">I", d[off + 4:off + 8])[0] / 65536
    # Поворот на 90°/270°: в матрице a=d=0, b и c ненулевые.
    if matrix[0] == 0 and matrix[4] == 0 and (matrix[1] or matrix[3]):
        w, h = h, w
    return int(round(w)), int(round(h))
