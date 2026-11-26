// Раздел «Боты»: слева – боты (свои, без токена в Студии, общие MAX-боты), справа – профиль
// выбранного бота или «Добавить бота». Открывается из «Ещё» и по адресу #bots.
import { Bot, KeyRound, Plus, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useStudio } from "../store";
import { botsApi, type BotCard, type OtherBot } from "./api";
import { BotAvatar } from "./BotAvatar";
import { BotProfileView } from "./BotProfile";
import { Steps } from "./Hints";
import { hashSelection, useBots } from "./store";
import { TOKEN_STEPS, whereLine } from "./text";
import "./bots.css";

function AddBot({ note }: { note?: string }) {
  const toast = useStudio((s) => s.toast);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const keyErr = useBots((s) => s.list?.key.error);
  const add = async () => {
    setBusy(true);
    setErr(null);
    try {
      const card = await botsApi.add(token);
      setToken("");
      toast(card.fresh ? `Бот @${card.username} добавлен` : `Токен @${card.username} обновлён`, "ok");
      await useBots.getState().loadList();
      useBots.getState().select(card.id);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="bot-main bot-add" data-testid="bot-add">
      <h3>Добавить бота</h3>
      {note && <div className="info-box">{note}</div>}
      <p className="muted">Вставьте токен бота из @BotFather. Студия покажет имя, аватар и профиль бота и даст их поменять.
        Сообщений бот из Студии не шлёт и работе бота не мешает – даже если его сейчас ведёт bot-engine.</p>
      <div className="row">
        <input className="inp mono bot-token" type="password" autoComplete="off" spellCheck={false}
          placeholder="123456789:AAH…" value={token} onChange={(e) => setToken(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && token.trim()) void add(); }} data-testid="bot-token" aria-label="Токен бота" />
        <button type="button" className="primary" disabled={!token.trim() || busy} onClick={() => void add()} data-testid="bot-add-go">
          {busy ? "Проверяю…" : "Добавить"}
        </button>
      </div>
      {err && <div className="err-box" data-testid="bot-add-error">{err}</div>}
      {keyErr && <div className="warn-box">{keyErr}</div>}
      <details className="bf-hint" open>
        <summary><b>Где взять токен</b></summary>
        <Steps steps={TOKEN_STEPS} />
      </details>
      <p className="field-hint"><KeyRound size={12} strokeWidth={2} /> Токен хранится на сервере Студии в зашифрованном виде и обратно
        в браузер не отдаётся – в разделе видно только его последние 4 символа.</p>
    </div>
  );
}

function OwnRow({ b, on }: { b: BotCard; on: boolean }) {
  const select = useBots((s) => s.select);
  return (
    <button type="button" className={"bot-row" + (on ? " on" : "")} onClick={() => select(b.id)} data-testid="bot-row">
      <BotAvatar id={b.id} name={b.first_name} size={36} />
      <span className="bot-row-text">
        <b>{b.first_name || b.username}</b>
        <span className="muted">@{b.username}</span>
        {b.where.length
          ? b.where.map((w) => <span key={w.kind + w.slug} className="bot-row-where">{whereLine(w)}</span>)
          : <span className="bot-row-where faint">пока ни в одном запуске</span>}
      </span>
    </button>
  );
}

function OtherRow({ o, onAdd }: { o: OtherBot; onAdd: () => void }) {
  return (
    <div className="bot-row other" data-testid="bot-other">
      <BotAvatar username={o.username} size={30} />
      <span className="bot-row-text">
        <span>@{o.username}{o.test && <span className="muted"> · тестовый</span>}</span>
        {o.where.map((w) => <span key={w.kind + w.slug} className="bot-row-where">{whereLine(w)}</span>)}
      </span>
      <button type="button" className="ghost sm" onClick={onAdd} data-tip="Вставить токен этого бота, чтобы видеть и менять его профиль">Добавить</button>
    </div>
  );
}

export function BotsLayer({ onOpenFunnel }: { onOpenFunnel: (name: string) => void }) {
  const open = useBots((s) => s.open);
  const sel = useBots((s) => s.selected);
  const list = useBots((s) => s.list);
  const listErr = useBots((s) => s.listErr);
  const { show, hide, select } = useBots.getState();
  const [addNote, setAddNote] = useState<string | undefined>();
  useEffect(() => {
    const s = hashSelection();
    if (s !== undefined) show(s);
    else void useBots.getState().loadIndex();
    // ссылка #bots, открытая в той же вкладке, – без перезагрузки страницы
    const onHash = () => {
      const h = hashSelection();
      if (h !== undefined && !useBots.getState().open) show(h);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [show]);
  if (!open) return null;
  const toFunnel = (name: string) => { hide(); onOpenFunnel(name); };
  return (
    <div className="modal-back" onClick={hide}>
      <div className="modal bots-modal" onClick={(e) => e.stopPropagation()} data-testid="bots-screen" role="dialog" aria-label="Боты">
        <div className="modal-head">
          <h3><Bot size={18} strokeWidth={1.75} /> Боты</h3>
          <button type="button" className="x" aria-label="Закрыть" onClick={hide}><X size={18} strokeWidth={1.75} /></button>
        </div>
        <div className="bots-body">
          <aside className="bots-list">
            <button type="button" className={"bot-row add" + (sel === "add" ? " on" : "")} onClick={() => { setAddNote(undefined); select("add"); }} data-testid="bot-add-open">
              <span className="bot-ava bot-ava-empty" style={{ width: 36, height: 36 }}><Plus size={18} strokeWidth={2} /></span>
              <span className="bot-row-text"><b>Добавить бота</b><span className="muted">по токену из @BotFather</span></span>
            </button>
            {listErr && <div className="err-box">{listErr}</div>}
            {!list && !listErr && <div className="muted">Загружаю…</div>}
            {list?.bots.map((b) => <OwnRow key={b.id} b={b} on={sel === b.id} />)}
            {list?.engine.error && <div className="warn-box">Нет связи с ботами Студии: {list.engine.error}</div>}
            {!!list?.others.length && (
              <details className="bots-others" data-testid="bots-others">
                <summary className="bots-group" data-tip="Бот работает в запуске, но его токена в Студии нет – профиль не виден">
                  Без токена в Студии · {list.others.length}
                </summary>
                {list.others.map((o) => (
                  <OtherRow key={o.username} o={o} onAdd={() => {
                    setAddNote(`Вставьте токен @${o.username} – в @BotFather он у владельца этого бота.`);
                    select("add");
                  }} />
                ))}
              </details>
            )}
            {!!list?.max.length && (
              <>
                <div className="bots-group">Общие MAX-боты – только показ</div>
                {list.max.map((g) => (
                  <div key={g.key} className="bot-row other" data-testid="bot-max">
                    <span className="bot-ava bot-ava-empty max" style={{ width: 30, height: 30 }}>M</span>
                    <span className="bot-row-text">
                      <span>{g.username ? `@${g.username}` : "общий MAX-бот"}</span>
                      <span className="bot-row-where">
                        {[...g.launches.map((l) => l.slug), ...g.studio.map((s) => `Студия: ${s.slug}`)].join(", ")}
                      </span>
                    </span>
                  </div>
                ))}
                <div className="field-hint">Имя и аватар MAX-бота меняются в кабинете MAX для бизнеса – один бот на все запуски.</div>
              </>
            )}
          </aside>
          {sel === "add" || (sel === null && list && !list.bots.length) ? <AddBot note={addNote} />
            : typeof sel === "number" ? <BotProfileView key={sel} id={sel} onOpenFunnel={toFunnel} />
              : <div className="bot-main"><div className="empty-state"><Bot size={28} strokeWidth={1.5} /><b>Выберите бота слева</b><p>или добавьте нового по токену из @BotFather.</p></div></div>}
        </div>
      </div>
    </div>
  );
}
