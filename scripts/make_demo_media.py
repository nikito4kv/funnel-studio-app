"""Generate the placeholder pictures and stub video circles used by the demo bot repository.

    uv run python scripts/make_demo_media.py

Writes into demo/botrepo/launches/<launch>/static/. Pictures are simple generated cards;
the .mp4 files are header-only stubs (no frames) with a 640x640 track and a 6 s duration:
enough for the Studio and the engine, which only read the container header.
"""
from __future__ import annotations

import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent / "demo" / "botrepo" / "launches"

# file name -> (headline, sub-line, accent colour)
CARDS = {
    "contact.jpg": ("CONFIRM YOU ARE HUMAN", "Share your contact to unlock the gifts", "#792aff"),
    "welcome.jpg": ("SPREADSHEET AUTOMATION WORKSHOP", "Live - 17 September - 11:00 MSK", "#792aff"),
    "reminder.jpg": ("STILL NOT IN THE CHANNEL?", "Two gifts are waiting inside", "#3a7afe"),
    "reminder2.jpg": ("THE CLASSROOM IS WARMING UP", "Formula cheat-sheet + template pack", "#3a7afe"),
    "vip.jpg": ("WORKSHOP TICKET", "Recording - 1:1 review - certificate", "#c8a200"),
    "vipnudge.jpg": ("YOUR TICKET IS STILL WAITING", "Access closes soon", "#c8a200"),
    "survey.jpg": ("A PERSONAL GIFT FOR YOU", "Answer 3 short questions", "#1f9d6b"),
    "botavatar.jpg": ("NORTHWIND", "Academy", "#792aff"),
    "botdesc.jpg": ("NORTHWIND ACADEMY", "Spreadsheets that work for you", "#792aff"),
}
EXTRA = {
    "autowebinar": {
        "auto-welcome.jpg": ("FROM MESSY DATA TO A CLEAN DASHBOARD", "3 days - 1 live lesson a day", "#792aff"),
        "auto-offer.jpg": ("THE FULL COURSE", "12 lessons - templates - mentor feedback", "#c8a200"),
    },
    "leadmagnet": {
        "cheatsheet.jpg": ("25 FORMULAS ON ONE PAGE", "Free formula cheat-sheet", "#1f9d6b"),
    },
}
LAUNCH_CARDS = {"webinar": list(CARDS), "autowebinar": [], "leadmagnet": []}


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def card(path: Path, head: str, sub: str, accent: str, size=(960, 540)) -> None:
    img = Image.new("RGB", size, "#111111")
    d = ImageDraw.Draw(img)
    w, h = size
    d.rounded_rectangle((28, 28, w - 28, h - 28), radius=25, fill="#212121")
    d.rounded_rectangle((28, 28, 44, h - 28), radius=8, fill=accent)
    # a few "doodle" blobs so the cards do not look like slides
    d.ellipse((w - 280, h - 260, w - 60, h - 40), outline=accent, width=6)
    d.line((w - 250, h - 90, w - 160, h - 190, w - 80, h - 110), fill="#bcfe53", width=7)
    f1, f2 = _font(54), _font(30)
    y = 120
    words, line, lines = head.split(), "", []
    for word in words:
        t = (line + " " + word).strip()
        if d.textlength(t, font=f1) > w - 240:
            lines.append(line)
            line = word
        else:
            line = t
    lines.append(line)
    for ln in lines:
        d.text((90, y), ln, font=f1, fill="#ffffff")
        y += 70
    d.text((90, y + 24), sub, font=f2, fill="#c9c9c9")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=78, optimize=True)


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def stub_mp4(path: Path, size: int = 640, seconds: int = 6) -> None:
    ftyp = _box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2mp41")
    timescale = 1000
    matrix = struct.pack(">9i", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
    # mvhd v0: version/flags, created, modified, timescale, duration, rate, volume, reserved, matrix, predefined, next id
    mvhd = _box(b"mvhd", struct.pack(">IIIII", 0, 0, 0, timescale, seconds * timescale)
                + struct.pack(">IH", 0x00010000, 0x0100) + bytes(10) + matrix + bytes(24) + struct.pack(">I", 2))
    # tkhd v0: version/flags, created, modified, track id, reserved, duration, 8 reserved, layer/group/volume/reserved,
    # matrix, width and height as 16.16 fixed point
    tkhd = _box(b"tkhd", struct.pack(">IIIIII", 3, 0, 0, 1, 0, seconds * timescale) + bytes(8)
                + struct.pack(">HHHH", 0, 0, 0, 0) + matrix + struct.pack(">II", size << 16, size << 16))
    trak = _box(b"trak", tkhd)
    moov = _box(b"moov", mvhd + trak)
    path.write_bytes(ftyp + moov + _box(b"mdat", b""))


def main() -> None:
    for launch, names in LAUNCH_CARDS.items():
        for name in names:
            head, sub, accent = CARDS[name]
            card(ROOT / launch / "static" / name, head, sub, accent)
    for launch, cards in EXTRA.items():
        for name, (head, sub, accent) in cards.items():
            card(ROOT / launch / "static" / name, head, sub, accent)
    for launch in ("webinar", "autowebinar"):
        for name in ("circle1.mp4", "circle2.mp4"):
            stub_mp4(ROOT / launch / "static" / name)
    print("demo media written to", ROOT)


if __name__ == "__main__":
    main()
