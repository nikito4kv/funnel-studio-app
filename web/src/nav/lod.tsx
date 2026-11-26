// Уровни масштаба. На карточке всегда лежит упрощённый слой (.card-lod), но показывается он
// только если включено «упрощать карточки при отдалении» (по умолчанию выключено – карточка
// целиком, как в Miro), и только издалека и средне – это решает класс на обёртке холста
// (data-zoom="near|mid|far|vfar", для карточек – data-lod), без пересборки узлов. Исходное содержимое карточки при этом
// прячется через visibility – размер карточки не меняется, стрелки остаются на месте,
// React Flow ничего не пересчитывает.
//
//   близко  (≥ 0.55) – вся карточка;
//   средне  (0.35–0.55) – тип, первая строка текста, подписи кнопок, фишка времени, значки;
//   издалека (< 0.35) – тип и название крупно, фишка времени, значки условий;
//   совсем издалека (< 0.14) – значок типа и фишка; главное – названия полос.
import { useStore } from "@xyflow/react";
import { useEffect } from "react";
import { Play } from "lucide-react";
import type { Button } from "../model/types";
import { Clock, CondIcon, StepIcon } from "../design/icons";
import { conditionBadges } from "../phrases";
import { useUi } from "../simple/mode";
import { stepName } from "../simple/names";
import type { StepNodeData } from "../canvas/nodes";

export type ZoomLevel = "near" | "mid" | "far" | "vfar";

export function zoomLevel(z: number): ZoomLevel {
  if (z >= 0.55) return "near";
  if (z >= 0.35) return "mid";
  if (z >= 0.14) return "far";
  return "vfar";
}

/** Ставит data-zoom на .canvas при переходе порога (не на каждом кадре масштаба) и
 *  data-lod (= уровень или "off"), если постановщик включил упрощение карточек при отдалении.
 *  По умолчанию карточки на любом масштабе целиком – как на доске Miro. */
export function ZoomClass() {
  const level = useStore((s) => zoomLevel(s.transform[2]));
  const simplify = useUi((s) => s.simplifyFar);
  useEffect(() => {
    const el = document.querySelector(".canvas");
    if (!el) return;
    el.setAttribute("data-zoom", level);
    // упрощение карточек – отдельным атрибутом: смена data-zoom тогда пересчитывает стили только
    // подписей стрелок и полос, а не содержимого всех карточек (это давало рывки при масштабе)
    el.setAttribute("data-lod", simplify ? level : "off");
  }, [level, simplify]);
  return null;
}


export function CardLod({ data, buttons, circle }: { data: StepNodeData; buttons: Button[]; circle?: boolean }) {
  const s = data.step;
  const name = data.simple || s.title ? stepName(s, 70) : s.key;
  const badges = conditionBadges(s.conditions);
  return (
    <div className={circle ? "card-lod lod-circle" : "card-lod"} aria-hidden>
      <div className="lod-head">
        <StepIcon type={s.type} survey={!!s.survey.qid} size={28} strokeWidth={1.5} />
        {data.entry && <span className="lod-entry"><Play size={14} strokeWidth={2.5} fill="currentColor" />вход</span>}
      </div>
      <div className="lod-name">{name}</div>
      {!!buttons.length && (
        <div className="lod-btns">
          {buttons.slice(0, 4).map((b, i) => <span className="lod-btn" key={i}>{b.text || "…"}</span>)}
          {buttons.length > 4 && <span className="lod-more">ещё {buttons.length - 4}</span>}
        </div>
      )}
      <div className="lod-meta">
        {data.chip && (
          <span className="lod-chip">
            <Clock size={16} strokeWidth={2} /><span className="lod-chip-full">{data.chip}</span><span className="lod-chip-short">{data.chip.split(" · ")[0]}</span>
          </span>
        )}
        {badges.map((b, i) => <span className="lod-badge" key={i} title={b.text}><CondIcon emoji={b.icon} size={16} /><span className="lod-badge-text"> {b.text}</span></span>)}
      </div>
    </div>
  );
}
