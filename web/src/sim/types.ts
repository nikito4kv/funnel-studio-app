// Ответы API симулятора (studio/sim) – см. studio/server/simulate.py.

export interface Persona {
  attended_days: number[];
  vip_paid: boolean;
  vip_paid_after_min: number;
  course_paid: boolean;
  course_paid_after_min: number;
  channel_joined: boolean;
  channel_joined_after_min: number;
  link_clicked: boolean;
  link_clicked_after_min: number;
  manager_dm: boolean;
  manager_dm_after_min: number;
  survey_done: boolean;
  phone_known: boolean;
  email_known: boolean;
  has_lm: boolean;
  contact: "given" | "no";
  contact_after_s: number;
  claim: boolean;
  claim_after_s: number;
  survey_answer: boolean;
  survey_after_s: number;
  email: boolean;
  email_after_s: number;
}

/** «Типовой человек» – как умолчания в studio/sim/models.py. */
export const TYPICAL: Persona = {
  attended_days: [],
  vip_paid: false, vip_paid_after_min: 0,
  course_paid: false, course_paid_after_min: 0,
  channel_joined: false, channel_joined_after_min: 0,
  link_clicked: false, link_clicked_after_min: 0,
  manager_dm: false, manager_dm_after_min: 0,
  survey_done: false, phone_known: false, email_known: false, has_lm: true,
  contact: "given", contact_after_s: 15,
  claim: true, claim_after_s: 20,
  survey_answer: true, survey_after_s: 20,
  email: true, email_after_s: 40,
};

export type EventKind =
  | "start" | "sent" | "gated" | "past_skip" | "parallel_lost" | "replaced" | "dup" | "wait"
  | "human" | "timeout" | "end" | "chain" | "chain_ignored" | "stuck" | "error" | "jump"
  | "skip_known" | "tg_skip" | "skip_max" | "recovered" | "cancelled" | "claim_cancel"
  | "edit_end" | "unknown_step";

export interface SimEvent {
  t: string;
  at: string;
  time: string;
  day: number;
  kind: EventKind;
  key: string | null;
  step_id: string | null;
  type: string | null;
  type_label: string | null;
  title: string;
  by: string | null;
  text: string;
  late_from?: string;
  dnd_from?: string;
  no_dnd?: string;
  condition?: string;
  lost?: string[];
  lost_ids?: (string | null)[];
  /** потеря не случилась: шаг всё равно пришёл другим путём */
  resolved?: boolean;
}

export interface Dropped {
  key: string;
  step_id: string | null;
  reason: string;
  text: string;
  at: string;
}

export interface SimMark {
  state: "sent" | "dropped" | "gated";
  label: string;
  title: string;
  count: number;
}

export interface LaunchInfo {
  slug: string;
  found: boolean;
  notes: string[];
  dnd_enabled: boolean;
  dnd_used?: boolean;
}

export interface SimResult {
  ok: boolean;
  registered_at: string;
  platform: "tg" | "max";
  branch: { id: string | null; window?: string; day?: string; webinar?: string | null; webinar_label?: string; note?: string } | null;
  launch: LaunchInfo;
  events: SimEvent[];
  days: { day: number; date: string; title: string; airs: string[] }[];
  summary: {
    sent: number;
    sent_steps: number;
    late: number;
    dnd: number;
    replaced: number;
    dropped: Dropped[];
    dropped_by_reason: Record<string, number>;
    gated: { key: string; step_id: string | null; condition: string | null; text: string; at: string }[];
    stuck: string[];
    truncated: string | null;
  };
  marks: Record<string, SimMark>;
}

export interface MatrixRow {
  registered_at: string;
  label: string;
  branch: string | null;
  webinar: string;
  sent: number;
  late: number;
  dropped: Dropped[];
  gated: number;
  stuck: string[];
  truncated: string | null;
}

export interface MatrixResult {
  ok: boolean;
  platform: string;
  rows: MatrixRow[];
  steps: { key: string; step_id: string | null; moments: string[]; reasons: string[] }[];
  launch: LaunchInfo;
}

export interface TestRunStatus {
  ready: boolean;
  proxy: boolean;
  hint: string;
}
