// Стрелка холста: линия – SVG, подпись («через 1 мин», «дал номер») – обычная HTML-плашка
// поверх линии (EdgeLabelRenderer React Flow).
//
// Почему не SVG-текст: браузер заново раскладывает SVG-текст при каждом изменении масштаба
// (размер букв зависит от экранного масштаба), а из-за этого перерисовывал весь холст на каждом
// кадре плавного масштаба колесом – 40+ рывков на пятидневке. HTML-подпись масштабируется вместе
// со слоем холста и не перерисовывается.
import { BaseEdge, EdgeLabelRenderer, getBezierPath, type EdgeProps } from "@xyflow/react";
import { memo } from "react";
import { useStudio } from "../store";

export const StudioEdge = memo(function StudioEdge(p: EdgeProps) {
  const [path, lx, ly] = getBezierPath({
    sourceX: p.sourceX, sourceY: p.sourceY, sourcePosition: p.sourcePosition,
    targetX: p.targetX, targetY: p.targetY, targetPosition: p.targetPosition,
  });
  const label = typeof p.label === "string" ? p.label : null;
  const selectable = p.selectable !== false;
  return (
    <>
      <BaseEdge id={p.id} path={path} style={p.style} markerEnd={p.markerEnd} interactionWidth={p.interactionWidth} />
      {label && (
        <EdgeLabelRenderer>
          <div className={"edge-label nodrag nopan" + (p.selected ? " on" : "") + (selectable ? "" : " quiet")}
            style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)` }}
            data-tip={(p.data as { tip?: string | null } | undefined)?.tip ?? undefined}
            onClick={selectable ? () => useStudio.getState().select({ edge: p.id, steps: [], note: null }) : undefined}>
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
});

export const edgeTypes = { studio: StudioEdge };
