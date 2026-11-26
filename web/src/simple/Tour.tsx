// Короткое знакомство при первом открытии: 5 подсказок с подсветкой нужного места экрана.
// Закрыть можно в любой момент; вызвать снова – «Ещё → Показать знакомство со Студией».
// «Больше не показывать» запоминается в браузере (simple/mode.ts).
import { ArrowLeft, ArrowRight, X } from "lucide-react";
import { useEffect, useLayoutEffect, useState } from "react";
import { useUi } from "./mode";

interface TourStep {
  target: string;
  title: string;
  text: string;
  side: "right" | "left" | "bottom";
}

const STEPS: TourStep[] = [
  { target: ".canvas", side: "bottom", title: "Холст", text: "Каждая карточка – одно сообщение бота, стрелки – их порядок. Колесо мыши – масштаб, пробел и мышь – двигать холст." },
  { target: '[data-testid="palette"]', side: "right", title: "Добавить шаг", text: "Кликните тип слева – карточка появится на холсте. Следующее сообщение удобнее добавлять кнопкой «Следующее сообщение» под выбранной карточкой." },
  { target: ".right", side: "left", title: "Панель шага", text: "Кликните карточку – справа её картинка, текст, кнопки и время отправки, в том же порядке, что увидит человек. «Как в телефоне» покажет сообщение целиком." },
  { target: '[data-testid="save"]', side: "bottom", title: "Черновик сохраняется сам", text: "Правки сохраняются через пару секунд. Черновик видите только вы и команда – в бот он не уходит." },
  { target: '[data-testid="publish-open"]', side: "bottom", title: "Запустить в боте", text: "Когда всё готово – здесь. Студия проверит воронку, скажет, что будет с людьми внутри, и запустит версию в боте." },
];

export function Tour() {
  const open = useUi((s) => s.tour);
  const close = useUi((s) => s.closeTour);
  const [i, setI] = useState(0);
  const [never, setNever] = useState(true);
  const [box, setBox] = useState<DOMRect | null>(null);
  const step = STEPS[i];
  useLayoutEffect(() => {
    if (!open) return;
    const el = document.querySelector(step.target);
    setBox(el ? el.getBoundingClientRect() : null);
  }, [open, i, step.target]);
  useEffect(() => { if (open) setI(0); }, [open]);
  useEffect(() => {
    if (!open) return;
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") close(never);
      if (e.key === "ArrowRight") setI((x) => Math.min(STEPS.length - 1, x + 1));
      if (e.key === "ArrowLeft") setI((x) => Math.max(0, x - 1));
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [open, never, close]);
  if (!open) return null;
  const last = i === STEPS.length - 1;
  let style: React.CSSProperties = { left: "50%", top: "40%", transform: "translate(-50%, -50%)" };
  if (box) {
    const w = 340;
    if (step.side === "right") style = { left: box.right + 16, top: Math.min(box.top + 40, window.innerHeight - 260) };
    else if (step.side === "left") style = { left: Math.max(16, box.left - w - 16), top: box.top + 60 };
    else style = { left: Math.min(window.innerWidth - w - 16, Math.max(16, box.left + box.width / 2 - w / 2)), top: box.top + Math.min(box.height, 60) + 14 };
  }
  return (
    <div className="tour-back" data-testid="tour" role="dialog" aria-label="Знакомство со Студией">
      {box && <div className="tour-hole" style={{ left: box.left - 4, top: box.top - 4, width: box.width + 8, height: box.height + 8 }} />}
      <div className="tour-card" style={style}>
        <div className="row">
          <span className="tour-step">{i + 1} из {STEPS.length}</span>
          <span className="grow" />
          <button type="button" className="x" aria-label="Закрыть" onClick={() => close(never)}><X size={15} strokeWidth={2} /></button>
        </div>
        <div className="tour-title">{step.title}</div>
        <div className="tour-text">{step.text}</div>
        <div className="row tour-foot">
          <div className="tour-dots">{STEPS.map((_, j) => <span key={j} className={j === i ? "on" : ""} />)}</div>
          <span className="grow" />
          {i > 0 && <button type="button" className="ghost icon" aria-label="Назад" onClick={() => setI(i - 1)}><ArrowLeft size={15} strokeWidth={2} /></button>}
          <button type="button" className="primary" onClick={() => (last ? close(never) : setI(i + 1))}>
            {last ? "Понятно" : <>Дальше<ArrowRight size={15} strokeWidth={2} /></>}
          </button>
        </div>
        <label className="check faint small"><input type="checkbox" checked={never} onChange={(e) => setNever(e.target.checked)} /> больше не показывать при открытии</label>
      </div>
    </div>
  );
}
