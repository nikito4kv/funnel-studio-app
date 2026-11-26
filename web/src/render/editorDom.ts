// Telegram-HTML ↔ DOM визуального редактора.
// В редакторе плейсхолдеры и «непрозрачные» теги (tg-time, tg-spoiler, tg-emoji…) – плашки
// contenteditable=false с исходным текстом в data-raw: их нельзя сломать правкой, и при
// сохранении они возвращаются в текст байт в байт.
import { PLACEHOLDERS } from "../model/placeholders";
import { PLACEHOLDER_HUMAN } from "../simple/labels";

const FORMAT = new Map<string, string>([
  ["B", "b"], ["STRONG", "b"], ["I", "i"], ["EM", "i"], ["U", "u"], ["INS", "u"],
  ["S", "s"], ["STRIKE", "s"], ["DEL", "s"], ["CODE", "code"], ["PRE", "pre"], ["BLOCKQUOTE", "blockquote"],
]);

const LABEL = new Map(PLACEHOLDERS.map((p) => [p.token, p.label]));

function esc(t: string): string {
  // Telegram требует экранировать все &, < и > вне тегов
  return t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function escAttr(t: string): string {
  return t.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

export function chip(raw: string, label: string, kind: "ph" | "raw"): HTMLElement {
  const el = document.createElement("span");
  el.className = kind === "ph" ? "ph" : "ph ph-raw";
  el.contentEditable = "false";
  el.dataset.raw = raw;
  el.textContent = label;
  return el;
}

export function placeholderChip(token: string, simple = false): HTMLElement {
  if (simple) {
    const el = chip(token, PLACEHOLDER_HUMAN[token] ?? `неизвестная вставка ${token}`, "ph");
    el.title = token;
    return el;
  }
  return chip(token, LABEL.get(token) ?? token.slice(1, -1), "ph");
}

/** Текст → фрагмент с плашками вместо {токенов}. */
export function textToFragment(text: string, simple = false): DocumentFragment {
  const frag = document.createDocumentFragment();
  let last = 0;
  for (const m of text.matchAll(/\{[^{}\s]+\}/g)) {
    const at = m.index ?? 0;
    if (at > last) frag.appendChild(document.createTextNode(text.slice(last, at)));
    frag.appendChild(placeholderChip(m[0], simple));
    last = at + m[0].length;
  }
  if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
  return frag;
}

function convert(src: Node, dst: Node, simple: boolean): void {
  src.childNodes.forEach((n) => {
    if (n.nodeType === Node.TEXT_NODE) {
      dst.appendChild(textToFragment(n.textContent ?? "", simple));
      return;
    }
    if (n.nodeType !== Node.ELEMENT_NODE) return;
    const el = n as Element;
    const tag = FORMAT.get(el.tagName);
    if (tag) {
      const out = document.createElement(tag);
      convert(el, out, simple);
      dst.appendChild(out);
    } else if (el.tagName === "A") {
      const a = document.createElement("a");
      a.setAttribute("href", el.getAttribute("href") ?? "");
      a.title = el.getAttribute("href") ?? "";
      convert(el, a, simple);
      dst.appendChild(a);
    } else {
      // tg-time, tg-spoiler, tg-emoji и всё незнакомое – плашкой, как есть
      const label = el.tagName === "TG-TIME" ? `время: ${el.textContent}` : `‹${el.tagName.toLowerCase()}› ${el.textContent ?? ""}`;
      dst.appendChild(chip(el.outerHTML, label, "raw"));
    }
  });
}

export function htmlToEditor(html: string, into: HTMLElement, simple = false): void {
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
  into.replaceChildren();
  convert(doc.body, into, simple);
}

/** DOM редактора → Telegram-HTML. */
export function editorToHtml(root: Node): string {
  let out = "";
  root.childNodes.forEach((n, i) => {
    if (n.nodeType === Node.TEXT_NODE) {
      out += esc(n.textContent ?? "");
      return;
    }
    if (n.nodeType !== Node.ELEMENT_NODE) return;
    const el = n as HTMLElement;
    if (el.dataset.raw !== undefined) {
      out += el.dataset.raw;
      return;
    }
    if (el.tagName === "BR") {
      out += "\n";
      return;
    }
    if (el.tagName === "DIV" || el.tagName === "P") {
      // Chrome иногда оборачивает новую строку в <div> – это перенос
      if (i > 0 && !out.endsWith("\n")) out += "\n";
      out += editorToHtml(el);
      return;
    }
    const tag = FORMAT.get(el.tagName);
    if (tag) {
      const inner = editorToHtml(el);
      out += inner ? `<${tag}>${inner}</${tag}>` : "";
      return;
    }
    if (el.tagName === "A") {
      out += `<a href="${escAttr(el.getAttribute("href") ?? "")}">${editorToHtml(el)}</a>`;
      return;
    }
    out += editorToHtml(el); // span со стилями и т.п. – только содержимое
  });
  return out;
}
