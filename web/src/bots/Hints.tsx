// Пошаговые подсказки для @BotFather: как взять токен и что через API не меняется.
import { Check, Copy, Download, ImagePlus } from "lucide-react";
import { useState } from "react";
import { api } from "../api";
import { useStudio } from "../store";
import { botsApi, type BotProfile } from "./api";
import { botfatherHints, type HintStep } from "./text";

export function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // http без https: буфер обмена недоступен – через временное поле
      const t = document.createElement("textarea");
      t.value = text;
      document.body.appendChild(t);
      t.select();
      document.execCommand("copy");
      t.remove();
    }
    setDone(true);
    window.setTimeout(() => setDone(false), 1500);
  };
  return (
    <button type="button" className="copy-btn" onClick={() => void copy()} aria-label={`Скопировать ${text}`} data-tip="Скопировать">
      <code>{text}</code>{done ? <Check size={13} strokeWidth={2} /> : <Copy size={13} strokeWidth={1.75} />}
    </button>
  );
}

export function Steps({ steps }: { steps: HintStep[] }) {
  return (
    <ol className="bf-steps">
      {steps.map((s, i) => <li key={i}><span>{s.text}</span>{s.copy && <CopyButton text={s.copy} />}</li>)}
    </ol>
  );
}

/** Подготовить картинку описания 640×360 и скачать – для /setdescriptionpic. */
function DescriptionPic() {
  const toast = useStudio((s) => s.toast);
  const [media, setMedia] = useState<{ name: string; url: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const pick = async (f: File | undefined) => {
    if (!f) return;
    setBusy(true);
    try {
      const info = await api.upload(f);
      if (info.kind !== "image") throw new Error("нужна картинка jpg или png");
      setMedia({ name: info.name, url: info.url });
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="bf-pic">
      {media ? <img src={media.url} alt="" className="bf-pic-preview" /> : <div className="bf-pic-preview empty"><ImagePlus size={20} strokeWidth={1.5} />16:9</div>}
      <div className="bf-pic-actions">
        <label className="btn-like">
          <ImagePlus size={14} strokeWidth={1.75} />{busy ? "Загружаю…" : media ? "Другая картинка" : "Выбрать картинку"}
          <input type="file" accept="image/*" hidden onChange={(e) => { void pick(e.target.files?.[0]); e.target.value = ""; }} />
        </label>
        {media && (
          <a className="btn-like" href={botsApi.descriptionPicUrl(media.name)} download="description_640x360.jpg" data-testid="descpic-download">
            <Download size={14} strokeWidth={1.75} />Скачать 640×360
          </a>
        )}
        <span className="field-hint">Обрежем по центру до 640×360 – такой размер просит @BotFather</span>
      </div>
    </div>
  );
}

export function BotFatherHints({ profile }: { profile: BotProfile }) {
  const hints = botfatherHints(profile.username, profile.flags);
  return (
    <div className="bf-hints" data-testid="botfather-hints">
      {hints.map((h) => (
        <details key={h.id} className="bf-hint" data-testid={`hint-${h.id}`}>
          <summary><b>{h.title}</b>{h.state && <span className="muted"> · {h.state}</span>}</summary>
          <p className="muted">{h.why}</p>
          <Steps steps={h.steps} />
          {h.id === "descpic" && <DescriptionPic />}
        </details>
      ))}
    </div>
  );
}
