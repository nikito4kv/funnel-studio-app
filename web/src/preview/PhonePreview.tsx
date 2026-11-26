// Окно «📱 Как в телефоне»: имитация экрана Telegram и MAX для выбранного шага.
// Плейсхолдеры подставлены примерами («Анна», время эфира ветки, дата), разметка – как в
// Telegram, кнопки – под сообщением (TG) или внутри пузыря (MAX).
import { TriangleAlert, X } from "lucide-react";
import { useMemo, useState } from "react";
import { mediaUrl } from "../api";
import type { Funnel, Platform, Step } from "../model/types";
import { schedulePhrase } from "../phrases";
import { branchNames, branchShort, humanizeTokens } from "../simple/labels";
import { useSimple } from "../simple/mode";
import { stepName } from "../simple/names";
import { useStudio } from "../store";
import { buildPreview, type PreviewButton, type PreviewMsg } from "./model";
import { bubbleHtml, sampleValues } from "./sample";

function cohortPhrase(when: Step["content"]["variants"][number]["when"]): string {
  const parts: string[] = [];
  if (when.utm_source) parts.push(`источник = ${when.utm_source}`);
  if (when.utm_medium) parts.push(`utm_medium = ${when.utm_medium}`);
  if (when.utm_campaign) parts.push(`кампания = ${when.utm_campaign}`);
  if (when.trigger) parts.push(`вход = ${when.trigger}`);
  if (when.lm_post_id) parts.push(`пост = ${when.lm_post_id}`);
  if (when.lm_post_id_in.length) parts.push(`пост ∈ ${when.lm_post_id_in.join(", ")}`);
  if (when.platforms.length) parts.push(when.platforms.join("/").toUpperCase());
  return parts.join(", ") || "всем";
}

function Media({ msg, launch }: { msg: PreviewMsg; launch: string }) {
  const url = mediaUrl(msg.media ?? null, launch);
  if (msg.mediaKind === "image" && url) return <img className="ph-media" src={url.startsWith("files/") ? `${url}?w=640` : url} alt="" />;
  if ((msg.mediaKind === "video" || msg.mediaKind === "animation") && url) {
    return <video className="ph-media" src={url} preload="metadata" muted playsInline controls={false} />;
  }
  const icon = msg.mediaKind === "video" ? "🎬" : msg.mediaKind === "animation" ? "🎞" : msg.mediaKind === "rich" ? "📰" : "🖼";
  return <div className="ph-media ph-media-empty">{icon}<span>{msg.media ? msg.media.split("/").pop() : "картинки пока нет"}</span></div>;
}

function Buttons({ buttons, platform, values }: { buttons: PreviewButton[]; platform: Platform; values: Record<string, string> }) {
  return (
    <div className={platform === "tg" ? "ph-kb" : "ph-kb ph-kb-max"}>
      {buttons.map((b, i) => (
        <div key={i} className="ph-kbtn" title={b.target ? humanizeTokens(b.target) : ""}>
          <span>{b.text.replace(/\{[^{}\s]+\}/g, (t) => values[t] ?? t)}</span>
          {b.kind === "url" && <span className="ph-kbtn-arrow">↗</span>}
        </div>
      ))}
    </div>
  );
}

function Bubble({ msg, platform, values, launch }: { msg: PreviewMsg; platform: Platform; values: Record<string, string>; launch: string }) {
  const html = msg.text ? bubbleHtml(msg.text, values) : "";
  if (msg.kind === "info") return <div className="ph-info">{msg.text}</div>;
  if (msg.kind === "sms") return <div className="ph-sms"><div className="ph-sms-head">SMS</div>{humanizeTokens(msg.text ?? "") || "текст SMS пуст"}</div>;
  if (msg.kind === "call") return <div className="ph-info">☎ {msg.text}</div>;
  if (msg.kind === "circle") {
    const url = mediaUrl(msg.media ?? null, launch);
    return (
      <div className="ph-row">
        <div className="ph-circle">
          {url ? <video src={url} preload="metadata" muted playsInline /> : <span>◉</span>}
        </div>
        {msg.buttons && platform === "tg" && <Buttons buttons={msg.buttons} platform={platform} values={values} />}
      </div>
    );
  }
  const inside = platform === "max" && msg.buttons?.length;
  return (
    <div className="ph-row">
      <div className={"ph-bubble" + (msg.kind === "media" ? " ph-bubble-media" : "")}>
        {msg.kind === "media" && <Media msg={msg} launch={launch} />}
        {html && <div className="ph-text" dangerouslySetInnerHTML={{ __html: html }} />}
        {inside ? <Buttons buttons={msg.buttons!} platform={platform} values={values} /> : null}
        <span className="ph-time">13:00</span>
      </div>
      {!inside && msg.buttons && msg.buttons.length > 0 && <Buttons buttons={msg.buttons} platform={platform} values={values} />}
    </div>
  );
}

export function PhonePreview({ step, funnel, onClose }: { step: Step; funnel: Funnel; onClose: () => void }) {
  const simple = useSimple();
  const storePlatform = useStudio((s) => s.platform);
  const storeBranch = useStudio((s) => s.branch);
  const [platform, setPlatform] = useState<Platform>(storePlatform);
  const branches = funnel.branch_router?.branches ?? [];
  const [branchId, setBranchId] = useState<string | null>(storeBranch ?? branches[0]?.id ?? null);
  const [variant, setVariant] = useState<number | null>(null);
  const branch = branches.find((b) => b.id === branchId) ?? null;
  const launch = funnel.source?.launch ?? "";
  const values = useMemo(() => sampleValues(step, funnel, { platform, branch }), [step, funnel, platform, branch]);
  const pv = useMemo(() => buildPreview(step, funnel, platform, variant), [step, funnel, platform, variant]);
  const when = schedulePhrase(step.schedule, branch, branchNames(branches, simple));
  const mx = step.content.max;
  const maxState = platform === "max"
    ? [mx.text === null ? "текст как в TG" : mx.text === "" ? "текст скрыт" : "свой текст",
      mx.buttons === null ? "кнопки как в TG" : mx.buttons.length === 0 ? "кнопки скрыты" : "свои кнопки"].join(" · ")
    : null;
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal phone-modal" onClick={(e) => e.stopPropagation()} data-testid="phone-preview">
        <div className="modal-head">
          <h3>Как увидит человек · {stepName(step)}</h3>
          <button type="button" className="x" aria-label="Закрыть" onClick={onClose}><X size={15} strokeWidth={2} /></button>
        </div>
        <div className="phone-wrap">
          <div className="phone-side">
            <div className="seg" data-testid="preview-platform">
              {(["tg", "max"] as Platform[]).map((p) => (
                <button type="button" key={p} className={platform === p ? "seg-on" : ""} onClick={() => setPlatform(p)}>{p === "tg" ? "Telegram" : "MAX"}</button>
              ))}
            </div>
            {maxState && <div className="muted small" data-testid="preview-max-state">MAX: {maxState}</div>}
            {branches.length > 1 && (
              <label className="field">
                <span className="field-label">ветка</span>
                <select className="inp" value={branchId ?? ""} onChange={(e) => setBranchId(e.target.value)}>
                  {branches.map((b) => <option key={b.id} value={b.id}>{simple ? branchShort(b, branches) : `${b.id} · ${b.webinar_time}`}</option>)}
                </select>
              </label>
            )}
            {step.content.variants.length > 0 && (
              <label className="field">
                <span className="field-label">кто пришёл</span>
                <select className="inp" value={variant ?? ""} onChange={(e) => setVariant(e.target.value === "" ? null : Number(e.target.value))}>
                  <option value="">обычный текст</option>
                  {step.content.variants.map((v, i) => <option key={i} value={i}>если {cohortPhrase(v.when)}</option>)}
                </select>
              </label>
            )}
            <div className="ph-legend">
              <div><b>Подставлено для примера:</b></div>
              <div>имя – «{values["{Имя}"]}», время эфира – {values["{webinar.time}"]}, дата – {values["{webinar.date}"]}</div>
              <div className="muted">ссылки показаны примерными адресами</div>
            </div>
            {when && <div className="phrase">⏱ {when}</div>}
            {pv.warnings.map((w) => <div className="warn-box" key={w} data-testid="preview-warning"><TriangleAlert size={14} strokeWidth={2} />{w}</div>)}
            {pv.notes.map((n) => <div className="muted small" key={n}>ℹ {n}</div>)}
          </div>
          <div className={`phone phone-${platform}`}>
            <div className="phone-top">
              <span className="phone-ava">{platform === "tg" ? "🤖" : "M"}</span>
              <div>
                <div className="phone-bot">{funnel.title || funnel.source?.launch || "Бот"}</div>
                <div className="phone-sub">{platform === "tg" ? "бот" : "чат-бот MAX"}</div>
              </div>
            </div>
            <div className="phone-chat">
              {pv.messages.map((m, i) => <Bubble key={i} msg={m} platform={platform} values={values} launch={launch} />)}
            </div>
            {pv.reply && <div className="phone-reply">{pv.reply}</div>}
            <div className="phone-input">{platform === "tg" ? "Сообщение" : "Написать сообщение"}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
