// Диалог «Запустить в боте» (без ботов – «Сохранить версию»): какой бот и что будет после запуска –
// сверху, простыми словами; проблемы, которые нужно исправить, – красным; что изменилось; под
// «Подробнее» – режим для тех, кто уже внутри, отчёт по шагам, куда перевести людей, проверка выгрузки
// для bot-engine. Перед проверкой несохранённые правки сохраняются. Одна кнопка: версия Студии и сразу – бот.
import { CircleCheck, CircleX, TriangleAlert, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError } from "../api";
import { SERVER_CHECKED_RULES, validate } from "../model/validate";
import { newId, useStudio } from "../store";
import { versionsApi, type Blocker, type PublishCheck } from "./api";
import { DiffList, usePickChange } from "./DiffList";
import { actionLabel, engineApi, loadAuthor, publishGate, saveAuthor, sentOk } from "./engine";
import { BotPicker, EngineDetails, usePublishEngine } from "./EngineSection";
import { useVersions } from "./store";
import { restoreAsTombstone } from "./tombstone";

async function saveIfDirty(): Promise<void> {
  const st = useStudio.getState();
  if (!st.name || !st.funnel || st.funnel === st.saved) return;
  const snap = st.funnel;
  await versionsApi.save(st.name, snap);
  if (useStudio.getState().funnel === snap) useStudio.getState().markSaved();
}

/** Ошибки панели «Проверка», которые считает браузер (пустые шаги проверяет сервер сам). */
function browserErrors() {
  return validate(useStudio.getState().funnel!).filter((i) => i.level === "error" && !SERVER_CHECKED_RULES.has(i.rule));
}

function RemovedBlocker({ b, onFixed }: { b: Blocker; onFixed: () => void }) {
  const funnel = useStudio((s) => s.funnel)!;
  const [to, setTo] = useState("");
  const options = useMemo(() => funnel.steps.filter((s) => s.type !== "jump"), [funnel]);
  const fix = () => {
    if (!b.step || !to) return;
    const ok = useStudio.getState().apply((g) => restoreAsTombstone(g, { step: b.step!, redirectTo: to, edgeId: newId() }));
    if (ok) onFixed();
  };
  return (
    <div className="blocker-fix">
      <span>Куда вести этих людей:</span>
      <select className="inp" value={to} onChange={(e) => setTo(e.target.value)} data-testid="tomb-target">
        <option value="">выберите шаг…</option>
        {options.map((s) => <option key={s.id} value={s.id}>{s.key}{s.title ? ` – ${s.title}` : ""}</option>)}
      </select>
      <button type="button" disabled={!to} onClick={fix} data-testid="tomb-fix">Вернуть как переход</button>
    </div>
  );
}

export function PublishDialog() {
  const name = useStudio((s) => s.name)!;
  const funnel = useStudio((s) => s.funnel)!;
  const close = () => useVersions.getState().set({ publishOpen: false });
  const [chk, setChk] = useState<PublishCheck | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [comment, setComment] = useState("");
  const [author, setAuthor] = useState(loadAuthor);
  const pick = usePickChange();
  const eng = usePublishEngine(name, chk !== null);
  const issues = useMemo(() => validate(funnel).filter((i) => i.level === "error" && !SERVER_CHECKED_RULES.has(i.rule)), [funnel]);

  const run = useCallback(async () => {
    setErr(null);
    setChk(null);
    try {
      await saveIfDirty();
      setChk(await versionsApi.check(name, browserErrors().length));
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae.message);
      const sid = ae.errors?.find((x) => x.step_id)?.step_id;
      if (sid) useStudio.getState().focusStep(sid);
    }
  }, [name]);

  useEffect(() => { void run(); }, [run]);

  const blockers = (chk?.blockers ?? []).filter((b) => b.kind !== "nothing");
  const nothing = !!chk?.blockers.some((b) => b.kind === "nothing");
  const already = eng.on && nothing ? sentOk(eng.eng?.history ?? [], chk?.published ?? null, eng.tenant) : undefined;
  // в Студии менять нечего, а в выбранный бот эта версия ещё не уходила – запускаем её
  const sendPublished = eng.on && nothing && !blockers.length && !already;

  const done = async (engineRes: { ok: boolean; error: string; entry?: { ok: boolean; error?: string } | null } | undefined, number: number) => {
    const st = useStudio.getState();
    if (!engineRes) st.toast(`Версия ${number} сохранена в Студии. В бот она не отправлялась`, "ok");
    else if (engineRes.ok) {
      const entry = engineRes.entry?.ok ? " · теперь /start ведёт сюда" : engineRes.entry ? ` · стартовой сделать не вышло: ${engineRes.entry.error}` : "";
      st.toast(`Запущено в боте ${eng.bot} · версия ${number}${entry}. Проверить – «Проверить в боте» в шапке`, "ok");
    } else {
      st.toast(`Версия ${number} сохранена в Студии, но в бот ${eng.bot} НЕ ушла: ${engineRes.error}. Повторить – в «Истории»`, "error");
    }
    await useVersions.getState().refresh(name);
    close();
  };

  const go = async () => {
    if (!chk) return;
    setBusy(true);
    saveAuthor(author.trim());
    const who = author.trim() || "я";
    try {
      if (sendPublished && chk.published !== null && eng.req) {
        const r = await engineApi.send(name, chk.published, { ...eng.req, author: who, comment });
        await done(r, chk.published);
      } else {
        const r = await versionsApi.publish(name, {
          comment, number: chk.number, validation_errors: issues.length, author: who, engine: eng.req,
        });
        await done(r.engine, r.number);
      }
    } catch (e) {
      setErr((e as Error).message);
      void run();
    } finally {
      setBusy(false);
    }
  };

  const gate = nothing && !sendPublished && !blockers.length
    ? { ok: false, why: null }    // отправлять нечего – объяснено в тексте выше
    : publishGate({
      studioBlockers: blockers.length, studioReady: chk !== null, engineOn: eng.on,
      tenant: eng.tenant, check: eng.check, checking: eng.checking, busy,
    });
  const ex = chk?.export;
  const warns = chk?.step_warnings ?? [];
  const repoWarns = chk?.repo_warnings ?? [];
  const drift = chk?.engine_drift;
  const title = eng.on ? "Запустить в боте" : "Сохранить версию";
  const refused = eng.check !== null && !eng.check.ok;
  return (
    <div className="modal-back" onClick={close}>
      <div className="modal publish-modal" onClick={(e) => e.stopPropagation()} data-testid="publish-dialog">
        <div className="modal-head">
          <h3>{title} · {funnel.title || funnel.source?.launch || funnel.id}</h3>
          <button type="button" className="x" aria-label="Закрыть" onClick={close}><X size={15} strokeWidth={2} /></button>
        </div>
        {err && <div className="err-box">{err}</div>}
        {!chk && !err && <div className="muted">Сохраняю черновик и проверяю воронку…</div>}
        {drift && drift.same === false && (
          <div className="warn-box" data-testid="engine-drift">
            <TriangleAlert size={14} strokeWidth={2} />
            <span>Копия движка в Студии не совпадает с bot-engine: {drift.text.charAt(0).toLowerCase() + drift.text.slice(1)}.
              {" "}Запуск в боте Студии это не блокирует – не сработает только сверка выгрузки с bot-engine.</span>
          </div>
        )}
        <div className="publish-body">
          {chk && <BotPicker st={eng} />}
          {blockers.length > 0 && (
            <div className="blockers" data-testid="blockers">
              <div className="blockers-title">Нужно исправить, прежде чем {eng.on ? "запускать" : "сохранять"} · {blockers.length}</div>
              {blockers.map((b, i) => (
                <div key={i} className="blocker">
                  <button type="button" className="blocker-text" onClick={() => b.step_id && pick({ step_id: b.step_id })}><CircleX size={14} strokeWidth={2} />{b.text}</button>
                  {b.kind === "validation" && (
                    <div className="blocker-sub">
                      {issues.slice(0, 6).map((i) => (
                        <button type="button" key={i.id} className="diff-row" onClick={() => i.stepId && pick({ step_id: i.stepId })}>{i.message}</button>
                      ))}
                      {issues.length > 6 && <span className="muted">…и ещё {issues.length - 6} – вкладка «Проверка»</span>}
                    </div>
                  )}
                  {b.kind === "removed" && <RemovedBlocker b={b} onFixed={() => void run()} />}
                </div>
              ))}
            </div>
          )}
          {chk && nothing && !blockers.length && (
            <div className={sendPublished ? "ok-box" : "muted"} data-testid="publish-nothing">
              {already
                ? `Изменений нет – в боте ${eng.bot} уже работает версия ${chk.published}.`
                : sendPublished
                  ? `В Студии изменений нет, а в бот ${eng.bot} версия ${chk.published} ещё не уходила – её можно запустить.`
                  : `Изменений нет – версия ${chk.published} уже сохранена.`}
            </div>
          )}
          {warns.length > 0 && (
            <div className="step-warns" data-testid="step-warnings">
              <div className="diff-group-title warn">Стоит проверить · {warns.length}</div>
              {warns.map((w, i) => (
                <button type="button" key={i} className="diff-row diff-warn" onClick={() => w.step_id && pick({ step_id: w.step_id })}>{w.text}</button>
              ))}
            </div>
          )}
          {chk && !nothing && (
            // первая версия – список из всех шагов, он только мешает: свёрнут
            <details className="publish-changes" open={chk.published !== null ? true : undefined} data-testid="publish-changes">
              <summary className="publish-sub">
                {chk.published
                  ? `Что изменилось с версии ${chk.published} · ${chk.diff.summary.total}`
                  : `Первая версия воронки · шагов ${funnel.steps.filter((s) => s.type !== "jump").length}`}
              </summary>
              <DiffList diff={chk.diff} warnings={chk.warnings} empty={chk.published ? "Изменений нет." : "Все шаги – новые."} />
            </details>
          )}
          {chk && (
            <details className="publish-more" data-testid="publish-more" open={refused || eng.mode === "migrate" ? true : undefined}>
              <summary>Подробнее: кто уже внутри воронки, отчёт по шагам, выгрузка для bot-engine</summary>
              <EngineDetails st={eng} />
              {repoWarns.map((w, i) => <div key={i} className="warn-box" data-testid="repo-warning">{w}</div>)}
              {ex && (
                <div className={ex.engine.ok ? "ok-box" : "err-box"} data-testid="publish-engine">
                  {ex.engine.ok
                    ? <><CircleCheck size={14} strokeWidth={2} /> Старый бот (bot-engine) тоже сможет прочитать эту воронку – шагов {ex.engine.steps}{ex.compare ? (ex.compare.same === null ? ` · сверить с ${ex.compare.path} не вышло` : ex.compare.same ? ` · совпадает с ${ex.compare.path}` : ` · отличий от ${ex.compare.path}: ${ex.compare.differences}`) : ""}</>
                    : <>Старый бот (bot-engine) не прочитает эту воронку: {ex.export_error ?? ex.engine.error}</>}
                </div>
              )}
            </details>
          )}
        </div>
        <div className="publish-foot">
          <textarea className="inp" rows={2} placeholder="Что поменяли и зачем (необязательно) – останется в истории версий" value={comment}
            onChange={(e) => setComment(e.target.value)} data-testid="publish-comment" />
          {gate.why && <div className="muted publish-why" data-testid="publish-why">{gate.why}</div>}
          <div className="row">
            <span className="muted">Автор:</span>
            <input className="inp publish-author" placeholder="ваше имя" value={author} data-testid="publish-author"
              onChange={(e) => setAuthor(e.target.value)} />
            <span className="grow" />
            <button type="button" onClick={close}>Отмена</button>
            <button type="button" className="primary" data-testid="publish-go"
              disabled={!gate.ok}
              onClick={() => void go()}>
              {actionLabel({ bot: eng.bot, busy })}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function VersionsLayer() {
  const open = useVersions((s) => s.publishOpen);
  const funnel = useStudio((s) => s.funnel);
  if (!open || !funnel) return null;
  return <PublishDialog />;
}
