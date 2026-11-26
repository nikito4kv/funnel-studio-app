// Медиа шага: большая зона – перетащить файл, кликнуть или вставить из буфера (Ctrl+V); превью;
// «Заменить» и «Убрать» при наведении. Сервер чистит картинку (без EXIF, ≤1920 px, ≤1 МБ),
// видео хранит как есть. Путь к файлу – только в режиме разработчика.
//
// Тип «текст / сообщение с картинкой» Студия ведёт сама: положили картинку в текстовый шаг –
// он стал «с картинкой», убрали картинку – снова «текст». Иначе тип спорил с содержимым.
import { ImagePlus, Paperclip, RefreshCw, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { api, mediaUrl } from "../api";
import * as ops from "../model/ops";
import type { Media, Step } from "../model/types";
import { useSimple } from "../simple/mode";
import { useStudio } from "../store";
import { Check, Field } from "./ui";

function Slot({ label, value, launch, accept, image, onSet, hint, testId, bare }: {
  label: string; value: string | null; launch: string; accept: string; image?: boolean;
  onSet: (v: string | null) => void; hint?: string; testId?: string;
  /** Подпись уже в заголовке раздела – не повторять. */
  bare?: boolean;
}) {
  const file = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const simple = useSimple();
  const toast = useStudio((s) => s.toast);
  const url = image ? mediaUrl(value, launch) : null;
  const upload = async (f: File) => {
    setBusy(true);
    try {
      const info = await api.upload(f);
      onSet(info.ref);
      toast(`Загружено: ${info.kind === "image" ? `${info.width}×${info.height}, ` : ""}${Math.round(info.size / 1024)} КБ`, "ok");
    } catch (e) {
      toast(String((e as Error).message), "error");
    } finally {
      setBusy(false);
    }
  };
  const pick = () => file.current?.click();
  const fromList = (files: FileList | null | undefined) => {
    const f = files?.[0];
    if (f) void upload(f);
  };
  return (
    <div className="slot" data-testid={testId}>
      {(!bare || hint) && <div className="slot-head">{!bare && <b>{label}</b>}{hint && <span className="field-hint">{hint}</span>}</div>}
      <div className={"drop" + (value ? " has-file" : "") + (over ? " over" : "")} tabIndex={value ? -1 : 0} role={value ? undefined : "button"}
        aria-label={value ? undefined : `${label}: загрузить файл`}
        onClick={() => { if (!value && !busy) pick(); }}
        onKeyDown={(e) => { if (!value && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); pick(); } }}
        onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setOver(true); } }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); fromList(e.dataTransfer.files); }}
        onPaste={(e) => fromList(e.clipboardData.files)}>
        {value ? (
          <>
            {url ? <img className="slot-img" src={url} alt="" /> : (
              <div className="slot-file"><Paperclip size={15} strokeWidth={1.75} />{value.split("/").pop()}</div>
            )}
            <div className="slot-actions">
              <button type="button" onClick={pick} disabled={busy}><RefreshCw size={13} strokeWidth={2} />{busy ? "Загрузка…" : "Заменить"}</button>
              <button type="button" onClick={() => onSet(null)} aria-label="Убрать файл"><Trash2 size={13} strokeWidth={2} />Убрать</button>
            </div>
          </>
        ) : (
          <>
            {busy ? <Upload size={22} strokeWidth={1.5} /> : <ImagePlus size={22} strokeWidth={1.5} />}
            <span>{busy ? "Загружаю…" : over ? "Отпустите – загрузим" : "Перетащите файл сюда или кликните"}</span>
            <span className="faint">можно вставить из буфера: Ctrl+V</span>
          </>
        )}
        <input ref={file} type="file" accept={accept} hidden onChange={(e) => { fromList(e.target.files); e.target.value = ""; }} />
      </div>
      {!simple && (
        <input className="inp mono slot-path" value={value ?? ""} placeholder="./static/файл, https://… или file_id"
          onChange={(e) => onSet(e.target.value || null)} />
      )}
    </div>
  );
}

export function MediaEditor({ step, launch }: { step: Step; launch: string }) {
  const apply = useStudio((s) => s.apply);
  const m = step.media;
  const patch = (p: Partial<Media>) => apply((g) => ops.setMedia(g, { stepId: step.id, patch: p }), { coalesce: `media:${step.id}:${Object.keys(p).join()}` });
  const setImage = (v: string | null) => apply((g) => {
    let out = ops.setMedia(g, { stepId: step.id, patch: { image: v } });
    const s = out.steps.find((x) => x.id === step.id)!;
    if (v && s.type === "text" && !s.survey.qid) out = ops.setType(out, { stepId: step.id, type: "photo" });
    if (!v && s.type === "photo" && !s.media.images?.length) out = ops.setType(out, { stepId: step.id, type: "text" });
    return out;
  });
  const t = step.type;
  const img = "image/jpeg,image/png,image/webp";
  const vid = "video/mp4,video/quicktime,video/webm";
  return (
    <div className="media-ed">
      {(t === "photo" || t === "text" || t === "phone_request" || t === "email_request" || m.image) && (
        <Slot label="Картинка" bare={t === "photo" || t === "text"} value={m.image} launch={launch} accept={img} image onSet={setImage}
          hint={t === "photo" ? "Подпись к картинке – до 1024 знаков, длиннее уйдёт вторым сообщением" : undefined} testId="slot-image" />
      )}
      {m.images && <div className="muted small">Ещё картинки (альбом): {m.images.join(", ")}</div>}
      {t === "video_note" && (
        <>
          <Slot label="Кружок" value={m.video_note} launch={launch} accept={vid} onSet={(v) => patch({ video_note: v })}
            hint="Квадрат, до 60 секунд; в MAX уходит обычным видео" />
          <Slot label="Запасное видео" value={m.video_fallback} launch={launch} accept={vid} onSet={(v) => patch({ video_fallback: v })}
            hint="Тем, у кого кружки запрещены" />
          {m.video_note && m.video_fallback !== m.video_note && (
            <button type="button" onClick={() => patch({ video_fallback: m.video_note })}>Запасное = тот же файл</button>
          )}
        </>
      )}
      {t === "video" && <Slot label="Видео" bare value={m.video} launch={launch} accept={vid} onSet={(v) => patch({ video: v })} hint="Не тяжелее 20 МБ – больше Telegram не отдаёт" />}
      {t === "animation" && <Slot label="GIF / анимация" bare value={m.animation} launch={launch} accept={vid + ",image/gif"} onSet={(v) => patch({ animation: v })} />}
      {t === "rich" && (
        <Field label="Статья" hint="Только в Telegram; в MAX уйдёт текстом">
          <input className="inp mono" value={m.rich ?? ""} onChange={(e) => patch({ rich: e.target.value || null })} />
        </Field>
      )}
      {(t === "video" || t === "animation") && (
        <Check checked={m.media_optional} onChange={(v) => patch({ media_optional: v })} label="без файла – отправить один текст" />
      )}
      {(t === "video" || t === "animation") && !m.video && !m.animation && !m.media_optional && (
        <div className="warn-box">Без файла шаг молча пропустится.</div>
      )}
    </div>
  );
}
