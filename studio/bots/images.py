"""Картинки раздела «Боты»: аватар (квадрат по центру, JPEG) и картинка описания (640×360)."""
from __future__ import annotations

import io
import re
from pathlib import Path

from PIL import Image, ImageOps

MEDIA_NAME = re.compile(r"^[0-9a-f]{64}\.(jpg|jpeg|png|webp)$")
AVATAR_MIN = 160          # меньше Telegram рисует мылом, а совсем мелкое не принимает
AVATAR_MAX = 1024
DESC_PIC = (640, 360)     # размер, который просит @BotFather для /setdescriptionpic


class ImageError(ValueError):
    pass


def media_file(media_dir: Path, name: str) -> Path:
    if not MEDIA_NAME.match(name or ""):
        raise ImageError("это не загруженная в Студию картинка – загрузите файл заново")
    p = Path(media_dir) / name
    if not p.is_file():
        raise ImageError("картинки нет в Студии – загрузите файл заново")
    return p


def _rgb(img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return img.convert("RGB")


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90, optimize=True)   # новый файл – без EXIF
    return buf.getvalue()


def avatar_jpeg(src: Path) -> bytes:
    """Квадрат по центру, не больше 1024 px, JPEG – Telegram принимает аватар только в .jpg."""
    try:
        with Image.open(src) as im:
            img = _rgb(im)
    except Exception as e:  # noqa: BLE001
        raise ImageError(f"не удалось прочитать картинку: {type(e).__name__}") from None
    side = min(img.size)
    if side < AVATAR_MIN:
        raise ImageError(f"картинка слишком маленькая ({img.width}×{img.height}): для аватара нужна хотя бы "
                         f"{AVATAR_MIN}×{AVATAR_MIN}, лучше 640×640")
    left, top = (img.width - side) // 2, (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side))
    if side > AVATAR_MAX:
        img = img.resize((AVATAR_MAX, AVATAR_MAX), Image.LANCZOS)
    return _jpeg(img)


def description_pic(src: Path) -> bytes:
    """Картинка описания для @BotFather: 640×360, обрезка по центру."""
    try:
        with Image.open(src) as im:
            img = _rgb(im)
    except Exception as e:  # noqa: BLE001
        raise ImageError(f"не удалось прочитать картинку: {type(e).__name__}") from None
    return _jpeg(ImageOps.fit(img, DESC_PIC, Image.LANCZOS, centering=(0.5, 0.5)))
