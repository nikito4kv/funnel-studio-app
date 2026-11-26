# VENDORED из bot-engine: common/launch_engine/funnel/render.py
# Коммит bot-engine: dd105332b3ddf41a2edab574d6605ea381d55283 (файл-источник не менялся с f171a355)
# Правки против оригинала (подстановки те же):
#   1. `cfg` – любой объект с теми же полями, что LaunchConfig (slug,
#      display_name, channel.*, webinar.*, infra.*); в движке Студии это
#      runtime.settings.RenderCfg – импорт LaunchConfig убран;
#   2. {vip_link}: персональный прокси /govip строится, только если у запуска
#      задан infra.base_url (прокси живёт в bot-engine). Иначе – прямая
#      ВИП-страница (webinar.vip_target): кнопка с адресом «/govip?…» без
#      домена уронила бы Telegram-сообщение целиком.
"""Подстановка template-токенов в текстах и URL-ах.

Поддерживается:
- {Имя}, {имя}                       — first_name юзера (fallback "друг")
- {channel.link}, {channel.title}     — канал из cfg.channel (per-platform)
- {webinar.unix}                      — unix-stamp эфира (per-user если
                                        передан webinar_dt, иначе cfg)
- {webinar.datetime_msk}              — строка "YYYY-MM-DDTHH:MM" из cfg
- {webinar.time}                      — "HH:MM" эфира (per-user если
                                        webinar_dt, иначе из cfg.webinar.datetime_msk)
- {webinar.date}                      — "DD.MM" эфира (per-user)
- {webinar.main_time}                 — "HH:MM" ОСНОВНОГО эфира дня, даже
                                        если сам шаг привязан к повтору
- {webinar.main_unix}                 — unix-stamp того же момента
- {stream_url}                        – cfg.webinar.stream_url (per-platform)
- {gift_url}                          — cfg.webinar.gift_url
- {slug}, {display_name}

Для MAX/VK можно задать channel.max_link / channel.vk_link — тогда
{channel.link} рендерится в платформенную ссылку для соответствующего
канала. Если в одном сообщении нужны обе ссылки сразу (кнопки «в Telegram»
и «в MAX» рядом) — есть явные {channel.tg_link} / {channel.max_link} /
{channel.vk_link}, они не зависят от платформы читателя.

Так же устроен и {stream_url}: если у запуска задан webinar.stream_url_max,
MAX-юзер получает ссылку на свой зал Bizon, TG/ВК – на основной.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from typing import Any as LaunchConfig  # см. шапку: cfg – утиный тип


def render_text(
    text: str,
    cfg: LaunchConfig,
    user_first_name: str | None = None,
    platform: str = "tg",
    *,
    webinar_dt: datetime | None = None,
    webinar_main_dt: datetime | None = None,
    tg_id: int | None = None,
    max_id: int | None = None,
    vk_id: int | None = None,
    lm_title: str | None = None,
    lm_url: str | None = None,
    lm_intro: str | None = None,
    lm_post_id: str | None = None,
    utm: dict | None = None,
    step_key: str | None = None,
    ticket: dict | None = None,
    diploma: dict | None = None,
) -> str:
    """Подставляет token'ы в текст.

    webinar_dt: timezone-aware datetime МСК — момент эфира конкретного юзера.
    Если None, используются cfg.webinar.* (legacy-режим).
    webinar_main_dt: момент ОСНОВНОГО эфира того же дня — для шагов повтора,
    где текст зовёт на завтрашний эфир («встречаемся завтра в 13:00»), а не на
    повтор, внутри которого шаг отправляется. Если None, {webinar.main_time}
    равен {webinar.time} — то есть у всех прежних воронок ничего не меняется.
    tg_id: для подстановки {tg_id} (используется в URL'ах опроса, и т.п.).
    lm_title / lm_url: per-user LM-источник для трипваер-flow
    (передаются sender'ом из participant.lm_post_id через mapping).
    Если None/пусто — fallback на дефолт (для /start trip без LM-поста).
    """
    if not text:
        return text
    name = (user_first_name or "друг").strip() or "друг"
    # Per-platform channel link override
    channel_link = cfg.channel.link
    if platform == "max" and cfg.channel.max_link:
        channel_link = cfg.channel.max_link
    elif platform == "vk" and cfg.channel.vk_link:
        channel_link = cfg.channel.vk_link

    # Per-user webinar tokens (если передан webinar_dt)
    if webinar_dt is not None:
        webinar_unix = str(int(webinar_dt.timestamp()))
        webinar_time = webinar_dt.strftime("%H:%M")
        webinar_date = webinar_dt.strftime("%d.%m")
        webinar_dtstr = webinar_dt.strftime("%Y-%m-%dT%H:%M")
    else:
        webinar_unix = str(cfg.webinar.unix)
        webinar_dtstr = cfg.webinar.datetime_msk
        webinar_time = ""
        webinar_date = ""
        if cfg.webinar.datetime_msk:
            try:
                _dt = datetime.fromisoformat(cfg.webinar.datetime_msk)
                webinar_time = _dt.strftime("%H:%M")
                webinar_date = _dt.strftime("%d.%m")
            except Exception:
                pass

    # Основное время дня. Шаг повтора получает webinar_dt = время ПОВТОРА,
    # и {webinar.time} там печатает его – это верно для «начинаем через 5
    # минут», но неверно для «встречаемся завтра»: завтрашний эфир идёт в
    # основное время. Отсюда отдельная пара токенов.
    if webinar_main_dt is not None:
        webinar_main_time = webinar_main_dt.strftime("%H:%M")
        webinar_main_unix = str(int(webinar_main_dt.timestamp()))
    else:
        webinar_main_time, webinar_main_unix = webinar_time, webinar_unix

    # Human-readable «14 мая в 13:00 МСК» (per-user) — для fallback'а
    # внутри <tg-time>. Mac-safe форма: БЕЗ format="r", fallback date-like
    # OK (см. tg_time_cheatsheet). Длина ≤30 chars (ENTITY_DATE_TOO_LONG).
    _MONTHS_RU = (
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    )
    webinar_short = ""
    webinar_tgtime = ""
    if webinar_dt is not None:
        try:
            ws = f"{webinar_dt.day} {_MONTHS_RU[webinar_dt.month - 1]} в {webinar_dt.strftime('%H:%M')} МСК"
            webinar_short = ws
            webinar_tgtime = f'<tg-time unix="{webinar_unix}">{ws}</tg-time>'
        except Exception:
            pass

    # Platform tag — для атрибуции платформы в deep-link payload'ах
    # (handoff в другой бот через t.me/...?start=<tag>). TG → пусто (back-compat),
    # VK → "_vk", MAX → "_max". Используется в yaml-URLs кнопок:
    #   url: "https://t.me/<bot>?start=promoreg_launchb{platform_tag}_d09"
    # → TG юзеры видят `promoreg_launchb_d09`, VK → `promoreg_launchb_vk_d09`.
    # Принимающий бот может различить платформу источника.
    platform_tag = ""
    if platform == "vk":
        platform_tag = "_vk"
    elif platform == "max":
        platform_tag = "_max"

    out = text
    out = out.replace("{Имя}", name)
    out = out.replace("{имя}", name.lower())
    out = out.replace("{platform_tag}", platform_tag)
    out = out.replace("{slug}", cfg.slug)
    out = out.replace("{display_name}", cfg.display_name)
    out = out.replace("{channel.link}", channel_link)
    # Явные per-платформенные ссылки — когда в ОДНОМ сообщении нужны обе
    # кнопки сразу («забрать подарки в Telegram» / «в MAX»), независимо от
    # того, с какой платформы читает юзер. {channel.link} остаётся
    # платформо-зависимым (старое поведение не меняется).
    out = out.replace("{channel.tg_link}", cfg.channel.link)
    out = out.replace("{channel.max_link}", cfg.channel.max_link or cfg.channel.link)
    out = out.replace("{channel.vk_link}", cfg.channel.vk_link or cfg.channel.link)
    out = out.replace("{channel.title}", cfg.channel.title)
    out = out.replace("{webinar.unix}", webinar_unix)
    out = out.replace("{webinar.datetime_msk}", webinar_dtstr)
    out = out.replace("{webinar.time}", webinar_time)
    out = out.replace("{webinar.date}", webinar_date)
    out = out.replace("{webinar.main_time}", webinar_main_time)
    out = out.replace("{webinar.main_unix}", webinar_main_unix)
    # Зал эфира per-platform: если у запуска задан webinar.stream_url_max,
    # MAX-юзеров зовём в свою комнату (TG/ВК остаются на основной).
    out = out.replace("{stream_url}", cfg.webinar.stream_url_for(platform) or "")
    out = out.replace("{gift_url}", cfg.webinar.gift_url or "")
    # {vip_link} — per-user ВИП-прокси: логирует клик + редиректит на ВИП-страницу
    # с данными юзера. Если uid неизвестен — fallback на сырой адрес страницы
    # (webinar.vip_url, а при пустом — gift_url).
    #
    # `s=<step_key>` — ИЗ КАКОГО сообщения человек кликнул (сам оффер или
    # конкретный дожим). Прокси кладёт это в utm_content оплаты, поэтому в
    # AmoCRM у сделки видно, что именно её принесло. Без метки все продажи
    # сливаются в одну строку и дожимы невозможно оценить.
    _vip_uid = tg_id if platform == "tg" else (max_id if platform == "max" else vk_id)
    if _vip_uid and (cfg.infra.base_url or "").strip():
        _base = (cfg.infra.base_url or "").rstrip("/")
        _pref = cfg.infra.go_prefix or ""
        vip_link = f"{_base}{_pref}/govip?u={_vip_uid}&pl={platform}"
        if step_key:
            vip_link += f"&s={quote(str(step_key), safe='')}"
    else:
        vip_link = cfg.webinar.vip_target or ""
    out = out.replace("{vip_link}", vip_link)

    # ── Билеты розыгрыша ──────────────────────────────────────────────
    # Значения подставляет sender (он ходит в БД), сюда приходят готовыми.
    # Чистим плейсхолдеры ВСЕГДА, даже когда розыгрыш выключен: иначе человек
    # получит сообщение с буквальным «{ticket}» — а выключить розыгрыш могут
    # быстрее, чем вычистить его из текстов воронки.
    _t = ticket or {}
    out = out.replace("{ticket}", _t.get("number", "") or "")
    out = out.replace("{tickets_count}", str(_t.get("count", "") or ""))
    out = out.replace("{tickets_all}", _t.get("all", "") or "")
    out = out.replace("{ticket_image}", _t.get("image", "") or "")
    out = out.replace("{ref_link}", _t.get("ref_link", "") or "")
    out = out.replace("{game_url}", _t.get("game_url", "") or "")

    # ── Диплом ────────────────────────────────────────────────────────
    # Как и билет: значения приходят от sender'а готовыми, а плейсхолдеры
    # чистим ВСЕГДА – шаблон могут отключить быстрее, чем вычистить текст.
    _d = diploma or {}
    out = out.replace("{diploma_number}", _d.get("number", "") or "")
    out = out.replace("{diploma_code}", _d.get("code", "") or "")
    out = out.replace("{diploma_url}", _d.get("url", "") or "")
    out = out.replace("{diploma_image}", _d.get("image", "") or "")
    out = out.replace("{webinar.short}", webinar_short)
    out = out.replace("{webinar.tgtime}", webinar_tgtime)
    if tg_id is not None:
        out = out.replace("{tg_id}", str(tg_id))
    # {max_id} — id MAX-юзера (для survey-URL в MAX-флоу: ?u={max_id}&platform=max)
    if max_id is not None:
        out = out.replace("{max_id}", str(max_id))
    # {user_id} — платформо-агностичный id (tg_id ИЛИ max_id, что задано).
    # Для кросс-платформенных URL'ов в одном funnel.yaml (TG+MAX), напр.
    # survey: ?u={user_id}&platform={platform}. {platform} — 'tg'/'max'/'vk'.
    _uid = tg_id if tg_id is not None else max_id
    if _uid is not None:
        out = out.replace("{user_id}", str(_uid))
    out = out.replace("{platform}", platform)
    out = out.replace("{base_url}", cfg.infra.base_url or "")
    out = out.replace("{go_prefix}", cfg.infra.go_prefix or "")
    # {utm_source/medium/campaign/content/term} — метки, с которыми юзер
    # ЗАРЕГИСТРИРОВАЛСЯ (из participant.utm_*, sender передаёт через utm=…).
    # Для динамических offer/landing-ссылок: profesiavb?utm_source={utm_source}
    # …&id={tg_id} — сквозная атрибуция «с чем пришёл». Пусто → "" (не {utm_x}).
    _utm = utm or {}
    for _k in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"):
        out = out.replace("{" + _k + "}", str(_utm.get(_k) or ""))
    # LM-источник для трипваер-flow. Дефолт: «7 новых способов
    # заработка с Claude в 2026 году» (главный гайд) для /start trip без
    # LM-поста. URL по дефолту — cfg.webinar.gift_url (тоже главный гайд).
    _lm_title_final = (lm_title or "").strip() or "7 новых способов заработка с Claude в 2026 году"
    _lm_url_final = (lm_url or "").strip() or (cfg.webinar.gift_url or "")
    out = out.replace("{lm_title}", _lm_title_final)
    out = out.replace("{lm_url}", _lm_url_final)
    # {lm_post_id} — id поста-источника для UTM-атрибуции в URL'ах статьи.
    # Для /start trip без LM-поста → "trip" (метка дефолт-входа).
    _lm_post_id_final = (lm_post_id or "").strip() or "trip"
    out = out.replace("{lm_post_id}", _lm_post_id_final)
    # {src_suffix} — origin-маркер источника для ХЕНДОФФ deep-link'ов в ДРУГИЕ
    # боты (Уровень 2 сквозной атрибуции). '_xorig<lm_post_id>' если есть
    # реальный источник, иначе пусто. Принимающий бот вынимает токен
    # xorig<code> и кладёт code в utm_content — payload'ы БЕЗ xorig парсятся
    # байт-в-байт как раньше (зеро-риск для существующего трафика).
    # Уровень 2: источник (_xorig<code>). Уровень 3: + origin-личность
    # (_xid<plat><id>) для cross-platform стича (vk_id ↔ downstream tg_id в
    # принимающем боте). Эмитим только если есть реальный источник.
    _src_raw = (lm_post_id or "").strip()
    if _src_raw and _src_raw != "trip":
        _suf = f"_xorig{_src_raw}"
        _origin_uid = {"tg": tg_id, "vk": vk_id, "max": max_id}.get(platform)
        if _origin_uid:
            _suf += f"_xid{platform}{_origin_uid}"
        out = out.replace("{src_suffix}", _suf)
    else:
        out = out.replace("{src_suffix}", "")
    # {lm_intro} — абзац «Посмотрите короткий урок, пока готовится гайд "X" – бот пришлёт
    # его в течение 1 минуты.». Передаётся sender'ом: пустой для NO_LM_POSTS,
    # заполненный для LM_GIFTS и default trip flow.
    # Если пуст ("" для NO_LM) — удаляем плейсхолдер ВМЕСТЕ с его строкой,
    # чтобы не оставалось двойных пустых строк в выходе.
    if lm_intro == "":
        # Удаляем плейсхолдер + следующую пустую строку (для NO_LM_POSTS:
        # без абзаца про гайд и без зияния из 2 пустых строк).
        out = out.replace("{lm_intro}\n\n", "")
        out = out.replace("{lm_intro}\n", "")
        out = out.replace("{lm_intro}", "")
    else:
        if lm_intro is None:
            _lm_intro_final = (
                f'Посмотрите короткий урок, пока готовится гайд "{_lm_title_final}" – '
                f'бот пришлет его в течение 1 минуты.'
            )
        else:
            _lm_intro_final = lm_intro.rstrip("\n")  # strip trailing newlines
        out = out.replace("{lm_intro}", _lm_intro_final)
    return out
