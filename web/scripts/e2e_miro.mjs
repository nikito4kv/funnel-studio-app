// Импорт из Miro в настоящем браузере – с фальшивым Miro (снимок фрейма workshop):
// «Открыть воронку» → «Импорт из Miro…» → ссылка, запуск, имя → ход импорта → итог «собрано N,
// K мест проверить» → воронка на холсте с картинками → замечания в «Проверке» со ссылкой на Miro
// → «Проверено» → ручная правка → доска поменялась → «Обновить из Miro…» → «было / стало»
// → «Применить» → изменение доски пришло, ручная правка осталась.
//
//   cd web && npm run build && npm run e2e:miro
//
// Сам поднимает фальшивый Miro (studio.miro.fake, порт 8797) и сервер Студии (порт 8778,
// временная папка данных, MIRO_API_BASE на фальшивый Miro). Настоящий Miro не трогается.
import { execFileSync, spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const PORT = Number(process.env.E2E_PORT ?? 8778);
const MIRO_PORT = Number(process.env.E2E_MIRO_PORT ?? 8797);
const BASE = `http://127.0.0.1:${PORT}`;
const BROWSER = process.env.BROWSER ?? "C:/Program Files/Google/Chrome/Application/chrome.exe";
const SHOTS = process.env.SCREENS === "1";
const OUT = join(ROOT, "docs", "screens", "miro");
const LINK = "https://miro.com/app/board/uXjDemoWorkshop=/?moveToWidget=7000000000000000316";
let failed = 0;
const check = (ok, what) => {
  console.log(`${ok ? "✓" : "✗"} ${what}`);
  if (!ok) failed++;
};

// ── снимки доски: исходный и «постановщик поправил текст второго дожима» ──
const tmp = mkdtempSync(join(tmpdir(), "fs-e2e-miro-"));
const snapPath = join(ROOT, "tests", "fixtures", "miro", "workshop.json");
const edited = JSON.parse(readFileSync(snapPath, "utf-8"));
for (const it of edited.items) {
  if (it.id === "7000000000000000008") it.data.content = it.data.content.replace("пока ты читаешь", "пока ты ЧИТАЕШЬ");
}
const editedPath = join(tmp, "edited.json");
writeFileSync(editedPath, JSON.stringify(edited));

const ENV = { ...process.env, PYTHONIOENCODING: "utf-8", MIRO_TOKEN: "e2e", MIRO_API_BASE: `http://127.0.0.1:${MIRO_PORT}/v2` };
delete ENV.ANTHROPIC_API_KEY;
const procs = [];
const start = (args, env = ENV) => { const p = spawn("uv", ["run", "python", ...args], { cwd: ROOT, env, stdio: "ignore" }); procs.push(p); return p; };
const stop = () => {
  for (const p of procs) {
    try {
      if (process.platform === "win32") execFileSync("taskkill", ["/pid", String(p.pid), "/T", "/F"], { stdio: "ignore" });
      else p.kill();
    } catch { /* уже нет */ }
  }
};
process.on("exit", stop);
start(["-m", "studio.miro.fake", snapPath, "--port", String(MIRO_PORT), "--board-edit", editedPath]);
start(["-m", "studio.server", "--port", String(PORT), "--data", join(tmp, "data"), "--runtime-dsn", ""]);
for (let i = 0; i < 150; i++) {
  try { if ((await fetch(`${BASE}/api/health`)).ok) break; } catch { /* ждём */ }
  await new Promise((r) => setTimeout(r, 200));
}
const st = await (await fetch(`${BASE}/api/miro/status`)).json();
check(st.token === true, "сервер знает токен Miro");

const browser = await chromium.launch({ executablePath: BROWSER });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("dialog", (d) => d.accept());
await page.goto(`${BASE}/`);
if (SHOTS) mkdirSync(OUT, { recursive: true });
const shot = async (file, sel) => {
  if (!SHOTS) return;
  await page.waitForTimeout(400);
  await (sel ? page.locator(sel) : page).screenshot({ path: join(OUT, file) });
};

// 1. диалог открытия → «Импорт из Miro…»
await page.getByRole("button", { name: "Новая воронка или импорт" }).first().click();
await page.getByTestId("miro-from-open").click();
await page.waitForSelector('[data-testid="miro-dialog"]');
await page.locator('[data-testid="miro-rules"] > summary').click();
const rules = await page.getByTestId("miro-rules").innerText();
check(rules.includes("Одна колонка – одно сообщение") && rules.includes("синий стикер"), "подсказка «Как рисовать в Miro» – в диалоге");
check(await page.getByTestId("miro-go").isDisabled(), "без ссылки импорт недоступен");
await shot("01_dialog_rules.png", '[data-testid="miro-dialog"]');
await page.getByTestId("miro-link-input").fill(LINK);
await page.getByTestId("miro-launch").fill("webinar");
await page.getByTestId("miro-name").fill("e2e");
await page.getByTestId("miro-go").click();
await page.waitForSelector('[data-testid="miro-result"]', { timeout: 30000 });
const summary = await page.getByTestId("miro-summary").innerText();
check(/собрано 15 сообщ\., картинок 7(, заметок на холсте \d+)?, \d+ мест проверить/.test(summary), `итог: ${summary}`);
await shot("02_result.png", '[data-testid="miro-dialog"]');

// 2. воронка на холсте: картинки видны, сомнительное подсвечено
await page.getByTestId("miro-open-funnel").click();
await page.waitForFunction(() => window.__studio?.getState().funnel?.steps.length === 15);
await page.waitForFunction(() => Object.keys(window.__studio.getState().funnel.layout.positions).length >= 15, null, { timeout: 20000 });
await page.evaluate(() => window.__rf.fitView({ padding: 0.05 }));
await page.waitForTimeout(800);
const imgs = await page.evaluate(() => [...document.querySelectorAll(".react-flow__node img")].filter((i) => i.complete && i.naturalWidth > 0).length);
check(imgs >= 5, `картинки из Miro видны на карточках: ${imgs}`);
const marks = await page.getByTestId("miro-mark").count();
check(marks >= 3, `карточки «из Miro: проверить» отмечены: ${marks}`);
await shot("03_canvas.png");

// 3. «Проверка»: замечания импорта со ссылкой на элемент доски
await page.getByTestId("check-button").click();
await page.waitForSelector('[data-testid="validation-panel"]');
const links = await page.getByTestId("miro-link").count();
const href = await page.getByTestId("miro-link").first().getAttribute("href");
check(links >= 3 && /miro\.com\/app\/board\/.+moveToWidget=\d+/.test(href ?? ""), `в «Проверке» ${links} замечаний из Miro со ссылкой на доску`);
await shot("04_check_panel.png");
const before = await page.getByTestId("miro-ok").count();
await page.getByTestId("miro-ok").first().click();
check(await page.getByTestId("miro-ok").count() === before - 1, "«Проверено» убирает замечание");

// 4. панель шага: «открыть в Miro» и сомнения карточки
const circle = await page.evaluate(() => window.__studio.getState().funnel.steps.find((s) => s.type === "video_note" && s.miro.doubts.some((d) => d.code === "video_note_file")).id);
await page.evaluate((id) => { const s = window.__studio.getState(); s.set({ rightTab: "props" }); s.focusStep(id); }, circle);
await page.waitForSelector('[data-testid="miro-step"]');
check((await page.getByTestId("miro-open-item").getAttribute("href"))?.includes("moveToWidget="), "у карточки – ссылка «открыть в Miro»");
check((await page.getByTestId("miro-step").innerText()).includes("загрузите ролик"), "у кружка – «загрузите ролик»");
await shot("05_step_panel.png");

// 5. ручная правка в Студии и сохранение
await page.evaluate(() => {
  const s = window.__studio.getState();
  const id = s.funnel.steps.find((x) => x.key === "m04").id;
  s.apply((g) => ({ ...g, steps: g.steps.map((x) => (x.id === id ? { ...x, content: { ...x.content, tg: { ...x.content.tg, text: x.content.tg.text + "\nРУЧНАЯ ПРАВКА" } } } : x)) }));
});
await page.keyboard.press("Control+s");
await page.waitForFunction(() => {
  const s = window.__studio.getState();
  return s.savedFunnel === s.funnel || document.body.innerText.includes("Черновик сохранён");
}, null, { timeout: 10000 }).catch(() => {});

// 6. доска поменялась → «Обновить из Miro…» → «было / стало» → «Применить»
await page.getByTestId("more").click();
await page.getByTestId("miro-open").click();
await page.waitForSelector('[data-testid="miro-dialog"]');
check((await page.getByTestId("miro-link-input").inputValue()).includes("moveToWidget=7000000000000000316"), "ссылка подставлена из воронки");
await page.getByTestId("miro-go").click();
await page.waitForSelector('[data-testid="miro-result"]', { timeout: 30000 });
const diff = await page.getByTestId("diff-list").innerText();
check(diff.includes("ЧИТАЕШЬ"), "«было / стало»: изменение текста с доски");
await shot("06_reimport_diff.png", '[data-testid="miro-dialog"]');
check(!diff.includes("РУЧНАЯ"), "ручная правка не попала в «было / стало» как потеря");
await page.getByTestId("miro-apply").click();
await page.waitForFunction(() => window.__studio.getState().funnel.steps.some((s) => s.content.tg.text.includes("ЧИТАЕШЬ")), null, { timeout: 10000 });
const kept = await page.evaluate(() => window.__studio.getState().funnel.steps.find((s) => s.key === "m04").content.tg.text.endsWith("РУЧНАЯ ПРАВКА"));
check(kept, "после «Применить» ручная правка осталась");

check(errors.length === 0, `ошибок страницы нет${errors.length ? ": " + errors.join("; ") : ""}`);
await browser.close();
stop();
console.log(failed ? `\nПровалено: ${failed}` : "\nВсё прошло");
process.exit(failed ? 1 : 0);
