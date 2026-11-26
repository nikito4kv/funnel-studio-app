// Снимает кадры для README: настоящий интерфейс на демо-воронках из demo/botrepo (1440x900).
//
//   cd web && npm run build && node scripts/capture_screenshots.mjs
//
// Сам поднимает сервер Студии (временная папка данных), движок и Telegram не нужны, токены не вводятся.
// Результат – docs/screenshots/*.png.
import { spawn, execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const PORT = Number(process.env.SHOT_PORT ?? 8780);
const BASE = `http://127.0.0.1:${PORT}`;
const BROWSER = process.env.BROWSER ?? "C:/Program Files/Google/Chrome/Application/chrome.exe";
const OUT = join(ROOT, "docs", "screenshots");
mkdirSync(OUT, { recursive: true });

const data = mkdtempSync(join(tmpdir(), "fs-shots-"));
const ENV = { ...process.env, PYTHONIOENCODING: "utf-8", STUDIO_RUNTIME_DSN: "", BOT_REPO_PATH: join(ROOT, "demo", "botrepo") };
const server = spawn("uv", ["run", "python", "-m", "studio.server", "--port", String(PORT), "--data", data],
  { cwd: ROOT, env: ENV, stdio: "ignore" });
const stop = () => {
  try {
    if (process.platform === "win32") execFileSync("taskkill", ["/pid", String(server.pid), "/T", "/F"], { stdio: "ignore" });
    else server.kill();
  } catch { /* уже нет */ }
};
process.on("exit", stop);
for (let i = 0; i < 150; i++) {
  try { if ((await fetch(`${BASE}/api/health`)).ok) break; } catch { /* ждём */ }
  await new Promise((r) => setTimeout(r, 200));
}

async function api(method, path, body) {
  const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json" }, body: body && JSON.stringify(body) });
  return r.json();
}
const webinar = (await api("POST", "/api/import", { path: "launches/webinar/funnel.yaml" })).name;
const auto = (await api("POST", "/api/import", { path: "launches/autowebinar/funnel.yaml" })).name;
await api("POST", "/api/import", { path: "launches/leadmagnet/funnel.yaml" });

const browser = await chromium.launch({ executablePath: BROWSER });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
const shot = (name) => page.screenshot({ path: join(OUT, name) });
const open = async (name) => {
  await page.goto(`${BASE}/#f=${name}`);
  await page.reload();
  await page.waitForFunction(() => window.__studio?.getState().funnel);
  await page.waitForSelector(".react-flow__node");
  await page.waitForTimeout(1200);
};

// 1. холст: регистрация на эфир, выбран шаг, справа панель
await open(webinar);
await page.evaluate(() => {
  const st = window.__studio.getState();
  const ids = ["s_confirm", "s_greet", "s_welcome", "s_remind1", "s_remind2"].map((k) => st.funnel.steps.find((s) => s.key === k).id);
  st.select({ steps: [ids[2]] });
  window.__rf.fitView({ nodes: ids.map((id) => ({ id })), padding: 0.25, duration: 0 });
});
await page.waitForTimeout(1200);
await shot("01-canvas.png");

// 2. «как в телефоне»: тот же шаг глазами человека
await page.getByTestId("phone-button").click();
await page.waitForSelector('[data-testid="phone-preview"]');
await page.waitForTimeout(600);
await shot("02-phone-preview.png");
await page.keyboard.press("Escape");

// 3. симулятор: автовебинар, регистрация в 11:59
await open(auto);
await page.getByTestId("tab-sim").click();
await page.getByTestId("sim-when").fill("2026-09-24T11:59");
await page.getByTestId("sim-run").click();
await page.waitForSelector('[data-testid="sim-summary"]');
await page.waitForTimeout(900);
await shot("03-simulator.png");

// 4. версии: опубликовали v1, поправили текст и стрелку, убрали шаг – диалог «было / стало»
await open(webinar);
await page.getByTestId("publish-open").click();
await page.waitForSelector('[data-testid="publish-go"]:not([disabled])', { timeout: 20000 });
await page.getByTestId("publish-comment").fill("first public version");
await page.getByTestId("publish-go").click();
await page.waitForFunction(() => /Опубликована v1/.test(document.querySelector('[data-testid="version-badge"]')?.textContent ?? ""), null, { timeout: 15000 });
await page.evaluate(() => {
  const st = window.__studio.getState();
  const k = (key) => st.funnel.steps.find((s) => s.key === key).id;
  const welcome = k("s_welcome"), remind = k("s_remind1"), nudge = k("s_survey_nudge");
  st.apply((g) => ({
    ...g,
    steps: g.steps.filter((s) => s.id !== nudge).map((s) => (s.id === welcome
      ? { ...s, content: { ...s.content, tg: { ...s.content.tg, text: s.content.tg.text.replace("17 September", "24 September") } } }
      : s)),
    edges: g.edges.filter((e) => e.from_step !== nudge && e.to_step !== nudge)
      .map((e) => (e.from_step === welcome && e.to_step === remind ? { ...e, delay_seconds: 300 } : e)),
  }));
});
await page.waitForTimeout(500);
await page.getByTestId("publish-open").click();
await page.waitForSelector('[data-testid="diff-list"]', { timeout: 20000 });
await page.waitForTimeout(600);
await shot("04-versions-diff.png");
await page.keyboard.press("Escape");

// 5. выгрузка yaml: движок прочитал выгрузку так же, как оригинал
await open(auto);
await page.getByTestId("mode-switch").getByRole("button", { name: "Разработчик" }).click();
await page.getByTestId("more").click();
await page.getByTestId("export").click();
await page.waitForSelector(".modal .ok-box, .modal .err-box", { timeout: 20000 });
await page.waitForTimeout(800);
await shot("05-export-roundtrip.png");
await page.keyboard.press("Escape");

// 6. раздел «Боты»: боты из демо-репозитория, без токенов
await page.goto(`${BASE}/#bots`);
await page.waitForSelector('[data-testid="bot-add"]');
await page.locator('[data-testid="bots-others"] > summary').click();
await page.waitForTimeout(700);
await shot("06-bots.png");

console.log(errors.length ? `ошибки страницы: ${errors.join("; ")}` : "ошибок страницы нет");
await browser.close();
stop();
