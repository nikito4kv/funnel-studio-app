// Этап 2A в настоящем браузере: правка → опубликовать → правка → дифф на холсте и в диалоге →
// блокер «удалён без надгробия» → надгробие → публикация v2 → история → откат к v1.
//
//   uv run python -m studio.server --port 8771 --data <временная папка>
//   node scripts/e2e_versions.mjs [http://127.0.0.1:8771]
import { chromium } from "playwright";

const BASE = (process.argv[2] ?? process.env.STUDIO_URL ?? "http://127.0.0.1:8771").replace(/\/$/, "");
const BROWSER = process.env.BROWSER ?? "C:/Program Files/Google/Chrome/Application/chrome.exe";
let failed = 0;
const check = (ok, what) => {
  console.log(`${ok ? "✓" : "✗"} ${what}`);
  if (!ok) failed++;
};

async function api(method, path, body) {
  const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json" }, body: body && JSON.stringify(body) });
  return r.json();
}

// своя копия webinar под уникальным именем – у неё своя история версий
const imp = await api("POST", "/api/import", { path: "launches/webinar/funnel.yaml" });
const src = await api("GET", `/api/funnels/${imp.name}`);
const short = `v${Date.now()}`;
await api("POST", "/api/funnels", { launch: "webinar", name: short, id: src.id });
const name = `webinar__${short}`;
await api("PUT", `/api/funnels/${name}`, src);

const browser = await chromium.launch({ executablePath: BROWSER });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("dialog", (d) => d.accept());
const badge = () => page.getByTestId("version-badge").innerText();
const waitBadge = (re) => page.waitForFunction((s) => new RegExp(s).test(document.querySelector('[data-testid="version-badge"]')?.textContent ?? ""), re.source, { timeout: 8000 });

await page.goto(`${BASE}/#f=${name}`);
await page.waitForFunction(() => window.__studio?.getState().funnel);
// ждём автораскладку: координаты карточек – часть версии (по ним рисуется «тень» удалённого)
await page.waitForFunction(() => Object.keys(window.__studio.getState().funnel.layout.positions).length > 0);
await waitBadge(/не опубликована/);
check(true, `шапка: «${await badge()}»`);

// 1. первая публикация
await page.getByTestId("publish-open").click();
await page.waitForSelector('[data-testid="publish-go"]:not([disabled])', { timeout: 20000 });
check(/Сохранить версию/.test(await page.getByTestId("publish-go").innerText()), "без ботов в диалоге кнопка «Сохранить версию»");
check((await page.getByTestId("engine-off").innerText()).includes("не подключена к ботам"), "без ботов: версия сохранится только в Студии");
await page.locator('[data-testid="publish-more"] > summary').click();
check((await page.getByTestId("publish-engine").innerText()).includes("сможет прочитать"), "выгрузка прошла круговую проверку");
await page.getByTestId("publish-comment").fill("как в bot-engine");
await page.getByTestId("publish-go").click();
await waitBadge(/Опубликована v1/);
check(true, `после публикации: «${await badge()}»`);

// 2. правки: текст, задержка стрелки, шаг исчезает без надгробия (как если бы его стёрли руками)
await page.evaluate(() => {
  const st = window.__studio.getState();
  const k = (key) => st.funnel.steps.find((s) => s.key === key).id;
  const z01 = k("s_welcome"), z02 = k("s_remind1"), gone = k("s_survey_nudge");
  st.apply((g) => ({
    ...g,
    steps: g.steps.filter((s) => s.id !== gone).map((s) => (s.id === z01
      ? { ...s, content: { ...s.content, tg: { ...s.content.tg, text: s.content.tg.text.replace("17 September", "18 September") } } }
      : s)),
    edges: g.edges.filter((e) => e.from_step !== gone && e.to_step !== gone)
      .map((e) => (e.from_step === z01 && e.to_step === z02 ? { ...e, delay_seconds: 300 } : e)),
  }));
});
await waitBadge(/изменено 3/);
check(true, `шапка после правок: «${await badge()}»`);

// 3. «Показать изменения» на холсте
await page.getByTestId("show-changes").check();
// вся воронка на экране: рисуются только видимые карточки, а открывается она с входа
await page.evaluate(() => window.__rf.fitView({ padding: 0.05 }));
await page.waitForSelector(".react-flow__node.diff-changed");
const changed = await page.locator(".react-flow__node.diff-changed").count();
// тень удалённого шага: берём её место из диффа и показываем (невидимое за краем экрана не рисуется)
const gpos = await page.evaluate(() => window.__versions.getState().overlay.ghosts.map((g) => g.position));
if (gpos[0]) await page.evaluate((p) => window.__rf.setCenter(p.x + 160, p.y + 60, { zoom: 0.8, duration: 0 }), gpos[0]);
await page.waitForTimeout(300);
const ghosts = await page.locator(".react-flow__node-ghost").count();
check(changed >= 1 && gpos.length === 1 && ghosts === 1, `холст: изменённых карточек ${changed}+, тень удалённого шага ${ghosts}`);

// 4. диалог: блокер «исчез без надгробия», публикация недоступна
await page.getByTestId("publish-open").click();
await page.waitForSelector('[data-testid="blockers"]', { timeout: 20000 });
const bl = await page.getByTestId("blockers").innerText();
check(bl.includes("s_survey_nudge") && bl.includes("и удалён"), "блокер: шаг удалён");
check(await page.getByTestId("publish-go").isDisabled(), "кнопка публикации недоступна при блокере");
const lines = await page.getByTestId("diff-list").innerText();
check(lines.includes("Шаг «s_remind1»: время «параллельно · через 3 мин» → «параллельно · через 5 мин»"), "строка диффа про время – словами карточки");
check(lines.includes("18 September"), "строка диффа про текст");

// 5. клик по строке диффа – фокус на карточку
await page.locator(".diff-row", { hasText: "Шаг «s_welcome»: текст TG" }).click();
await page.waitForTimeout(400);
const sel = await page.evaluate(() => { const s = window.__studio.getState(); return s.funnel.steps.find((x) => x.id === s.selection.steps[0])?.key; });
check(sel === "s_welcome", `клик по строке выбрал шаг ${sel}`);

// 6. вернуть шаг надгробием прямо из диалога – блокер уходит
const target = await page.evaluate(() => window.__studio.getState().funnel.steps.find((s) => s.key === "s_thanks").id);
await page.getByTestId("tomb-target").selectOption(target);
await page.getByTestId("tomb-fix").click();
await page.waitForSelector('[data-testid="publish-go"]:not([disabled])', { timeout: 20000 });
check(!(await page.getByTestId("blockers").count()), "после надгробия блокеров нет");
const lines2 = await page.getByTestId("diff-list").innerText();
check(lines2.includes("Шаг «s_survey_nudge» удалён – люди с него переходят на «s_thanks»"), "в диффе – удалённый шаг стал переходом");
await page.getByTestId("publish-comment").fill("дожим через 5 минут");
await page.getByTestId("publish-go").click();
await waitBadge(/Опубликована v2/);
check(true, `вторая публикация: «${await badge()}»`);

// 7. история: v2 опубликована, v1 в архиве, сравнение с предыдущей
await page.getByTestId("history-tab").click();
await page.waitForSelector('[data-testid="version-2"]');
const v2 = await page.getByTestId("version-2").innerText();
const v1 = await page.getByTestId("version-1").innerText();
check(v2.includes("опубликована") && v2.includes("дожим через 5 минут") && v2.includes("МСК"), "v2 опубликована, комментарий и время МСК");
check(v1.includes("архив"), "v1 – в архиве");
await page.getByTestId("version-2").getByText("Сравнить с предыдущей").click();
await page.waitForSelector('[data-testid="history-compare"]');
check((await page.getByTestId("history-compare").innerText()).includes("v1 → v2"), "сравнение v1 → v2 в панели");

// 8. откат к v1: черновик = копия v1, опубликованной остаётся v2
await page.getByTestId("rollback-1").click();
await waitBadge(/Черновик · изменено/);
const back = await page.evaluate(() => {
  const g = window.__studio.getState().funnel;
  const k = (key) => g.steps.find((s) => s.key === key);
  return { delay: g.edges.find((e) => e.from_step === k("s_welcome").id && e.to_step === k("s_remind1").id).delay_seconds, type: k("s_survey_nudge").type };
});
check(back.delay === 180 && back.type === "text", `откат: задержка ${back.delay}, шаг s_survey_nudge снова ${back.type}`);
const info = await api("GET", `/api/funnels/${name}/versions`);
check(info.published === 2 && info.draft.base_version === 1, "откат не публикует: опубликована v2, черновик от v1");

check(errors.length === 0, `ошибок страницы нет${errors.length ? ": " + errors.join("; ") : ""}`);
await browser.close();
console.log(failed ? `\nПровалено: ${failed}` : "\nВсё прошло");
process.exit(failed ? 1 : 0);
