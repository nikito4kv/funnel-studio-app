// Симулятор в настоящем браузере: вкладка, прогон, лента по дням, клик по событию → фокус на
// карточку, отметки на холсте, «Все ветки», персона «был на эфире», «Очистить».
//
//   uv run python -m studio.server --data <временная папка>
//   node scripts/e2e_sim.mjs [http://127.0.0.1:8765]
import { chromium } from "playwright";

const BASE = (process.argv[2] ?? process.env.STUDIO_URL ?? "http://127.0.0.1:8765").replace(/\/$/, "");
const BROWSER = process.env.BROWSER ?? "C:/Program Files/Google/Chrome/Application/chrome.exe";
let failed = 0;
const check = (ok, what) => {
  console.log(`${ok ? "✓" : "✗"} ${what}`);
  if (!ok) failed++;
};

const imp = await fetch(`${BASE}/api/import`, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ path: "launches/autowebinar/funnel.yaml" }),
}).then((r) => r.json());
const name = imp.name;

const browser = await chromium.launch({ executablePath: BROWSER });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
await page.goto(`${BASE}/#f=${name}`);
await page.waitForFunction(() => window.__studio?.getState().funnel);
await page.waitForSelector(".react-flow__node"); // демо-автовебинар открывается с входа
await page.waitForTimeout(1500);

// 1. вкладка и прогон «рег в 11:59»
await page.getByTestId("tab-sim").click();
await page.getByTestId("sim-when").fill("2026-09-24T11:59");
await page.getByTestId("sim-run").click();
await page.waitForSelector('[data-testid="sim-summary"]');
const sum = await page.getByTestId("sim-summary").innerText();
check(/ветка\s+DAY/.test(sum) && sum.includes("24.09 13:00"), `сводка: ветка DAY, эфир 24.09 13:00`);
const rows = await page.getByTestId("sim-event").count();
check(rows > 15, `лента: ${rows} событий`);
const dayHeads = await page.locator(".sim-dayh").allInnerTexts();
check(dayHeads[0].startsWith("День регистрации") && dayHeads.some((h) => h.startsWith("День 3")), `дни: ${dayHeads.length} («${dayHeads[0].split("\n")[0]}» …)`);

// 2. отметки на холсте
const badges = await page.getByTestId("sim-badge").count();
check(badges > 0, `отметки «уйдёт в …» на видимых карточках: ${badges}`);
const marks = await page.evaluate(() => window.__sim.getState().marks.size);
check(marks > 15, `отметок в прогоне: ${marks}`);

// 3. клик по событию → фокус на карточку
// в режиме «Постановщик» key в ленте не показывается – ищем событие по id шага
const d106 = await page.evaluate(() => window.__studio.getState().funnel.steps.find((s) => s.key === "d1_live").id);
const target = page.locator(`[data-testid="sim-event"][data-kind="sent"][data-step="${d106}"]`).first();
const sid = await target.getAttribute("data-step");
await target.click();
await page.waitForTimeout(600);
const sel = await page.evaluate(() => window.__studio.getState().selection.steps);
check(sel.length === 1 && sel[0] === sid, "клик по событию выделил шаг");
const inView = await page.evaluate((id) => {
  const el = document.querySelector(`.react-flow__node[data-id="${id}"]`);
  if (!el) return false;
  const r = el.getBoundingClientRect();
  return r.left < window.innerWidth && r.right > 0 && r.top < window.innerHeight && r.bottom > 0;
}, sid);
check(inView, "карточка d1_live на экране");
const badge = await page.locator(`.react-flow__node[data-id="${sid}"] [data-testid="sim-badge"]`).innerText();
check(badge.includes("уйдёт 13:00"), `значок на карточке: «${badge.trim()}»`);

// 4. выпавший шаг – красная рамка
const dropBtn = page.getByTestId("sim-drop").first();
check(await dropBtn.count() === 1 && (await dropBtn.innerText()).includes("d1_prep"), "в сводке выпал d1_prep (10:10 уже прошло)");
await dropBtn.click();
await page.waitForTimeout(600);
const dropId = await page.evaluate(() => window.__studio.getState().selection.steps[0]);
const dashed = await page.locator(`.react-flow__node[data-id="${dropId}"] .card.sim-dropped`).count();
check(dashed === 1, "выпавшая карточка в красной пунктирной рамке");

// 5. персона: был на эфире дня 1 → блок повтора пропущен условием
await page.getByTestId("sim-persona-sec").locator("summary").click();
await page.getByTestId("sim-air-1").check();
// ждём НОВЫЙ прогон: с 23.09 и без персоны что-то пропущено условием (час регистрации)
await page.evaluate(() => { window.__prevSim = window.__sim.getState().result; });
await page.getByTestId("sim-run").click();
await page.waitForFunction(() => { const r = window.__sim.getState().result; return r && r !== window.__prevSim; });
const gated = await page.evaluate(() => window.__sim.getState().result.summary.gated.length);
check(gated >= 1, `был на эфире дня 1: по условию пропущено ${gated}`);
await page.getByTestId("sim-air-1").uncheck();

// 6. все ветки
await page.getByTestId("sim-matrix").click();
await page.waitForSelector('[data-testid="sim-mx-row"]');
const mx = await page.getByTestId("sim-mx-row").count();
check(mx >= 6, `«Все ветки»: ${mx} моментов регистрации`);
const late = await page.getByTestId("sim-mx-row").filter({ hasText: "16:59" }).innerText();
check(late.includes("d1_prep"), "регистрация в 16:59 теряет d1_prep");
await page.getByTestId("sim-mx-row").filter({ hasText: "12:30" }).click();
await page.waitForFunction(() => window.__sim.getState().result?.branch?.id === "EVE");
check(true, "клик по строке матрицы – прогон этого момента (ветка EVE)");

// 7. очистить
await page.getByTestId("sim-clear").click();
await page.waitForTimeout(300);
check(await page.getByTestId("sim-badge").count() === 0, "«Очистить» убрал отметки с холста");
check(await page.getByTestId("sim-testrun").isDisabled(), "тест в TG без тестового бота неактивен");

check(errors.length === 0, `ошибок в консоли: ${errors.length}${errors.length ? " – " + errors.join("; ") : ""}`);
await browser.close();
console.log(failed ? `\nПРОВАЛЕНО: ${failed}` : "\nВсё прошло");
process.exit(failed ? 1 : 0);
