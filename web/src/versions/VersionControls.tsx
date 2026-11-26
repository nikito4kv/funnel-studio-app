// Что с воронкой в боте – в шапке: «Работает в боте @X · версия 2» / «Ещё не запущена ни в одном
// боте» / «Есть правки, которых нет в боте», «Проверить в боте» и главная кнопка «Запустить в боте…».
// Студия без связи с ботами – как на этапе 2: «Черновик · изменено N» / «Опубликована v3».
// «Изменения» (подсветка на холсте) – в панели холста, это вид холста, а не действие с ботом.
import { ExternalLink, Rocket } from "lucide-react";
import { useStudio } from "../store";
import { BotAvatar } from "../bots/BotAvatar";
import { botBadge, mainBot } from "./engine";
import { useVersions } from "./store";
import "./versions.css";

/** Состояние бота и «Проверить в боте». */
export function BotStatus() {
  const funnel = useStudio((s) => s.funnel);
  const info = useVersions((s) => s.info);
  const live = useVersions((s) => s.live);
  const eng = useVersions((s) => s.engine);
  if (!funnel) return null;
  const pub = info?.published ?? null;
  const n = live?.summary.total ?? 0;
  const openHistory = () => useStudio.getState().set({ rightTab: "history" });
  const bot = botBadge({ eng, published: pub, changes: n });
  const withBots = !!eng?.configured;
  const liveBot = bot.kind === "live" ? mainBot(eng?.tenants.find((x) => x.slug === eng?.tenant))?.username : undefined;

  if (withBots) {
    const tone = bot.kind === "live" ? (bot.behind ? "vb-behind" : "vb-pub") : "vb-none";
    // статус зовёт «Запустить в боте» – туда он и ведёт (раньше открывал историю)
    return (
      <div className="vcontrols" data-testid="version-controls">
        <button type="button" data-testid="bot-badge-box" data-tip={`${bot.title}. Клик – запуск в боте`}
          className={`vbadge vb-stack ${tone}`} onClick={() => useVersions.getState().set({ publishOpen: true })}>
          <span className="vb-dot" aria-hidden />
          {liveBot && <BotAvatar username={liveBot} size={20} />}
          <span className="vb-lines">
            <span className="vb-main" data-testid="bot-badge">{bot.text}</span>
            {bot.behind && (
              <span className="vb-note" data-testid="bot-behind">Есть правки, которых нет в боте – нажмите «Запустить в боте»</span>
            )}
          </span>
        </button>
        {eng?.test_link && (
          <a className="test-link" href={eng.test_link} target="_blank" rel="noreferrer" data-testid="bot-test-link"
            data-tip="Откроет бота в Telegram и запустит эту воронку с начала – только для вас, других людей это не трогает">
            Проверить в боте<ExternalLink size={13} strokeWidth={2} aria-hidden />
          </a>
        )}
      </div>
    );
  }
  let text: string;
  let cls = "vbadge";
  if (!info) text = "…";
  else if (pub === null) { text = "Черновик · не опубликована"; cls += " vb-draft"; }
  else if (n) { text = `Черновик · изменено ${n}`; cls += " vb-draft"; }
  else { text = `Опубликована v${pub}`; cls += " vb-pub"; }
  return (
    <div className="vcontrols" data-testid="version-controls">
      <button type="button" className={cls} data-testid="version-badge"
        data-tip={pub ? `изменения черновика против опубликованной v${pub} – открыть историю` : "история версий"}
        onClick={openHistory}>
        <span className="vb-dot" aria-hidden />
        <span className="vb-main">{text}</span>
      </button>
    </div>
  );
}

/** Главная кнопка шапки: «Запустить в боте…» (или «Опубликовать…» без связи с ботами). */
export function PublishButton() {
  const funnel = useStudio((s) => s.funnel);
  const withBots = useVersions((s) => !!s.engine?.configured);
  const setV = useVersions((s) => s.set);
  if (!funnel) return null;
  return (
    <button type="button" className="primary publish-btn" data-testid="publish-open" onClick={() => setV({ publishOpen: true })}
      data-tip={withBots ? "Сохранить версию и отправить её в бота" : "Сохранить неизменяемую версию воронки"}>
      <Rocket size={15} strokeWidth={2} aria-hidden />
      {withBots ? "Запустить в боте…" : "Опубликовать…"}
    </button>
  );
}

/** «Изменения» – подсветить на холсте, что изменилось с последней сохранённой версии. */
export function ChangesToggle() {
  const funnel = useStudio((s) => s.funnel);
  const info = useVersions((s) => s.info);
  const show = useVersions((s) => s.showChanges);
  const base = useVersions((s) => s.base);
  const setV = useVersions((s) => s.set);
  if (!funnel) return null;
  const pub = info?.published ?? null;
  if (pub === null && base === null) return null;
  return (
    <label className={"check toggle" + (show ? " on" : "")} data-tip="Подсветить на холсте, что изменилось с последней сохранённой версии">
      <input type="checkbox" data-testid="show-changes" checked={show} onChange={(e) => setV({ showChanges: e.target.checked })} />
      <span>Изменения{base !== null ? ` с v${base}` : ""}</span>
    </label>
  );
}

/** Старый набор целиком – для мест, где шапка ещё собирается одним куском. */
export function VersionControls() {
  return (
    <>
      <BotStatus />
      <ChangesToggle />
      <PublishButton />
    </>
  );
}
