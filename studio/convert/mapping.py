"""Карта «поле движка → место в модели Студии».

Одна таблица на оба направления: импорт раскладывает плоский `FunnelStep`
по группам, экспорт собирает обратно. Если в движке появится новое поле, а
сюда его не добавили, импорт не потеряет его молча – оно уйдёт в
`Step.advanced`, и тест покрытия (`tests/test_mapping.py`) упадёт.
"""

from __future__ import annotations

from ..model.funnel import EdgeKind

# поле движка → путь в Step (кортеж атрибутов)
STEP_FIELD_PATHS: dict[str, tuple[str, ...]] = {
    # контент по платформам
    "text": ("content", "tg", "text"),
    "buttons": ("content", "tg", "buttons"),
    "claim_button_text": ("content", "tg", "claim_button_text"),
    "text_max": ("content", "max", "text"),
    "buttons_max": ("content", "max", "buttons"),
    "claim_button_text_max": ("content", "max", "claim_button_text"),
    "variants": ("content", "variants"),
    # медиа
    "image": ("media", "image"),
    "images": ("media", "images"),
    "image_lm": ("media", "image_lm"),
    "video_note": ("media", "video_note"),
    "video_fallback": ("media", "video_fallback"),
    "video": ("media", "video"),
    "animation": ("media", "animation"),
    "rich": ("media", "rich"),
    "media_optional": ("media", "media_optional"),
    # расписание
    "after_start_seconds": ("schedule", "after_start_seconds"),
    "absolute_time_msk": ("schedule", "absolute_time_msk"),
    "day_offset": ("schedule", "day_offset"),
    "day_offset_by_branch": ("schedule", "day_offset_by_branch"),
    "webinar_offset_minutes": ("schedule", "webinar_offset_minutes"),
    "webinar_offset_by_branch": ("schedule", "webinar_offset_by_branch"),
    "anchor": ("schedule", "anchor"),
    "skip_if_past": ("schedule", "skip_if_past"),
    "dnd_exempt": ("schedule", "dnd_exempt"),
    "min_delay_seconds": ("schedule", "min_delay_seconds"),
    # условия
    "gated_by_attendance": ("conditions", "attendance"),
    "bizon_room": ("conditions", "bizon_room"),
    "bizon_room_max": ("conditions", "bizon_room_max"),
    "gate_window_hours": ("conditions", "gate_window_hours"),
    "gated_by_not_paid": ("conditions", "not_paid"),
    "gated_by_paid": ("conditions", "paid"),
    "gated_by_not_course_paid": ("conditions", "not_course_paid"),
    "gated_by_course_paid": ("conditions", "course_paid"),
    "gated_by_channel_joined": ("conditions", "channel"),
    "gated_by_survey_done": ("conditions", "survey_done"),
    "gated_by_has_lm": ("conditions", "has_lm"),
    "gated_by_no_link_click": ("conditions", "no_link_click"),
    "gated_by_no_manager_dm": ("conditions", "no_manager_dm"),
    "max_subscription_gate": ("conditions", "max_subscription_gate"),
    "skip_if_registered_msk": ("conditions", "registered_windows"),
    # телефон
    "contact_button": ("contact", "contact_button"),
    "skip_if_user_phone_known": ("contact", "skip_if_known"),
    "always_send_text": ("contact", "always_send_text"),
    "text_if_phone_known": ("contact", "text_if_known"),
    "manual_phone_max": ("contact", "manual_phone_max"),
    "manual_phone_max_text": ("contact", "manual_phone_max_text"),
    "manual_phone_retry_text": ("contact", "manual_phone_retry_text"),
    # почта
    "skip_if_user_email_known": ("email", "skip_if_known"),
    "text_if_email_known": ("email", "text_if_known"),
    "email_retry_text": ("email", "retry_text"),
    # опрос
    "survey_qid": ("survey", "qid"),
    "survey_multi": ("survey", "multi"),
    "survey_final": ("survey", "final"),
    "survey_free": ("survey", "free"),
    "survey_done_button": ("survey", "done_button"),
    "survey_edit": ("survey", "edit"),
    # звонок / SMS
    "call_audio_url": ("call", "audio_url"),
    "call_audio_id": ("call", "audio_id"),
    "call_text": ("call", "text"),
    "sms_text": ("sms", "text"),
    # claim-кнопка и закреп
    "claim_ack_text": ("claim", "ack_text"),
    "claim_keeps_chain": ("claim", "keeps_chain"),
    "pin": ("pin", "pin"),
    "air_pin": ("pin", "air_pin"),
    "diploma_template": ("diploma_template",),
}

# Поля модели, которых у движка bot-engine НЕТ: работают только в движке Студии
# (runtime/), в yaml не выгружаются (у FunnelStep extra="forbid" – лишнее поле
# уронило бы загрузку воронки). Экспорт предупреждает о них (to_engine.studio_only_notes).
STUDIO_ONLY_PATHS: dict[tuple[str, ...], str] = {
    ("conditions", "link_click"): "условие «кликнул ссылку»",
}

# Стрелки с одной целью: поле-цель → (вид стрелки, поле задержки или None)
SINGLE_EDGE_FIELDS: dict[str, tuple[EdgeKind, str | None]] = {
    "on_done": (EdgeKind.done, "on_done_after_seconds"),
    "on_contact": (EdgeKind.contact, "on_contact_after_seconds"),
    "on_skip": (EdgeKind.skip, "on_skip_after_seconds"),
    "on_skip_if_phone_known": (EdgeKind.skip_if_phone_known, "on_skip_if_phone_known_after_seconds"),
    "on_email": (EdgeKind.email, "on_email_after_seconds"),
    "on_skip_if_email_known": (EdgeKind.skip_if_email_known, "on_skip_if_email_known_after_seconds"),
    "on_callback": (EdgeKind.callback, None),
    "survey_next": (EdgeKind.survey_next, None),
    "jump_to": (EdgeKind.jump, None),
}

# Поля, которые конвертер обрабатывает особым образом
EDGE_SPECIAL_FIELDS = {"also_fire", "chain_to_funnel", "typing_before_chain"}
IDENTITY_FIELDS = {"key", "type"}
# Мёртвое поле: все сендеры шлют HTML. Отличное от HTML значение – в мешок.
DEAD_FIELDS = {"parse_mode"}

DELAY_FIELDS = {d for _, d in SINGLE_EDGE_FIELDS.values() if d}

# Поля кнопок и когорт: движок ↔ Студия
COHORT_FIELDS = {
    "when_utm_source": "utm_source",
    "when_utm_medium": "utm_medium",
    "when_utm_campaign": "utm_campaign",
    "when_trigger": "trigger",
    "when_lm_post_id": "lm_post_id",
    "when_lm_post_id_in": "lm_post_id_in",
    "platforms": "platforms",
}
VARIANT_CONTENT_FIELDS = ("text", "text_max", "buttons", "claim_button_text")
BUTTON_FIELDS = ("text", "url", "callback_data", "style", "web_app")


def covered_step_fields() -> set[str]:
    return (set(STEP_FIELD_PATHS) | set(SINGLE_EDGE_FIELDS) | DELAY_FIELDS
            | EDGE_SPECIAL_FIELDS | IDENTITY_FIELDS | DEAD_FIELDS)
