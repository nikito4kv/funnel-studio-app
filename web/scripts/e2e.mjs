// Проверка редактора в настоящем браузере: палитра, ввод текста с автозаменой, перетаскивание,
// протягивание стрелки мышью, undo/redo, удаление, надгробие, сохранение, выгрузка.
//
//   uv run python -m studio.server --data <временная папка>
//   node scripts/e2e.mjs [http://127.0.0.1:8765]
import { chromium } from "playwright";

const BASE = (process.argv[2] ?? "http://127.0.0.1:8765").replace(/\/$/, "");
const BROWSER = process.env.BROWSER ?? "C:/Program Files/Google/Chrome/Application/chrome.exe";
const name = `e2e${Date.now()}`;
let failed = 0;
const check = (ok, what) => {
  console.log(`${ok ? "✓" : "✗"} ${what}`);
  if (!ok) failed++;
};

async function api(method, path, body) {
  const r = await fetch(BASE + path, { method, headers: { "Content-Type": "application/json" }, body: body && JSON.stringify(body) });
  return r.json();
}
await api("POST", "/api/funnels", { launch: "e2e", name, id: "e2e_funnel" });
const full = `e2e__${name}`;

const browser = await chromium.launch({ executablePath: BROWSER });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
const G = () => page.evaluate(() => window.__studio.getState().funnel);

await page.goto(`${BASE}/?e2e=1#f=${full}`);
await page.waitForFunction(() => window.__studio?.getState().funnel);
await page.waitForSelector(".react-flow");

// 0. пустая воронка – подсказка; режим по умолчанию – «Постановщик»
check(await page.getByTestId("empty-hint").isVisible(), "пустая воронка: подсказка «перетащите карточку слева»");
check(await page.evaluate(() => document.querySelector(".app")?.classList.contains("mode-simple")), "по умолчанию – простой режим");

// 1. палитра → карточка, фокус в тексте, автозамены при вводе
await page.getByTestId("palette").getByText("Сообщение с картинкой").click();
await page.waitForSelector(".react-flow__node-step");
await page.waitForTimeout(300);
await page.keyboard.type("Привет, [ИМЯ]! Ждём — тебя");
await page.waitForTimeout(200);
let g = await G();
const a = g.steps[0];
check(g.steps.length === 1 && a.type === "photo" && a.key === "msg_01", "палитра добавила шаг msg_01");
check(a.content.tg.text === "Привет, {Имя}! Ждём – тебя", `автозамена [ИМЯ] и тире: «${a.content.tg.text}»`);

// 2. жирный через панель инструментов
await page.keyboard.down("Shift");
for (let i = 0; i < 4; i++) await page.keyboard.press("ArrowLeft");
await page.keyboard.up("Shift");
await page.getByTitle("Жирный").click();
g = await G();
check(g.steps[0].content.tg.text.endsWith("<b>тебя</b>"), `жирный: «${g.steps[0].content.tg.text}»`);

// 3. второй шаг и перетаскивание
await page.getByTestId("palette").getByText("Текст", { exact: true }).click();
await page.waitForTimeout(300);
g = await G();
const b = g.steps[1];
await page.evaluate((id) => {
  const s = window.__studio.getState();
  s.apply((f) => ({ ...f, layout: { positions: { ...f.layout.positions, [id]: { x: (f.layout.positions[id]?.x ?? 0) + 600, y: f.layout.positions[id]?.y ?? 0 } } } }));
  window.__rf.fitView({ padding: 0.3 });
}, b.id);
await page.waitForTimeout(400);
const before = (await G()).layout.positions[a.id];
const head = page.locator(`.react-flow__node[data-id="${a.id}"] .card-head`);
const hb = await head.boundingBox();
await page.mouse.move(hb.x + 40, hb.y + 8);
await page.mouse.down();
await page.mouse.move(hb.x + 40, hb.y + 120, { steps: 8 });
await page.mouse.up();
await page.waitForTimeout(200);
const after = (await G()).layout.positions[a.id];
check(after.y > before.y + 50, `перетаскивание сохранило координаты (${Math.round(before.y)} → ${Math.round(after.y)})`);

// 4. стрелка мышью: из «по времени» в карточку
const src = await page.locator(`.react-flow__node[data-id="${a.id}"] .h-exit`).boundingBox();
const dst = await page.locator(`.react-flow__node[data-id="${b.id}"] .h-in`).boundingBox();
await page.mouse.move(src.x + src.width / 2, src.y + src.height / 2);
await page.mouse.down();
await page.mouse.move(dst.x + 2, dst.y + 2, { steps: 12 });
await page.mouse.move(dst.x + dst.width / 2, dst.y + dst.height / 2, { steps: 3 });
await page.mouse.up();
await page.waitForTimeout(300);
g = await G();
check(g.edges.length === 1 && g.edges[0].kind === "done" && g.edges[0].to_step === b.id, "протянутая стрелка «по времени» появилась");

// 5. undo / redo с клавиатуры (фокус на холсте)
await page.locator(".react-flow__pane").click({ position: { x: 24, y: 150 } });
await page.keyboard.press("Control+z");
check((await G()).edges.length === 0, "Ctrl+Z убрал стрелку");
await page.keyboard.press("Control+Shift+z");
check((await G()).edges.length === 1, "Ctrl+Shift+Z вернул стрелку");

// 6. задержка стрелки из панели
await page.evaluate(() => { const s = window.__studio.getState(); s.select({ edge: s.funnel.edges[0].id, steps: [] }); });
await page.getByTestId("edge-delay").getByRole("button", { name: "3 мин" }).click();
check((await G()).edges[0].delay_seconds === 180, "задержка «3 мин» из конструктора");

// 7. Delete шага со входящей стрелкой → диалог надгробия
await page.evaluate((id) => window.__studio.getState().select({ steps: [id], edge: null }), b.id);
await page.locator(".react-flow__pane").click({ position: { x: 24, y: 150 } });
await page.evaluate((id) => window.__studio.getState().select({ steps: [id], edge: null }), b.id);
await page.keyboard.press("Delete");
check(await page.getByText("Насовсем удалить нельзя").isVisible(), "у шага со входящей стрелкой – диалог «куда перенаправить»");
await page.getByRole("button", { name: "Отмена" }).click();

// 8. Ctrl+C / Ctrl+V
await page.evaluate((id) => window.__studio.getState().select({ steps: [id], edge: null }), a.id);
await page.keyboard.press("Control+c");
await page.keyboard.press("Control+v");
g = await G();
check(g.steps.length === 3 && g.steps[2].key === "msg_01_copy" && g.steps[2].id !== a.id, "копия шага: новый id и key msg_01_copy");

// 9. удалить новую копию (входящих нет) – удаляется насовсем
await page.keyboard.press("Delete");
check((await G()).steps.length === 2, "Delete удалил копию без входящих стрелок");

// 10. MAX: скрыть текст
await page.evaluate((id) => window.__studio.getState().select({ steps: [id], edge: null }), a.id);
await page.getByTestId("panel-platform").getByRole("button", { name: "MAX" }).click();
await page.getByTestId("max-text-mode").getByRole("button", { name: "скрыть" }).click();
check((await G()).steps[0].content.max.text === "", "MAX: «скрыть» → пустой текст MAX");
await page.getByTestId("max-text-mode").getByRole("button", { name: "как в TG" }).click();
check((await G()).steps[0].content.max.text === null, "MAX: «как в TG» → null");

// 11. время: «в определённое время»; в воронке без эфира дни – от регистрации, последствие словами
await page.getByTestId("time-mode").getByRole("button", { name: "в определённое время" }).click();
g = await G();
check(g.steps[0].schedule.absolute_time_msk === "10:00", "конструктор времени: «в определённое время» 10:00");
await page.getByTestId("day-select").selectOption("1");
await page.waitForTimeout(150);
check((await page.getByTestId("time-phrase").innerText()).includes("на следующий день после регистрации в 10:00"),
  `без эфира: «${await page.getByTestId("time-phrase").innerText()}»`);
check((await page.getByTestId("skip-consequence").innerText()).includes("не получит"), "«если время прошло» – последствие словами");
await page.getByTestId("time-mode").getByRole("button", { name: "после предыдущего" }).click();

// 12. сохранение (Ctrl+S) и выгрузка
await page.locator(".react-flow__pane").click({ position: { x: 24, y: 150 } });
await page.keyboard.press("Control+s");
await page.waitForTimeout(800);
const saved = await api("GET", `/api/funnels/${full}`);
check(saved.steps?.length === 2 && saved.edges?.length === 1, "Ctrl+S сохранил граф на сервер");
await page.getByTestId("more").click();
await page.getByTestId("export").click();
await page.waitForSelector(".modal .ok-box, .modal .err-box", { timeout: 20000 });
const status = await page.locator(".modal .ok-box, .modal .err-box").first().innerText();
check(status.includes("Движок читает"), `выгрузка: ${status.split("\n")[0]}`);

await page.keyboard.press("Escape");
await page.waitForTimeout(200);
check(!(await page.locator(".modal").count()), "Esc закрыл окно выгрузки");

// 13. Alt при протягивании – параллельная стрелка (пунктир)
await page.getByTestId("palette").getByText("Кружок", { exact: true }).click();
await page.waitForTimeout(300);
g = await G();
const c = g.steps[2];
await page.evaluate(([id, ref]) => {
  const s = window.__studio.getState();
  s.apply((f) => ({ ...f, layout: { positions: { ...f.layout.positions, [id]: { x: f.layout.positions[ref].x + 700, y: f.layout.positions[ref].y + 500 } } } }));
  s.select({ steps: [], edge: null, note: null });
  window.__rf.fitView({ padding: 0.2 });
}, [c.id, a.id]);
await page.waitForTimeout(400);
const s2 = await page.locator(`.react-flow__node[data-id="${a.id}"] .h-exit`).boundingBox();
const d2 = await page.locator(`.react-flow__node[data-id="${c.id}"] .h-in`).boundingBox();
await page.keyboard.down("Alt");
await page.mouse.move(s2.x + s2.width / 2, s2.y + s2.height / 2);
await page.mouse.down();
await page.mouse.move(d2.x + 2, d2.y + 2, { steps: 12 });
await page.mouse.move(d2.x + d2.width / 2, d2.y + d2.height / 2, { steps: 3 });
await page.mouse.up();
await page.keyboard.up("Alt");
await page.waitForTimeout(300);
g = await G();
check(g.edges.some((e) => e.kind === "also_fire" && e.from_step === a.id && e.to_step === c.id), "Alt + протянуть – параллельная стрелка (also_fire)");

// ── этап 2C ──
await page.keyboard.press("Escape");
await page.evaluate((id) => { const s = window.__studio.getState(); s.set({ platform: "tg" }); s.select({ steps: [id], edge: null, note: null }); }, a.id);
await page.waitForTimeout(300);

// 14. простой режим прячет key и HTML, плейсхолдеры – словами; режим разработчика – всё как было
const card = page.locator(`.react-flow__node[data-id="${a.id}"]`);
check(!(await page.getByTestId("panel-key").count()) && !(await page.getByRole("button", { name: "HTML", exact: true }).count()),
  "простой режим: в панели нет key и кнопки HTML");
check(!(await card.innerText()).includes("msg_01") && (await card.innerText()).includes("имя человека"),
  "простой режим: на карточке нет key, {Имя} – «имя человека»");
check(!(await page.getByTestId("sec-advanced").count()), "простой режим: раздела «Расширенные» нет");
await page.getByTestId("mode-switch").getByRole("button", { name: "Разработчик" }).click();
await page.waitForTimeout(300);
check(await page.getByTestId("panel-key").isVisible() && await page.getByRole("button", { name: "HTML", exact: true }).isVisible()
  && (await card.innerText()).includes("msg_01") && await page.getByTestId("sec-advanced").isVisible(),
  "режим разработчика: key, HTML и «Расширенные» на месте");
check(await page.evaluate(() => localStorage.getItem("studio.mode")) === "dev", "режим запомнился в браузере");
await page.getByTestId("mode-switch").getByRole("button", { name: "Постановщик" }).click();
await page.waitForTimeout(300);

// 15. превью «как в телефоне»: примерное имя, кнопки, MAX
await page.getByTestId("phone-button").click();
const phone = page.getByTestId("phone-preview");
check(await phone.isVisible() && (await phone.locator(".phone-chat").innerText()).includes("Привет, Анна!"),
  "превью: окно открылось, {Имя} подставлено примером «Анна»");
await phone.getByTestId("preview-platform").getByRole("button", { name: "MAX" }).click();
check((await phone.getByTestId("preview-max-state").innerText()).includes("как в TG") && await phone.locator(".phone-max").isVisible(),
  "превью MAX: экран MAX и «как в TG»");
await phone.locator(".modal-head .x").click();

// 16. поиск Ctrl+F находит шаг по тексту и ведёт к нему
await page.locator(".react-flow__pane").click({ position: { x: 24, y: 150 } });
await page.keyboard.press("Control+f");
await page.getByTestId("search-input").fill("ждём");
await page.waitForTimeout(200);
const hits = page.getByTestId("search-hits").locator(".search-hit");
check(await hits.count() >= 1 && (await hits.first().innerText()).toLowerCase().includes("ждём"), "поиск: нашёл шаг по слову из текста");
await page.getByTestId("search-input").press("Enter");
await page.waitForTimeout(400);
check((await page.evaluate(() => window.__studio.getState().selection.steps))[0] === a.id, "поиск: Enter выделил найденный шаг");
await page.getByTestId("search-input").press("Escape");

// 17. «Исправить» в проверке: неизвестная вставка → выбрать из списка
await page.evaluate((id) => window.__studio.getState().apply((f) => ({ ...f, steps: f.steps.map((s) => (s.id === id ? { ...s, content: { ...s.content, tg: { ...s.content.tg, text: "Держи {подарок_дня}" } } } : s)) })), b.id);
await page.getByTestId("check-button").click();
const fixRow = page.getByTestId("issue").filter({ hasText: "{подарок_дня}" });
await fixRow.getByTestId("fix-choice").selectOption("{gift_url}");
await fixRow.getByTestId("fix-button").click();
g = await G();
check(g.steps.find((s) => s.id === b.id).content.tg.text === "Держи {gift_url}", "проверка: «Исправить» подставил выбранное вместо неизвестной вставки");
await page.getByTestId("check-button").click();

// 18. ветки по времени регистрации: включить, найти дыру в сутках, закрыть второй веткой
await page.evaluate(() => window.__studio.getState().select({ steps: [], edge: null, note: null }));
await page.waitForTimeout(200);
await page.getByTestId("sec-branches").locator("summary").click();
await page.getByTestId("enable-branches").click();
await page.getByTestId("branch-time").fill("13:00");
await page.getByTestId("win-to").fill("11:59");
await page.waitForTimeout(200);
check((await page.getByTestId("branch-problem").allInnerTexts()).join().includes("с 12:00 по 23:59 не попадает ни в одну ветку"),
  "ветки: дыра в сутках видна словами");
await page.getByTestId("add-branch").click();
await page.getByTestId("branch-card").nth(1).getByTestId("add-window").click();
await page.getByTestId("branch-card").nth(1).getByTestId("branch-time").fill("18:00");
await page.waitForTimeout(200);
g = await G();
check(g.branch_router?.branches.length === 2 && g.branch_router.branches[0].webinar_time === "13:00"
  && g.branch_router.branches[1].windows[0].from === "12:00" && !(await page.getByTestId("branch-problem").count()),
  "ветки: вторая ветка закрыла дыру, замечаний нет");
check((await page.getByTestId("branch-card").first().innerText()).includes("дневная ветка · эфир 13:00"), "ветки: называются по времени эфира");


// ── этап 4 ──
// 19. «＋ Следующее сообщение» под выбранной карточкой: новая карточка сразу связана, через 1 мин
await page.evaluate(() => window.__studio.getState().select({ steps: [], edge: null, note: null }));
let g4 = await G();
const lastId = g4.steps.find((s) => s.type === "video_note")?.id ?? g4.steps[g4.steps.length - 1].id;
await page.evaluate((id) => { const s = window.__studio.getState(); s.select({ steps: [id], edge: null, note: null }); s.focusStep(id); }, lastId);
await page.waitForTimeout(500);
const nextBtn = page.locator(`.react-flow__node[data-id="${lastId}"] [data-testid="add-next"]`);
check(await nextBtn.isVisible(), "у выбранной карточки – кнопка «Следующее сообщение»");
const stepsBefore = g4.steps.length;
await nextBtn.click();
await page.waitForTimeout(300);
g4 = await G();
const added = g4.steps[g4.steps.length - 1];
const link = g4.edges.find((e) => e.from_step === lastId && e.to_step === added.id);
check(g4.steps.length === stepsBefore + 1 && link?.kind === "done" && link.delay_seconds === 60,
  `«Следующее сообщение»: новая карточка связана «через 1 мин» (${link?.kind}, ${link?.delay_seconds})`);
check((await page.evaluate(() => window.__studio.getState().selection.steps))[0] === added.id, "новая карточка выделена");
await page.locator(".react-flow__pane").click({ position: { x: 24, y: 150 } });
await page.keyboard.press("Control+z");
check((await G()).steps.length === stepsBefore, "Ctrl+Z убрал и карточку, и стрелку разом");

// 20. стороны стрелок: цель правее – выход справа, вход слева; цель ниже – снизу и сверху
const sides = await page.evaluate(() => {
  const rf = window.__rf;
  return rf.getEdges().filter((e) => !e.id.startsWith("start-")).map((e) => {
    const a = rf.getNode(e.source);
    const b = rf.getNode(e.target);
    return { src: e.sourceHandle, dst: e.targetHandle, dx: b.position.x - (a.position.x + (a.measured?.width ?? 320)), dy: b.position.y - (a.position.y + (a.measured?.height ?? 260)) };
  });
});
const bad = sides.filter((x) => (x.dx >= 40 && x.dx >= x.dy && x.src === "done" && x.dst !== "in") || (x.dy >= 0 && x.dx < 40 && x.dst === "in" && x.src === "done-r"));
check(sides.length > 0 && !bad.length, `стрелки цепляются к сторонам по положению карточек: ${JSON.stringify(sides)}`);

// 21. кнопка: цель пустая «куда?», своя ссылка – поле во всю ширину, пустая ссылка – ошибка проверки
await page.evaluate((id) => window.__studio.getState().select({ steps: [id], edge: null, note: null }), a.id);
await page.waitForTimeout(200);
await page.getByTestId("buttons-editor").getByRole("button", { name: "Кнопка", exact: true }).click();
g4 = await G();
const nb = g4.steps.find((s) => s.id === a.id).content.tg.buttons.at(-1);
check(nb.url === null && nb.callback_data === null, "новая кнопка ни на что не ведёт, пока не выбрали");
await page.getByTestId("button-target").last().selectOption("url");
const urlBox = await page.getByTestId("button-url").last().boundingBox();
check(urlBox && urlBox.width > 200, `поле своей ссылки широкое: ${Math.round(urlBox?.width ?? 0)} px`);
const errs = await page.evaluate(() => window.__studio.getState().funnel && 0);
void errs;
await page.getByTestId("check-button").click();
check((await page.getByTestId("validation-panel").innerText()).includes("не вписана ссылка"), "пустая ссылка кнопки – ошибка проверки");
await page.getByTestId("check-button").click();

// 22. шпаргалка клавиш по «?» и закрытие Esc
await page.locator(".react-flow__pane").click({ position: { x: 24, y: 150 } });
await page.keyboard.press("Shift+Slash");
check(await page.getByTestId("shortcuts").isVisible(), "«?» открыл шпаргалку клавиш");
await page.keyboard.press("Escape");
check(!(await page.getByTestId("shortcuts").count()), "Esc закрыл шпаргалку");

check(errors.length === 0, `ошибок страницы нет${errors.length ? ": " + errors.join("; ") : ""}`);
await browser.close();
console.log(failed ? `\nПровалено: ${failed}` : "\nВсё прошло");
process.exit(failed ? 1 : 0);
