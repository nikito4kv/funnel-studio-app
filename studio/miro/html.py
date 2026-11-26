"""Разметка Miro → HTML Telegram.

Miro хранит текст элемента как HTML редактора доски: абзац – `<p>`, перенос –
`<br />`, жирный – `<strong>`, курсив – `<em>`, ссылки – `<a href>`, цвет –
`<span style>`. Telegram понимает узкое подмножество: `b i u s a code pre
blockquote tg-spoiler`. Здесь – перевод одного в другое:

  * абзац и `<br>` – перевод строки, больше двух переводов подряд – два;
  * `strong/b` → `<b>`, `em/i` → `<i>`, `u`, `s/strike/del` → `<s>`, `a` → `<a href>`;
  * `span` и прочее оформление (цвет, фон) – выбрасываются, текст остаётся;
  * пробелы и переводы строк на краях тега выносятся наружу (`<b>\nТекст</b>` →
    `\n<b>Текст</b>`), соседние одинаковые теги склеиваются, пустые – пропадают;
  * `&`, `<`, `>` в тексте экранируются, числовые сущности (`&#x1f447;`) – в символы.
"""
from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser

TAG_MAP = {"strong": "b", "b": "b", "em": "i", "i": "i", "u": "u", "ins": "u",
           "s": "s", "strike": "s", "del": "s", "code": "code", "pre": "pre",
           "blockquote": "blockquote"}
BREAK_TAGS = {"br"}
BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"}
_SIMPLE = ("b", "i", "u", "s", "code")


class _Conv(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []        # открытые теги Telegram (для закрытия по порядку)
        self.block_open = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in BREAK_TAGS:
            self.out.append("\n")
            return
        if tag in BLOCK_TAGS:
            if self.block_open:
                self.out.append("\n")
            self.block_open = True
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            self.out.append(f'<a href="{_html.escape(href, quote=True)}">')
            self.stack.append("a")
            return
        t = TAG_MAP.get(tag)
        if t:
            self.out.append(f"<{t}>")
            self.stack.append(t)
        else:
            self.stack.append("")          # span и т.п. – без следа

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in BLOCK_TAGS or tag in BREAK_TAGS:
            return
        if not self.stack:
            return
        t = self.stack.pop()
        if t:
            self.out.append(f"</{t}>")

    def handle_startendtag(self, tag, attrs):
        if tag.lower() in BREAK_TAGS:
            self.out.append("\n")

    def handle_data(self, data):
        self.out.append(_html.escape(data, quote=False))


def _tidy(s: str) -> str:
    for _ in range(4):
        prev = s
        # пробелы/переводы на краях тега – наружу
        s = re.sub(r"<(b|i|u|s|code)>(\s+)", r"\2<\1>", s)
        s = re.sub(r"(\s+)</(b|i|u|s|code)>", r"</\2>\1", s)
        s = re.sub(r'(<a href="[^"]*">)(\s+)', r"\2\1", s)
        s = re.sub(r"(\s+)</a>", r"</a>\1", s)
        # пустые теги
        s = re.sub(r"<(b|i|u|s|code)></\1>", "", s)
        s = re.sub(r'<a href="[^"]*"></a>', "", s)
        # соседние одинаковые теги склеиваем
        s = re.sub(r"</(b|i|u|s)>(\s*)<\1>", r"\2", s)
        if s == prev:
            break
    return s


def to_tg_html(content: str | None) -> str:
    """HTML доски → HTML Telegram (без правил Студии: тире, плейсхолдеры – отдельно)."""
    if not content:
        return ""
    p = _Conv()
    p.feed(content)
    p.close()
    s = "".join(p.out)
    s = s.replace(" ", " ")
    s = _tidy(s)
    lines = [ln.rstrip() for ln in s.split("\n")]
    s = "\n".join(lines)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip("\n").strip()


_TAG_RE = re.compile(r"<[^>]+>")


def plain(content: str | None) -> str:
    """Текст без разметки: для классификации и сравнения."""
    s = to_tg_html(content)
    s = _TAG_RE.sub("", s)
    return _html.unescape(s).strip()


def html_plain(tg_html: str) -> str:
    """HTML Telegram → текст (для сравнения и имён)."""
    return _html.unescape(_TAG_RE.sub("", tg_html or "")).strip()


def links(content: str | None) -> list[tuple[str, str]]:
    """Ссылки элемента: [(href, видимый текст)] в порядке появления + голые адреса в тексте."""
    out: list[tuple[str, str]] = []
    if not content:
        return out
    for m in re.finditer(r'<a\s+[^>]*href="([^"]*)"[^>]*>(.*?)</a>', content, re.S | re.I):
        out.append((_html.unescape(m.group(1)).strip(), plain(m.group(2))))
    bare = _TAG_RE.sub(" ", re.sub(r"<a\s[^>]*>.*?</a>", " ", content, flags=re.S | re.I))
    for m in re.finditer(r"https?://[^\s<>\"']+", _html.unescape(bare)):
        out.append((m.group(0).rstrip(".,;:!?)»"), m.group(0)))
    return out
