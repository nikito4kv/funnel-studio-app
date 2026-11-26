// «Воссоздать воронку с нуля»: по готовому графу строится последовательность операций
// редактора (те же, что вызывает интерфейс), которая из пустого графа собирает ту же воронку.
// Это главный тест полноты операций: если какое-то поле нельзя задать операцией,
// воссозданный граф разойдётся с исходным.
import {
  defaultCall, defaultClaim, defaultConditions, defaultContact, defaultEmail, defaultMedia,
  defaultPin, defaultSchedule, defaultSms, defaultSurvey, emptyFunnel,
} from "./defaults";
import * as ops from "./ops";
import type { Funnel, Uuid } from "./types";

export interface OpCall {
  name: string;
  run: (g: Funnel) => Funnel;
}

function diff<T extends object>(base: T, cur: T): Partial<T> {
  const out: Partial<T> = {};
  for (const k of Object.keys(cur) as (keyof T)[]) {
    if (JSON.stringify(cur[k]) !== JSON.stringify(base[k])) out[k] = cur[k];
  }
  return out;
}

const nonEmpty = (o: object) => Object.keys(o).length > 0;

export function recreateScript(src: Funnel, newId: () => Uuid): OpCall[] {
  const script: OpCall[] = [];
  const push = (name: string, run: (g: Funnel) => Funnel) => script.push({ name, run });
  const idMap = new Map<Uuid, Uuid>();
  const map = (id: Uuid): Uuid => {
    const v = idMap.get(id);
    if (!v) throw new Error(`нет шага ${id}`);
    return v;
  };
  if (src.unresolved.length) {
    throw new Error("в воронке есть ссылки на несуществующие шаги – операциями такое не собрать");
  }

  const meta: ops.FunnelMetaPatch = {};
  if (src.title) meta.title = src.title;
  if (src.air_email_profile) meta.air_email_profile = src.air_email_profile;
  if (!src.max_video_note_caption) meta.max_video_note_caption = false;
  if (src.resend_entry_on_repeat) meta.resend_entry_on_repeat = true;
  if (src.webinar_room) meta.webinar_room = src.webinar_room;
  if (nonEmpty(meta)) push("setFunnelMeta", (g) => ops.setFunnelMeta(g, meta));

  for (const s of src.steps) {
    const id = newId();
    idMap.set(s.id, id);
    push("addStep", (g) => ops.addStep(g, { id, type: s.type, key: s.key }));

    const misc: ops.MiscPatch = {};
    if (s.title) misc.title = s.title;
    if (s.role !== null) misc.role = s.role;
    if (s.role_day !== null) misc.role_day = s.role_day;
    if (s.diploma_template) misc.diploma_template = s.diploma_template;
    if (nonEmpty(s.advanced)) misc.advanced = s.advanced;
    if (nonEmpty(misc)) push("setMisc", (g) => ops.setMisc(g, { stepId: id, patch: misc }));

    const { tg, max, variants } = s.content;
    if (tg.text) push("setText", (g) => ops.setText(g, { stepId: id, platform: "tg", value: tg.text }));
    if (max.text !== null) {
      push("setText", (g) => ops.setText(g, {
        stepId: id, platform: "max", mode: max.text === "" ? "hide" : "set", value: max.text!,
      }));
    }
    if (tg.buttons.length) push("setButtons", (g) => ops.setButtons(g, { stepId: id, platform: "tg", buttons: tg.buttons }));
    if (max.buttons !== null) {
      push("setButtons", (g) => ops.setButtons(g, {
        stepId: id, platform: "max", mode: max.buttons!.length ? "set" : "hide", buttons: max.buttons!,
      }));
    }
    if (tg.claim_button_text !== null) {
      push("setClaim", (g) => ops.setClaim(g, { stepId: id, platform: "tg", value: tg.claim_button_text! }));
    }
    if (max.claim_button_text !== null) {
      push("setClaim", (g) => ops.setClaim(g, {
        stepId: id, platform: "max", mode: max.claim_button_text === "" ? "hide" : "set", value: max.claim_button_text!,
      }));
    }
    if (variants.length) push("setVariants", (g) => ops.setVariants(g, { stepId: id, variants }));

    const claim = diff(defaultClaim(), s.claim);
    if (nonEmpty(claim)) push("setClaimSettings", (g) => ops.setClaimSettings(g, { stepId: id, patch: claim }));
    const media = diff(defaultMedia(), s.media);
    if (nonEmpty(media)) push("setMedia", (g) => ops.setMedia(g, { stepId: id, patch: media }));
    const sched = diff(defaultSchedule(), s.schedule);
    if (nonEmpty(sched)) push("setSchedule", (g) => ops.setSchedule(g, { stepId: id, patch: sched }));
    const cond = diff(defaultConditions(), s.conditions);
    if (nonEmpty(cond)) push("setConditions", (g) => ops.setConditions(g, { stepId: id, patch: cond }));
    const contact = diff(defaultContact(), s.contact);
    if (nonEmpty(contact)) push("setContact", (g) => ops.setContact(g, { stepId: id, patch: contact }));
    const email = diff(defaultEmail(), s.email);
    if (nonEmpty(email)) push("setEmail", (g) => ops.setEmail(g, { stepId: id, patch: email }));
    const survey = diff(defaultSurvey(), s.survey);
    if (nonEmpty(survey)) push("setSurvey", (g) => ops.setSurvey(g, { stepId: id, patch: survey }));
    const call = diff(defaultCall(), s.call);
    if (nonEmpty(call)) push("setCall", (g) => ops.setCall(g, { stepId: id, patch: call }));
    const sms = diff(defaultSms(), s.sms);
    if (nonEmpty(sms)) push("setSms", (g) => ops.setSms(g, { stepId: id, patch: sms }));
    const pin = diff(defaultPin(), s.pin);
    if (nonEmpty(pin)) push("setPin", (g) => ops.setPin(g, { stepId: id, patch: pin }));
  }

  for (const e of src.edges) {
    const id = newId();
    push("addEdge", (g) => ops.addEdge(g, {
      id, from: map(e.from_step), kind: e.kind, to: e.to_step ? map(e.to_step) : null,
      toFunnel: e.to_funnel, delay: e.delay_seconds, typing: e.typing_before,
    }));
  }

  if (src.branch_router) {
    const router = {
      ...src.branch_router,
      branches: src.branch_router.branches.map((b) => ({ ...b, entry_step: map(b.entry_step) })),
    };
    push("setBranchRouter", (g) => ops.setBranchRouter(g, router));
  }
  if (src.entry_variants.length) {
    const ev = src.entry_variants.map((v) => ({ ...v, entry_step: map(v.entry_step) }));
    push("setEntryVariants", (g) => ops.setEntryVariants(g, ev));
  }

  const laneMap = new Map<Uuid, Uuid>();
  for (const l of [...src.lanes].sort((a, b) => a.order - b.order)) {
    const id = newId();
    laneMap.set(l.id, id);
    push("addLane", (g) => ops.addLane(g, { id, title: l.title, kind: l.kind, day: l.day }));
    if (l.step_ids.length) {
      push("assignLane", (g) => ops.assignLane(g, { stepIds: l.step_ids.map(map), laneId: id }));
    }
  }
  for (const n of src.notes) {
    const id = newId();
    const anchor = {
      id, text: n.text, color: n.color, author: n.author, createdAt: n.created_at,
      stepId: n.step_id ? map(n.step_id) : null, laneId: n.lane_id ? laneMap.get(n.lane_id)! : null,
      position: n.position,
    };
    if (n.kind === "revision") {
      push("addRevision", (g) => ops.addRevision(g, { ...anchor, action: n.action ?? "other" }));
      // статус меняется только по разрешённым переходам – как в интерфейсе
      const path: Record<string, ("done" | "accepted" | "rejected")[]> = {
        open: [], done: ["done"], accepted: ["done", "accepted"], rejected: ["rejected"],
      };
      for (const st of path[n.status ?? "open"]) {
        push("setRevisionStatus", (g) => ops.setRevisionStatus(g, { noteId: id, status: st }));
      }
    } else {
      push("addNote", (g) => ops.addNote(g, anchor));
    }
  }
  return script;
}

export function runScript(start: Funnel, script: OpCall[]): Funnel {
  let g = start;
  for (const op of script) g = op.run(g);
  return g;
}

/** Пустая воронка + операции → воронка «как исходная». */
export function recreate(src: Funnel, newId: () => Uuid): { graph: Funnel; ops: number } {
  const script = recreateScript(src, newId);
  const start = emptyFunnel(src.id, src.source?.launch ?? null);
  return { graph: runScript(start, script), ops: script.length };
}
