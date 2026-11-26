// «Все ветки»: прогон по типовым моментам регистрации – кто что теряет. Главное против тихих потерь.
import { CircleCheck, TriangleAlert } from "lucide-react";
import { useStudio } from "../store";
import { reasonRu, uniqueDropped } from "./format";
import { useSim } from "./simStore";

export function MatrixDialog() {
  const open = useSim((s) => s.matrixOpen);
  const m = useSim((s) => s.matrix);
  const loading = useSim((s) => s.matrixLoading);
  const close = useSim((s) => s.closeMatrix);
  const setParams = useSim((s) => s.setParams);
  const run = useSim((s) => s.run);
  const focusStep = useStudio((s) => s.focusStep);
  if (!open) return null;
  const pick = (when: string) => { setParams({ registeredAt: when }); close(); void run(); };
  return (
    <div className="modal-back" onClick={close}>
      <div className="modal sim-matrix" onClick={(e) => e.stopPropagation()} data-testid="sim-matrix-dialog">
        <div className="modal-head">
          <h3>Все ветки – что уходит и что выпадает</h3>
          <button type="button" onClick={close}>Закрыть</button>
        </div>
        {loading && <div className="muted">Прогоняю типовые моменты регистрации…</div>}
        {m && !loading && (
          <div className="sim-mx-body">
            <div className="field-hint">
              {m.platform === "tg" ? "Telegram" : "MAX"} · типовой человек (или заданный в панели) ·
              {" "}{m.launch.notes.join(" · ")}. Клик по строке – подробная лента этого момента.
            </div>
            <table className="diff sim-mx">
              <thead>
                <tr><th>регистрация</th><th>ветка · эфир</th><th>уходит</th><th>выпадает</th><th>по условию</th></tr>
              </thead>
              <tbody>
                {m.rows.map((r) => {
                  const d = uniqueDropped(r.dropped);
                  return (
                    <tr key={r.registered_at} className={d.length ? "mx-bad" : ""} onClick={() => pick(r.registered_at)}
                      data-testid="sim-mx-row">
                      <td><b>{r.registered_at.slice(11)}</b> <span className="muted">{r.registered_at.slice(8, 10)}.{r.registered_at.slice(5, 7)}</span>
                        <div className="small muted">{r.label}</div></td>
                      <td>{r.branch ?? "–"}<div className="small muted">{r.webinar}</div></td>
                      <td>{r.sent}{r.late > 0 && <div className="small muted">⏳ {r.late} с опозданием</div>}</td>
                      <td>
                        {d.length === 0 ? <span className="mx-ok">–</span> : d.map((x, i) => (
                          <div key={i} className="mx-drop"><b>{x.key}</b> <span className="muted">{reasonRu(x.reason)}</span></div>
                        ))}
                        {r.stuck.length > 0 && <div className="mx-drop"><TriangleAlert size={12} strokeWidth={2} /> {r.stuck.length} ожидание без выхода</div>}
                        {r.truncated && <div className="small muted">{r.truncated}</div>}
                      </td>
                      <td>{r.gated}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {m.steps.length > 0 && (
              <>
                <h4>Шаги, которые выпадают</h4>
                <div className="sim-mx-steps">
                  {m.steps.map((s) => (
                    <button type="button" key={s.key} className="sim-drop" onClick={() => { if (s.step_id) { close(); focusStep(s.step_id); } }}>
                      <b>{s.key}</b> – {s.reasons.map(reasonRu).join(", ")}
                      <span className="muted"> · {s.moments.length === m.rows.length ? "при любой регистрации" : s.moments.join("; ")}</span>
                    </button>
                  ))}
                </div>
              </>
            )}
            {!m.steps.length && <div className="ok-box"><CircleCheck size={14} strokeWidth={2} />Ни в одной ветке ничего не выпадает.</div>}
          </div>
        )}
      </div>
    </div>
  );
}
