// Этап 3 в настоящем браузере: «Запустить в боте» – Студия → движок.
// открыть воронку → «Запустить в боте…» → выбор бота «@… (название)», последствия одной-двумя фразами,
// без служебных слов → запуск → движок получил версию (funnel_versions + NOTIFY fs_versions) → шапка:
// «Работает в боте @… · версия 1» и «Проверить в боте» с правильной ссылкой → правка → «Есть правки,
// которых нет в боте» → в движке появились люди на шаге, которого в Студии нет → «Подробнее»:
// «перевести всех», подсказка карты, отказ без карты, запуск с картой → история → пустой шаг блокирует.
//
//   cd web && npm run build && npm run e2e:publish        (SCREENS=1 – ещё и docs/screens/stage3/*.png)
//
// Сам поднимает сервер Студии (порт 8773, временная папка данных) с --runtime-dsn на своей базе
// движка fs_runtime_e2e_publish (STUDIO_E2E_DSN) – рабочий движок и его база не затрагиваются.
import { spawn, execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const PORT = Number(process.env.E2E_PORT ?? 8773);
const BASE = `http://127.0.0.1:${PORT}`;
const DSN = process.env.STUDIO_E2E_DSN ?? "postgresql://fs:fsruntime@127.0.0.1:55432/fs_runtime_e2e_publish";
const BROWSER = process.env.BROWSER ?? "C:/Program Files/Google/Chrome/Application/chrome.exe";
const SHOTS = process.env.SCREENS === "1";
const OUT = join(ROOT, "docs", "screens", "stage3");
const ENV = { ...process.env, STUDIO_E2E_DSN: DSN, PYTHONIOENCODING: "utf-8" };
let failed = 0;
const check = (ok, what) => {
  console.log(`${ok ? "✓" : "✗"} ${what}`);
  if (!ok) failed++;
};

const db = (...args) => execFileSync("uv", ["run", "python", "scripts/e2e_engine_db.py", ...args], { cwd: ROOT, env: ENV, encoding: "utf-8" }).trim();
async function api(method, path, body) {
  const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json" }, body: body && JSON.stringify(body) });
  return r.json();
}

// ── база движка и сервер Студии ──
const { bot: BOT } = JSON.parse(db("setup"));
const data = mkdtempSync(join(tmpdir(), "fs-e2e-publish-"));
const server = spawn("uv", ["run", "python", "-m", "studio.server", "--port", String(PORT), "--data", data, "--runtime-dsn", DSN],
  { cwd: ROOT, env: ENV, stdio: "ignore" });
// uv запускает python дочерним процессом: на Windows гасим всё дерево, иначе сервер останется на порту
const stop = () => {
  try {
    if (process.platform === "win32") execFileSync("taskkill", ["/pid", String(server.pid), "/T", "/F"], { stdio: "ignore" });
    else server.kill();
  } catch { /* уже нет */ }
};
process.on("exit", stop);
for (let i = 0; i < 100; i++) {
  try { if ((await fetch(`${BASE}/api/health`)).ok) break; } catch { /* ждём */ }
  await new Promise((r) => setTimeout(r, 200));
}
const health = await api("GET", "/api/health");
check(health.engine === "127.0.0.1:55432/fs_runtime_e2e_publish", `сервер Студии знает движок: ${health.engine}`);

// своя копия webinar с уникальным id воронки – в движке у неё своя история версий
const imp = await api("POST", "/api/import", { path: "launches/webinar/funnel.yaml" });
const src = await api("GET", `/api/funnels/${imp.name}`);
const fid = `p${Date.now().toString(36)}`;
await api("POST", "/api/funnels", { launch: "webinar", name: fid, id: fid });
const name = `webinar__${fid}`;
await api("PUT", `/api/funnels/${name}`, { ...src, id: fid });

const browser = await chromium.launch({ executablePath: BROWSER });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("dialog", (d) => d.accept());
if (SHOTS) mkdirSync(OUT, { recursive: true });
const shot = async (file, testid = "publish-dialog") => {
  if (!SHOTS) return;
  await page.waitForTimeout(500);
  await page.getByTestId(testid).screenshot({ path: join(OUT, file) });
  console.log("  📷", file);
};
const shotHeader = async (file) => {
  if (!SHOTS) return;
  await page.waitForTimeout(500);
  await page.addStyleTag({ content: ".toasts { visibility: hidden !important; }" });   // тост закрыл бы шапку
  await page.locator("header.topbar").screenshot({ path: join(OUT, file) });
  await page.addStyleTag({ content: ".toasts { visibility: visible !important; }" });
  console.log("  📷", file);
};
const text = (id) => page.getByTestId(id).innerText();
const waitText = (id, re) => page.waitForFunction(([t, s]) => new RegExp(s).test(document.querySelector(`[data-testid="${t}"]`)?.textContent ?? ""), [id, re.source], { timeout: 15000 });
const openMore = async () => {
  const open = await page.getByTestId("publish-more").evaluate((d) => d.open);
  if (!open) await page.locator('[data-testid="publish-more"] > summary').click();
};
const JARGON = /движ|tenant|дифф|блокер|надгроб|STUDIO_[A-Z]|python -m|запуск «|v\d движка/;

await page.goto(`${BASE}/#f=${name}`);
await page.waitForFunction(() => window.__studio?.getState().funnel);
await page.waitForFunction(() => Object.keys(window.__studio.getState().funnel.layout.positions).length > 0);
await page.evaluate(() => localStorage.setItem("fs.publish.author", "Alex (e2e)"));

// 0. шапка до запуска: «Ещё не запущена ни в одном боте», кнопки «Сохранить черновик» и «Запустить в боте…»
await waitText("bot-badge", /Ещё не запущена ни в одном боте/);
check(true, "шапка: «Ещё не запущена ни в одном боте»");
check(await page.getByTestId("bot-test-link").count() === 0, "«Проверить в боте» ещё нет");
check((await text("publish-open")).includes("Запустить в боте…"), "кнопка шапки – «Запустить в боте…»");
// первая раскладка импортированной воронки сохраняется сама – дождаться её
await waitText("save", /^Черновик сохранён$/);
const saveTitle = await page.getByTestId("save").getAttribute("title");
check((await text("save")) === "Черновик сохранён" && saveTitle.includes("Бот этого не увидит"),
  `кнопка сохранения: «${await text("save")}», подсказка «${saveTitle}»`);

// 1. диалог: выбор бота, последствия словами, без служебных слов
await page.getByTestId("publish-open").click();
await page.waitForSelector('[data-testid="engine-section"]', { timeout: 20000 });
const opt = await page.getByTestId("engine-tenant").evaluate((s) => [...s.options].map((o) => o.text));
check(opt.includes(`@${BOT} (E2E: публикация из Студии)`), `бот в списке с именем: ${opt.join(" | ")}`);
await page.getByTestId("engine-tenant").selectOption("e2e-pub");
await page.waitForSelector('[data-testid="publish-consequences"]', { timeout: 20000 });
await page.waitForSelector('[data-testid="publish-go"]:not([disabled])', { timeout: 20000 });
const conseq = await text("publish-consequences");
check(conseq.includes("Новые люди пойдут по этой воронке.") && conseq.includes("Боевые боты не затрагиваются."), `последствия: «${conseq}»`);
check((await text("publish-go")) === `Запустить в боте @${BOT}`, `кнопка – «Запустить в боте @${BOT}»`);
check(!(await page.getByTestId("publish-more").evaluate((d) => d.open)), "«Подробнее» свёрнуто");
const dlg = await text("publish-dialog");
check(!JARGON.test(dlg), `в диалоге нет служебных слов${JARGON.test(dlg) ? ": " + dlg.match(JARGON)[0] : ""}`);
// стартовая: у e2e-pub уже есть воронки прошлых прогонов – галочка «Сделать стартовой» (у тестового бота – включена)
const entryRow = await page.getByTestId("engine-entry-row").count();
if (entryRow) {
  check(await page.getByTestId("engine-entry").isChecked(), "«Сделать стартовой» у тестового бота включена по умолчанию");
  check((await text("engine-entry-row")).includes("/start в боте будет вести сюда"), "галочка объясняет, что делает");
}
await page.getByTestId("publish-comment").fill("первая версия в бот");
await shot("ux_dialog_after.png");
await shot("p_01_publish_engine.png");

// 2. запускаем – движок получил версию и NOTIFY; шапка – «Работает в боте … · версия 1», ссылка «Проверить в боте»
const listener = spawn("uv", ["run", "python", "scripts/e2e_engine_db.py", "listen", fid, "40"], { cwd: ROOT, env: ENV });
let heard = "";
const ready = new Promise((res) => listener.stdout.on("data", (d) => { heard += d.toString(); if (heard.includes("ready")) res(); }));
await ready;
const done = new Promise((res) => listener.on("exit", res));
await page.getByTestId("publish-go").click();
await waitText("bot-badge", new RegExp(`Работает в боте @${BOT} · версия 1`));
await done;
check(/\d+:p[0-9a-z]+:1/.test(heard), `NOTIFY fs_versions: ${heard.split("\n").filter(Boolean).pop()}`);
let vers = JSON.parse(db("versions", fid));
check(vers.length === 1 && vers[0].source === `studio:${name}@v1` && vers[0].published_by === "Alex (e2e)"
  && vers[0].comment === "первая версия в бот", `funnel_versions: v1 из studio:${name}@v1, автор и комментарий`);
check(true, `шапка: «${await text("bot-badge")}»`);
check(await page.getByTestId("bot-behind").count() === 0, "правок, которых нет в боте, нет");
await page.waitForSelector('[data-testid="bot-test-link"]');
const link = await page.getByTestId("bot-test-link").getAttribute("href");
check(link === `https://t.me/${BOT}?start=studio_test_${fid}`, `«Проверить в боте» → ${link}`);
check((await page.getByTestId("bot-test-link").getAttribute("target")) === "_blank", "ссылка открывается в новой вкладке");
await shotHeader("ux_header_after.png");

// 3. правка (несохранённая) – «Есть правки, которых нет в боте»; кнопка сохранения это подсказывает
await page.evaluate(() => {
  const st = window.__studio.getState();
  const id = st.funnel.steps.find((s) => s.key === "s_welcome").id;
  st.apply((g) => ({ ...g, steps: g.steps.map((s) => (s.id === id
    ? { ...s, content: { ...s.content, tg: { ...s.content.tg, text: s.content.tg.text.replace("17 September", "18 September") } } } : s)) }));
});
await page.waitForSelector('[data-testid="bot-behind"]', { timeout: 15000 });
check((await text("bot-behind")).includes("Есть правки, которых нет в боте"), "несохранённая правка: «Есть правки, которых нет в боте»");
check((await text("save")) === "Сохранить черновик •", "кнопка сохранения – «Сохранить черновик •»");
await shotHeader("ux_header_behind.png");
await page.getByTestId("save").click();
await waitText("save", /^Черновик сохранён$/);
check(await page.getByTestId("bot-behind").count() === 1, "после «Сохранить черновик» правка всё ещё не в боте");

// 4. в движке появились люди на шаге z_old, которого в Студии нет (версия из командной строки)
const seeded = JSON.parse(db("seed", fid));
await page.getByTestId("publish-open").click();
await page.waitForSelector('[data-testid="publish-consequences"]', { timeout: 20000 });
await page.waitForFunction(() => /уже внутри/.test(document.querySelector('[data-testid="publish-consequences"]')?.textContent ?? ""), null, { timeout: 20000 });
const c2 = await text("publish-consequences");
check(c2.includes("Новые люди получат эту версию.") && c2.includes("5 человек уже внутри – они дойдут по старой."), `последствия с людьми: «${c2}»`);
await openMore();
await page.waitForFunction(() => document.querySelector('[data-testid="engine-people"]'), null, { timeout: 20000 });
const ppl = await text("engine-people");
check(ppl.includes("«z_old» – у 3 человека запланирован этот шаг") && ppl.includes("доедут по старой версии"),
  "«Подробнее»: у 3 чел. запланирован z_old – доедут по старой версии");
check(ppl.includes("2 человека ждут, когда пришлют контакт"), "видно, кто ждёт контакт");
check((await text("engine-report")).includes(`В боте @${BOT} работает прошлая версия этой воронки`), "шапка отчёта – словами");
check((await text("engine-current")).includes("запущена не из Студии"), `что сейчас в боте: ${await text("engine-current")}`);

// 5. «перевести всех»: подсказка карты – следующий шаг s_remind2
await page.getByTestId("engine-mode-migrate").click();
await page.waitForSelector('[data-testid="engine-map"]', { timeout: 20000 });
await page.waitForFunction(() => document.querySelector('[data-testid="engine-map-z_old"]')?.value, null, { timeout: 20000 });
const sugg = await page.getByTestId("engine-map-z_old").evaluate((s) => s.options[s.selectedIndex].text);
check(sugg.startsWith("s_remind2"), `подсказка карты: z_old → ${sugg}`);
await page.waitForSelector('[data-testid="publish-go"]:not([disabled])', { timeout: 20000 });
check((await text("engine-report")).includes("переведём на «s_remind2»"), "в отчёте: переведём на «s_remind2»");
check((await text("publish-consequences")).includes("перейдут на эту версию"), "последствия в режиме «перевести всех»");
await shot("p_02_migrate_map.png");

// 6. карту убрали – бот не примет версию, кнопка недоступна
await page.getByTestId("engine-map-z_old").selectOption("");
await page.waitForSelector('[data-testid="engine-refused"]', { timeout: 20000 });
const refused = await text("engine-refused");
check(refused.includes("Бот не примет эту версию") && refused.includes("шаг «z_old» удалён") && refused.includes("у 3 чел. запланирован")
  && !/надгроб/.test(refused), `отказ словами: ${refused}`);
check(await page.getByTestId("publish-go").isDisabled(), "при отказе кнопка недоступна");
await shot("p_03_engine_refused.png");

// 7. карту вернули – запускаем; люди переведены на новую версию
const z02b = await page.evaluate(() => window.__studio.getState().funnel.steps.find((s) => s.key === "s_remind2").id);
await page.getByTestId("engine-map-z_old").selectOption(z02b);
await page.waitForSelector('[data-testid="publish-go"]:not([disabled])', { timeout: 20000 });
await page.getByTestId("publish-comment").fill("18 September, всех переводим");
await page.getByTestId("publish-go").click();
await waitText("bot-badge", new RegExp(`Работает в боте @${BOT} · версия 2`));
vers = JSON.parse(db("versions", fid));
const last = vers[vers.length - 1];
check(last.version === seeded.version + 1 && last.mode === "migrate" && last.source === `studio:${name}@v2`
  && last.pending === 3, `движок: v${last.version} migrate, задач на новой версии – ${last.pending}`);
check(await page.getByTestId("bot-behind").count() === 0, "после запуска правок, которых нет в боте, нет");

// 8. история: куда и каким режимом ушла каждая версия
await page.getByTestId("history-tab").click();
await page.waitForSelector('[data-testid="version-engine-2"]');
const h2 = await text("version-engine-2");
const h1 = await text("version-engine-1");
check(h2.includes(`→ запущена в боте @${BOT} · перевести всех`), `история v2: ${h2}`);
check(h1.includes(`→ запущена в боте @${BOT} · только новым`), `история v1: ${h1}`);

// 9. пустой шаг блокирует запуск; «сообщение с картинкой» без картинки – только предупреждение
await page.evaluate(() => {
  const st = window.__studio.getState();
  const e = st.funnel.steps.find((s) => s.key === "s_remind1").id;
  const p = st.funnel.steps.find((s) => s.type === "photo" && s.key !== "s_remind1").id;
  st.apply((g) => ({ ...g, steps: g.steps.map((s) => (s.id === e
    ? { ...s, title: "Текст", media: { ...s.media, image: null, images: null }, content: { ...s.content, tg: { ...s.content.tg, text: "" } } }
    : s.id === p ? { ...s, media: { ...s.media, image: null, images: null } } : s)) }));
});
await page.getByTestId("publish-open").click();
await page.waitForSelector('[data-testid="blockers"]', { timeout: 20000 });
const bl = await text("blockers");
check(bl.includes("Шаг «Текст» пустой – бот ничего не отправит"), `пустой шаг – проблема: ${bl.split("\n").slice(0, 2).join(" / ")}`);
check(await page.getByTestId("publish-go").isDisabled(), "с пустым шагом «Запустить в боте» недоступна");
const sw = await text("step-warnings");
check(sw.includes("сообщение с картинкой, но картинки нет"), "картинка без картинки – предупреждение");
await shot("ux_dialog_empty.png");
await page.keyboard.press("Escape");

check(errors.length === 0, `ошибок страницы нет${errors.length ? ": " + errors.join("; ") : ""}`);
await browser.close();
stop();
console.log(failed ? `\nПровалено: ${failed}` : "\nВсё прошло");
process.exit(failed ? 1 : 0);
