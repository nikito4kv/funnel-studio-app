# VENDORED из bot-engine: common/launch_engine/deep_link.py
# Коммит bot-engine: dd105332b3ddf41a2edab574d6605ea381d55283 (файл-источник не менялся с f171a355)
# Скопировано как есть. Глобальный реестр брендовых префиксов
# (register_brand_prefix) движок Студии НЕ использует: префиксы – настройка
# запуска, а не состояние процесса (в одном процессе много запусков).
"""Парсер /start payload — общий для TG, VK, MAX.

Поддерживаемые форматы (см. the platform's start handler):
  ref_<CODE>                     → реферальная ссылка (trigger='ref')
  reg_<SRC>_<MED>_<CMP>_<C>_<T>  → UTM-трекинг (trigger='reg')
  utm_source=X&utm_medium=Y      → query-string UTM
  <slug>_<SRC>_<MED>_...         → бренд-префикс (trigger=slug)
  пусто / неизвестно             → trigger='direct'
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ParsedDeepLink:
    trigger: str = "direct"
    referrer_code: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None
    utm_term: str | None = None
    # qid — id квиз-сессии лендинга (quiz_<seg>_t<time>_<qid>). Нужен для
    # сквозной аналитики: связывает визит/ответы квиза (launchb_lp_events,
    # vid=qid) с регистрацией в боте (launchb_quiz_picks по telegram_id).
    qid: str | None = None


_KNOWN_BRAND_PREFIXES: set[str] = set()


def register_brand_prefix(prefix: str) -> None:
    """Запуск может зарегистрировать свой брендовый префикс
    (например, 'promo_' → trigger='promo' с UTM-разбором хвоста)."""
    _KNOWN_BRAND_PREFIXES.add(prefix.rstrip("_"))


def parse_deep_link(payload: str | None) -> ParsedDeepLink:
    """Обёртка: ДО разбора вынимает зарезервированные ХЕНДОФФ-токены
    `_xorig<code>` (источник → utm_content), `_xid<plat><id>` (origin-личность,
    Уровень 3 — ребро стича пишет принимающий бот) и `_xref<tid>` (партнёрская
    реф-метка @partners_bot: tid партнёра → referrer_code, позволяет
    совмещать UTM и рефералку в одном payload: reg_a_b_c_d_e_xref123) и
    стрипает их из payload. Payload'ы БЕЗ этих токенов разбираются ПОБАЙТНО
    как раньше (зеро-риск)."""
    import re as _re
    origin = None
    xref = None
    s = payload or ""
    # ЕРИД рекламной маркировки. В ссылку он ставится отдельным параметром
    # (…?start=reg_a_b&erid=2Vtz…) и до бота доходить не должен – его читает
    # рекламная площадка. Но если площадка или мессенджер склеят весь хвост в
    # start, payload приедет как `reg_a_b&erid=2Vtz…`, а это для разборщика
    # уже «строка с = и &» → trigger=direct и ВСЕ метки закупки теряются.
    # Поэтому вырезаем erid в любом виде до разбора: и через ?/&, и через _.
    if s and "erid" in s.lower():
        s = _re.sub(r"[?&]erid=[A-Za-z0-9_\-]+", "", s, flags=_re.I)
        s = _re.sub(r"_erid[_=][A-Za-z0-9\-]+", "", s, flags=_re.I)
        s = s.rstrip("?&_")
    if s and "_xorig" in s:
        m = _re.search(r"_xorig([A-Za-z0-9]+)", s)
        if m:
            origin = m.group(1)
            s = s[:m.start()] + s[m.end():]
    if s and "_xid" in s:
        m = _re.search(r"_xid(?:tg|vk|max)[0-9]+", s)
        if m:
            s = s[:m.start()] + s[m.end():]
    if s and "_xref" in s:
        m = _re.search(r"_xref([0-9]+)", s)
        if m:
            xref = m.group(1)
            s = s[:m.start()] + s[m.end():]
    out = _parse_deep_link_core(s if payload is not None else payload)
    if origin and not out.utm_content:
        out.utm_content = origin
    if xref and not out.referrer_code:
        out.referrer_code = xref
    return out


def _parse_deep_link_core(payload: str | None) -> ParsedDeepLink:
    p = (payload or "").strip()
    out = ParsedDeepLink()

    if not p:
        return out

    if "=" in p and ("&" in p or p.startswith("utm_")):
        out.trigger = "direct"
        for kv in p.split("&"):
            if "=" not in kv:
                continue
            k, v = kv.split("=", 1)
            k = k.strip()
            v = v.strip()
            if not v:
                continue
            if k == "utm_source":
                out.utm_source = v
            elif k == "utm_medium":
                out.utm_medium = v
            elif k == "utm_campaign":
                out.utm_campaign = v
            elif k == "utm_content":
                out.utm_content = v
            elif k == "utm_term":
                out.utm_term = v
        return out

    if p.startswith("ref_"):
        out.trigger = "ref"
        out.referrer_code = p[4:].strip() or None
        return out

    if p.startswith("reg_"):
        out.trigger = "reg"
        parts = p[4:].split("_")
        out.utm_source = parts[0] if len(parts) > 0 and parts[0] else None
        out.utm_medium = parts[1] if len(parts) > 1 and parts[1] else None
        out.utm_campaign = parts[2] if len(parts) > 2 and parts[2] else None
        out.utm_content = parts[3] if len(parts) > 3 and parts[3] else None
        out.utm_term = parts[4] if len(parts) > 4 and parts[4] else None
        return out

    # poreg_<SRC>_<MED>_<CMP>_<C>_<T> – «посевы», отдельная воронка с меткой
    # trigger='poreg'. Хвост раскладывается по UTM так же как у reg_:
    # одно-сегментный poreg_vkpost1 → utm_source=vkpost1, multi
    # poreg_1_2_3_4_5 → utm_source=1, utm_medium=2, utm_campaign=3,
    # utm_content=4, utm_term=5. В рассыльщике фильтруется отдельно от
    # обычных регистраций (кнопка «Посевы»).
    if p == "poreg" or p.startswith("poreg_"):
        out.trigger = "poreg"
        parts = p[6:].split("_") if p.startswith("poreg_") else []
        out.utm_source = parts[0] if len(parts) > 0 and parts[0] else None
        out.utm_medium = parts[1] if len(parts) > 1 and parts[1] else None
        out.utm_campaign = parts[2] if len(parts) > 2 and parts[2] else None
        out.utm_content = parts[3] if len(parts) > 3 and parts[3] else None
        out.utm_term = parts[4] if len(parts) > 4 and parts[4] else None
        return out

    # 5d[_<SRC>_<MED>_<CMP>_<C>_<T>] – вход в ВЕЧНОЗЕЛЁНУЮ ПЯТИДНЕВКУ
    # (evergreen auto-webinar funnel). Хвост – позиционные UTM
    # как у reg_.
    #
    # Зачем отдельная метка, а не тот же reg_: у запуска ДВЕ жизни в одном
    # боте. Ссылки живого мероприятия 27-31.08 разошлись по базам и рекламе,
    # их владельцы продолжают жать /start – им отвечает заглушка «всё
    # прошло». Пускать в автовеб надо только новый холодный трафик, и он же
    # один должен попадать в дашборд пятидневки. Различить эти две группы
    # можно ТОЛЬКО по ссылке входа: и там и там reg_-метки, одни и те же
    # источники, одна воронка.
    if p == "5d" or p.startswith("5d_"):
        out.trigger = "5d"
        parts = p[3:].split("_") if p.startswith("5d_") else []
        out.utm_source = parts[0] if len(parts) > 0 and parts[0] else None
        out.utm_medium = parts[1] if len(parts) > 1 and parts[1] else None
        out.utm_campaign = parts[2] if len(parts) > 2 and parts[2] else None
        out.utm_content = parts[3] if len(parts) > 3 and parts[3] else None
        out.utm_term = parts[4] if len(parts) > 4 and parts[4] else None
        return out

    # quiz_<seg>_t<time>_<qid> — вход с квиз-лендинга
    # (launch B). trigger='quiz' (отдельный учёт), seg→utm_medium (A–E),
    # time→utm_content (a–f, без префикса 't'), qid→ParsedDeepLink.qid.
    # Ветка ВЫШЕ brand-loop: id extra_funnel'а 'quiz' тоже регистрируется как
    # brand-prefix (core.register_brand_prefix), но явная ветка даёт чистую
    # семантику (utm_source='quiz', а не seg в utm_source).
    if p == "quiz" or p.startswith("quiz_"):
        out.trigger = "quiz"
        out.utm_source = "quiz"
        parts = p[5:].split("_") if p.startswith("quiz_") else []
        if len(parts) > 0 and parts[0]:
            out.utm_medium = parts[0]                       # сегмент A–E
        if len(parts) > 1 and parts[1]:
            tval = parts[1]
            out.utm_content = tval[1:] if tval[:1] == "t" else tval  # время a–f
        if len(parts) > 2 and parts[2]:
            out.qid = parts[2]                              # quiz-session id
        return out

    # pr_…nbpost<pid>[_<SRC>_<MED>_<CMP>_<C>_<T>] — ПРЯМЫЕ рекламные ссылки
    # на ЛМ-посты launch B (трафик мимо канала, 08.07.2026). База
    # pr_lm_launchb_nbpost<pid> матчит extra-funnel по nbpost<pid>; хвост
    # ПОСЛЕ nbpost<pid> — стандартные позиционные UTM (как у reg_).
    # trigger='pr' → в листах/дашбордах видно «прямой заход».
    if p.startswith("pr_"):
        import re as _re_pr
        m = _re_pr.search(r"nbpost\d+", p)
        if m:
            out.trigger = "pr"
            tail = p[m.end():].strip("_")
            parts = tail.split("_") if tail else []
            out.utm_source = parts[0] if len(parts) > 0 and parts[0] else None
            out.utm_medium = parts[1] if len(parts) > 1 and parts[1] else None
            out.utm_campaign = parts[2] if len(parts) > 2 and parts[2] else None
            out.utm_content = parts[3] if len(parts) > 3 and parts[3] else None
            out.utm_term = parts[4] if len(parts) > 4 and parts[4] else None
            return out

    # c7pryam[_<SRC>_<MED>_<CMP>_<C>_<T>] — прямой заход на ЛМ «7 способов»
    # (замена поста 2772, воронка pryam7). Хвост = позиционные UTM.
    if p == "c7pryam" or p.startswith("c7pryam_"):
        out.trigger = "pr"
        parts = p[8:].split("_") if p.startswith("c7pryam_") else []
        out.utm_source = parts[0] if len(parts) > 0 and parts[0] else None
        out.utm_medium = parts[1] if len(parts) > 1 and parts[1] else None
        out.utm_campaign = parts[2] if len(parts) > 2 and parts[2] else None
        out.utm_content = parts[3] if len(parts) > 3 and parts[3] else None
        out.utm_term = parts[4] if len(parts) > 4 and parts[4] else None
        return out

    for brand in _KNOWN_BRAND_PREFIXES:
        prefix = f"{brand}_"
        if p.startswith(prefix):
            out.trigger = brand
            parts = p[len(prefix):].split("_")
            out.utm_source = parts[0] if len(parts) > 0 and parts[0] else None
            out.utm_medium = parts[1] if len(parts) > 1 and parts[1] else None
            out.utm_campaign = parts[2] if len(parts) > 2 and parts[2] else None
            out.utm_content = parts[3] if len(parts) > 3 and parts[3] else None
            out.utm_term = parts[4] if len(parts) > 4 and parts[4] else None
            return out

    return out
