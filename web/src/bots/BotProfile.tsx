// Профиль бота: имя, описания, аватар, команды меню – читается из Telegram при открытии, правится
// по полям. У боевого бота сервер без подтверждения ничего не меняет (409 «было → стало») – тогда
// прямо в разделе поля появляется подтверждение с тем, что сейчас в Telegram, и тем, что станет.
import { ArrowRight, ExternalLink, ImagePlus, Plus, RefreshCw, Trash2, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useStudio } from "../store";
import { botsApi, NeedConfirm, type BotCommand, type BotProfile as Profile, type ProfileField } from "./api";
import { BotAvatar } from "./BotAvatar";
import { BotFatherHints } from "./Hints";
import { useBots } from "./store";
import { cleanCommands, commandsError, counter, FIELD_LABEL, sameValue, showValue, whereLine } from "./text";

interface Pending { field: ProfileField; value: unknown; was: unknown; now: unknown; where: string[] }

function Confirm({ p, busy, onYes, onNo }: { p: Pending; busy: boolean; onYes: () => void; onNo: () => void }) {
  return (
    <div className="bot-confirm" data-testid="bot-confirm" role="alertdialog">
      <div className="bot-confirm-head"><TriangleAlert size={16} strokeWidth={2} />
        <b>Бот работает у людей{p.where.length ? ` (запуск ${p.where.join(", ")})` : ""} – изменение увидят все. Проверьте:</b>
      </div>
      <div className="bot-diff">
        <div><span className="field-hint">было</span><pre data-testid="confirm-was">{showValue(p.field, p.was)}</pre></div>
        <ArrowRight size={16} strokeWidth={1.75} className="bot-diff-arrow" />
        <div><span className="field-hint">станет</span><pre data-testid="confirm-now">{showValue(p.field, p.now)}</pre></div>
      </div>
      <div className="row end">
        <button type="button" onClick={onNo} disabled={busy}>Отмена</button>
        <button type="button" className="primary" onClick={onYes} disabled={busy} data-testid="confirm-yes">
          {busy ? "Меняю…" : `Изменить «${FIELD_LABEL[p.field]}»`}
        </button>
      </div>
    </div>
  );
}

function useSaver(profile: Profile, reload: () => Promise<void>) {
  const toast = useStudio((s) => s.toast);
  const bump = useBots((s) => s.bumpAvatar);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState<ProfileField | null>(null);
  /** последнее удачное сохранение – по нему поле сбрасывает свой черновик (аватар) */
  const [lastSaved, setLastSaved] = useState<{ field: ProfileField; n: number } | null>(null);
  const run = async (field: ProfileField, value: unknown, confirm: boolean) => {
    setBusy(field);
    try {
      await botsApi.save(profile.id, field, value, confirm);
      setPending(null);
      setLastSaved((x) => ({ field, n: (x?.n ?? 0) + 1 }));
      toast(`${FIELD_LABEL[field]}: сохранено в Telegram`, "ok");
      if (field === "photo") bump();
      await reload();
      if (field === "name") void useBots.getState().loadList();
    } catch (e) {
      if (e instanceof NeedConfirm) setPending({ field, value, was: e.was, now: e.now, where: e.where });
      else toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  };
  return {
    pending, busy, lastSaved,
    save: (field: ProfileField, value: unknown) => void run(field, value, false),
    confirm: () => { if (pending) void run(pending.field, pending.value, true); },
    cancel: () => setPending(null),
  };
}

type Saver = ReturnType<typeof useSaver>;

function TextField({ field, label, hint, value, limit, multiline, saver, testId }: {
  field: "name" | "description" | "short_description"; label: string; hint: string; value: string; limit: number;
  multiline?: boolean; saver: Saver; testId: string; }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  const c = counter(v, limit);
  const changed = !sameValue(field, v, value);
  const Input = multiline ? "textarea" : "input";
  return (
    <section className="bot-field" data-testid={testId}>
      <div className="bot-field-head">
        <b>{label}</b><span className={"bot-count" + (c.over ? " over" : "")} data-testid={`${testId}-count`}>{c.text}</span>
      </div>
      <div className="field-hint">{hint}</div>
      <Input className={"inp" + (c.over ? " invalid" : "")} value={v} rows={multiline ? 4 : undefined}
        onChange={(e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setV(e.target.value)} data-testid={`${testId}-input`} />
      {saver.pending?.field === field ? (
        <Confirm p={saver.pending} busy={saver.busy === field} onYes={saver.confirm} onNo={saver.cancel} />
      ) : (
        <div className="row end">
          {changed && <button type="button" className="ghost" onClick={() => setV(value)}>Вернуть как было</button>}
          <button type="button" disabled={!changed || c.over || saver.busy === field || (field === "name" && !v.trim())}
            onClick={() => saver.save(field, v)} data-testid={`${testId}-save`}>
            {saver.busy === field ? "Сохраняю…" : "Сохранить"}
          </button>
        </div>
      )}
    </section>
  );
}

function Commands({ value, saver }: { value: BotCommand[]; saver: Saver }) {
  const [rows, setRows] = useState<BotCommand[]>(value);
  const key = JSON.stringify(value);
  // перечитали профиль (сохранили другое поле) – несохранённые правки команд не трогаем, если команды те же
  useEffect(() => setRows(value), [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const err = commandsError(rows);
  const changed = !sameValue("commands", rows, value);
  const edit = (i: number, p: Partial<BotCommand>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...p } : r)));
  return (
    <section className="bot-field" data-testid="bot-commands">
      <div className="bot-field-head"><b>Команды меню</b><span className="bot-count">{cleanCommands(rows).length}</span></div>
      <div className="field-hint">Кнопка «Меню» слева от поля ввода: человек видит список «/команда – описание».</div>
      <div className="bot-cmds">
        {rows.map((r, i) => (
          <div className="bot-cmd" key={i}>
            <span className="bot-cmd-slash">/</span>
            <input className="inp mono" value={r.command} placeholder="start" aria-label="Команда"
              onChange={(e) => edit(i, { command: e.target.value })} data-testid="cmd-command" />
            <input className="inp" value={r.description} placeholder="что делает команда" aria-label="Описание команды"
              onChange={(e) => edit(i, { description: e.target.value })} data-testid="cmd-description" />
            <button type="button" className="icon-btn" aria-label="Убрать команду" data-tip="Убрать команду"
              onClick={() => setRows(rows.filter((_, j) => j !== i))}><Trash2 size={14} strokeWidth={1.75} /></button>
          </div>
        ))}
        {!rows.length && <div className="muted">Команд нет – кнопки «Меню» у бота не будет.</div>}
      </div>
      {err && changed && <div className="err-box">{err}</div>}
      {saver.pending?.field === "commands" ? (
        <Confirm p={saver.pending} busy={saver.busy === "commands"} onYes={saver.confirm} onNo={saver.cancel} />
      ) : (
        <div className="row end">
          <button type="button" className="ghost" onClick={() => setRows([...rows, { command: "", description: "" }])} data-testid="cmd-add">
            <Plus size={14} strokeWidth={2} />Команда
          </button>
          {changed && <button type="button" className="ghost" onClick={() => setRows(value)}>Вернуть как было</button>}
          <button type="button" disabled={!changed || !!err || saver.busy === "commands"}
            onClick={() => saver.save("commands", cleanCommands(rows))} data-testid="cmd-save">
            {saver.busy === "commands" ? "Сохраняю…" : "Сохранить команды"}
          </button>
        </div>
      )}
    </section>
  );
}

function Avatar({ profile, saver }: { profile: Profile; saver: Saver }) {
  const toast = useStudio((s) => s.toast);
  const [media, setMedia] = useState<{ name: string; url: string; w: number; h: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const pick = async (f: File | undefined) => {
    if (!f) return;
    setBusy(true);
    try {
      const info = await api.upload(f);
      if (info.kind !== "image") throw new Error("для аватара нужна картинка jpg или png");
      setMedia({ name: info.name, url: info.url, w: info.width ?? 0, h: info.height ?? 0 });
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => { if (saver.lastSaved?.field === "photo") setMedia(null); }, [saver.lastSaved]);
  const small = !!media && Math.min(media.w, media.h) < 160;
  return (
    <section className="bot-field" data-testid="bot-photo">
      <div className="bot-field-head"><b>Аватар</b></div>
      <div className="field-hint">Круглая картинка бота в списке чатов и в профиле. Telegram берёт квадрат – обрежем по центру, как в кружке ниже.</div>
      <div className="bot-photo-row">
        <div className="bot-photo-col"><BotAvatar id={profile.id} name={profile.first_name} size={72} /><span className="field-hint">сейчас</span></div>
        {media && <>
          <ArrowRight size={18} strokeWidth={1.75} className="bot-diff-arrow" />
          <div className="bot-photo-col"><img className="bot-ava" src={media.url} alt="" style={{ width: 72, height: 72 }} data-testid="photo-preview" /><span className="field-hint">станет</span></div>
        </>}
        <div className="bot-photo-actions">
          <label className="btn-like">
            <ImagePlus size={14} strokeWidth={1.75} />{busy ? "Загружаю…" : media ? "Другая картинка" : "Выбрать картинку"}
            <input type="file" accept="image/*" hidden data-testid="photo-file" onChange={(e) => { void pick(e.target.files?.[0]); e.target.value = ""; }} />
          </label>
          {small && <span className="field-hint err-text">Маленькая ({media!.w}×{media!.h}) – нужна хотя бы 160×160, лучше 640×640</span>}
        </div>
      </div>
      {saver.pending?.field === "photo" ? (
        <Confirm p={saver.pending} busy={saver.busy === "photo"} onYes={saver.confirm} onNo={saver.cancel} />
      ) : media && (
        <div className="row end">
          <button type="button" className="ghost" onClick={() => setMedia(null)}>Отмена</button>
          <button type="button" disabled={small || saver.busy === "photo"} onClick={() => saver.save("photo", media.name)} data-testid="photo-save">
            {saver.busy === "photo" ? "Ставлю…" : "Поставить аватар"}
          </button>
        </div>
      )}
    </section>
  );
}

/** Как увидит человек: пустой чат до /start (описание) и профиль бота (короткое описание). */
function Preview({ profile }: { profile: Profile }) {
  return (
    <div className="bot-preview" data-testid="bot-preview">
      <div className="bot-phone">
        <div className="bot-phone-bar"><BotAvatar id={profile.id} name={profile.first_name} size={26} /><b>{profile.name || profile.first_name}</b><span className="muted">бот</span></div>
        <div className="bot-phone-chat">
          <div className="bot-phone-desc">
            <b>Что умеет этот бот?</b>
            <p data-testid="preview-description">{profile.description || "Описания нет – человек увидит пустой чат с кнопкой «Старт»."}</p>
          </div>
          <div className="bot-phone-start">СТАРТ</div>
        </div>
        <div className="field-hint">пустой чат до /start</div>
      </div>
      <div className="bot-phone">
        <div className="bot-phone-profile">
          <BotAvatar id={profile.id} name={profile.first_name} size={56} />
          <b>{profile.name || profile.first_name}</b>
          <span className="muted">@{profile.username}</span>
          <p data-testid="preview-short">{profile.short_description || "Короткого описания нет"}</p>
        </div>
        <div className="field-hint">профиль бота и превью ссылки t.me/{profile.username}</div>
      </div>
    </div>
  );
}

export function BotProfileView({ id, onOpenFunnel }: { id: number; onOpenFunnel: (name: string) => void }) {
  const toast = useStudio((s) => s.toast);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const reload = useCallback(async () => {
    try {
      setProfile(await botsApi.profile(id));
      setErr(null);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [id]);
  useEffect(() => { setProfile(null); void reload(); }, [reload]);
  const saver = useSaver(profile ?? ({ id } as Profile), reload);
  if (err && !profile) return <div className="bot-main"><div className="err-box" data-testid="bot-profile-error">{err}</div></div>;
  if (!profile) return <div className="bot-main"><div className="muted">Читаю профиль из Telegram…</div></div>;
  const remove = async () => {
    try {
      await botsApi.remove(profile.id);
      toast(`@${profile.username} убран из Студии. В Telegram ничего не изменилось`, "ok");
      useBots.getState().select(null);
      await useBots.getState().loadList();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <div className="bot-main" data-testid="bot-profile">
      <div className="bot-head">
        <BotAvatar id={profile.id} name={profile.first_name} size={56} />
        <div className="bot-head-text">
          <h3 data-testid="bot-title">{profile.name || profile.first_name}</h3>
          <div className="muted">@{profile.username} · токен …{profile.last4}</div>
          <div className="bot-tags">
            {profile.careful
              ? <span className="chip chip-warn" data-testid="bot-careful">работает у людей – правки с подтверждением</span>
              : <span className="chip chip-ok" data-testid="bot-test">тестовый бот</span>}
          </div>
        </div>
        <div className="bot-head-actions">
          <a className="btn-like" href={`https://t.me/${profile.username}`} target="_blank" rel="noreferrer"><ExternalLink size={14} strokeWidth={1.75} />Открыть в Telegram</a>
          <button type="button" className="icon-btn" aria-label="Перечитать из Telegram" data-tip="Перечитать из Telegram" onClick={() => void reload()}>
            <RefreshCw size={15} strokeWidth={1.75} />
          </button>
        </div>
      </div>
      {err && <div className="err-box">{err}</div>}

      <section className="bot-where" data-testid="bot-where">
        <b>Где работает</b>
        {!profile.where.length && <div className="muted">Ни в одном запуске – ни в Студии, ни в bot-engine.</div>}
        {profile.where.map((w) => (
          <div key={w.kind + w.slug} className="bot-where-row">
            <span>{whereLine(w)}</span>
            {w.funnels.length > 0 && (
              <span className="bot-where-funnels">
                {w.funnels.map((f) => (
                  <button key={f.name} type="button" className="linkish" onClick={() => onOpenFunnel(f.name)} data-testid="bot-open-funnel">
                    {f.label}{w.entry_funnel && f.name.endsWith(`__${w.entry_funnel}`) ? " (стартовая)" : ""}
                  </button>
                ))}
              </span>
            )}
          </div>
        ))}
      </section>

      <div className="bot-cols">
        <div className="bot-fields">
          <TextField field="name" label="Имя" testId="bot-name" value={profile.name} limit={profile.limits.name} saver={saver}
            hint="Как бот называется в списке чатов и в шапке переписки." />
          <TextField field="description" label="Описание" testId="bot-description" value={profile.description}
            limit={profile.limits.description} multiline saver={saver}
            hint="Что видит человек в пустом чате до /start – «Что умеет этот бот?»." />
          <TextField field="short_description" label="Короткое описание" testId="bot-short" value={profile.short_description}
            limit={profile.limits.short_description} multiline saver={saver}
            hint="В профиле бота и в превью, когда ссылкой на бота делятся." />
          <Avatar profile={profile} saver={saver} />
          <Commands value={profile.commands} saver={saver} />
        </div>
        <div className="bot-side">
          <b>Как увидит человек</b>
          <Preview profile={profile} />
          <b>Чего нельзя из Студии – через @BotFather</b>
          <BotFatherHints profile={profile} />
          <div className="bot-remove">
            {removing ? (
              <div className="row">
                <span className="muted">Студия забудет токен. В Telegram и в ботах ничего не изменится.</span>
                <button type="button" onClick={() => setRemoving(false)}>Отмена</button>
                <button type="button" className="danger" onClick={() => void remove()} data-testid="bot-remove-yes">Убрать</button>
              </div>
            ) : (
              <button type="button" className="ghost" onClick={() => setRemoving(true)} data-testid="bot-remove"><Trash2 size={14} strokeWidth={1.75} />Убрать бота из Студии</button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
