// Раздел «Боты» в настоящем браузере – на ФАЛЬШИВОМ Bot API (studio.bots.fake), Telegram не трогается.
// «Ещё → Боты» → неверный токен – понятная ошибка → тестовый бот: карточка, аватар, профиль из «Telegram»,
// счётчики, правка описаний и команд, аватар (квадрат по центру), подсказки @BotFather → «боевой» бот
// (его @username есть в launches/webinar/launch.yaml bot-engine): «где работает», правка только после
// «было → стало» → токен не попал ни в один ответ сервера и ни в его журнал, опасные методы не вызывались.
//
//   cd web && npm run build && npm run e2e:bots        (SCREENS=1 – ещё и docs/screens/stage3/bots_*.png)
import { spawn, execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const PORT = Number(process.env.E2E_PORT ?? 8774);
const FAKE_PORT = Number(process.env.E2E_FAKE_PORT ?? 8799);
const BASE = `http://127.0.0.1:${PORT}`;
const FAKE = `http://127.0.0.1:${FAKE_PORT}`;
const BROWSER = process.env.BROWSER ?? "C:/Program Files/Google/Chrome/Application/chrome.exe";
const SHOTS = process.env.SCREENS === "1";
const OUT = join(ROOT, "docs", "screens", "stage3");
const REPO = process.env.BOT_REPO_PATH ?? join(ROOT, "demo", "botrepo");
// те же, что в studio/bots/fake.py
const TEST_TOKEN = "1111111111:AAtestTESTtestTESTtestTESTtest_0001";
const COMBAT_TOKEN = "2222222222:AAcombatCOMBATcombatCOMBATcomb_0002";
let failed = 0;
const check = (ok, what) => {
  console.log(`${ok ? "✓" : "✗"} ${what}`);
  if (!ok) failed++;
};

const data = mkdtempSync(join(tmpdir(), "fs-e2e-bots-"));
const ENV = {
  ...process.env, PYTHONIOENCODING: "utf-8", STUDIO_TG_API: FAKE, STUDIO_RUNTIME_DSN: "",
  STUDIO_SECRET_KEY: "e2e-bots-secret-key-0123456789abcdef", BOT_REPO_PATH: REPO,
};
const procs = [];
let serverLog = "";
const run = (args, collect) => {
  const p = spawn("uv", ["run", "python", ...args], { cwd: ROOT, env: ENV, stdio: ["ignore", "pipe", "pipe"] });
  const eat = (b) => { if (collect) serverLog += b.toString(); };
  p.stdout.on("data", eat);
  p.stderr.on("data", eat);
  procs.push(p);
  return p;
};
const stop = () => {
  for (const p of procs) {
    try {
      if (process.platform === "win32") execFileSync("taskkill", ["/pid", String(p.pid), "/T", "/F"], { stdio: "ignore" });
      else p.kill();
    } catch { /* уже нет */ }
  }
};
process.on("exit", stop);
run(["-m", "studio.bots.fake", "--port", String(FAKE_PORT)], false);
run(["-m", "studio.server", "--port", String(PORT), "--data", data], true);
const waitUp = async (url) => {
  for (let i = 0; i < 150; i++) {
    try { if ((await fetch(url)).ok) return true; } catch { /* ждём */ }
    await new Promise((r) => setTimeout(r, 200));
  }
  return false;
};
check(await waitUp(`${FAKE}/__calls`), "фальшивый Bot API поднялся");
check(await waitUp(`${BASE}/api/health`), "сервер Студии поднялся");
const fake = async () => (await fetch(`${FAKE}/__calls`)).json();

const browser = await chromium.launch({ executablePath: BROWSER });
const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
const errors = [];
const bodies = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("response", async (r) => {
  if (r.url().includes("/api/")) { try { bodies.push(await r.text()); } catch { /* картинка */ } }
});
if (SHOTS) mkdirSync(OUT, { recursive: true });
const shot = async (file, testid = "bots-screen") => {
  if (!SHOTS) return;
  await page.waitForTimeout(400);
  await page.addStyleTag({ content: ".toasts { visibility: hidden !important; }" });
  await page.getByTestId(testid).screenshot({ path: join(OUT, file) });
  await page.addStyleTag({ content: ".toasts { visibility: visible !important; }" });
  console.log("  📷", file);
};
const text = (id) => page.getByTestId(id).first().innerText();
const JARGON = /getUpdates|[Ww]ebhook|tenant|STUDIO_[A-Z]|движок|secret_ref|env:/;

// 1. вход из «Ещё»
await page.goto(BASE);
await page.waitForFunction(() => window.__studio);
await page.waitForLoadState("networkidle");
for (let i = 0; i < 3 && !(await page.getByTestId("more-menu").isVisible()); i++) {
  await page.getByTestId("more").click();
  await page.waitForTimeout(300);
}
await page.getByTestId("bots-open").click();
await page.waitForSelector('[data-testid="bots-screen"]');
check(new URL(page.url()).hash.includes("bots"), `адрес экрана: ${new URL(page.url()).hash}`);
await page.waitForSelector('[data-testid="bot-add"]');
check((await text("bot-add")).includes("/mybots"), "«где взять токен»: пошагово, с /mybots");
await page.locator('[data-testid="bots-others"] > summary').click();
const others = await page.getByTestId("bot-other").allInnerTexts();
check(others.some((t) => t.includes("@northwind_workshop_bot") && t.includes("в bot-engine: запуск webinar")),
  `боты bot-engine без токена видны с запуском (${others.length})`);
check((await page.getByTestId("bot-max").count()) > 0, "общие MAX-боты – показом");
await shot("bots_01_add.png");

// 2. неверный токен – понятная ошибка
await page.getByTestId("bot-token").fill("abc");
await page.getByTestId("bot-add-go").click();
await page.waitForSelector('[data-testid="bot-add-error"]');
check((await text("bot-add-error")).includes("не похоже на токен"), `кривой токен: «${await text("bot-add-error")}»`);
await page.getByTestId("bot-token").fill("3333333333:AAunknownUNKNOWNunknownUNKNOWNxx_03");
await page.getByTestId("bot-add-go").click();
await page.waitForFunction(() => /не знает такой токен/.test(document.querySelector('[data-testid="bot-add-error"]')?.textContent ?? ""));
check(true, "чужой токен: «Telegram не знает такой токен…»");

// 3. тестовый бот
await page.getByTestId("bot-token").fill(TEST_TOKEN);
await page.getByTestId("bot-add-go").click();
await page.waitForSelector('[data-testid="bot-profile"]');
await page.waitForSelector('[data-testid="bot-name-input"]');
check((await text("bot-title")) === "Тест Студии", `карточка: ${await text("bot-title")}`);
check(await page.getByTestId("bot-test").count() === 1, "бот помечен тестовым");
check((await page.getByTestId("bot-profile").innerText()).includes("…0001") &&
  !(await page.getByTestId("bot-profile").innerText()).includes(TEST_TOKEN), "видны только последние 4 символа токена");
const ava = page.locator('[data-testid="bot-profile"] img[data-testid="bot-avatar"]').first();
await ava.waitFor();
check(await ava.evaluate((i) => i.complete && i.naturalWidth > 0), "аватар из Telegram загрузился");
check((await page.getByTestId("bot-row").count()) === 1, "бот в списке слева");
check(await page.getByTestId("bot-description-input").inputValue() === "Тестовый бот Студии", "описание прочитано из Telegram");
check((await text("bot-name-count")) === "11 / 64", `счётчик имени: ${await text("bot-name-count")}`);

// короткое описание: счётчик, сохранение без подтверждения (тестовый), превью
await page.getByTestId("bot-short-input").fill("к".repeat(121));
check((await text("bot-short-count")) === "121 / 120" && await page.getByTestId("bot-short-save").isDisabled(), "длиннее 120 – сохранить нельзя");
await page.getByTestId("bot-short-input").fill("Бот для проверок воронок");
await page.getByTestId("bot-short-save").click();
await page.waitForFunction(() => /Бот для проверок воронок/.test(document.querySelector('[data-testid="preview-short"]')?.textContent ?? ""));
let f = await fake();
check(f.bots["1111"].short_description === "Бот для проверок воронок", "короткое описание ушло в Telegram, превью обновилось");

// команды
await page.getByTestId("cmd-add").click();
await page.getByTestId("cmd-command").last().fill("help");
await page.getByTestId("cmd-description").last().fill("Помощь");
await page.getByTestId("cmd-save").click();
await page.waitForTimeout(600);
f = await fake();
check(JSON.stringify(f.bots["1111"].commands) === JSON.stringify([{ command: "start", description: "Начать" }, { command: "help", description: "Помощь" }]),
  `команды: ${JSON.stringify(f.bots["1111"].commands)}`);

// аватар: картинка → превью «станет» → поставить
await page.getByTestId("photo-file").setInputFiles(join(REPO, "launches", "webinar", "static", "survey.jpg"));
await page.waitForSelector('[data-testid="photo-preview"]');
await page.getByTestId("photo-save").click();
await page.waitForFunction(() => !document.querySelector('[data-testid="photo-preview"]'), null, { timeout: 15000 });
f = await fake();
check(f.bots["1111"].photos === 2, `аватар поставлен (фотографий у бота: ${f.bots["1111"].photos})`);

// подсказки @BotFather
await page.locator('[data-testid="hint-descpic"] > summary').click();
const hint = await text("hint-descpic");
check(hint.includes("/setdescriptionpic") && hint.includes("@e2e_studio_TEST_bot"), "подсказка картинки описания с командой и ботом");
check(await page.locator('[data-testid="hint-descpic"] .copy-btn').count() >= 3, "кнопки «скопировать»");
check((await page.getByTestId("botfather-hints").innerText()).includes("Приватность"), "приватность групп – в подсказках");
check(!JARGON.test(await page.getByTestId("bots-screen").innerText()), "без служебных слов");
await shot("bots_02_profile.png");

// 4. «боевой» бот: где работает, подтверждение «было → стало»
await page.getByTestId("bot-add-open").click();
await page.getByTestId("bot-token").fill(COMBAT_TOKEN);
await page.getByTestId("bot-add-go").click();
await page.waitForSelector('[data-testid="bot-careful"]');
await page.waitForSelector('[data-testid="bot-short-input"]');
const where = await text("bot-where");
check(where.includes("в bot-engine: запуск webinar"), `где работает: ${where.replace(/\n/g, " | ")}`);
await page.getByTestId("bot-short-input").fill("Новый текст");
await page.getByTestId("bot-short-save").click();
await page.waitForSelector('[data-testid="bot-confirm"]');
check((await text("confirm-was")) === "Эфир 17.09" && (await text("confirm-now")) === "Новый текст", "подтверждение: было «Эфир 17.09» → станет «Новый текст»");
f = await fake();
check(f.bots["2222"].short_description === "Эфир 17.09", "до подтверждения в Telegram ничего не поменялось");
await shot("bots_03_confirm.png", "bot-short");
await page.getByTestId("confirm-yes").click();
await page.waitForFunction(() => !document.querySelector('[data-testid="bot-confirm"]'));
await page.waitForTimeout(400);
f = await fake();
check(f.bots["2222"].short_description === "Новый текст", "после подтверждения – поменялось");

// 5. Esc закрывает, адрес #bots открывает снова
await page.keyboard.press("Escape");
await page.waitForSelector('[data-testid="bots-screen"]', { state: "detached" });
check(true, "Esc закрывает раздел");
await page.goto(`${BASE}/#bots`);
await page.waitForSelector('[data-testid="bot-row"]');
check((await page.getByTestId("bot-row").count()) === 2, "по адресу #bots – список из двух ботов");

// 6. безопасность
f = await fake();
check(f.forbidden.length === 0, `опасные методы не вызывались (${f.forbidden.join(", ") || "ни одного"})`);
const used = [...new Set(f.calls.map((c) => c.method))].sort();
console.log(`  методы Bot API: ${used.join(", ")}`);
const leak = (s) => s.includes(TEST_TOKEN) || s.includes(COMBAT_TOKEN) || s.includes(TEST_TOKEN.split(":")[1]) || s.includes(COMBAT_TOKEN.split(":")[1]);
check(!bodies.some(leak), `токен не вернулся ни в одном ответе API (${bodies.length})`);
check(!leak(serverLog), "токена нет в журнале сервера");

check(errors.length === 0, `ошибок страницы нет${errors.length ? ": " + errors.join("; ") : ""}`);
await browser.close();
stop();
console.log(failed ? `\nПровалено: ${failed}` : "\nВсё прошло");
process.exit(failed ? 1 : 0);
