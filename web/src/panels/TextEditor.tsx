// Редактор текста сообщения: визуальный режим (жирный, курсив, ссылка, плашки плейсхолдеров)
// и режим «HTML». Автозамены при вводе: [ИМЯ] / ИМЯ, → {Имя}, ОСНОВНОЕ ВРЕМЯ, ВРЕМЯ ПОВТОРА →
// токены времени, длинное тире → короткое. Счётчик: 1024 с медиа / 4096 без.
import { CircleAlert, Link2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { autoReplaceAll, autoReplaceAt, PLACEHOLDERS } from "../model/placeholders";

/** Постановщику – 8 частых вставок, остальное в «ещё»; служебные токены он не видит вовсе. */
const FREQUENT = ["{Имя}", "{channel.link}", "{stream_url}", "{vip_link}", "{gift_url}", "{webinar.time}", "{webinar.date}", "{ref_link}"];
const SERVICE = ["{base_url}", "{go_prefix}", "{slug}", "{platform_tag}", "{platform}", "{webinar.datetime_msk}", "{webinar.unix}",
  "{webinar.main_unix}", "{tg_id}", "{max_id}", "{user_id}", "{src_suffix}"];
import { htmlProblems, plainText } from "../model/validate";
import { editorToHtml, htmlToEditor, placeholderChip, textToFragment } from "../render/editorDom";
import { PLACEHOLDER_HUMAN } from "../simple/labels";
import { useSimple } from "../simple/mode";
import { useStudio } from "../store";

/** Простой режим: слово вместо адреса ссылки → токен движка. */
const LINK_WORDS: Record<string, string> = {
  "канал": "{channel.link}", "эфир": "{stream_url}", "вип": "{vip_link}", "оплата": "{vip_link}", "подарок": "{gift_url}",
};

interface Props {
  value: string;
  onChange: (v: string) => void;
  limit?: number;
  limitNote?: string;
  /** Меняется при смене шага/платформы – редактор перечитывает текст целиком. */
  docKey: string;
  autoFocus?: boolean;
  minRows?: number;
  testId?: string;
}

function insertNodeAtCaret(root: HTMLElement, node: Node): void {
  const sel = window.getSelection();
  let range: Range;
  if (sel && sel.rangeCount && root.contains(sel.getRangeAt(0).startContainer)) range = sel.getRangeAt(0);
  else {
    range = document.createRange();
    range.selectNodeContents(root);
    range.collapse(false);
  }
  range.deleteContents();
  const last = node.nodeType === Node.DOCUMENT_FRAGMENT_NODE ? node.lastChild : node;
  range.insertNode(node);
  if (last) {
    range.setStartAfter(last);
    range.collapse(true);
    sel?.removeAllRanges();
    sel?.addRange(range);
  }
}

export function TextEditor({ value, onChange, limit, limitNote, docKey: rawKey, autoFocus, minRows = 6, testId }: Props) {
  const simple = useSimple();
  const docKey = `${rawKey}:${simple ? "s" : "d"}`;
  const [htmlMode, setMode] = useState<"visual" | "html">("visual");
  // в простом режиме HTML не показываем – только визуальный редактор
  const mode = simple ? "visual" : htmlMode;
  const ref = useRef<HTMLDivElement>(null);
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const emitted = useRef<string | null>(null);
  const focusText = useStudio((s) => s.focusText);

  // внешние изменения (другой шаг, undo) → перечитать; свои правки не перерисовываем
  const lastKey = useRef<string | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (mode !== "visual" || !el) return;
    if (lastKey.current === docKey && emitted.current === value && el.dataset.filled) return;
    lastKey.current = docKey;
    htmlToEditor(value, el, simple);
    el.dataset.filled = "1";
    emitted.current = value;
  }, [value, docKey, mode]);

  useEffect(() => {
    if (!focusText && !autoFocus) return;
    const el = mode === "visual" ? ref.current : areaRef.current;
    if (!el) return;
    el.focus();
    if (el === ref.current) {
      // курсор – в конец текста
      const r = document.createRange();
      r.selectNodeContents(el);
      r.collapse(false);
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(r);
    }
    useStudio.getState().textFocused();
  }, [focusText, autoFocus, mode]);

  // место курсора – чтобы вставка плейсхолдера из списка шла туда, где стоял курсор
  const savedRange = useRef<Range | null>(null);
  const saveRange = () => {
    const sel = window.getSelection();
    if (sel && sel.rangeCount && ref.current?.contains(sel.getRangeAt(0).startContainer)) {
      savedRange.current = sel.getRangeAt(0).cloneRange();
    }
  };

  const emit = () => {
    saveRange();
    if (!ref.current) return;
    const html = editorToHtml(ref.current);
    emitted.current = html;
    onChange(html);
  };

  const onInput = () => {
    const sel = window.getSelection();
    if (sel && sel.isCollapsed && sel.anchorNode?.nodeType === Node.TEXT_NODE) {
      const node = sel.anchorNode as Text;
      const before = node.data.slice(0, sel.anchorOffset);
      const r = autoReplaceAt(before);
      if (r) {
        const range = document.createRange();
        range.setStart(node, sel.anchorOffset - r.remove);
        range.setEnd(node, sel.anchorOffset);
        sel.removeAllRanges();
        sel.addRange(range);
        insertNodeAtCaret(ref.current!, textToFragment(r.insert, simple));
      }
    }
    emit();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      document.execCommand("insertText", false, "\n");
    }
  };

  const onPaste = (e: React.ClipboardEvent) => {
    e.preventDefault();
    const text = autoReplaceAll(e.clipboardData.getData("text/plain"));
    insertNodeAtCaret(ref.current!, textToFragment(text, simple));
    emit();
  };

  const cmd = (name: "bold" | "italic" | "underline" | "strikeThrough") => {
    document.execCommand("styleWithCSS", false, "false");
    document.execCommand(name);
    emit();
  };

  const link = () => {
    const raw = simple
      ? window.prompt("Куда ведёт ссылка? Адрес https://… или слово: канал, эфир, вип, подарок", "канал")
      : window.prompt("Ссылка (можно плейсхолдер, например {channel.link}):", "{channel.link}");
    if (!raw) return;
    const url = simple ? (LINK_WORDS[raw.trim().toLowerCase()] ?? raw.trim()) : raw;
    document.execCommand("createLink", false, url);
    emit();
  };

  const insertToken = (token: string) => {
    if (mode === "visual") {
      ref.current?.focus();
      if (savedRange.current) {
        const sel = window.getSelection();
        sel?.removeAllRanges();
        sel?.addRange(savedRange.current);
      }
      insertNodeAtCaret(ref.current!, placeholderChip(token, simple));
      emit();
    } else {
      const el = areaRef.current!;
      const s = el.selectionStart;
      const next = value.slice(0, s) + token + value.slice(el.selectionEnd);
      onChange(next);
      requestAnimationFrame(() => { el.focus(); el.selectionStart = el.selectionEnd = s + token.length; });
    }
  };

  const onArea = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const el = e.target;
    let v = el.value;
    let caret = el.selectionStart;
    const r = autoReplaceAt(v.slice(0, caret));
    if (r) {
      v = v.slice(0, caret - r.remove) + r.insert + v.slice(caret);
      caret = caret - r.remove + r.insert.length;
      requestAnimationFrame(() => { el.selectionStart = el.selectionEnd = caret; });
    }
    onChange(v);
  };

  const len = plainText(value).length;
  const problems = htmlProblems(value);
  const keep = (e: React.MouseEvent) => e.preventDefault(); // кнопки панели не забирают фокус

  return (
    <div className="texted" data-testid={testId}>
      <div className="toolbar">
        <button type="button" onMouseDown={keep} onClick={() => cmd("bold")} disabled={mode === "html"} title="Жирный"><b>Ж</b></button>
        <button type="button" onMouseDown={keep} onClick={() => cmd("italic")} disabled={mode === "html"} title="Курсив"><i>К</i></button>
        <button type="button" onMouseDown={keep} onClick={() => cmd("underline")} disabled={mode === "html"} title="Подчёркнутый"><u>Ч</u></button>
        <button type="button" onMouseDown={keep} onClick={link} disabled={mode === "html"} title="Ссылка" aria-label="Ссылка"><Link2 size={15} strokeWidth={2} /></button>
        <select className="ph-picker" value="" onMouseDown={(e) => e.stopPropagation()} onChange={(e) => e.target.value && insertToken(e.target.value)} title="Вставить плейсхолдер">
          <option value="">{simple ? "Вставить…" : "{ } вставить…"}</option>
          {(simple ? PLACEHOLDERS.filter((p) => FREQUENT.includes(p.token)) : PLACEHOLDERS.filter((p) => p.palette)).map((p) => (
            <option key={p.token} value={p.token}>{simple ? PLACEHOLDER_HUMAN[p.token] : `${p.label} – ${p.token}`}</option>
          ))}
          <optgroup label="ещё">
            {(simple ? PLACEHOLDERS.filter((p) => !FREQUENT.includes(p.token) && !SERVICE.includes(p.token)) : PLACEHOLDERS.filter((p) => !p.palette))
              .map((p) => <option key={p.token} value={p.token}>{simple ? PLACEHOLDER_HUMAN[p.token] ?? p.token : p.token}</option>)}
          </optgroup>
        </select>
        <span className="grow" />
        {!simple && <button type="button" className={"html-btn" + (mode === "html" ? " on" : "")} onClick={() => setMode(mode === "html" ? "visual" : "html")} title="Показать разметку">HTML</button>}
      </div>
      {mode === "visual" ? (
        <div ref={ref} className="rich mint" contentEditable suppressContentEditableWarning
          style={{ minHeight: minRows * 19 }} onInput={onInput} onKeyDown={onKeyDown} onPaste={onPaste} onKeyUp={saveRange} onMouseUp={saveRange} data-testid="rich" />
      ) : (
        <textarea ref={areaRef} className="inp mono html-area" rows={Math.max(minRows, 10)} value={value} onChange={onArea} />
      )}
      <div className="text-foot">
        {limit !== undefined && (
          <span className={len > limit ? "count over" : "count"} title={limitNote}>
            {len} / {limit}{limitNote && len > limit ? ` – ${limitNote}` : ""}
          </span>
        )}
        {problems.map((p) => <span className="err" key={p}><CircleAlert size={12} strokeWidth={2} /> {p}</span>)}
      </div>
    </div>
  );
}
