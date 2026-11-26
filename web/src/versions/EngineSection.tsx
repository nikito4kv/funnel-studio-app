// Диалог «Запустить в боте»: выбор бота, что будет после запуска (одна-две фразы), «сделать
// стартовой»; под «Подробнее» – режим для тех, кто уже внутри, отчёт по шагам и куда перевести людей
// с удалённых шагов. Состояние держит сам диалог (usePublishEngine) – разделы только рисуют.
// Сервер – studio/server/versions.py (там же связь с базой ботов – «движком»).
import { CircleCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useStudio } from "../store";
import {
  botName, botOption, consequences, mainBot, engineApi, headline, mergeRedirects, MODE_HINT, MODE_LABEL, onlyTestBots,
  peopleLines, removedLines,
  type EngineCheck, type EngineMode, type EngineReq, type FunnelEngine, type Tenant,
} from "./engine";
import { mskTime } from "./api";
import { BotChip } from "../bots/BotAvatar";
import { useBots } from "../bots/store";
import { useVersions } from "./store";

export interface EngineState {
  eng: FunnelEngine | null;
  loadErr: string | null;
  /** связь с ботами есть (сервер подключён к базе движка и она отвечает) */
  usable: boolean;
  /** выбран бот – версия уйдёт в него */
  on: boolean;
  tenant: string;
  t: Tenant | null;
  /** как назвать выбранного бота: «@funnel_studio_TEST_bot» */
  bot: string | null;
  mode: EngineMode;
  chosen: Record<string, string>;
  check: EngineCheck | null;
  checking: boolean;
  checkErr: string | null;
  /** воронка ещё не стартовая у бота (по /start он ведёт в другую) */
  notEntry: boolean;
  makeEntry: boolean;
  req: EngineReq | null;
  setTenant: (v: string) => void;
  setMode: (v: EngineMode) => void;
  setMakeEntry: (v: boolean) => void;
  choose: (stepId: string, to: string) => void;
  recheck: () => void;
  reload: () => Promise<void>;
}

/** ready – черновик сохранён и проверен Студией (можно спрашивать бота). */
export function usePublishEngine(name: string, ready: boolean): EngineState {
  const funnelId = useStudio((s) => s.funnel?.id ?? "");
  const [eng, setEng] = useState<FunnelEngine | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [tenant, setTenantRaw] = useState("");
  const [mode, setMode] = useState<EngineMode>("new-only");
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [suggestions, setSuggestions] = useState<Record<string, string>>({});
  const [check, setCheck] = useState<EngineCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkErr, setCheckErr] = useState<string | null>(null);
  const [makeEntry, setMakeEntry] = useState<boolean | null>(null);
  const [nonce, setNonce] = useState(0);
  const seq = useRef(0);

  const reload = useCallback(async () => {
    try {
      const e = await engineApi.funnel(name);
      setEng(e);
      setLoadErr(null);
      if (e.configured && !e.error) {
        setTenantRaw((t) => t || e.tenant || (e.tenants.length === 1 ? e.tenants[0].slug : ""));
      }
    } catch (err) {
      setLoadErr((err as Error).message);
    }
  }, [name]);

  useEffect(() => { void reload(); }, [reload]);

  const usable = !!eng?.configured && !eng.error;
  const on = usable && !!tenant;
  const t = eng?.tenants.find((x) => x.slug === tenant) ?? null;
  const notEntry = !!t && !!t.entry_funnel && t.entry_funnel !== funnelId;
  // по умолчанию стартовой делаем только у тестового бота: боевой /start без явной галочки не трогаем
  const entry = notEntry && (makeEntry ?? onlyTestBots(t));
  const removed = useMemo(() => removedLines(check?.report ?? null), [check]);
  const redirects = useMemo(
    () => (mode === "migrate" ? mergeRedirects(suggestions, chosen, removed) : {}),
    [mode, suggestions, chosen, removed],
  );
  const redirectsKey = JSON.stringify(redirects);
  const req = on ? { tenant, mode, redirects, make_entry: entry } : null;

  useEffect(() => {
    if (!ready || !on) {
      setCheck(null);
      return;
    }
    const my = ++seq.current;
    setChecking(true);
    setCheckErr(null);
    engineApi.check(name, { tenant, mode, redirects: JSON.parse(redirectsKey) })
      .then((c) => {
        if (my !== seq.current) return;
        setCheck(c);
        // подсказки карты пришли впервые / поменялись – перепроверим уже с ними
        if (JSON.stringify(c.suggestions) !== JSON.stringify(suggestions)) setSuggestions(c.suggestions);
      })
      .catch((e) => { if (my === seq.current) { setCheck(null); setCheckErr((e as Error).message); } })
      .finally(() => { if (my === seq.current) setChecking(false); });
    // suggestions намеренно не в зависимостях: их смена меняет redirectsKey
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name, ready, on, tenant, mode, redirectsKey, nonce]);

  const setTenant = (v: string) => {
    setTenantRaw(v);
    setChosen({});
    setSuggestions({});
    setMakeEntry(null);
    if (v) void engineApi.setTarget(name, v).then(reload).catch(() => undefined);
  };

  return {
    eng, loadErr, usable, on, tenant, t, bot: on ? botName(t) : null, mode, chosen, check, checking, checkErr,
    notEntry, makeEntry: entry, req,
    setTenant, setMode, setMakeEntry: (v) => setMakeEntry(v),
    choose: (stepId, to) => setChosen((c) => ({ ...c, [stepId]: to })),
    recheck: () => setNonce((n) => n + 1),
    reload,
  };
}

/** Верх диалога: какой бот, что будет после запуска, «сделать стартовой», отказ бота. */
export function BotPicker({ st }: { st: EngineState }) {
  const funnel = useStudio((s) => s.funnel)!;
  const { eng, check } = st;
  if (st.loadErr) return <div className="err-box">Не получилось узнать, что с ботами: {st.loadErr}</div>;
  if (!eng) return <div className="eng muted">Смотрю, какие боты подключены…</div>;
  if (!eng.configured) {
    return (
      <div className="eng eng-off" data-testid="engine-off">
        Студия пока не подключена к ботам – версия сохранится только здесь, в ботах ничего не изменится.
      </div>
    );
  }
  if (eng.error) {
    return (
      <div className="eng eng-off" data-testid="engine-off">
        <b>Нет связи с ботами</b> – версия сохранится только в Студии, в бот она не уйдёт.
        <div className="muted small">{eng.error}</div>
      </div>
    );
  }
  const entryTitle = (id: string | null | undefined) => (id === funnel.id ? "эту воронку" : `воронку «${id}»`);
  return (
    <div className="eng" data-testid="engine-section">
      <div className="row eng-pick">
        <b>Бот:</b>
        <select className="inp" value={st.tenant} onChange={(e) => st.setTenant(e.target.value)} data-testid="engine-tenant">
          <option value="">не отправлять в бота – только сохранить версию</option>
          {eng.tenants.map((t) => <option key={t.slug} value={t.slug}>{botOption(t)}</option>)}
        </select>
      </div>
      {st.on && mainBot(st.t) && (
        <div className="eng-bot">
          <BotChip username={mainBot(st.t)!.username} onOpen={() => {
            const id = useBots.getState().index?.[mainBot(st.t)!.username.toLowerCase()]?.id;
            if (!id) return;
            useVersions.getState().set({ publishOpen: false });
            useBots.getState().show(id);
          }} />
        </div>
      )}
      {!eng.tenants.length && (
        <div className="muted">Ботов пока нет – версия сохранится только в Студии. Подключить бота помогут разработчики.</div>
      )}
      {!st.on && eng.tenants.length > 0 && (
        <div className="muted" data-testid="publish-consequences">Бот не выбран – версия сохранится только в Студии, ни в одном боте ничего не изменится.</div>
      )}
      {st.on && st.checkErr && <div className="err-box">{st.checkErr}</div>}
      {st.on && !check && !st.checkErr && <div className="muted">Смотрю, что будет с людьми в боте…</div>}
      {st.on && check?.ok && (
        <div className="eng-conseq" data-testid="publish-consequences">
          {consequences(check.report, st.mode, onlyTestBots(st.t))}
        </div>
      )}
      {st.on && st.notEntry && (
        <label className="check eng-entry" data-testid="engine-entry-row">
          <input type="checkbox" checked={st.makeEntry} onChange={(e) => st.setMakeEntry(e.target.checked)} data-testid="engine-entry" />
          <span>
            <b>Сделать стартовой: /start в боте будет вести сюда.</b>{" "}
            <span className="muted">Сейчас /start ведёт в {entryTitle(st.t?.entry_funnel)}.{st.makeEntry ? "" : " Без галочки проверить эту воронку можно кнопкой «Проверить в боте»."}</span>
          </span>
        </label>
      )}
      {st.on && st.t && !st.t.entry_funnel && (
        <div className="muted">Других воронок у бота нет – /start будет вести сюда.</div>
      )}
      {st.on && check && !check.ok && (
        <div className="err-box" data-testid="engine-refused">
          <b>Бот не примет эту версию:</b> {check.error}
        </div>
      )}
    </div>
  );
}

/** «Подробнее»: режим, что сейчас в боте, отчёт по шагам, куда перевести людей, предупреждения. */
export function EngineDetails({ st }: { st: EngineState }) {
  const funnel = useStudio((s) => s.funnel)!;
  const keyOf = useCallback((id: string) => funnel.steps.find((s) => s.id === id)?.key, [funnel]);
  const targets = useMemo(() => funnel.steps.filter((s) => s.type !== "jump"), [funnel]);
  const { eng, check } = st;
  if (!st.on || !eng) return null;
  const rep = check?.report ?? null;
  const lines = peopleLines(rep, keyOf);
  const removed = removedLines(rep);
  const current = eng.tenant === st.tenant ? eng.in_bot : null;
  return (
    <div className="eng eng-details" data-testid="engine-details">
      <div className="row">
        <span>Кто уже внутри воронки:</span>
        <div className="seg eng-mode" role="radiogroup">
          {(["new-only", "migrate"] as EngineMode[]).map((m) => (
            <button type="button" key={m} className={st.mode === m ? "seg-on" : ""} data-testid={`engine-mode-${m}`}
              onClick={() => st.setMode(m)}>{m === "new-only" ? "Доходят по старой" : "Перевести всех на новую"}</button>
          ))}
        </div>
      </div>
      <div className="muted">{MODE_HINT[st.mode]}</div>
      {current && (
        <div className="muted" data-testid="engine-current">
          Сейчас в боте: версия {current.studio_number ?? current.engine_version} от {mskTime(current.published_at)}
          {current.published_by ? `, запустил(а) ${current.published_by}` : ""}
          {current.studio_number === null ? " (запущена не из Студии)" : ""}
        </div>
      )}
      {check && (
        <div className={`eng-report${check.ok ? "" : " eng-refused"}`} data-testid="engine-report">
          <div className="eng-headline">{headline(rep, st.bot ?? "")}</div>
          {lines.length > 0 && (
            <div className="eng-lines" data-testid="engine-people">
              {lines.map((l) => (
                <div key={l.step_id} className={`eng-line tone-${l.tone}`}>
                  <span className="mono">«{l.key}»</span> – {l.text}. <span className="eng-fate">{l.fate[0].toUpperCase() + l.fate.slice(1)}.</span>
                </div>
              ))}
            </div>
          )}
          {st.mode === "migrate" && removed.length > 0 && (
            <div className="eng-map" data-testid="engine-map">
              <div className="eng-map-title">Куда перевести людей с удалённых шагов</div>
              {removed.map((l) => {
                const val = l.step_id in st.chosen ? st.chosen[l.step_id] : (check.suggestions[l.step_id] ?? "");
                return (
                  <div key={l.step_id} className="row">
                    <span className="mono">«{l.key}»</span>
                    <span>→</span>
                    <select className="inp" value={val} onChange={(e) => st.choose(l.step_id, e.target.value)}
                      data-testid={`engine-map-${l.key}`}>
                      <option value="">не выбрано</option>
                      {targets.map((s) => <option key={s.id} value={s.id}>{s.key}{s.title ? ` – ${s.title}` : ""}</option>)}
                    </select>
                    {check.suggestions[l.step_id] && val === check.suggestions[l.step_id] && <span className="muted">подсказка Студии</span>}
                  </div>
                );
              })}
            </div>
          )}
          {rep?.warnings.map((w, i) => <div key={i} className="eng-line tone-warn">{w}</div>)}
          {check.ok && <div className="ok-box"><CircleCheck size={14} strokeWidth={2} />Бот примет эту версию{st.mode === "migrate" ? " и переведёт людей" : ""} ({MODE_LABEL[st.mode]}).</div>}
        </div>
      )}
    </div>
  );
}
