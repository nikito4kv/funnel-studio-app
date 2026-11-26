// Безопасный показ HTML-текста шага: оставляем только разметку Telegram, остальное – текстом.
// Плейсхолдеры {Имя}, {channel.link} … показываются плашками: в простом режиме – словами
// («имя человека»), в режиме разработчика – как в yaml.
import { placeholderLabel } from "../simple/labels";

const ALLOWED = new Set([
  "B", "STRONG", "I", "EM", "U", "INS", "S", "STRIKE", "DEL", "CODE", "PRE", "A", "BLOCKQUOTE",
]);

function placeholders(text: string, into: Node, simple: boolean): void {
  const re = /\{[^{}\s]+\}/g;
  let last = 0;
  for (const m of text.matchAll(re)) {
    const at = m.index ?? 0;
    if (at > last) into.appendChild(document.createTextNode(text.slice(last, at)));
    const chip = document.createElement("span");
    chip.className = "ph";
    chip.textContent = placeholderLabel(m[0], simple);
    if (simple) chip.title = m[0];
    into.appendChild(chip);
    last = at + m[0].length;
  }
  if (last < text.length) into.appendChild(document.createTextNode(text.slice(last)));
}

function copy(src: Node, dst: Node, simple: boolean): void {
  src.childNodes.forEach((n) => {
    if (n.nodeType === Node.TEXT_NODE) {
      placeholders(n.textContent ?? "", dst, simple);
    } else if (n.nodeType === Node.ELEMENT_NODE) {
      const el = n as Element;
      if (ALLOWED.has(el.tagName)) {
        const isLink = el.tagName === "A";
        const out = document.createElement(isLink ? "span" : el.tagName.toLowerCase());
        if (isLink) out.className = "link";
        copy(el, out, simple);
        dst.appendChild(out);
      } else {
        copy(el, dst, simple); // tg-time, tg-spoiler и прочее – только содержимое
      }
    }
  });
}

export function safeHtml(text: string, simple = false): string {
  const doc = new DOMParser().parseFromString(`<body>${text}</body>`, "text/html");
  const out = document.createElement("div");
  copy(doc.body, out, simple);
  return out.innerHTML;
}
