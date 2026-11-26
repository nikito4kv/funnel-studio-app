// Единые всплывающие подсказки. Любому элементу достаточно атрибута data-tip="текст"
// (горячая клавиша – data-kbd="Ctrl+S", сторона – data-tip-side="top|right", по умолчанию снизу).
// Первая подсказка – с задержкой, соседние – сразу, пока человек ведёт мышью по панели.
// Нативный title остаётся там, где он уже был: это запасной путь и опора для автотестов.
import { useEffect, useLayoutEffect, useRef, useState } from "react";

interface Tip {
  text: string;
  kbd: string | null;
  box: DOMRect;
  side: "top" | "right" | "bottom";
}

const DELAY = 450;
const WARM = 500;

export function TooltipLayer() {
  const [tip, setTip] = useState<Tip | null>(null);
  const [instant, setInstant] = useState(false);
  const el = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let timer = 0;
    let lastHide = 0;
    let cur: HTMLElement | null = null;
    let shown = false;
    const show = (t: HTMLElement) => {
      const text = t.dataset.tip;
      if (!text || !t.isConnected) return;
      const box = t.getBoundingClientRect();
      const want = (t.dataset.tipSide as Tip["side"] | undefined) ?? "bottom";
      const side = want === "top" && box.top < 60 ? "bottom" : want;
      shown = true;
      setTip({ text, kbd: t.dataset.kbd ?? null, box, side });
    };
    const hide = () => {
      window.clearTimeout(timer);
      if (shown) lastHide = performance.now();
      shown = false;
      cur = null;
      setTip(null);
    };
    const over = (e: PointerEvent) => {
      const t = (e.target as HTMLElement | null)?.closest?.("[data-tip]") as HTMLElement | null;
      if (t === cur) return;
      window.clearTimeout(timer);
      if (!t) { hide(); return; }
      const warm = shown || performance.now() - lastHide < WARM;
      cur = t;
      setInstant(warm);
      if (warm) show(t);
      else timer = window.setTimeout(() => show(t), DELAY);
    };
    window.addEventListener("pointerover", over, true);
    window.addEventListener("pointerdown", hide, true);
    window.addEventListener("wheel", hide, { capture: true, passive: true });
    window.addEventListener("keydown", hide, true);
    window.addEventListener("blur", hide);
    return () => {
      window.removeEventListener("pointerover", over, true);
      window.removeEventListener("pointerdown", hide, true);
      window.removeEventListener("wheel", hide, true);
      window.removeEventListener("keydown", hide, true);
      window.removeEventListener("blur", hide);
      window.clearTimeout(timer);
    };
  }, []);

  // место – после отрисовки, когда известна ширина подсказки; не за краем экрана
  useLayoutEffect(() => {
    const d = el.current;
    if (!d || !tip) return;
    const w = d.offsetWidth;
    const h = d.offsetHeight;
    const b = tip.box;
    let x: number;
    let y: number;
    if (tip.side === "right") { x = b.right + 8; y = b.top + b.height / 2 - h / 2; }
    else if (tip.side === "top") { x = b.left + b.width / 2 - w / 2; y = b.top - h - 8; }
    else { x = b.left + b.width / 2 - w / 2; y = b.bottom + 8; }
    d.style.left = `${Math.max(8, Math.min(window.innerWidth - w - 8, x))}px`;
    d.style.top = `${Math.max(8, Math.min(window.innerHeight - h - 8, y))}px`;
    d.classList.add("on");
  }, [tip]);

  if (!tip) return null;
  return (
    <div ref={el} className={"tip" + (instant ? " instant" : "")} role="tooltip">
      {tip.text}
      {tip.kbd && <kbd>{tip.kbd}</kbd>}
    </div>
  );
}
