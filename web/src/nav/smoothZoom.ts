// Плавный масштаб колесом мыши и щипком трекпада.
//
// Стандартный масштаб React Flow (d3-zoom) прыгает на 18 % за щелчок колеса и пересчитывает холст
// прямо в обработчике колеса. Здесь колесо только задаёт ЦЕЛЕВОЙ масштаб, а сам масштаб догоняет
// его по кадрам (requestAnimationFrame) – получается плавно, и частые события колеса сливаются в
// одно движение. Точка под курсором остаётся на месте, как в Miro.
//
// Пока идёт масштаб, кадры двигают только слой холста (transform у .react-flow__viewport) – без
// React и без перерисовки фона и мини-карты: тогда браузер не пересобирает слои на каждом кадре
// (на пятидневке это и было главной ценой). React Flow узнаёт итоговый масштаб один раз, когда
// движение встало, или сразу, если человек в это время схватил холст мышью.
// Класс .is-zooming даёт слою will-change: текст карточек не перерисовывается на каждом кадре.
import type { ReactFlowInstance } from "@xyflow/react";

export interface SmoothZoomOptions {
  minZoom: number;
  maxZoom: number;
  /** Элементы внутри холста, где колесо должно прокручивать, а не масштабировать. */
  ignore?: string;
}

/** Во сколько раз меняется масштаб от одного события колеса – как в d3-zoom (щелчок ≈ ×1,18). */
export function wheelFactor(e: Pick<WheelEvent, "deltaY" | "deltaMode" | "ctrlKey">): number {
  const k = e.deltaMode === 1 ? 0.05 : e.deltaMode ? 1 : 0.002;
  return Math.pow(2, -e.deltaY * k * (e.ctrlKey ? 10 : 1));
}

/** Следующий масштаб на пути к цели: экспоненциальное сглаживание, не зависит от частоты кадров. */
export function approach(current: number, target: number, dtMs: number, tauMs = 70): number {
  const next = current + (target - current) * (1 - Math.exp(-dtMs / tauMs));
  return Math.abs(target - next) / target < 0.002 ? target : next;
}

export function attachSmoothZoom(el: HTMLElement, rf: ReactFlowInstance, o: SmoothZoomOptions): () => void {
  let target = 0;
  let ax = 0;
  let ay = 0;
  let raf = 0;
  let last = 0;
  let endTimer = 0;
  /** Текущий вид во время движения (React Flow узнает его в commit). */
  let cur: { x: number; y: number; zoom: number } | null = null;
  const layer = () => el.querySelector<HTMLElement>(".react-flow__viewport");

  const commit = () => {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    last = 0;
    if (cur) void rf.setViewport(cur);
    cur = null;
  };

  const frame = (t: number) => {
    const dt = last ? Math.min(t - last, 64) : 16;
    last = t;
    const vp = cur ?? rf.getViewport();
    const z = approach(vp.zoom, target, dt);
    // точка холста под курсором остаётся под курсором
    const fx = (ax - vp.x) / vp.zoom;
    const fy = (ay - vp.y) / vp.zoom;
    cur = { x: ax - fx * z, y: ay - fy * z, zoom: z };
    const l = layer();
    if (l) l.style.transform = `translate(${cur.x}px, ${cur.y}px) scale(${cur.zoom})`;
    if (z !== target) raf = requestAnimationFrame(frame);
    else { raf = 0; last = 0; }
  };

  // React Flow узнаёт вид, когда колесо затихло на четверть секунды, а не после каждого щелчка:
  // иначе он на каждом щелчке дорисовывал бы въехавшие в кадр карточки (рывок на каждый щелчок)
  let idle = 0;
  const commitLater = () => {
    window.clearTimeout(idle);
    idle = window.setTimeout(() => { if (raf) commitLater(); else commit(); }, 250);
  };

  const onWheel = (e: WheelEvent) => {
    const t = e.target as HTMLElement | null;
    if (o.ignore && t?.closest(o.ignore)) return;
    e.preventDefault();
    const r = el.getBoundingClientRect();
    const from = raf ? target : (cur ?? rf.getViewport()).zoom;
    target = Math.min(o.maxZoom, Math.max(o.minZoom, from * wheelFactor(e)));
    ax = e.clientX - r.left;
    ay = e.clientY - r.top;
    el.classList.add("is-zooming");
    window.clearTimeout(endTimer);
    endTimer = window.setTimeout(() => el.classList.remove("is-zooming"), 220);
    if (!raf) raf = requestAnimationFrame(frame);
    commitLater();
  };

  // схватили холст или карточку посреди движения – React Flow должен знать настоящий вид
  const onDown = () => { if (raf || cur) { window.clearTimeout(idle); commit(); } };
  el.addEventListener("wheel", onWheel, { passive: false });
  el.addEventListener("pointerdown", onDown, true);
  return () => {
    el.removeEventListener("wheel", onWheel);
    el.removeEventListener("pointerdown", onDown, true);
    commit();
    window.clearTimeout(endTimer);
    window.clearTimeout(idle);
  };
}
