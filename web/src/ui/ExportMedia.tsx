// Выгрузка: какие загруженные в Студию файлы положить в static запуска + архив «yaml + файлы».
import { Download } from "lucide-react";
import { useState } from "react";
import { api, type ExportMediaItem } from "../api";
import type { Funnel } from "../model/types";
import { useStudio } from "../store";

export function ExportMedia({ funnel, media }: { funnel: Funnel; media: ExportMediaItem[] }) {
  const [busy, setBusy] = useState(false);
  const toast = useStudio((s) => s.toast);
  const launch = funnel.source?.launch || "<запуск>";
  const download = async () => {
    setBusy(true);
    try {
      const { blob, name } = await api.exportArchive(funnel);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="export-media" data-testid="export-media">
      <div className="row">
        <button type="button" className="primary" onClick={download} disabled={busy} data-testid="download-archive">
          <Download size={15} strokeWidth={1.75} />{busy ? "Собираю…" : "Скачать архив выгрузки (yaml + файлы)"}
        </button>
        <span className="muted small">в архиве – инструкция «куда что положить»</span>
      </div>
      {media.length > 0 ? (
        <div className="warn-box">
          <b>Эти файлы загружены в Студию – положите их в <span className="mono">launches/{launch}/static/</span>, не меняя имён:</b>
          <ul className="media-list">
            {media.map((m) => (
              <li key={m.name}><span className="mono">{m.name}</span> <span className="muted">· {Math.max(1, Math.round(m.size / 1024))} КБ · шаги: {m.steps.join(", ")}</span></li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="muted small">Загруженных в Студию файлов нет – картинки и видео уже лежат в static запуска.</div>
      )}
    </div>
  );
}
