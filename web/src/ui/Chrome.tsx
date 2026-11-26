// Шапка, палитра, строка состояния, всплывающие сообщения.
//
// Шапка – одна строка: слева «какая воронка», справа «что с ней в боте» и действия по порядку
// важности: проверка → сохранить черновик → запустить в боте (главная, залита цветом) → «Ещё».
// Вид холста (ветка, TG/MAX, заметки, изменения, масштаб, отмена) – в панели на самом холсте.
import { useReactFlow } from "@xyflow/react";
import {
  Bot, Check, CircleAlert, CircleCheck, Download, Ellipsis, FilePlus2, Info, Keyboard, NotebookPen, Save, ScanSearch, Send, StickyNote,
  TriangleAlert, Waypoints, type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { addFromPalette, DND_MIME, type PaletteItem } from "../canvas/Canvas";
import { plainLabel, StepIcon } from "../design/icons";
import { STEP_TYPE_LABEL } from "../model/ops";
import type { StepType } from "../model/types";
import { issueCounts, validate } from "../model/validate";
import { isDirty, useStudio } from "../store";
import { BotStatus, PublishButton } from "../versions/VersionControls";
import { useVersions } from "../versions/store";
import { useSim } from "../sim/simStore";
import { openMiroImport } from "../miro/MiroImport";
import { useUi } from "../simple/mode";
import { ModeSwitch } from "../simple/ModeSwitch";
import { useBots } from "../bots/store";

export interface HeaderProps {
  funnels: { name: string; label: string }[];
  onOpen: (name: string) => void;
  onSave: () => void;
  onExport: () => void;
  saving: boolean;
}

function MoreMenu({ onExport }: { onExport: () => void }) {
  const funnel = useStudio((s) => s.funnel);
  const set = useStudio((s) => s.set);
  const openTour = useUi((s) => s.openTour);
  const setKeys = useUi((s) => s.setShortcuts);
  const simple = useUi((s) => s.mode === "simple");
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", esc); };
  }, [open]);
  const item = (Icon: LucideIcon, label: string, run: () => void, testId?: string, disabled?: boolean) => (
    <button type="button" data-testid={testId} disabled={disabled} onClick={() => { setOpen(false); run(); }}>
      <Icon size={16} strokeWidth={1.75} />{label}
    </button>
  );
  return (
    <div className="menu-wrap" ref={ref}>
      <button type="button" className="icon-btn more-btn" data-testid="more" aria-label="Ещё" data-tip="Ещё: боты, выгрузка, новая воронка, подсказки"
        aria-expanded={open} onClick={() => setOpen(!open)}>
        <Ellipsis size={18} strokeWidth={1.75} />
      </button>
      {open && (
        <div className="pop right" data-testid="more-menu">
          {item(FilePlus2, "Новая воронка или импорт…", () => set({ importOpen: true }))}
          {item(ScanSearch, funnel?.miro ? "Обновить из Miro…" : "Импорт из Miro…", openMiroImport, "miro-open")}
          {(!simple || !funnel) ? item(Download, "Выгрузить yaml", onExport, "export", !funnel) : item(Download, "Выгрузить для разработчика", onExport, "export")}
          {item(Bot, "Боты: профиль, аватар, где работают…", () => useBots.getState().show(), "bots-open")}
          <div className="pop-sep" />
          {item(Info, "Показать знакомство со Студией", openTour, "tour-open", !funnel)}
          {item(Keyboard, "Горячие клавиши", () => setKeys(true), "shortcuts-open")}
        </div>
      )}
    </div>
  );
}

/** «Проверить у себя»: черновик – себе в тестового бота, все сообщения подряд, после подтверждения.
 *  «Проверить в боте» (рядом со статусом) – это уже запущенная версия. */
function TestRunButton() {
  const funnel = useStudio((s) => s.funnel);
  const toast = useStudio((s) => s.toast);
  const tr = useSim((s) => s.testrun);
  const busy = useSim((s) => s.testrunBusy);
  const [ask, setAsk] = useState(false);
  useEffect(() => { if (funnel && !useSim.getState().testrun) void useSim.getState().loadTestrun(); }, [funnel]);
  useEffect(() => {
    if (!ask) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setAsk(false); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [ask]);
  if (!funnel || !tr?.ready) return null;
  const n = funnel.steps.filter((s) => s.type !== "jump").length;
  return (
    <>
      <button type="button" className="ghost testrun-btn" data-testid="testrun-open" disabled={busy} onClick={() => setAsk(true)}
        data-tip="Черновик – себе в тестового бота, все сообщения подряд. Люди ничего не получат">
        <Send size={15} strokeWidth={1.75} />{busy ? "Шлю…" : "Проверить у себя"}
      </button>
      {ask && (
        <div className="modal-back" onClick={() => setAsk(false)}>
          <div className="modal small-modal" onClick={(e) => e.stopPropagation()} data-testid="testrun-dialog" role="dialog">
            <h3>Проверить черновик у себя</h3>
            <p>Отправим эту воронку в тестового бота – только вам. Сообщения придут подряд, по одному в полторы секунды, без ожидания времени ({n} шт.). Бот с людьми ничего не получит.</p>
            <div className="row end">
              <button type="button" onClick={() => setAsk(false)}>Отмена</button>
              <button type="button" className="primary" data-testid="testrun-go" onClick={() => {
                setAsk(false);
                useSim.getState().sendTestrun().then((m) => toast(m, "ok")).catch((e) => toast((e as Error).message, "error"));
              }}><Send size={15} strokeWidth={2} />Отправить себе</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export function Header({ funnels, onOpen, onSave, onExport, saving }: HeaderProps) {
  const funnel = useStudio((s) => s.funnel);
  const name = useStudio((s) => s.name);
  const rightTab = useStudio((s) => s.rightTab);
  const dirty = useStudio(isDirty);
  const withBots = useVersions((s) => !!s.engine?.configured);
  const { set } = useStudio.getState();
  const issues = useMemo(() => (funnel ? validate(funnel) : []), [funnel]);
  const { errors, warns } = issueCounts(issues);
  const CheckIcon = errors ? CircleAlert : warns ? TriangleAlert : CircleCheck;
  return (
    <header className="topbar">
      <div className="brand" aria-label="Студия воронок">
        <span className="brand-mark"><Waypoints size={16} strokeWidth={2} /></span>
        <span className="brand-name">Студия</span>
      </div>
      <select className="inp funnel-select" value={name ?? ""} data-testid="funnel-select" aria-label="Воронка" onChange={(e) => {
        if (e.target.value === "__import__") set({ importOpen: true });
        else if (e.target.value) onOpen(e.target.value);
      }}>
        {!name && <option value="">выберите воронку…</option>}
        {funnels.map((f) => <option key={f.name} value={f.name}>{f.label}</option>)}
        <option value="__import__">Новая воронка или импорт…</option>
      </select>
      <span className="grow" />
      <BotStatus />
      {funnel && <span className="topbar-sep" />}
      {funnel && (
        <button type="button" data-testid="check-button"
          className={"check-btn" + (errors ? " has-err" : warns ? " has-warn" : " is-ok") + (rightTab === "check" ? " on" : "")}
          data-tip={errors ? "Есть ошибки: люди не получат сообщение или получат не то" : warns ? "Есть предупреждения – посмотрите" : "Замечаний нет"}
          onClick={() => set({ rightTab: rightTab === "check" ? "props" : "check" })}>
          <CheckIcon size={16} strokeWidth={2} aria-hidden />
          {errors + warns ? <span className={"count-badge" + (errors ? " err" : " warn")}>{errors + warns}</span> : null}
          <span className="check-label">Проверка</span>
        </button>
      )}
      <TestRunButton />
      {funnel && (
        <button type="button" className={"save-btn" + (dirty ? " is-dirty" : "")} onClick={onSave} disabled={saving} data-testid="save" data-kbd="Ctrl+S"
          title={withBots
            ? "Сохраняет черновик в Студии. Бот этого не увидит – для бота нажмите «Запустить в боте»"
            : "Сохраняет черновик в Студии. Версию фиксирует «Опубликовать…»"}>
          {dirty ? <Save size={15} strokeWidth={1.75} aria-hidden /> : <Check size={15} strokeWidth={2} aria-hidden />}
          {saving ? "Сохраняю…" : dirty ? "Сохранить черновик •" : "Черновик сохранён"}
        </button>
      )}
      <PublishButton />
      <MoreMenu onExport={onExport} />
    </header>
  );
}

// ── палитра ──

interface PalGroup {
  title: string;
  items: { type: StepType; survey?: boolean; label?: string; hint: string }[];
}

const GROUPS: PalGroup[] = [
  {
    title: "Сообщения",
    items: [
      { type: "photo", hint: "Картинка, текст и кнопки – самый частый шаг" },
      { type: "text", hint: "Просто текст с кнопками" },
      { type: "video_note", hint: "Кружок: квадратное видео до 60 секунд" },
      { type: "video", hint: "Видео до 20 МБ" },
      { type: "animation", hint: "GIF-анимация" },
    ],
  },
  {
    title: "Спросить",
    items: [
      { type: "phone_request", hint: "Кнопка «поделиться контактом»" },
      { type: "email_request", hint: "Бот ждёт почту обычным сообщением" },
      { type: "text", survey: true, label: "Вопрос опроса", hint: "Кнопки – варианты ответа" },
    ],
  },
  {
    title: "Вне чата",
    items: [
      { type: "call", hint: "Автозвонок записью из кабинета" },
      { type: "sms", hint: "SMS на телефон" },
    ],
  },
];

export function Palette() {
  const rf = useReactFlow();
  const set = useStudio((s) => s.set);
  const pending = useStudio((s) => s.pendingNote);
  const funnel = useStudio((s) => s.funnel);
  const add = (item: PaletteItem) => {
    const el = document.querySelector(".canvas") as HTMLElement | null;
    const r = el?.getBoundingClientRect();
    const center = r ? { x: r.left + r.width / 2, y: r.top + r.height / 2 } : { x: 600, y: 400 };
    const p = rf.screenToFlowPosition(center);
    addFromPalette(item, { x: p.x - 160, y: p.y - 100 });
  };
  return (
    <aside className="palette" data-testid="palette">
      <div className="pal-head">Добавить шаг</div>
      <div className="pal-hint">Перетащите на холст или кликните</div>
      {GROUPS.map((g) => (
        <div className="pal-group" key={g.title}>
          <div className="pal-title">{g.title}</div>
          {g.items.map((it) => {
            const label = it.label ?? plainLabel(STEP_TYPE_LABEL[it.type]);
            return (
              <button type="button" key={label} className="pal-item" draggable disabled={!funnel}
                data-tip={it.hint} data-tip-side="right"
                onDragStart={(e) => { e.dataTransfer.setData(DND_MIME, JSON.stringify({ kind: "step", type: it.type, survey: it.survey })); e.dataTransfer.effectAllowed = "copy"; }}
                onClick={() => add({ kind: "step", type: it.type, survey: it.survey })}>
                <StepIcon type={it.type} survey={it.survey} />
                <span>{label}</span>
              </button>
            );
          })}
        </div>
      ))}
      <div className="pal-group">
        <div className="pal-title">Для команды</div>
        <button type="button" className={"pal-item pal-note" + (pending === "note" ? " on" : "")} draggable disabled={!funnel}
          data-tip="Стикер на холсте. В бот не уходит никогда" data-tip-side="right"
          onDragStart={(e) => e.dataTransfer.setData(DND_MIME, JSON.stringify({ kind: "note" }))}
          onClick={() => set({ pendingNote: pending === "note" ? null : "note", showNotes: true })}>
          <StickyNote size={16} strokeWidth={1.75} /><span>Заметка</span>
        </button>
        <button type="button" className={"pal-item pal-rev" + (pending === "revision" ? " on" : "")} disabled={!funnel}
          data-tip="Правка с номером и статусом: «открыта → сделана → принята»" data-tip-side="right"
          onClick={() => set({ pendingNote: pending === "revision" ? null : "revision", showNotes: true })}>
          <NotebookPen size={16} strokeWidth={1.75} /><span>Правка</span>
        </button>
      </div>
    </aside>
  );
}

// ── строка состояния ──

function plural(n: number, one: string, few: string, many: string): string {
  const d = n % 10;
  const h = n % 100;
  if (d === 1 && h !== 11) return one;
  if (d >= 2 && d <= 4 && (h < 12 || h > 14)) return few;
  return many;
}

export function StatusBar() {
  const funnel = useStudio((s) => s.funnel);
  const simple = useUi((s) => s.mode === "simple");
  const setKeys = useUi((s) => s.setShortcuts);
  if (!funnel) return null;
  const notes = funnel.notes.filter((n) => n.kind === "note").length;
  const revs = funnel.notes.filter((n) => n.kind === "revision");
  const by = (st: string) => revs.filter((r) => r.status === st).length;
  return (
    <footer className="statusbar" data-testid="statusbar">
      <span>{simple ? `${funnel.steps.length} ${plural(funnel.steps.length, "сообщение", "сообщения", "сообщений")}` : `Шагов ${funnel.steps.length} · стрелок ${funnel.edges.length} · полос ${funnel.lanes.length}`}</span>
      {notes > 0 && <span className="sb-item"><StickyNote size={12} strokeWidth={2} />Заметки: {notes}</span>}
      {revs.length > 0 && (
        <span className="sb-item"><NotebookPen size={12} strokeWidth={2} />Правки: {revs.length} (сделано {by("done")}, открыто {by("open")}, принято {by("accepted")}, отклонено {by("rejected")})</span>
      )}
      <span className="grow" />
      <button type="button" className="ghost sm" data-testid="shortcuts-button" onClick={() => setKeys(true)} data-kbd="?" data-tip="Горячие клавиши">
        <Keyboard size={13} strokeWidth={1.75} />Клавиши
      </button>
      <ModeSwitch />
    </footer>
  );
}

// ── всплывающие сообщения ──

export function Toasts() {
  const toasts = useStudio((s) => s.toasts);
  const drop = useStudio((s) => s.dropToast);
  return (
    <div className="toasts" role="status">
      {toasts.map((t) => {
        const I = t.kind === "error" ? CircleAlert : t.kind === "ok" ? CircleCheck : Info;
        return (
          <div key={t.id} className={`toast toast-${t.kind}`} onClick={() => drop(t.id)}>
            <I size={16} strokeWidth={2} aria-hidden /><span>{t.text}</span>
          </div>
        );
      })}
    </div>
  );
}
