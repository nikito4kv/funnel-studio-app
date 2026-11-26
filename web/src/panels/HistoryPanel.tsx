// Панель «История»: черновик и версии (номер, статус, время МСК, автор, комментарий),
// «Сравнить с черновиком», «Сравнить с предыдущей», «Откатить сюда»; в какой бот версия ушла
// (режим или «не ушла»), «Запустить в боте» для последней версии.
import { CircleCheck, CircleX, History, X } from "lucide-react";
import { useState } from "react";
import { isDirty, useStudio } from "../store";
import { mskTime, versionsApi, type VersionRow } from "../versions/api";
import { DiffList } from "../versions/DiffList";
import { botName, engineApi, loadAuthor, sendLabel, sentOk } from "../versions/engine";
import { useVersions } from "../versions/store";

export function HistoryPanel() {
  const name = useStudio((s) => s.name)!;
  const info = useVersions((s) => s.info);
  const live = useVersions((s) => s.live);
  const overlay = useVersions((s) => s.overlay);
  const base = useVersions((s) => s.base);
  const compare = useVersions((s) => s.compare);
  const error = useVersions((s) => s.error);
  const v = useVersions.getState();
  const [busy, setBusy] = useState<string | null>(null);

  const withDraft = (n: number) => {
    v.setBase(n);
    v.set({ compare: null });
    useStudio.getState().toast(`Холст: изменения черновика против v${n}`, "info");
  };
  const withPrev = async (row: VersionRow) => {
    setBusy(`prev${row.number}`);
    try {
      const d = await versionsApi.diff(name, row.number - 1, row.number);
      v.set({ compare: { title: `v${row.number - 1} → v${row.number}`, diff: d, at: row.number } });
    } catch (e) {
      useStudio.getState().toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  };
  const rollback = async (row: VersionRow) => {
    const st = useStudio.getState();
    const warn = isDirty(st) ? "\nНесохранённые правки черновика пропадут." : "";
    if (!window.confirm(`Черновик станет копией v${row.number} (заметки останутся).${warn}\nОпубликовать – отдельной кнопкой. Откатить?`)) return;
    setBusy(`rb${row.number}`);
    try {
      const r = await versionsApi.rollback(name, row.number);
      useStudio.getState().load(name, r.graph);
      v.set({ compare: null });
      await v.refresh(name);
      useStudio.getState().toast(`Черновик = копия v${row.number}. Чтобы это ушло в бот – «Запустить в боте…»`, "ok");
    } catch (e) {
      useStudio.getState().toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  };

  const sendToEngine = async (row: VersionRow, tenant: string) => {
    setBusy(`eng${row.number}`);
    try {
      const r = await engineApi.send(name, row.number, { tenant, mode: "new-only", redirects: {}, author: loadAuthor() || "я" });
      useStudio.getState().toast(r.ok ? `Версия ${row.number} запущена в боте ${bot(tenant)}` : `В бот не ушла: ${r.error}`, r.ok ? "ok" : "error");
      await v.refresh(name);
    } catch (e) {
      useStudio.getState().toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  };

  const n = live?.summary.total ?? 0;
  const target = info?.engine_target ?? null;
  const tenants = useVersions((s) => s.engine?.tenants ?? []);
  const withBots = useVersions((s) => !!s.engine?.configured);
  function bot(slug: string): string {
    const t = tenants.find((x) => x.slug === slug);
    return t ? botName(t) : `«${slug}»`;
  }
  return (
    <div className="panel-body history" data-testid="history-panel">
      <div className="panel-title"><span><History size={18} strokeWidth={1.75} />История версий</span>
        {info && <span className="muted">версий {info.versions.length}</span>}
      </div>
      {error && <div className="err-box">{error}</div>}
      {!info && !error && <div className="muted">Загружаю…</div>}
      {info && (
        <div className="hv hv-draft">
          <div className="hv-head">
            <b>Черновик</b>
            <span className="hv-status st-draft">{info.published === null ? "не опубликован" : n ? `изменено ${n}` : "как опубликованная"}</span>
          </div>
          <div className="muted">
            сохранён {mskTime(info.draft.updated_at)}{info.draft.base_version ? ` · от v${info.draft.base_version}` : ""}
            {info.draft.migrated_from ? ` · перенесён из ${info.draft.migrated_from}` : ""}
          </div>
          {(overlay ?? live) && (overlay ?? live)!.summary.total > 0 && (
            <details className="hv-draft-diff" open>
              <summary>Что изменено{base !== null ? ` относительно v${base}` : ""} · {(overlay ?? live)!.summary.total}</summary>
              <DiffList diff={(overlay ?? live)!} />
            </details>
          )}
          {info.published !== null && (
            <div className="row">
              <button type="button" onClick={() => v.set({ publishOpen: true })}>{withBots ? "Запустить в боте…" : "Опубликовать…"}</button>
              {base !== null && <button type="button" onClick={() => v.setBase(null)}>Сравнивать с опубликованной</button>}
            </div>
          )}
        </div>
      )}
      {info?.versions.map((row) => (
        <div key={row.number} className="hv-wrap">
        <div className={`hv hv-${row.status}`} data-testid={`version-${row.number}`}>
          <div className="hv-head">
            <b>v{row.number}</b>
            <span className={`hv-status st-${row.status}`}>{row.status === "published" ? "опубликована" : "архив"}</span>
            <span className="grow" />
            <span className="muted">{mskTime(row.created_at)}</span>
          </div>
          <div className="hv-comment">{row.comment || <span className="muted">без комментария</span>}</div>
          <div className="muted">
            автор: {row.author || "–"}{row.parent ? ` · от v${row.parent}` : ""}
            {row.number === 1 ? " · первая версия" : row.report.summary ? ` · изменений ${row.report.summary.total}` : ""}
          </div>
          {(row.engine?.length ?? 0) > 0 && (
            <div className="hv-engine" data-testid={`version-engine-${row.number}`}>
              {row.engine!.map((e) => (
                <span key={e.id} className={e.ok ? "ok" : "bad"} title={`${mskTime(e.created_at)} · ${e.author}${e.engine_version ? ` · в базе ботов – №${e.engine_version}` : ""}`}>
                  {/* «✕» из текста строки (общий с тестами #363) – на экране иконкой */}
                  {e.ok ? <CircleCheck size={12} strokeWidth={2} /> : <CircleX size={12} strokeWidth={2} />}
                  {sendLabel(e, bot(e.tenant)).replace(/^✕\s*/, "")}
                </span>
              ))}
            </div>
          )}
          <div className="row hv-actions">
            {row.status === "published" && target && !sentOk(row.engine ?? [], row.number, target) && (
              <button type="button" disabled={busy === `eng${row.number}`} onClick={() => void sendToEngine(row, target)}
                data-testid={`engine-send-${row.number}`} title="Кто уже внутри воронки, дойдёт по прежней версии; новые люди – по этой">
                Запустить в боте {bot(target)}
              </button>
            )}
            <button type="button" onClick={() => withDraft(row.number)} className={base === row.number ? "on" : ""}>Сравнить с черновиком</button>
            {row.number > 1 && (
              <button type="button" disabled={busy === `prev${row.number}`} onClick={() => void withPrev(row)}>Сравнить с предыдущей</button>
            )}
            <button type="button" disabled={busy === `rb${row.number}`} onClick={() => void rollback(row)} data-testid={`rollback-${row.number}`}>Откатить сюда</button>
          </div>
        </div>
        {compare?.at === row.number && (
          <div className="hv-compare" data-testid="history-compare">
            <div className="hv-head"><b>Сравнение {compare.title}</b>
              <span className="grow" />
              <button type="button" className="x" aria-label="Закрыть сравнение" onClick={() => v.set({ compare: null })}><X size={15} strokeWidth={2} /></button>
            </div>
            <DiffList diff={compare.diff} />
          </div>
        )}
        </div>
      ))}
      {info && !info.versions.length && (
        <div className="muted">Версий пока нет. {withBots
          ? "«Запустить в боте…» в шапке сохранит черновик как v1 и отправит его в бота."
          : "«Опубликовать…» в шапке зафиксирует черновик как v1."}</div>
      )}
    </div>
  );
}
