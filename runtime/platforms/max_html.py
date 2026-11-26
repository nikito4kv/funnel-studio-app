"""HTML Telegram → HTML MAX.

Перенос `html_to_max` из bot-engine `common/max/html_convert.py` (коммит
f171a355) без изменений в поведении.

MAX с format=html понимает <b>, <i>, <u>, <s>, <code>, <pre>, <a href>. Не
понимает <tg-time> (показал бы как есть), <blockquote>, <tg-spoiler> и прочее –
тег снимаем, текст оставляем; <em>/<strong> → <i>/<b>.
"""
from __future__ import annotations

import re

_KEEP = {"b", "i", "u", "s", "code", "pre", "a"}
_RENAME = {"em": "i", "strong": "b"}


def html_to_max(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<tg-time[^>]*>(.*?)</tg-time>", r"\1", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<blockquote[^>]*>(.*?)</blockquote>", r"\n\1\n", text,
                  flags=re.DOTALL | re.IGNORECASE)
    for old, new in _RENAME.items():
        text = re.sub(rf"<{old}(\s[^>]*)?>", f"<{new}>", text, flags=re.IGNORECASE)
        text = re.sub(rf"</{old}>", f"</{new}>", text, flags=re.IGNORECASE)

    def _scrub(m: re.Match) -> str:
        return m.group(0) if m.group(1).lower() in _KEEP else ""

    text = re.sub(r"</?([a-zA-Z][a-zA-Z0-9-]*)(?:\s[^>]*)?/?>", _scrub, text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
