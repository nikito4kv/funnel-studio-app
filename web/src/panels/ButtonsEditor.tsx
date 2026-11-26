// Кнопки под сообщением: подпись + «Куда ведёт». Цель выбирается из списка, а не пишется
// руками: канал, ВИП, эфир, подарок, своя ссылка. У новой кнопки цели нет – «Куда ведёт?»,
// чтобы кнопка молча не увела людей в канал. Своя ссылка – отдельной строкой во всю ширину.
// Действие игры (callback) и мини-апп – только в режиме разработчика (или если уже заданы).
import { ArrowDown, ArrowUp, ExternalLink, Plus, X } from "lucide-react";
import { defaultButton } from "../model/defaults";
import type { Button } from "../model/types";
import { linkFilled } from "../model/validate";
import { useSimple } from "../simple/mode";
import { Check } from "./ui";

type Target = "channel" | "vip" | "stream" | "gift" | "url" | "callback" | "answer";

const TOKEN: Partial<Record<Target, string>> = {
  channel: "{channel.link}",
  vip: "{vip_link}",
  stream: "{stream_url}",
  gift: "{gift_url}",
};

export const TARGET_LABEL: Record<Target, string> = {
  channel: "канал",
  vip: "ВИП (касса)",
  stream: "эфир",
  gift: "подарок",
  url: "своя ссылка",
  callback: "действие игры (callback)",
  answer: "ответ опроса / без ссылки",
};

const SIMPLE_LABEL: Partial<Record<Target, string>> = {
  channel: "в закрытый канал",
  vip: "на оплату ВИП",
  stream: "на эфир",
  gift: "на подарок",
  url: "по своей ссылке",
  answer: "никуда – это ответ на вопрос",
};

export function targetOf(b: Button): Target {
  for (const [t, tok] of Object.entries(TOKEN)) if (b.url === tok) return t as Target;
  if (b.url !== null) return "url";
  if (b.callback_data !== null) return "callback";
  return "answer";
}

function retarget(b: Button, t: Target): Button {
  if (TOKEN[t]) return { ...b, url: TOKEN[t]!, callback_data: null };
  if (t === "url") return { ...b, url: b.url && !Object.values(TOKEN).includes(b.url) ? b.url : "", callback_data: null };
  if (t === "callback") return { ...b, url: null, web_app: false, callback_data: b.callback_data ?? "gm:" };
  return { ...b, url: null, callback_data: null, web_app: false };
}

export function ButtonsEditor({ buttons, onChange, survey }: { buttons: Button[]; onChange: (b: Button[]) => void; survey?: boolean }) {
  const simple = useSimple();
  const set = (i: number, b: Button) => onChange(buttons.map((x, j) => (j === i ? b : x)));
  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= buttons.length) return;
    const next = [...buttons];
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };
  return (
    <div className="buttons-ed" data-testid="buttons-editor">
      {!buttons.length && <div className="faint small">{survey ? "Вариантов ответа пока нет." : "Кнопок нет – сообщение уйдёт без них."}</div>}
      {buttons.map((b, i) => {
        const t = targetOf(b);
        // «без ссылки» у обычной кнопки – это «цель не выбрана»
        const value = t === "answer" && !survey ? "" : t;
        const options = (Object.keys(TARGET_LABEL) as Target[]).filter((k) =>
          k === t || !simple || (k !== "callback" && (k !== "answer" || survey)));
        const badUrl = t === "url" && !linkFilled(b.url ?? "");
        return (
          <div className="btn-row" key={i}>
            <div className="row">
              <input className="inp grow btn-face" value={b.text} placeholder={survey ? "Вариант ответа" : "Подпись кнопки"} aria-label="Подпись кнопки"
                onChange={(e) => set(i, { ...b, text: e.target.value })} />
              {buttons.length > 1 && <button type="button" className="icon-btn" aria-label="Выше" data-tip="Выше" onClick={() => move(i, -1)} disabled={i === 0}><ArrowUp size={14} strokeWidth={2} /></button>}
              {buttons.length > 1 && <button type="button" className="icon-btn" aria-label="Ниже" data-tip="Ниже" onClick={() => move(i, 1)} disabled={i === buttons.length - 1}><ArrowDown size={14} strokeWidth={2} /></button>}
              <button type="button" className="x" aria-label="Удалить кнопку" data-tip="Удалить кнопку" onClick={() => onChange(buttons.filter((_, j) => j !== i))}><X size={15} strokeWidth={2} /></button>
            </div>
            {(!survey || t !== "answer" || !simple) && (
              <div className="btn-target">
                <span className="muted">Ведёт</span>
                <select className={"inp grow" + (value === "" ? " empty" : "")} value={value} data-testid="button-target"
                  onChange={(e) => set(i, retarget(b, (e.target.value || "answer") as Target))}>
                  {value === "" && <option value="">куда? – выберите</option>}
                  {options.map((k) => <option key={k} value={k}>{simple ? SIMPLE_LABEL[k] ?? TARGET_LABEL[k] : TARGET_LABEL[k]}</option>)}
                </select>
              </div>
            )}
            {t === "url" && (
              <div className="row">
                <input className={"inp grow" + (badUrl ? " invalid" : "")} value={b.url ?? ""} placeholder="https://…" aria-label="Адрес ссылки" data-testid="button-url"
                  onChange={(e) => set(i, { ...b, url: e.target.value })} />
                {!badUrl && <a className="icon-link" href={b.url ?? ""} target="_blank" rel="noreferrer" aria-label="Открыть ссылку" data-tip="Открыть ссылку в новой вкладке"><ExternalLink size={15} strokeWidth={1.75} /></a>}
              </div>
            )}
            {t === "url" && badUrl && <div className="field-hint err">Вставьте адрес целиком: https://…</div>}
            {t === "callback" && <input className="inp mono" value={b.callback_data ?? ""} onChange={(e) => set(i, { ...b, callback_data: e.target.value })} />}
            {t !== "callback" && t !== "answer" && (!simple || b.web_app) && (
              <Check checked={b.web_app} onChange={(v) => set(i, { ...b, web_app: v })} label="открыть внутри Telegram (мини-апп)" hint="в MAX – обычная ссылка" />
            )}
            {t === "vip" && /подар/i.test(b.text) && <div className="warn-box">Кнопка ведёт на оплату ВИП, а подпись – про подарок. Проверьте.</div>}
            {t === "gift" && /vip|вип/i.test(b.text) && <div className="warn-box">ВИП-кнопка ведёт на подарок – нужна оплата.</div>}
          </div>
        );
      })}
      <button type="button" className="add" onClick={() => onChange([...buttons, { ...defaultButton(survey ? "Вариант ответа" : "Кнопка"), url: null }])}>
        <Plus size={15} strokeWidth={2} />{survey ? "Вариант ответа" : "Кнопка"}
      </button>
    </div>
  );
}
