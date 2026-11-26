# VENDORED из bot-engine: common/launch_engine/funnel/loader.py
# Коммит bot-engine: dd105332b3ddf41a2edab574d6605ea381d55283
# Скопировано как есть, логика не менялась. Нужен как «оракул» кругового теста:
# load_funnel(оригинал) должен совпасть с load_funnel(yaml после круга).
# Зависимости – pydantic и pyyaml; импорт reg_window переведён на studio.engine_ref.reg_window.
# Обновлять: scripts/vendor_engine.py (копирует заново и ставит новый коммит).
"""Yaml-схема funnel.yaml — последовательность шагов воронки.

Шаги связываются явно через `on_done` / `on_contact` / `on_skip` /
`on_skip_if_phone_known` (одно имя следующего шага). Цепочка строится
рекурсивно. Никакой неявной "linear order from yaml".

Типы шагов:
  text          — обычное сообщение (text + опц. кнопки)
  photo         — сообщение с фото (caption + кнопки)
  video_note    — отправка кружка через bot.send_video_note. Текст
                  игнорируется (у video_note нет caption) — текст
                  идёт следующим шагом через on_done. В MAX круглых видео
                  нет: шлётся обычным видео, и текст там уходит подписью.
                  Нужен паритет с TG (кружок без текста) – на всю воронку
                  max_video_note_caption: false, на отдельный шаг text_max: "".
  phone_request — запрос телефона (ReplyKeyboardMarkup, request_contact),
                  с auto-skip и шорткатом если phone уже известен
  email_request – запрос почты. Кнопки для этого нигде нет, поэтому ждём
                  обычное текстовое сообщение (TG и MAX одинаково), с
                  auto-skip и шорткатом если email уже известен
  jump          — безусловный переход на jump_to (без отправки сообщения)
  call          — автозвонок по телефону юзера через engine.telephony.
                  В заглушке логирует и пушит admin'у. on_done продолжается.
  sms           — SMS по телефону юзера через engine.sms.
                  В заглушке логирует. on_done продолжается.

Дополнительно шаг может иметь:
  absolute_time_msk: "HH:MM"  — абсолютное время МСК (привязка к
                                webinar_date юзера, см. branch_router)
  day_offset: int             — сдвиг от дня эфира (0=день эфира, -1
                                = за день до, и т.д.)
  skip_if_past: bool=True     — если absolute-таргет уже прошёл,
                                шаг пропускается, сразу on_done
  min_delay_seconds: int      – шаг с временем уходит не раньше, чем через
                                N секунд после того, как его запланировали
  gated_by_attendance: was|was_not — гейт перед отправкой через
                                bizon attendance (engine.bizon)
  bizon_room: str             — переопределяет cfg.broadcast.air_room
  gated_by_survey_done: bool  — skip шаг если participant.survey_done=True
                                (для followup'а после GetCourse-опроса)

FunnelConfig поддерживает branch_router — выбор entry-step при /start
по локальному времени МСК. См. BranchRouter ниже.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import model_validator, BaseModel, ConfigDict, Field


class ButtonConfig(BaseModel):
    text: str
    url: str | None = None
    callback_data: str | None = None
    style: str | None = None  # "primary"/"success"/"danger" — Bot API 9.4+
    # Если True — кнопка открывает url как Telegram Mini App
    # (web_app=WebAppInfo). Юзер не покидает чат с ботом.
    web_app: bool = False


class CohortMatch(BaseModel):
    """Условие «человек пришёл по такой-то ссылке».

    Сравниваются метки участника, проставленные при /start (utm_* и trigger),
    без учёта регистра. Заданные поля должны совпасть ВСЕ; незаданные не
    проверяются. `platforms` (пусто = любая) ограничивает платформу — ссылка
    закупки обычно живёт только в одной.
    """
    model_config = ConfigDict(extra="forbid")

    when_utm_source: str | None = None
    when_utm_medium: str | None = None
    when_utm_campaign: str | None = None
    when_trigger: str | None = None
    # Лидмагнит, с которого человек пришёл (`participant.lm_post_id`). В
    # отличие от utm_*, эта метка перезаписывается на КАЖДОМ входе, поэтому
    # только она честно отвечает «под каким постом он нажал кнопку сейчас»
    # (§22 CLAUDE.md: utm участника остаются от первого захода навсегда).
    # Список – удобство: у одного текста обычно несколько постов-источников.
    when_lm_post_id: str | None = None
    when_lm_post_id_in: list[str] = []
    platforms: list[Literal["tg", "max", "vk"]] = []


class StepVariant(CohortMatch):
    """Другая формулировка ТОГО ЖЕ шага для отдельной когорты трафика.

    Зачем: закупщик/подрядчик просит свои тексты для своей аудитории, а
    воронка при этом обязана остаться одна – та же регистрация, те же
    таймеры, те же пуши, та же таблица. Дублировать воронку ради двух абзацев
    нельзя: копии расходятся уже на первой правке (проверено на посевах).

    Подменяются только формулировки; всё остальное в шаге (тип, картинка,
    гейты, цепочка) берётся из самого шага. None = «оставить как есть».

    Первый подошедший вариант выигрывает, поэтому частные условия ставим выше
    общих. Читается сендерами TG и MAX перед отправкой.
    """
    text: str | None = None
    text_max: str | None = None
    buttons: list[ButtonConfig] | None = None
    claim_button_text: str | None = None


class EntryVariant(CohortMatch):
    """Свой ВХОДНОЙ шаг для когорты (перебивает branch_router).

    Нужен, когда у отдельного источника трафика своё первое сообщение, а
    дальше человек идёт по общей воронке: entry_step ведёт на неё через
    on_done/on_callback. Читают раннеры TG и MAX в start_for_*.
    """
    entry_step: str


class FunnelStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    type: Literal[
        "text", "photo", "video_note", "video", "animation",
        "rich", "phone_request", "email_request", "jump", "call", "sms",
        # air_pin – закреп с живым отсчётом до эфира. Текста у шага нет: и
        # содержимое, и переключения «ждём → идёт → следующий» ведёт
        # services/air_pin по расписанию эфиров, шаг лишь ставит его человеку.
        "air_pin",
    ] = "text"

    text: str = ""
    # Сделать это сообщение закрепом с отсчётом до эфира: сверху появится
    # шапка «N дней до эфира» и дата, а сам текст шага останется под ней.
    # Дальше сообщение обновляет services/air_pin, меняя только шапку.
    # ⚠️ Оно же и закрепляется – отдельный `pin` для такого шага не нужен.
    air_pin: bool = False
    # Закрепить отправленное сообщение в чате с ботом. Нужно для шагов, к
    # которым человек возвращается: билет розыгрыша, ссылка на эфир, доступ.
    # Раннер закрепляет ТОЛЬКО первую выдачу шага человеку (повторы и команды
    # не перебивают закреп).
    # ⚠️ Пин перетирает предыдущий: помечать `pin` больше чем у одного шага
    # воронки почти всегда ошибка.
    pin: bool = False
    image: str | None = None
    images: list[str] | None = None
    # ── rich (статья, sendRichMessage, Bot API 07.2026) ──
    # Путь к JSON с блоками: {"blocks": [...]} – заголовки, абзацы, списки,
    # картинки с подписями. Такое сообщение собирает сам Telegram: в один
    # текст это не уложить (у обычного сообщения одно медиа и нет структуры).
    # Картинки внутри блоков задаются путём ./static/... – sender подменит их
    # на file_id (кэш FunnelMedia), потому что метод принимает только file_id:
    # внешний URL он отклоняет («wrong type of the web page content»).
    # `text` шага при этом НЕ пропадает: он остаётся текстовым запасом для
    # MAX/VK, где статей нет. Inline-кнопки работают как у обычного шага.
    rich: str | None = None
    video_note: str | None = None  # путь к mp4-кружку либо TG file_id
    # ── video / animation (обычное видео и GIF-mp4) ──
    # Значение: TG file_id, HTTPS-URL или путь к файлу (./static/...).
    # text шага уходит caption'ом (>1000 симв. → сплит: медиа + текст),
    # inline-кнопки поддерживаются. MAX/VK sender'ы шлют text-fallback.
    video: str | None = None       # для type: video
    animation: str | None = None   # для type: animation (GIF/mp4 без звука)
    # Fallback для video_note: если у юзера запрещены video notes/голосовые,
    # отправляем тот же контент как обычное video. Можно задать file_id
    # обычного video или путь к mp4-файлу. Если пусто — fallback на step.text.
    video_fallback: str | None = None
    # Шаг с видео, у которого текст ценен сам по себе (анонс эфира, оффер).
    # По умолчанию видео-шаг без ролика уходит В НИКУДА: «смотрите, что я
    # сделал» без ролика бессмысленно, и оба сендера такой шаг пропускают.
    # С media_optional: true шаг в этом случае отправляет ОДИН ТЕКСТ – и когда
    # ролика ещё нет (ждём, пока админ пришлёт его командой «айди <ключ>»), и
    # когда отправка видео сорвалась (протухший MAX-токен, файл тяжелее 20 МБ).
    # Ставить только там, где текст читается без видео.
    media_optional: bool = False
    # ── Диплом ──
    # Ключ шаблона из пульта (/diplomas). Шаг выдаёт человеку именной документ
    # и подставляет {diploma_number} / {diploma_code} / {diploma_url}; картинку
    # даёт `image: "{diploma_image}"`. Выдача идемпотентна – повторный показ
    # шага отдаёт тот же номер, а не печатает второй документ.
    diploma_template: str = ""
    buttons: list[ButtonConfig] = []
    parse_mode: str = "HTML"

    # ── MAX-версии текста и кнопок (читает ТОЛЬКО max sender) ──────────
    # Нужны, когда воронка общая для TG и MAX, а сообщение зовёт в телеграмный
    # канал: ссылку развести умеет {channel.link}, а вот подпись кнопки
    # («Телеграм-канал») и формулировки в тексте – нет. TG эти поля не видит
    # вообще, поэтому телеграмная сторона не меняется.
    # Во всех трёх None = «как в TG», а ПУСТОЕ значение осмысленно и означает
    # «в MAX этого нет»:
    #   text_max: ""              – сообщение без текста. Так задаётся кружок
    #                               без подписи: в TG у video_note caption'а нет
    #                               вовсе, а MAX круглых видео не умеет и шлёт
    #                               обычное видео, куда подпись влезает. Глобально
    #                               подпись гасить нельзя – в MAX-only воронках
    #                               текст кружка вообще единственный.
    #   buttons_max: []           – в MAX кнопок нет
    #   claim_button_text_max: "" – в MAX claim-кнопки нет (нужно, когда её цель
    #                               в MAX недоступна: например видео-шаг задан
    #                               телеграмным file_id и туда вести нечестно)
    text_max: str | None = None
    buttons_max: list[ButtonConfig] | None = None
    claim_button_text_max: str | None = None

    # ── Формулировки под отдельную когорту трафика (см. StepVariant) ──
    # Пусто (по умолчанию) – шаг ведёт себя ровно как раньше, лишних запросов
    # в базу не делается.
    variants: list[StepVariant] = []

    # ── call / sms ──
    call_audio_url: str | None = None     # mp3-URL для TTS/audio-провайдеров
    call_audio_id: int | None = None      # ID pre-uploaded аудио (zvonobot)
    call_text: str | None = None           # текст для TTS если без audio-URL/id
    sms_text: str | None = None            # шаблон SMS, {Имя}/{webinar.time} подставятся

    # Стартовый шаг указывает after_start_seconds (от момента /start).
    # Все остальные приходят через on_done предыдущего.
    after_start_seconds: int | None = None

    # ── Chain (для всех типов кроме phone_request) ──
    on_done: str | None = None              # следующий шаг
    on_done_after_seconds: int = 0          # задержка перед on_done

    # ── Phone request only ──
    contact_button: str = "📱 Оставить номер телефона"
    skip_if_user_phone_known: bool = False  # если у юзера уже есть phone — пропускаем
    # Если True — текст/картинку шага шлём ВСЕГДА (всем), а кнопку запроса
    # контакта добавляем ТОЛЬКО когда телефона нет. При известном телефоне
    # сообщение всё равно уходит (без кнопки), затем on_skip_if_phone_known.
    # MAX: всегда без кнопки (нет нативного контакта).
    always_send_text: bool = False
    # Альтернативный текст для случая «телефон уже известен» (и для MAX, где
    # контакт не запросить). Если задан — при known-phone шлём ЕГО вместо
    # `text` (без строк про «оставь контакт»). Пусто → используем `text`.
    text_if_phone_known: str = ""
    # MAX: РУЧНОЙ запрос телефона. В MAX нет нативной кнопки «поделиться
    # контактом», поэтому по умолчанию phone_request в MAX просто скипается.
    # true → шаг НЕ скипается: шлём текст (без кнопки), ставим «ждём номер»
    # в Redis и ждём, пока юзер пришлёт номер ОБЫЧНЫМ сообщением. Успешный
    # разбор → phone в участника + сразу on_contact. Не прислал за
    # on_skip_after_seconds (у нас 300с) → on_skip. На TG не влияет.
    manual_phone_max: bool = False
    # Текст просьбы прислать номер – ТОЛЬКО для MAX. Нужен, когда воронка общая
    # для TG и MAX: в TG `text` зовёт нажать кнопку «Отправить контакт», а в MAX
    # такой кнопки нет и та же фраза сбивает с толку. Пусто → берётся `text`.
    manual_phone_max_text: str = ""
    # Что ответить, если в режиме ожидания пришёл текст без номера.
    # Пусто → молчим (текст просто уходит в inbox /live как обычно).
    manual_phone_retry_text: str = ""

    on_contact: str | None = None           # следующий шаг при F.contact
    on_contact_after_seconds: int = 0
    on_skip_after_seconds: int | None = None  # auto-skip если не поделили контакт за N сек
    on_skip: str | None = None              # следующий шаг при auto-skip
    on_skip_if_phone_known: str | None = None  # шаг при skip_if_user_phone_known=true
    on_skip_if_phone_known_after_seconds: int = 0  # задержка перед on_skip_if_phone_known

    # ── Email request only ──
    # Кнопки «поделиться почтой» нет ни в TG, ни в MAX, поэтому шаг всегда
    # работает одинаково: шлём текст и ждём ОБЫЧНОЕ текстовое сообщение.
    # Ожидание живёт в Redis (services/awaiting.py), окно – on_skip_after_seconds.
    # Не дождались → on_skip. Ответ после дедлайна: почту сохраняем, воронку
    # не двигаем (она уже ушла по on_skip).
    skip_if_user_email_known: bool = False  # если почта уже есть – пропускаем шаг
    # Альтернативный текст для случая «почта уже известна» (без строк про
    # «напишите почту»). Пусто → шаг просто скипается молча.
    text_if_email_known: str = ""
    # Что ответить, если пришёл текст без почты. Пусто → молчим, сообщение
    # уходит в inbox /live как обычно, ожидание остаётся взведённым.
    email_retry_text: str = ""
    on_email: str | None = None             # следующий шаг когда почта получена
    on_email_after_seconds: int = 0         # задержка перед on_email
    on_skip_if_email_known: str | None = None      # шаг при skip_if_user_email_known=true
    on_skip_if_email_known_after_seconds: int = 0  # задержка перед ним

    # ── Claim-button (inline-кнопка с callback) ──
    # Если задано — рендерится inline-кнопка с этим текстом. По нажатию
    # отменяется таймер on_done и фигачится on_callback step.
    claim_button_text: str | None = None
    on_callback: str | None = None          # шаг при клике на claim-кнопку
    # Кнопка-подтверждение: on_callback не задан, никуда не ведёт, шаг за ней
    # не приходит. Нужна там, где ТЗ просит от человека простое «я приду» –
    # клик пишется в <slug>_user_actions (видно, сколько подтвердили), а в
    # ответ показывается всплывающая подсказка с этим текстом. Пусто – тихий
    # ack без подсказки. В MAX подсказок нет, там клик просто засчитывается.
    claim_ack_text: str | None = None

    # ── In-bot опрос (анкета внутри бота, кросс-платформенно TG+MAX) ──
    # Если survey_qid задан — шаг является ВОПРОСОМ опроса. Его `buttons`
    # (обычные, без url) движок сам превращает в варианты ответа с
    # callback_data `sv:<slug>:<step_key>:<idx>` (sender'ы TG+MAX). По клику
    # ответ пишется в <slug>_survey_responses.answers[survey_qid], и:
    #   • single-select        → сразу фигачится survey_next (следующий вопрос);
    #   • multi (survey_multi)  → вариант тоггл(ится); переход по кнопке
    #     survey_done_button (движок добавляет её последней строкой).
    # На вопрос-шаге движок НЕ планирует on_done/also_fire автоматически
    # (ждём ответ юзера). Переход только через survey_next по колбэку.
    # Если survey_final=True — по завершении этого вопроса опрос финализируется
    # (participant.survey_done + submitted_at + Google Sheets + отмена followup),
    # затем фигачится survey_next (шаг-«спасибо» + выдача лид-магнита).
    survey_qid: str | None = None
    survey_multi: bool = False
    survey_final: bool = False
    # Если True — вопрос со СВОБОДНЫМ ответом: кнопок вариантов нет, юзер
    # пишет ответ обычным сообщением (TG + MAX). Движок запоминает ожидание
    # в <slug>_survey_responses.awaiting_step, ловит следующее текстовое
    # сообщение юзера, кладёт его в answers[survey_qid] и идёт в survey_next.
    # survey_edit на таком шаге не применяется (ответ приходит сообщением,
    # редактировать нечего) — next_step всегда шлётся новым сообщением.
    survey_free: bool = False
    survey_next: str | None = None
    survey_done_button: str = "✅ Готово, продолжить"
    # Если True — при переходе к survey_next движок РЕДАКТИРУЕТ текущее сообщение
    # (editMessageText / MAX edit_message) вместо отправки нового. Работает только
    # для текстовых шагов (у вопросов нет медиа). Для multi-вопроса тап по варианту
    # ре-рендерит клавиатуру с галочками (editReplyMarkup). Default False —
    # старое поведение (каждый вопрос новым сообщением).
    survey_edit: bool = False

    # ── Параллельные независимые таймеры ──
    # Мapping step_key → delay_seconds. Запускаются параллельно с on_done,
    # имеют свои job_id и не сбиваются если on_callback срабатывает.
    also_fire: dict[str, int] = {}

    # ── Jump only ──
    jump_to: str | None = None

    # ── Absolute-time scheduling (для пушей привязанных ко времени эфира) ──
    # absolute_time_msk: "HH:MM" — момент МСК. Применяется к
    # webinar_date юзера (плюс day_offset). Перебивает on_done_after_seconds
    # на ВХОДЕ в шаг (delay = target_msk - now).
    absolute_time_msk: str | None = None
    day_offset: int = 0
    skip_if_past: bool = True
    # Шаг с временем (absolute_time_msk / webinar_offset_minutes) уходит НЕ
    # РАНЬШЕ, чем через столько секунд от момента, когда его запланировали.
    # Касается и будущего часа, и прошедшего при `skip_if_past: false`
    # (тогда вместо 2-минутного стаггера). Пропуск по `skip_if_past: true`
    # не меняется. 0 = прежнее поведение.
    #
    # Зачем: голову дня армируют на входе пачкой через `also_fire`, и шаг «в
    # 9:00» влезал ПОСРЕДИ регистрационной цепочки – у зашедшего в 8:55 в
    # 9:00, а у зашедшего днём (час прошёл) через 2 минуты. Так в пятидневке
    # evergreen-запуска кружок первого дня приходил между приветствием и следующим
    # сообщением регистрации, а в лидмагнитной – сразу за кружком регистрации
    # (23.09.2026). «Не раньше, чем кончится регистрация» ставит его после
    # неё, а армирование остаётся на входе: один раз и без зависимости от
    # того, доехала ли регистрационная цепочка.
    min_delay_seconds: int = 0

    # webinar_offset_minutes — СМЕЩЕНИЕ ОТ ЭФИРА участника вместо часа по
    # стенным часам. «За час до эфира» = -60, «через 17 минут эфира» = +17.
    # Перебивает absolute_time_msk, если заданы оба.
    #
    # Зачем: в воронке с несколькими ветками (эфир в 13:00 или в 18:00 —
    # по времени регистрации) у сообщений тексты ОБЩИЕ, а расходятся только
    # времена. С absolute_time_msk ветку приходится копировать целиком, и
    # дальше копии расходятся на первой правке текста (маркетолог правит
    # одну из двух). Со смещением обе ветки живут на ОДНОЙ цепочке шагов.
    #
    # anchor выбирает точку отсчёта:
    #   webinar (по умолчанию) — сам эфир дня;
    #   repeat                 — его повтор (branch.repeat_offset_minutes).
    # day_offset работает в обоих случаях: +N суток от дня эфира.
    webinar_offset_minutes: int | None = None
    anchor: Literal["webinar", "repeat"] = "webinar"
    # Смещение СВОЁ ДЛЯ КАЖДОЙ ВЕТКИ: {"DAY": -190, "EVE": -130}. Нужно
    # утренним сообщениям с часом по стенным часам в каждой ветке («в 9:50
    # для тех, кто на 13:00, и в 15:50 для тех, кто на 18:00» –
    # evergreen-воронка): от эфира это разные расстояния, одним числом не задать.
    # Ключ – id ветки из branch_router. Ветки нет в словаре (или участник без
    # ветки, VK) – берётся webinar_offset_minutes, поэтому его задают вместе.
    webinar_offset_by_branch: dict[str, int] | None = None
    # День СВОЙ ДЛЯ КАЖДОЙ ВЕТКИ: {"DAY": 5, "EVE": 6}. Нужно шагам с часом
    # по стенным часам, которые идут ПОСЛЕ повтора последнего дня: у вечерней
    # ветки повтор уезжает на следующие сутки, и общий день оказывается уже
    # в прошлом – шаг уходит пачкой следом за предыдущим вместо своего утра
    # (финальный оффер evergreen-воронки: три письма за час вместо трёх дней).
    # Ключ – id ветки из branch_router; ветки нет в словаре – берётся day_offset.
    day_offset_by_branch: dict[str, int] | None = None

    @model_validator(mode="after")
    def _day_offset_by_branch_needs_abs(self):
        # Смысл имеет только у шага с часом по стенным часам: у шага со
        # смещением от эфира день уже приходит из ветки участника.
        if self.day_offset_by_branch and not self.absolute_time_msk:
            raise ValueError(
                f"{self.key}: day_offset_by_branch применим только к шагу "
                "с absolute_time_msk")
        return self

    @model_validator(mode="after")
    def _offset_by_branch_needs_base(self):
        # Раннеры выбирают путь «время от эфира» по webinar_offset_minutes;
        # без него словарь по веткам молча не сработал бы.
        if self.webinar_offset_by_branch and self.webinar_offset_minutes is None:
            raise ValueError(
                f"{self.key}: webinar_offset_by_branch требует webinar_offset_minutes "
                "(запасное значение для участника без ветки)")
        return self

    # ── Bizon attendance gate ──
    # Перед send проверяется attendance юзера в комнате. "was" — шаг
    # шлётся только если был на эфире, "was_not" — только если НЕ был.
    # Окно проверки: webinar_dt..webinar_dt+gate_window_hours (default 4).
    gated_by_attendance: Literal["was", "was_not"] | None = None
    bizon_room: str | None = None
    # Комната ЭТОГО шага для MAX-участников. Пусто – работает как раньше:
    # webinar.bizon_room_max запуска, иначе bizon_room шага.
    #
    # ⚠️ Нужно воронкам, где эфиров БОЛЬШЕ ОДНОГО (пятидневка: пять залов в
    # TG и пять в MAX). Зал уровня запуска там один на все дни, и гейт
    # «был на эфире третьего дня» для MAX-юзера проверялся бы по залу
    # первого — то есть всегда мимо (§25 в CLAUDE.md: разводя платформы по
    # залам, переключать надо ВСЕ места сразу, а не только ссылку).
    bizon_room_max: str = ""
    gate_window_hours: int = 4

    # ── Survey gate (для followup'а после GetCourse-опроса) ──
    # Если True и participant.survey_done=True — шаг скипается и сразу
    # отрабатывает on_done. Используется для conditional follow-up'а
    # «Вы почти закончили опрос» — он не должен прийти если юзер уже прошёл.
    gated_by_survey_done: bool = False

    # ── Paid gate (VIP-дожимы не шлём оплатившим) ──
    # Если True и participant.vip_paid_at задан — шаг скипается, сразу
    # on_done (если есть). Применяется к VIP-продающим шагам (s_vip_circle,
    # s_vip_conditions): человек уже купил VIP → дожим не нужен.
    gated_by_not_paid: bool = False

    # ── Paid gate INVERSE — шаг ТОЛЬКО для оплативших VIP ──
    # Если True и participant.vip_paid_at НЕ задан — шаг скипается (сразу
    # on_done если есть). Для пост-эфирных бонусов оплатившим (запись/конспект).
    gated_by_paid: bool = False

    # ── Course gate (каскад автовебов) ──
    # Если True и participant.course_paid_at задан – шаг скипается. Ставится
    # на шаги ДОЖИМАЮЩЕГО вебинара: купившему основной продукт второй автовеб
    # не нужен. Отметку об оплате ставит services/course_sync (launch.course_sync).
    gated_by_not_course_paid: bool = False

    # ── Course gate INVERSE – шаг только для купивших курс ──
    gated_by_course_paid: bool = False

    # ── Channel-join gate ──
    # Гейт по факту вступления в закрытый канал (cfg.channel.chat_id, через
    # chat_member → Participant.channel_joined_at). "joined" — шаг шлётся
    # только если юзер УЖЕ в канале; "not_joined" — только если ещё НЕ вступил.
    # Если гейт не пройден — шаг скипается, сразу on_done (если задан).
    # Пример: напоминание «не перешёл в канал» через 2 мин — not_joined.
    gated_by_channel_joined: Literal["joined", "not_joined"] | None = None

    # ── LM-gift gate (tripwire flow) ──
    # Если True и participant.lm_post_id указан, но НЕ присутствует в
    # launches.<slug>.lm_meta.LM_GIFTS (т.е. NO-LM пост) — шаг скипается,
    # сразу on_done. Используется для s_t1_02_gift в trip_<seg>.yaml,
    # чтобы 2829/2768/2760/2725 не получали выдачу гайда.
    # Для пустого/None lm_post_id (default /start trip) — шаг шлётся
    # (есть дефолтный гайд «7 новых способов» с cfg.webinar.gift_url).
    gated_by_has_lm: bool = False

    # ── Per-LM картинка гайда (tripwire flow) ──
    # Если True — на шаге выдачи (s_t1_02_gift) картинка берётся не из
    # `image`, а из launches.<slug>.lm_meta.resolve_image(participant.lm_post_id)
    # (img под конкретный лидмагнит). Если LM-картинки нет — фоллбэк на `image`.
    # Читают ОБА sender'а (TG + MAX) — картинка гайда одинаковая на обеих платформах.
    image_lm: bool = False

    # ── MAX-only гейт подписки на канал (tripwire flow) ──
    # ⚠️ Читает ТОЛЬКО max_runner. TG-runner это поле ИГНОРИРУЕТ — в TG
    # проверка подписки делается в канале (channel_subscribe_gate), а гайд
    # внутри бота выдаётся сразу. В MAX канального gate нет, поэтому на шаге
    # выдачи проверяем подписку в боте: не подписан → «🎁 подарок готов,
    # подпишитесь» + recheck-кнопка, цепочка на паузе; подписан → выдаём гайд.
    max_subscription_gate: bool = False

    # ── Link-click gate (для тест-драйв push'ей в Day-3) ──
    # Если задан label_prefix — шаг скипается если у юзера уже есть
    # хотя бы один LinkClick по TrackedLink с label, начинающимся на
    # этот префикс. Используется чтобы прекратить push'и (d3_push_*)
    # после клика на «👉 Изучить уроки» в d3_lessons_cta.
    # Пример: gated_by_no_link_click: "funnel_s_d3_" — skip если был
    # клик по ЛЮБОЙ Day-3 ссылке (l_funnel_s_d3_10, s_d3_11, ...).
    gated_by_no_link_click: str = ""

    # ── Manager-DM gate (грант-воронка: дожимы до диалога с менеджером) ──
    # Если True — шаг скипается, когда человек УЖЕ написал менеджеру в личку
    # (Telegram Business другого сервиса, см. features.manager_dm). Признак —
    # запись в `<manager>_user_actions`, а не клик по ссылке: человек может
    # открыть посадку и не дойти до директа, и тогда дожимы обязаны идти.
    # Читают оба раннера, но источники разные: TG смотрит переписку с
    # менеджером (features.manager_dm), MAX – факт заявки в самом боте
    # (features.manager_dm_max); в MAX у человека нет telegram_id, по
    # которому менеджерскую переписку можно было бы найти.
    gated_by_no_manager_dm: bool = False

    # ── Гейт по часу регистрации ──
    # Не слать шаг тем, кто зарегистрировался в эти часы МСК: окна
    # "HH:MM-HH:MM", левая граница включается, правая нет ("11:00-12:00" =
    # 11:00…11:59). Шаг пропускается, on_done планируется как обычно – цепочка
    # не рвётся. Время регистрации = последняя отправка ВХОДНОГО шага этой
    # воронки человеку (entry_step веток branch_router и entry_variants), см.
    # funnel/reg_window.py. Не нашли его или упала база – шаг ШЛЁМ.
    #
    # Зачем (evergreen-воронка): кто пришёл за час до эфира,
    # получал «посмотрите на скриншоты» и «познакомимся поближе» вперемешку
    # со звонком и «через час начнём», а утренние 9:50 / 11:30 первого дня –
    # через несколько минут после регистрации.
    # Читают TG- и MAX-раннеры, VK – нет.
    skip_if_registered_msk: list[str] = []

    @model_validator(mode="after")
    def _registered_windows_valid(self):
        # Кривое окно должно ронять предполёт, а не молча не срабатывать в бою.
        from studio.engine_ref.reg_window import parse_windows
        try:
            parse_windows(self.skip_if_registered_msk)
        except ValueError as e:
            raise ValueError(f"{self.key}: skip_if_registered_msk – {e}") from None
        return self

    # Клейм-кнопка обычно ОТМЕНЯЕТ запланированный on_done (тап = «идём дальше
    # сейчас»). true → таймер остаётся: тап открывает ветку on_callback, а
    # основная цепочка едет своим ходом. Нужно там, где кнопка висит на каждом
    # шаге серии дожимов: без флага человек, нажавший её на пятом дожиме и
    # бросивший ветку на полпути, замолкал бы навсегда. Читает MAX-раннер.
    claim_keeps_chain: bool = False

    # ── DND exemption ──
    # Если True — шаг всегда шлётся, даже если планируется в DND-окно
    # cfg.features.dnd. Для STEP'ов которые «бонус-доставка» (welcome,
    # PDF-выдача) — должны прийти моментально по обещанию текста.
    # Default False — обычные follow-up'ы переносятся.
    dnd_exempt: bool = False

    # ── Chain to another funnel ──
    # Если задано — после отправки шага (с учётом on_done_after_seconds
    # как задержки) запускается engine.<name>_funnel_runner.start_for_tg.
    # Поведение взаимно исключает on_done — если задано и то и другое,
    # chain имеет приоритет, on_done игнорируется.
    # Значения: "default" → engine.funnel_runner, "alt" → engine.alt_funnel_runner.
    # Используется extra-funnel'ами для prefix-доставки бонуса перед
    # основной воронкой (например LM-посты → 2 шага гифта → основная воронка).
    chain_to_funnel: str = ""

    # Если True И chain_to_funnel задан И delay > 0 — runner вышлет
    # `sendChatAction(typing)` сразу после отправки этого шага, чтобы
    # юзер видел «бот печатает» в ожидании chain-целевого сообщения.
    # Telegram chat_action длится ~5 сек, поэтому имеет смысл при delay 2-5с.
    typing_before_chain: bool = False


# ── Branch router (выбор entry-step при /start по времени МСК) ──

class BranchWindow(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    from_: str = Field(alias="from")  # "HH:MM"
    to: str                            # "HH:MM" (включительно)
    # today/tomorrow — webinar_date относительно даты регистрации.
    # fixed — webinar_date = календарная дата `date` (фикс-дата эфира,
    # одна для всех регистраций; для launch'ей с конкретной датой практикума).
    day: Literal["today", "tomorrow", "fixed"] = "today"
    date: str | None = None  # "YYYY-MM-DD" — обязателен при day: fixed


class FunnelBranch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    entry_step: str
    webinar_time: str  # "HH:MM" МСК
    windows: list[BranchWindow]
    # На сколько минут позже эфира идёт его ПОВТОР в этой ветке. Точка
    # отсчёта для шагов с anchor: repeat.
    #   дневная ветка (эфир 13:00): повтор в тот же день в 18:00 → 300;
    #   вечерняя  (эфир 18:00): повтор назавтра в 13:00        → 1140.
    # 0 = повтора у ветки нет (шаги с anchor: repeat совпадут с эфиром).
    repeat_offset_minutes: int = 0


class BranchRouter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["time_of_day_msk"] = "time_of_day_msk"
    branches: list[FunnelBranch]

    def pick(self, now_msk_hhmm: str) -> tuple[FunnelBranch, BranchWindow] | None:
        """Возвращает (branch, window) для текущего времени МСК "HH:MM"."""
        def hm_to_min(s: str) -> int:
            h, m = s.split(":")
            return int(h) * 60 + int(m)

        now_min = hm_to_min(now_msk_hhmm)
        for br in self.branches:
            for w in br.windows:
                fr = hm_to_min(w.from_)
                to = hm_to_min(w.to)
                if fr <= now_min <= to:
                    return br, w
        return None


class FunnelWebinarRoom(BaseModel):
    """Свой зал эфира у отдельной воронки запуска.

    Зачем: `webinar.stream_url` в launch.yaml – ОДИН на весь запуск, развести
    его можно было только по платформам (`stream_url_max`). А когда под
    отдельный источник трафика заводят отдельную комнату Bizon (чтобы считать
    его доходимость и продажи в чистоте), делить надо по ВОРОНКЕ.

    ⚠️ Зал переключается сразу в двух местах, и порознь их задавать нельзя
    (граблевик §25): `stream_url` – куда зовём человека, `bizon_room` – где
    потом ищем «был ли он на эфире» (гейты was / was_not и мейл-каскад).
    Задать только первое = звать в новый зал, а повторы и письма слать по
    старому: человек отсидел эфир, а бот считает, что он не пришёл.

    Ограничение: зал ОДИН на воронку, без per-платформенного деления. Воронка
    с таким блоком заводится под конкретный источник, а у источника платформа
    обычно одна. Понадобится делить внутри – добавлять сюда `stream_url_max`
    и учить `max_runner` (там SMS-шаг берёт ссылку мимо render_text).
    """
    model_config = ConfigDict(extra="forbid")

    stream_url: str = ""
    bizon_room: str = ""
    # Зал родительской воронки, который подменяем. Нужен, когда зал прописан
    # у шагов руками (`bizon_room:` на каждом гейте) – без этого гейты
    # остались бы в старом зале, и человек, отсидевший эфир, для бота бы там
    # не появился: пошли бы повторы и письма «вы пропустили».
    #
    # Почему не затираем все залы подряд: у части воронок платформы залов
    # честно два (эфир + разбор), и слепая замена сломала бы второй. Поэтому
    # подмена явная: пусто – только заполняем шаги без зала.
    replaces_room: str = ""


class FunnelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    steps: list[FunnelStep]
    branch_router: BranchRouter | None = None
    # Свой зал эфира у этой воронки (см. FunnelWebinarRoom). Пусто – зал
    # запуска из launch.yaml, как у всех.
    webinar_room: FunnelWebinarRoom | None = None
    # Свой входной шаг для отдельной когорты трафика (см. EntryVariant).
    # Пусто – вход как всегда, через branch_router / первый шаг.
    entry_variants: list[EntryVariant] = []
    # Имя каскада писем для ЭТОЙ воронки – ключ в `air_email_profiles`
    # launch.yaml. Пусто – общий `air_email` запуска. Нужно, когда в одном
    # боте живут воронки с разной сеткой эфиров: у автовеба один эфир с
    # повторами, у пятидневки пять разных занятий, и один каскад на двоих
    # звал бы половину людей не в тот день. См. services/air_email.py.
    air_email_profile: str = ""
    # Подписывать ли кружки в MAX текстом шага. Круглых видео в MAX нет, и
    # текст туда физически влезает – но в воронках, где на кружке человек
    # ГОВОРИТ ровно то, что написано в text, это дубль: сначала послушал,
    # потом прочитал то же самое. В TG такого не бывает: у video_note нет
    # caption, и text там не отправляется вовсе.
    #   true  (по умолчанию) – как было; нужно MAX-only воронкам, где текст
    #                          кружка вообще единственный ;
    #   false – паритет с TG: в MAX уходит только видео.
    # Настройка уровня воронки, а не шага: иначе следующий добавленный кружок
    # снова притащит дубль, потому что про поле забудут.
    max_video_note_caption: bool = True
    # Повторный /start в окне дедупа (входной шаг ушёл <90 с назад, таймеры
    # живы) по умолчанию молча игнорируется – защита цепочки от инцидента
    # 05.08.2026. true – входной шаг переотправляется ещё раз, таймеры НЕ
    # трогаются. Нужно ботам-выдачам (подарки по кнопкам): человек жмёт
    # /start и ждёт сообщение, тишина выглядит как сломанный бот.
    resend_entry_on_repeat: bool = False

    def get_step(self, key: str) -> FunnelStep | None:
        for s in self.steps:
            if s.key == key:
                return s
        return None

    @property
    def first_step(self) -> FunnelStep | None:
        return self.steps[0] if self.steps else None


def _sub_in_step(step: FunnelStep, placeholder: str, value: str) -> FunnelStep:
    """Копия шага, где placeholder заменён во ВСЕХ строковых полях.

    Обходим дамп рекурсивно, а не перечисляем поля руками: ссылка на эфир
    живёт и в тексте, и в url кнопок, и в их MAX-вариантах, и список этих
    мест растёт с каждой новой фичей шага. Перечисление молча пропустило бы
    новое поле, и человек ушёл бы в чужой зал по одной забытой кнопке.
    """
    def walk(x: Any) -> Any:
        if isinstance(x, str):
            return x.replace(placeholder, value)
        if isinstance(x, list):
            return [walk(i) for i in x]
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items()}
        return x

    return FunnelStep(**walk(step.model_dump()))


def _apply_webinar_room(cfg: FunnelConfig) -> None:
    """Разворачивает `webinar_room` воронки в поля её шагов.

    Делаем на загрузке, а не на отправке: тогда зал понимают ВСЕ, кто работает
    со шагом – сендеры TG/MAX/ВК, тест-прогон, /review и гейты посещения.
    Иначе ссылку пришлось бы прокидывать через десяток вызовов render_text, и
    первый же забытый увёл бы человека в зал запуска.

    `bizon_room` шага сильнее: он задан руками для конкретного шага.
    """
    room = cfg.webinar_room
    if room is None:
        return
    if room.stream_url:
        cfg.steps = [_sub_in_step(s, "{stream_url}", room.stream_url)
                     for s in cfg.steps]
    if room.bizon_room:
        for s in cfg.steps:
            if not s.bizon_room or s.bizon_room == room.replaces_room:
                s.bizon_room = room.bizon_room


def _read_funnel_data(path: Path, _seen: set[Path] | None = None) -> dict[str, Any]:
    """YAML воронки с раскрытым `extends` (наследование от другой воронки).

    Зачем: под отдельный источник трафика нужна «та же воронка, но в своём
    зале». Физическая копия на сотню шагов расходится с оригиналом на первой
    же правке – этим граблям в проекте посвящено несколько разборов. С
    `extends` копия состоит из тега и блока зала, а шаги всегда те же, что у
    родителя, по построению.

    Ключи ребёнка перебивают родительские целиком (список шагов – тоже, если
    его задать). Путь – относительно файла-ребёнка.
    """
    path = path.resolve()
    _seen = _seen or set()
    if path in _seen:
        raise ValueError(f"циклический extends в воронке: {path}")
    _seen.add(path)

    with open(path, "r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}

    parent = data.pop("extends", None)
    if not parent:
        return data
    base = _read_funnel_data((path.parent / parent), _seen)
    base.update(data)
    return base


def load_funnel(path: str | Path) -> FunnelConfig:
    p = Path(path)
    cfg = FunnelConfig(**_read_funnel_data(p))
    _apply_webinar_room(cfg)
    # Настройку воронки «кружки в MAX без подписи» разворачиваем в уже
    # существующее поле шага text_max – тогда её понимают все, кто шлёт шаги
    # (sender, тест-прогон, /review), и прокидывать воронку никуда не нужно.
    # Явный text_max на шаге сильнее: он задан руками, значит так и хотели.
    if not cfg.max_video_note_caption:
        for s in cfg.steps:
            if s.type == "video_note" and s.text_max is None:
                s.text_max = ""
    return cfg
