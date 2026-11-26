// Вернуть шаг, исчезнувший из опубликованной версии, «надгробием»: тот же id и key, тип
// «переход», одна стрелка на выбранный шаг. У людей, которым шаг уже запланирован, он не
// потеряется – движок пропустит его и отправит дальше (исследование §6.1–6.2).
import { newStep } from "../model/defaults";
import { OpError, STEP_TYPE_LABEL } from "../model/ops";
import type { Funnel, Position, StepType } from "../model/types";

export interface RestoreArgs {
  step: { id: string; key: string; type: string; title: string; position: Position | null };
  redirectTo: string;
  edgeId: string;
}

export function restoreAsTombstone(g: Funnel, a: RestoreArgs): Funnel {
  if (g.steps.some((s) => s.id === a.step.id)) throw new OpError(`шаг «${a.step.key}» уже есть в черновике`);
  if (!g.steps.some((s) => s.id === a.redirectTo)) throw new OpError("выберите, куда перенаправить людей");
  const label = STEP_TYPE_LABEL[a.step.type as StepType] ?? a.step.type;
  const tomb = { ...newStep(a.step.id, a.step.key, "jump"), title: a.step.title || `удалён: был ${label}` };
  const positions = { ...g.layout.positions };
  if (a.step.position) positions[a.step.id] = a.step.position;
  return {
    ...g,
    steps: [...g.steps, tomb],
    edges: [...g.edges, {
      id: a.edgeId, from_step: a.step.id, kind: "jump", to_step: a.redirectTo, to_funnel: null,
      delay_seconds: null, typing_before: false,
    }],
    layout: { ...g.layout, positions },
  };
}
