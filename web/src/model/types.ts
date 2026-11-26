// Типы графа Студии – зеркало studio/model/funnel.py (схема: schema/funnel.schema.json).
// Значения по умолчанию – в defaults.ts, сверяются с pydantic тестом (fixtures/default_step.json).

export type Uuid = string;

export type StepType =
  | "text" | "photo" | "video_note" | "video" | "animation" | "rich"
  | "phone_request" | "email_request" | "jump" | "call" | "sms" | "air_pin";

export type EdgeKind =
  | "done" | "contact" | "skip" | "skip_if_phone_known" | "email" | "skip_if_email_known"
  | "callback" | "survey_next" | "jump" | "also_fire" | "chain_to_funnel";

export type Platform = "tg" | "max";

export interface Button {
  text: string;
  url: string | null;
  callback_data: string | null;
  style: string | null;
  web_app: boolean;
}

export interface CohortMatch {
  utm_source: string | null;
  utm_medium: string | null;
  utm_campaign: string | null;
  trigger: string | null;
  lm_post_id: string | null;
  lm_post_id_in: string[];
  platforms: ("tg" | "max" | "vk")[];
}

export interface StepVariant {
  when: CohortMatch;
  text: string | null;
  text_max: string | null;
  buttons: Button[] | null;
  claim_button_text: string | null;
}

export interface TgContent {
  text: string;
  buttons: Button[];
  claim_button_text: string | null;
}

/** MAX: null = «как в TG», "" / [] = «в MAX этого нет». */
export interface MaxContent {
  text: string | null;
  buttons: Button[] | null;
  claim_button_text: string | null;
}

export interface Content {
  tg: TgContent;
  max: MaxContent;
  variants: StepVariant[];
}

export interface Media {
  image: string | null;
  images: string[] | null;
  image_lm: boolean;
  video_note: string | null;
  video_fallback: string | null;
  video: string | null;
  animation: string | null;
  rich: string | null;
  media_optional: boolean;
}

export interface Schedule {
  after_start_seconds: number | null;
  absolute_time_msk: string | null;
  day_offset: number;
  day_offset_by_branch: Record<string, number> | null;
  webinar_offset_minutes: number | null;
  webinar_offset_by_branch: Record<string, number> | null;
  anchor: "webinar" | "repeat";
  skip_if_past: boolean;
  dnd_exempt: boolean;
  /** Не раньше чем через N с от постановки в очередь (min_delay_seconds движка); 0 – нет. */
  min_delay_seconds: number;
}

export interface Conditions {
  attendance: "was" | "was_not" | null;
  bizon_room: string | null;
  bizon_room_max: string;
  gate_window_hours: number;
  not_paid: boolean;
  paid: boolean;
  not_course_paid: boolean;
  course_paid: boolean;
  channel: "joined" | "not_joined" | null;
  survey_done: boolean;
  has_lm: boolean;
  no_link_click: string;
  /** Только кликнувшим ссылку с такой меткой. Поле Студии: в yaml bot-engine не выгружается. */
  link_click: string;
  no_manager_dm: boolean;
  max_subscription_gate: boolean;
  /** Не слать тем, кто зарегистрировался в эти часы МСК: ["11:00-12:00"] (skip_if_registered_msk). */
  registered_windows: string[];
}

export interface ContactSettings {
  contact_button: string;
  skip_if_known: boolean;
  always_send_text: boolean;
  text_if_known: string;
  manual_phone_max: boolean;
  manual_phone_max_text: string;
  manual_phone_retry_text: string;
}

export interface EmailSettings {
  skip_if_known: boolean;
  text_if_known: string;
  retry_text: string;
}

export interface SurveySettings {
  qid: string | null;
  multi: boolean;
  final: boolean;
  free: boolean;
  done_button: string;
  edit: boolean;
}

export interface CallSettings {
  audio_url: string | null;
  audio_id: number | null;
  text: string | null;
}

export interface SmsSettings {
  text: string | null;
}

export interface ClaimSettings {
  ack_text: string | null;
  keeps_chain: boolean;
}

export interface PinSettings {
  pin: boolean;
  air_pin: boolean;
}

export interface Step {
  id: Uuid;
  key: string;
  type: StepType;
  role: string | null;
  role_day: number | null;
  title: string;
  content: Content;
  media: Media;
  schedule: Schedule;
  conditions: Conditions;
  contact: ContactSettings;
  email: EmailSettings;
  survey: SurveySettings;
  call: CallSettings;
  sms: SmsSettings;
  claim: ClaimSettings;
  pin: PinSettings;
  diploma_template: string;
  /** Импорт из Miro: какой элемент доски, в чём импорт не уверен. В yaml не уходит. */
  miro: MiroStep | null;
  advanced: Record<string, unknown>;
}

/** Место, в котором импорт из Miro не уверен (check) или сделал что-то по правилу (info). */
export interface MiroDoubt {
  code: string;
  text: string;
  item_id: string | null;
  level: "check" | "info";
}

export interface MiroStep {
  item_id: string;
  item_ids: string[];
  doubts: MiroDoubt[];
  /** «Проверено» разработчиком – ключи «code:text», повторный импорт их не вернёт. */
  dismissed: string[];
  board: Record<string, unknown>;
}

export interface MiroSource {
  board_id: string;
  frame_id: string;
  frame_title: string;
  link: string;
  imported_at: string;
  doubts: MiroDoubt[];
  dismissed: string[];
  entry_item: string | null;
  edges_base: Record<string, unknown>[];
  bot_description: string;
}

export interface Edge {
  id: Uuid;
  from_step: Uuid;
  kind: EdgeKind;
  to_step: Uuid | null;
  to_funnel: string | null;
  delay_seconds: number | null;
  typing_before: boolean;
}

export interface BranchWindow {
  from: string;
  to: string;
  day: "today" | "tomorrow" | "fixed";
  date: string | null;
}

export interface Branch {
  id: string;
  entry_step: Uuid;
  webinar_time: string;
  windows: BranchWindow[];
  repeat_offset_minutes: number;
}

export interface BranchRouter {
  type: "time_of_day_msk";
  branches: Branch[];
}

export interface EntryVariant {
  when: CohortMatch;
  entry_step: Uuid;
}

export interface WebinarRoom {
  stream_url: string;
  bizon_room: string;
  replaces_room: string;
}

export interface UnresolvedRef {
  id: Uuid;
  key: string;
}

export interface Lane {
  id: Uuid;
  title: string;
  kind: string;
  day: number | null;
  order: number;
  step_ids: Uuid[];
}

export interface Position {
  x: number;
  y: number;
}

export interface Layout {
  positions: Record<Uuid, Position>;
}

export type RevisionAction = "replace_text" | "replace_media" | "replace_time" | "add" | "remove" | "other";
export type RevisionStatus = "open" | "done" | "accepted" | "rejected";

export interface Note {
  id: Uuid;
  kind: "note" | "revision";
  text: string;
  color: string;
  step_id: Uuid | null;
  lane_id: Uuid | null;
  position: Position | null;
  author: string;
  created_at: string;
  number: number | null;
  action: RevisionAction | null;
  status: RevisionStatus | null;
}

export interface SourceInfo {
  launch: string;
  path: string;
  extends: string | null;
  own_keys: string[];
}

export interface Funnel {
  schema_version: 1;
  id: string;
  title: string;
  source: SourceInfo | null;
  branch_router: BranchRouter | null;
  entry_variants: EntryVariant[];
  webinar_room: WebinarRoom | null;
  air_email_profile: string;
  max_video_note_caption: boolean;
  /** Повторный /start в окне анти-дубля шлёт первое сообщение ещё раз (resend_entry_on_repeat). */
  resend_entry_on_repeat: boolean;
  steps: Step[];
  edges: Edge[];
  unresolved: UnresolvedRef[];
  lanes: Lane[];
  layout: Layout;
  notes: Note[];
  /** Импорт из Miro: откуда воронка. В yaml не уходит. */
  miro: MiroSource | null;
}
