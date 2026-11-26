// Значения по умолчанию – ровно как у pydantic-модели (сверяется тестом с fixtures/default_step.json).
import type {
  Button, CallSettings, ClaimSettings, CohortMatch, Conditions, ContactSettings, Content,
  EmailSettings, Funnel, Media, Note, PinSettings, Schedule, SmsSettings, Step, StepType,
  SurveySettings, Uuid,
} from "./types";

export const DEFAULT_CONTACT_BUTTON = "📱 Оставить номер телефона";
export const DEFAULT_SURVEY_DONE_BUTTON = "✅ Готово, продолжить";

export const defaultContent = (): Content => ({
  tg: { text: "", buttons: [], claim_button_text: null },
  max: { text: null, buttons: null, claim_button_text: null },
  variants: [],
});

export const defaultMedia = (): Media => ({
  image: null, images: null, image_lm: false, video_note: null, video_fallback: null,
  video: null, animation: null, rich: null, media_optional: false,
});

export const defaultSchedule = (): Schedule => ({
  after_start_seconds: null, absolute_time_msk: null, day_offset: 0, day_offset_by_branch: null,
  webinar_offset_minutes: null, webinar_offset_by_branch: null, anchor: "webinar",
  skip_if_past: true, dnd_exempt: false, min_delay_seconds: 0,
});

export const defaultConditions = (): Conditions => ({
  attendance: null, bizon_room: null, bizon_room_max: "", gate_window_hours: 4,
  not_paid: false, paid: false, not_course_paid: false, course_paid: false, channel: null,
  survey_done: false, has_lm: false, no_link_click: "", link_click: "", no_manager_dm: false,
  max_subscription_gate: false, registered_windows: [],
});

export const defaultContact = (): ContactSettings => ({
  contact_button: DEFAULT_CONTACT_BUTTON, skip_if_known: false, always_send_text: false,
  text_if_known: "", manual_phone_max: false, manual_phone_max_text: "", manual_phone_retry_text: "",
});

export const defaultEmail = (): EmailSettings => ({ skip_if_known: false, text_if_known: "", retry_text: "" });

export const defaultSurvey = (): SurveySettings => ({
  qid: null, multi: false, final: false, free: false, done_button: DEFAULT_SURVEY_DONE_BUTTON, edit: false,
});

export const defaultCall = (): CallSettings => ({ audio_url: null, audio_id: null, text: null });
export const defaultSms = (): SmsSettings => ({ text: null });
export const defaultClaim = (): ClaimSettings => ({ ack_text: null, keeps_chain: false });
export const defaultPin = (): PinSettings => ({ pin: false, air_pin: false });

export const defaultCohort = (): CohortMatch => ({
  utm_source: null, utm_medium: null, utm_campaign: null, trigger: null,
  lm_post_id: null, lm_post_id_in: [], platforms: [],
});

export function defaultButton(text = ""): Button {
  return { text, url: null, callback_data: null, style: null, web_app: false };
}

export function newStep(id: Uuid, key: string, type: StepType = "text"): Step {
  return {
    id, key, type, role: null, role_day: null, title: "",
    content: defaultContent(), media: defaultMedia(), schedule: defaultSchedule(),
    conditions: defaultConditions(), contact: defaultContact(), email: defaultEmail(),
    survey: defaultSurvey(), call: defaultCall(), sms: defaultSms(), claim: defaultClaim(),
    pin: defaultPin(), diploma_template: "", miro: null, advanced: {},
  };
}

export function emptyFunnel(id: string, launch: string | null = null): Funnel {
  return {
    schema_version: 1, id, title: "",
    source: launch === null ? null : { launch, path: "", extends: null, own_keys: [] },
    branch_router: null, entry_variants: [], webinar_room: null, air_email_profile: "",
    max_video_note_caption: true, resend_entry_on_repeat: false, steps: [], edges: [], unresolved: [], lanes: [],
    layout: { positions: {} }, notes: [], miro: null,
  };
}

export function newNote(id: Uuid, text: string): Note {
  return {
    id, kind: "note", text, color: "#fff6b6", step_id: null, lane_id: null, position: null,
    author: "", created_at: "", number: null, action: null, status: null,
  };
}
