"""Модель воронки Студии – граф для редактора.

Это НЕ копия `FunnelStep` движка. Движок хранит шаг как плоский мешок из ~85
полей, где связи (`on_done`, `also_fire`, …), время и гейты перемешаны. Здесь
то же самое разложено так, как это видит человек на холсте:

  Funnel ── steps[]   карточки: тип, роль, контент по платформам, медиа,
         │            расписание (абсолютное / от эфира), условия, настройки
         │            контакта / почты / опроса / звонка / SMS / закрепа
         ├─ edges[]   стрелки: вид перехода + относительная задержка
         ├─ lanes[]   полосы (вход, день N, повтор, …) – только оформление
         ├─ notes[]   заметки и правки – отдельный слой, в yaml НЕ уходит
         └─ layout    координаты карточек – отдельно от смысла

Где живёт время (решение этапа 0):
  * относительная задержка «через N после предыдущего / после события» –
    на стрелке (`Edge.delay_seconds`): `on_done_after_seconds`,
    `on_contact_after_seconds`, `on_skip_after_seconds` (таймаут ожидания),
    `on_email_after_seconds`, `on_skip_if_*_known_after_seconds`, значения
    `also_fire`;
  * «когда отправить» по часам или от эфира – у шага (`Step.schedule`):
    `absolute_time_msk`, `day_offset`, `*_by_branch`, `webinar_offset_minutes`,
    `anchor`, `skip_if_past`, `dnd_exempt`, `after_start_seconds`,
    `min_delay_seconds` («не раньше чем через N после постановки»).
  Абсолютное время по правилам движка перебивает задержку входящей стрелки
  (задержка = цель − сейчас), поэтому оно свойство шага, а не стрелки.

Платформы: `content.tg` – как в Telegram; `content.max` – переопределения для
MAX с семантикой движка: None = «как в TG», пустое ("" или []) = «в MAX этого
нет». Смешивать эти два состояния нельзя – поэтому поля MAX Optional.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ── перечисления ─────────────────────────────────────────────────────────────

class StepType(str, Enum):
    text = "text"
    photo = "photo"
    video_note = "video_note"
    video = "video"
    animation = "animation"
    rich = "rich"
    phone_request = "phone_request"
    email_request = "email_request"
    jump = "jump"
    call = "call"
    sms = "sms"
    air_pin = "air_pin"


class StepRole(str, Enum):
    """Роль шага для дашбордов (исследование, §8.2). На этапе 0 не заполняется."""
    entry = "entry"
    contact = "contact"
    email = "email"
    channel_invite = "channel_invite"
    channel_nudge = "channel_nudge"
    vip_offer = "vip_offer"
    vip_nudge = "vip_nudge"
    survey_q = "survey_q"
    survey_final = "survey_final"
    gift = "gift"
    air_announce = "air_announce"
    air_start = "air_start"
    air_repeat = "air_repeat"
    offer = "offer"
    sale = "sale"
    handoff = "handoff"


class EdgeKind(str, Enum):
    done = "done"                                # on_done (+ on_done_after_seconds)
    contact = "contact"                          # on_contact – дал телефон
    skip = "skip"                                # on_skip – не дал за on_skip_after_seconds
    skip_if_phone_known = "skip_if_phone_known"  # телефон уже известен
    email = "email"                              # on_email – прислал почту
    skip_if_email_known = "skip_if_email_known"  # почта уже известна
    callback = "callback"                        # on_callback – нажал claim-кнопку
    survey_next = "survey_next"                  # ответ на вопрос опроса
    jump = "jump"                                # jump_to (надгробие)
    also_fire = "also_fire"                      # параллельный таймер (пунктир)
    chain_to_funnel = "chain_to_funnel"          # переход в другую воронку


# Какие виды стрелок несут задержку и что значит её отсутствие.
EDGE_DELAY_KINDS = {
    EdgeKind.done, EdgeKind.contact, EdgeKind.skip, EdgeKind.skip_if_phone_known,
    EdgeKind.email, EdgeKind.skip_if_email_known, EdgeKind.also_fire,
    EdgeKind.chain_to_funnel,
}


# ── общие кирпичи ────────────────────────────────────────────────────────────

class Button(_M):
    text: str
    url: str | None = None
    callback_data: str | None = None
    # В движке поле есть, но ни один сендер его не читает (исследование §4.2).
    style: str | None = None
    web_app: bool = False


class CohortMatch(_M):
    """«Человек пришёл по такой-то ссылке». Пустые поля не проверяются."""
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    trigger: str | None = None
    lm_post_id: str | None = None
    lm_post_id_in: list[str] = []
    platforms: list[Literal["tg", "max", "vk"]] = []


class StepVariant(_M):
    """Другая формулировка того же шага для когорты. None = «как в шаге»."""
    when: CohortMatch = CohortMatch()
    text: str | None = None
    text_max: str | None = None
    buttons: list[Button] | None = None
    claim_button_text: str | None = None


# ── контент по платформам ────────────────────────────────────────────────────

class TgContent(_M):
    text: str = ""
    buttons: list[Button] = []
    claim_button_text: str | None = None


class MaxContent(_M):
    """Переопределения для MAX. None = как в TG; "" / [] = в MAX этого нет."""
    text: str | None = None
    buttons: list[Button] | None = None
    claim_button_text: str | None = None


class Content(_M):
    tg: TgContent = TgContent()
    max: MaxContent = MaxContent()
    variants: list[StepVariant] = []


class Media(_M):
    image: str | None = None
    images: list[str] | None = None
    # картинка под лидмагнит участника, фоллбэк – image
    image_lm: bool = False
    video_note: str | None = None
    video_fallback: str | None = None
    video: str | None = None
    animation: str | None = None
    rich: str | None = None
    # видео-шаг без ролика шлёт один текст вместо молчаливого пропуска
    media_optional: bool = False


# ── расписание шага («когда отправить» по часам / от эфира) ─────────────────

class Schedule(_M):
    """Абсолютная часть времени. Относительная задержка живёт на стрелке.

    Режимы (см. `mode`):
      chain   – ни часов, ни эфира: шаг приходит по входящей стрелке;
      clock   – `absolute_time_msk` + `day_offset` (или `day_offset_by_branch`);
      webinar – `webinar_offset_minutes` от эфира/повтора (`anchor`), опц.
                `webinar_offset_by_branch`; перебивает clock, если заданы оба.
    """
    after_start_seconds: int | None = None
    absolute_time_msk: str | None = None
    day_offset: int = 0
    day_offset_by_branch: dict[str, int] | None = None
    webinar_offset_minutes: int | None = None
    webinar_offset_by_branch: dict[str, int] | None = None
    anchor: Literal["webinar", "repeat"] = "webinar"
    skip_if_past: bool = True
    dnd_exempt: bool = False
    # min_delay_seconds движка: шаг с часами / от эфира уходит НЕ РАНЬШЕ, чем через
    # столько секунд от момента, когда его поставили в очередь (для шагов, которые
    # армируются пачкой на входе, – от регистрации). Касается и будущего часа, и
    # прошедшего при «всё равно отправить» (вместо 2 минут). Пропуск по
    # skip_if_past не меняет. 0 – как раньше. У шага «по стрелке» не действует.
    min_delay_seconds: int = 0

    @property
    def mode(self) -> Literal["chain", "clock", "webinar"]:
        if self.webinar_offset_minutes is not None:
            return "webinar"
        if self.absolute_time_msk:
            return "clock"
        return "chain"

    # Те же правила, что у движка: ошибки ловим в редакторе, а не на старте бота.
    @model_validator(mode="after")
    def _by_branch_rules(self):
        if self.day_offset_by_branch and not self.absolute_time_msk:
            raise ValueError("day_offset_by_branch применим только с absolute_time_msk")
        if self.webinar_offset_by_branch and self.webinar_offset_minutes is None:
            raise ValueError("webinar_offset_by_branch требует webinar_offset_minutes")
        return self


# ── условия (гейты) ──────────────────────────────────────────────────────────

class Conditions(_M):
    """Когда шаг ПРОПУСКАЕТСЯ (и сразу идёт on_done). Все поля – по умолчанию «нет условия».

    Скрытая семантика движка: у пропущенного шага теряется `also_fire`.
    """
    # был / не был на эфире (Bizon) + зал шага, MAX-зал, окно проверки
    attendance: Literal["was", "was_not"] | None = None
    bizon_room: str | None = None
    bizon_room_max: str = ""
    gate_window_hours: int = 4
    # ВИП и курс
    not_paid: bool = False          # gated_by_not_paid – не слать оплатившим ВИП
    paid: bool = False              # gated_by_paid – только оплатившим ВИП
    not_course_paid: bool = False
    course_paid: bool = False
    # канал
    channel: Literal["joined", "not_joined"] | None = None
    # прочее
    survey_done: bool = False       # gated_by_survey_done – не слать прошедшим опрос
    has_lm: bool = False            # только тем, у кого есть лидмагнит
    no_link_click: str = ""         # префикс метки; не слать кликнувшим
    # префикс метки; ТОЛЬКО кликнувшим. Поле Студии: у движка bot-engine такого гейта нет,
    # поэтому в yaml оно не выгружается (studio.convert.mapping.STUDIO_ONLY_PATHS),
    # работает в движке Студии (runtime/gates)
    link_click: str = ""
    no_manager_dm: bool = False     # не слать написавшим менеджеру
    # skip_if_registered_msk движка: не слать тем, кто зарегистрировался в эти часы МСК.
    # Окна "HH:MM-HH:MM", левая граница включается, правая нет; через полночь – двумя
    # окнами. Регистрация = последняя отправка входного шага воронки. Не нашли её –
    # шаг уходит (fail-open).
    registered_windows: list[str] = []
    max_subscription_gate: bool = False  # MAX: подарок только подписчику канала

    @model_validator(mode="after")
    def _windows_valid(self):
        # та же проверка, что у движка (FunnelStep._registered_windows_valid)
        from ..engine_ref.reg_window import parse_windows
        try:
            parse_windows(self.registered_windows)
        except ValueError as e:
            raise ValueError(f"часы регистрации – {e}") from None
        return self

    def active(self) -> list[str]:
        """Список включённых условий – для значков на карточке и отчёта."""
        out = []
        for name, field in type(self).model_fields.items():
            if name in ("bizon_room", "bizon_room_max", "gate_window_hours"):
                continue
            if getattr(self, name) != field.default:
                out.append(name)
        return out


# ── настройки по типам шагов ─────────────────────────────────────────────────

DEFAULT_CONTACT_BUTTON = "📱 Оставить номер телефона"
DEFAULT_SURVEY_DONE_BUTTON = "✅ Готово, продолжить"


class ContactSettings(_M):
    contact_button: str = DEFAULT_CONTACT_BUTTON
    skip_if_known: bool = False
    always_send_text: bool = False
    text_if_known: str = ""
    manual_phone_max: bool = False
    manual_phone_max_text: str = ""
    manual_phone_retry_text: str = ""


class EmailSettings(_M):
    skip_if_known: bool = False
    text_if_known: str = ""
    retry_text: str = ""


class SurveySettings(_M):
    qid: str | None = None
    multi: bool = False
    final: bool = False
    free: bool = False
    done_button: str = DEFAULT_SURVEY_DONE_BUTTON
    edit: bool = False


class CallSettings(_M):
    audio_url: str | None = None
    audio_id: int | None = None
    text: str | None = None


class SmsSettings(_M):
    text: str | None = None


class ClaimSettings(_M):
    """Claim-кнопка: подпись – в content.tg/max, цель – стрелка callback."""
    ack_text: str | None = None
    keeps_chain: bool = False


class PinSettings(_M):
    pin: bool = False
    air_pin: bool = False


# ── откуда шаг на доске Miro (импорт из Miro) ────────────────────────────────

class MiroDoubt(_M):
    """Место, в котором импорт не уверен (или сделал что-то по правилу, а не по доске).

    level: check – стоит проверить (подсветка на холсте и в «Проверке»), info – сделано
    по правилу, для сведения."""
    code: str
    text: str
    item_id: str | None = None            # элемент доски, к которому относится
    level: Literal["check", "info"] = "check"


class MiroStep(_M):
    """Шаг собран импортом из Miro. В yaml не уходит."""
    item_id: str                          # главный элемент – текст сообщения или кружок
    item_ids: list[str] = []              # все элементы сообщения: картинка, кнопки, стикеры
    doubts: list[MiroDoubt] = []
    # «Проверено» разработчиком: ключи сомнений «code:text» – повторный импорт их не вернёт
    dismissed: list[str] = []
    # Как шаг выглядел на доске при последнем импорте: поля, которые даёт доска.
    # Повторный импорт сравнивает с этим, чтобы не затереть ручные правки.
    board: dict[str, Any] = {}


class MiroSource(_M):
    """Воронка импортирована из фрейма Miro. В yaml не уходит."""
    board_id: str
    frame_id: str
    frame_title: str = ""
    link: str = ""
    imported_at: str = ""                 # UTC ISO
    doubts: list[MiroDoubt] = []          # не отнесённое ни к одному сообщению и т.п.
    dismissed: list[str] = []             # «проверено» – ключи «code:text»
    entry_item: str | None = None         # элемент первого сообщения при последнем импорте
    # стрелки доски при последнем импорте: [{from, kind, to, delay}] (from/to – элементы Miro)
    edges_base: list[dict[str, Any]] = []
    bot_description: str = ""             # текст первой колонки («Что умеет этот бот?»)


# ── шаг ──────────────────────────────────────────────────────────────────────

class Step(_M):
    id: uuid.UUID
    key: str
    type: StepType = StepType.text
    role: StepRole | None = None
    role_day: int | None = None      # для air_start / air_repeat: «день N»
    title: str = ""                  # человеческое имя карточки (в yaml не уходит)

    content: Content = Content()
    media: Media = Media()
    schedule: Schedule = Schedule()
    conditions: Conditions = Conditions()

    contact: ContactSettings = ContactSettings()
    email: EmailSettings = EmailSettings()
    survey: SurveySettings = SurveySettings()
    call: CallSettings = CallSettings()
    sms: SmsSettings = SmsSettings()
    claim: ClaimSettings = ClaimSettings()
    pin: PinSettings = PinSettings()
    diploma_template: str = ""
    # импорт из Miro: какой элемент доски, в чём импорт не уверен (в yaml не уходит)
    miro: MiroStep | None = None

    # Экранирующий мешок: поля движка, которым в модели нет законного места
    # (мёртвый parse_mode ≠ HTML, задержка без цели и т.п.). Должен быть пуст
    # почти всегда – отчёт `python -m studio.report` считает, что сюда попало.
    advanced: dict[str, Any] = {}


# ── стрелка ──────────────────────────────────────────────────────────────────

class Edge(_M):
    id: uuid.UUID
    from_step: uuid.UUID
    kind: EdgeKind
    to_step: uuid.UUID | None = None      # для всех, кроме chain_to_funnel
    to_funnel: str | None = None          # только chain_to_funnel: "default" / "alt" / имя
    # Относительная задержка в секундах. None – у вида стрелки задержки нет
    # (callback, survey_next, jump) или она не задана (таймаут skip).
    delay_seconds: int | None = None
    # chain_to_funnel: показать «печатает…» в ожидании (typing_before_chain)
    typing_before: bool = False

    @model_validator(mode="after")
    def _target(self):
        if self.kind == EdgeKind.chain_to_funnel:
            if not self.to_funnel or self.to_step is not None:
                raise ValueError("chain_to_funnel: нужен to_funnel и не нужен to_step")
        else:
            if self.to_step is None or self.to_funnel is not None:
                raise ValueError(f"{self.kind.value}: нужен to_step")
            if self.typing_before:
                raise ValueError("typing_before бывает только у chain_to_funnel")
        return self


# ── настройки уровня воронки ─────────────────────────────────────────────────

class BranchWindow(_M):
    from_: str = Field(alias="from")      # "HH:MM" МСК
    to: str
    day: Literal["today", "tomorrow", "fixed"] = "today"
    date: str | None = None               # "YYYY-MM-DD" при day: fixed


class Branch(_M):
    id: str
    entry_step: uuid.UUID
    webinar_time: str
    windows: list[BranchWindow]
    repeat_offset_minutes: int = 0


class BranchRouter(_M):
    type: Literal["time_of_day_msk"] = "time_of_day_msk"
    branches: list[Branch]


class EntryVariant(_M):
    when: CohortMatch = CohortMatch()
    entry_step: uuid.UUID


class WebinarRoom(_M):
    stream_url: str = ""
    bizon_room: str = ""
    replaces_room: str = ""


class UnresolvedRef(_M):
    """Ссылка на шаг, которого в воронке нет (движок: «Unknown step»).

    Хранится, чтобы круг был без потерь, и чтобы предполёт мог показать её
    красной стрелкой в никуда."""
    id: uuid.UUID
    key: str


# ── оформление: полосы, раскладка, заметки ───────────────────────────────────

class Lane(_M):
    id: uuid.UUID
    title: str
    kind: str = ""                       # entry / day / repeat / not_attended / survey / vip_paid …
    day: int | None = None
    order: int = 0
    step_ids: list[uuid.UUID] = []


class Position(_M):
    x: float
    y: float


class Layout(_M):
    positions: dict[uuid.UUID, Position] = {}


class Note(_M):
    """Заметка или правка. Отдельный тип объекта – в бот не уходит никогда."""
    id: uuid.UUID
    kind: Literal["note", "revision"] = "note"
    text: str
    color: str = "#fff6b6"
    step_id: uuid.UUID | None = None      # булавка к карточке
    lane_id: uuid.UUID | None = None      # правка на полосу
    position: Position | None = None      # свободная заметка
    author: str = ""
    created_at: str = ""
    # только для правок
    number: int | None = None
    action: Literal["replace_text", "replace_media", "replace_time", "add", "remove", "other"] | None = None
    status: Literal["open", "done", "accepted", "rejected"] | None = None


class SourceInfo(_M):
    """Откуда импортирована воронка (в yaml не уходит)."""
    launch: str
    path: str = ""                        # относительно корня bot-engine
    # extends: движок сливает родителя с ребёнком (ключи ребёнка целиком
    # перебивают родительские). При импорте разворачиваем, но помним, откуда
    # и какие ключи были свои, – чтобы уметь выгрузить компактную форму.
    extends: str | None = None
    own_keys: list[str] = []


# ── воронка ──────────────────────────────────────────────────────────────────

class Funnel(_M):
    schema_version: Literal[1] = 1
    id: str
    title: str = ""
    source: SourceInfo | None = None

    branch_router: BranchRouter | None = None
    entry_variants: list[EntryVariant] = []
    webinar_room: WebinarRoom | None = None
    air_email_profile: str = ""
    max_video_note_caption: bool = True
    # resend_entry_on_repeat движка: повторный /start в окне анти-дубля (вход был
    # меньше 90 с назад, следующие шаги уже стоят) по умолчанию молча игнорируется.
    # true – первое сообщение уходит ещё раз, запланированное не трогается
    # (боты-выдачи: человек жмёт /start и ждёт ответа).
    resend_entry_on_repeat: bool = False

    steps: list[Step] = []
    edges: list[Edge] = []
    unresolved: list[UnresolvedRef] = []

    lanes: list[Lane] = []
    layout: Layout = Layout()
    notes: list[Note] = []
    # импорт из Miro: откуда воронка (в yaml не уходит)
    miro: MiroSource | None = None

    # ── удобства ──
    def step_by_id(self) -> dict[uuid.UUID, Step]:
        return {s.id: s for s in self.steps}

    def edges_from(self, step: uuid.UUID) -> list[Edge]:
        return [e for e in self.edges if e.from_step == step]

    @model_validator(mode="after")
    def _refs(self):
        known = {s.id for s in self.steps} | {u.id for u in self.unresolved}
        if len({s.id for s in self.steps}) != len(self.steps):
            raise ValueError("повтор id шага")
        for e in self.edges:
            if e.from_step not in known:
                raise ValueError(f"стрелка {e.id} из неизвестного шага")
            if e.to_step is not None and e.to_step not in known:
                raise ValueError(f"стрелка {e.id} в неизвестный шаг")
        refs = [ev.entry_step for ev in self.entry_variants]
        if self.branch_router:
            refs += [b.entry_step for b in self.branch_router.branches]
        for r in refs:
            if r not in known:
                raise ValueError(f"вход ведёт в неизвестный шаг {r}")
        return self
