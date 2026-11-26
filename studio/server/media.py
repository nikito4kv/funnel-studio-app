"""Загрузка медиа: файл → data/media/<sha256>.<ext>.

Картинки приводятся к виду, который принимают все платформы (исследование,
правила 16–27 и грабли VK из CLAUDE.md бота): без EXIF (VK молча отказывает),
не больше 1920 px по длинной стороне, не тяжелее 1 МБ. Поворот из EXIF
применяется к пикселям до того, как EXIF выбрасывается. Видео пока
сохраняется как есть.

sha256 считается от того, что реально сохранено: новое содержимое = новое имя
файла (правило 18 – кэш file_id по пути слал старую картинку).
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image, ImageOps

MAX_SIDE = 1920
MAX_BYTES = 1024 * 1024
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v"}
AUDIO_EXT = {".mp3", ".ogg", ".wav", ".m4a"}


class MediaError(ValueError):
    pass


def _encode_jpeg(img: Image.Image) -> bytes:
    if img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.getchannel("A"))
        img = bg
    for q in (90, 85, 80, 75, 70, 60, 50, 40):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        if buf.tell() <= MAX_BYTES:
            return buf.getvalue()
    # всё ещё тяжело – уменьшаем
    while True:
        img = img.resize((max(1, img.width * 3 // 4), max(1, img.height * 3 // 4)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=70, optimize=True, progressive=True)
        if buf.tell() <= MAX_BYTES or img.width < 200:
            return buf.getvalue()


def process_image(data: bytes) -> tuple[bytes, str, int, int]:
    """→ (байты, расширение, ширина, высота). Без EXIF, ≤1920 px, ≤1 МБ."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:  # noqa: BLE001
        raise MediaError(f"не удалось прочитать картинку: {e}") from e
    if getattr(img, "is_animated", False):
        raise MediaError("анимированная картинка – загрузите её как GIF-шаг (видео), а не как фото")
    img = ImageOps.exif_transpose(img)
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    if has_alpha:
        buf = io.BytesIO()
        clean = img.convert("RGBA")
        clean.save(buf, "PNG", optimize=True)   # новый файл без info/EXIF
        if buf.tell() <= MAX_BYTES:
            return buf.getvalue(), ".png", clean.width, clean.height
    out = _encode_jpeg(img)
    w, h = Image.open(io.BytesIO(out)).size
    return out, ".jpg", w, h


def save_upload(media_dir: Path, filename: str, data: bytes) -> dict:
    ext = Path(filename or "").suffix.lower()
    if not data:
        raise MediaError("пустой файл")
    width = height = None
    if ext in IMAGE_EXT:
        data, ext, width, height = process_image(data)
        kind = "image"
    elif ext in VIDEO_EXT:
        kind = "video"
    elif ext in AUDIO_EXT:
        kind = "audio"
    else:
        raise MediaError(f"тип файла «{ext or 'без расширения'}» не поддерживается: "
                         "картинки jpg/png/webp, видео mp4/mov/webm")
    sha = hashlib.sha256(data).hexdigest()
    name = f"{sha}{ext}"
    target = media_dir / name
    if not target.exists():
        target.write_bytes(data)
    return {
        "name": name, "sha256": sha, "kind": kind, "size": len(data),
        "width": width, "height": height,
        # так на медиа ссылается шаг: движок ищет ./static/<файл> в папке запуска
        "ref": f"./static/{name}",
    }


THUMB_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def thumbnail(src: Path, width: int, cache_dir: Path) -> Path | None:
    """Уменьшенная копия картинки для карточки холста (оригинал в 1–2 МБ на 300 px не нужен).

    Кэш по пути, времени изменения и ширине: подменили файл – копия пересоберётся.
    Не картинка или битый файл – None, отдаём оригинал.
    """
    if src.suffix.lower() not in THUMB_EXT:
        return None
    st = src.stat()
    key = hashlib.sha256(f"{src.resolve()}|{st.st_mtime_ns}|{st.st_size}|{width}".encode()).hexdigest()[:32]
    out = cache_dir / f"{key}.jpg"
    if out.is_file():
        return out
    try:
        with Image.open(src) as im:
            img = ImageOps.exif_transpose(im)
            if img.mode not in ("RGB", "L"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.convert("RGBA").split()[-1])
                img = bg
            if img.width > width:
                img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
            cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp")
            img.convert("RGB").save(tmp, "JPEG", quality=80, optimize=True)
            tmp.replace(out)
    except Exception:  # noqa: BLE001 – превью не должно ронять раздачу
        return None
    return out
