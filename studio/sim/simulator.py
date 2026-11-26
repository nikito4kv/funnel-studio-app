"""Симулятор расписания: что и когда получит человек и что у него выпадет.

Логика планирования перенесена из движка метод в метод:

  bot-engine common/launch_engine/funnel/runner.py      (TG, коммит dd105332)
  bot-engine common/launch_engine/funnel/max_runner.py  (MAX, тот же коммит)
  + funnel/reg_window.py – гейт «зарегистрировался в эти часы»
  + handlers/contact.py, services/max_phone.py, services/email_capture.py,
    services/survey.py, handlers/survey.py – ответы человека.

Время шагов и ночной режим считают СКОПИРОВАННЫЕ файлы движка
(`engine/branch.py`, `engine/dnd.py`) – чтобы не переписывать правила времени
своими словами. Вместо APScheduler – своя очередь таймеров с той же
семантикой: id таймера `<шаг>:fire`, `replace_existing=True` (второй путь в
тот же шаг перезаписывает первый таймер), отмена по id. Вместо базы –
«участник» в памяти, условия берутся из персоны.

Правила движка, которые симулятор воспроизводит дословно (строки runner.py):
  * время шага от эфира/по часам перебивает задержку входящей стрелки,
    прошло и `skip_if_past` – шаг выпадает, сразу `on_done` (1261–1329);
  * прошло и `skip_if_past: false` – уходит через 120 с (_PAST_STAGGER_S);
  * `min_delay_seconds` – шаг со временем уходит не раньше, чем через N с от
    постановки (и вместо 120 с, если время прошло); пропуск по времени не меняет;
  * `skip_if_registered_msk` – регистрация (последняя отправка входного шага) в
    этих часах МСК: шаг пропускается, идёт `on_done`; не нашли – шлём (fail-open);
  * ночной режим после расчёта времени, только для задержки > 0 (1284–1289);
  * шаг под условием: планируется `on_done`, `also_fire` НЕ срабатывает (447–555);
  * `also_fire` без своего времени ставится напрямую – без ночного режима (688–707);
  * повтор шага в окне 90 с – «DUP», шаг не шлётся, чинится хвост цепочки (384–409);
  * вопрос опроса не двигает цепочку – ждём ответ (630–638);
  * переход в воронку важнее `on_done` (640–668); в MAX переходов нет вовсе;
  * кнопка-действие отменяет таймер `on_done`, если нет `claim_keeps_chain` (940–957).

Чего нет: сетевых сбоев, повторного /start, когорт (`entry_variants`),
game-модулей, писем каскада. Переход в другую воронку – конец прогона.
"""
from __future__ import annotations

import heapq
import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Callable

import pytz

from ..engine_ref.loader import FunnelConfig, FunnelStep
from ..engine_ref.reg_window import entry_step_keys, in_windows
from . import clock
from .engine.branch import branch_by_id, compose_webinar_dt, pick_branch_for_now, resolve_step_target
from .engine.dnd import apply_dnd_to_delay
from .launch import LaunchSettings
from .models import Persona, SimOptions

MSK = pytz.timezone("Europe/Moscow")

PAST_STAGGER_S = 120        # runner._PAST_STAGGER_S
DEDUP_WINDOW_S = 90         # runner._DEDUP_WINDOW_S
MAX_DEPTH = 300             # защита от бесконечной цепочки мгновенных переходов
MAX_JOBS = 20000

TYPE_LABEL = {
    "text": "текст", "photo": "картинка", "video_note": "кружок", "video": "видео",
    "animation": "GIF", "rich": "статья", "phone_request": "запрос телефона",
    "email_request": "запрос почты", "jump": "переход", "call": "звонок", "sms": "SMS",
    "air_pin": "закреп",
}

# Какие события считаются «выпало» (красная рамка), а какие – «не отправлено по условию».
DROP_KINDS = {"past_skip", "parallel_lost", "claim_cancel", "edit_end", "unknown_step"}


def _fmt(dt: datetime) -> str:
    return dt.astimezone(MSK).strftime("%d.%m %H:%M")


def _hm(dt: datetime) -> str:
    d = dt.astimezone(MSK)
    return d.strftime("%H:%M:%S") if d.second else d.strftime("%H:%M")


def _dur(s: int | float) -> str:
    s = int(s)
    if s < 60:
        return f"{s} с"
    if s < 3600:
        m, r = divmod(s, 60)
        return f"{m} мин" + (f" {r} с" if r else "")
    if s < 86400:
        h, r = divmod(s, 3600)
        return f"{h} ч" + (f" {r // 60} мин" if r // 60 else "")
    d, r = divmod(s, 86400)
    return f"{d} сут" + (f" {r // 3600} ч" if r // 3600 else "")


_TAG = re.compile(r"<[^>]+>")


def first_line(text: str | None, limit: int = 90) -> str:
    if not text:
        return ""
    plain = html.unescape(_TAG.sub("", text.replace("<br>", "\n")))
    for line in plain.splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"
    return ""


@dataclass
class Job:
    id: str
    run_at: datetime
    fn: str
    kwargs: dict
    seq: int
    origin: str | None
    meta: dict = field(default_factory=dict)


@dataclass
class Participant:
    """Что движок читает у участника из базы."""
    created_at: datetime
    branch: str | None = None
    webinar_date: date | None = None
    webinar_time: str | None = None
    phone: bool = False
    email: bool = False
    survey_done: bool = False
    funnel_done: bool = False


class Simulator:
    def __init__(self, cfg: FunnelConfig, opts: SimOptions, launch: LaunchSettings,
                 key_to_id: dict[str, str] | None = None, link_click: dict[str, str] | None = None):
        self.funnel = cfg
        # «кликнул ссылку» (ключ шага → префикс метки): поле Студии, в yaml движка его нет
        self.link_click = link_click or {}
        self.opts = opts
        self.p: Persona = opts.persona
        self.platform = opts.platform
        self.launch = launch
        self.key_to_id = key_to_id or {}
        self.reg = MSK.localize(datetime.fromisoformat(opts.registered_at).replace(tzinfo=None))
        self.now = self.reg
        self.horizon = self.reg + timedelta(days=opts.horizon_days)
        self.dnd = SimpleNamespace(
            enabled=launch.dnd_enabled if opts.dnd is None else opts.dnd,
            from_=launch.dnd_from, to=launch.dnd_to, reschedule_to=launch.dnd_reschedule_to)
        self.part = Participant(created_at=self.reg, phone=self.p.phone_known,
                                email=self.p.email_known, survey_done=self.p.survey_done)
        self.jobs: dict[str, Job] = {}
        self._heap: list[tuple[datetime, int, str]] = []
        self._seq = 0
        self.recent: dict[str, datetime] = {}
        self.awaiting: dict[str, dict] = {}       # contact / email → {step, deadline}
        self.events: list[dict] = []
        self.truncated: str | None = None
        self.branch_info: dict[str, Any] | None = None
        self._stack: list[str] = []
        self._depth = 0
        # «регистрация» для skip_if_registered_msk: последняя отправка входного шага
        self.entry_keys = entry_step_keys(cfg)
        self.registered_msk: datetime | None = None
        self._handlers: dict[str, Callable[..., None]] = {
            "fire_step": lambda key, meta=None: self.fire_step(key, meta or {}),
            "_on_phone_skip": self._on_phone_skip,
            "force_deliver_gated": self._force_deliver_gated,
            "human_contact": self._human_contact,
            "human_email": self._human_email,
            "human_claim": self._human_claim,
            "human_survey": self._human_survey,
            "human_note": self._human_note,
        }

    # ── лента ────────────────────────────────────────────────────────────────

    def step_text(self, st: FunnelStep) -> str:
        if self.platform == "max" and st.text_max is not None:
            return first_line(st.text_max) or "(в MAX без текста)"
        return first_line(st.text)

    def ev(self, kind: str, key: str | None, text: str, **extra) -> None:
        if len(self.events) >= self.opts.max_events:
            self.truncated = self.truncated or f"лента обрезана на {self.opts.max_events} событиях"
            return
        st = self.funnel.get_step(key) if key else None
        d = self.now.astimezone(MSK)
        if kind == "sent" and key in self.entry_keys:
            self.registered_msk = d
        self.events.append({
            "t": d.isoformat(), "at": d.strftime("%d.%m %H:%M:%S"), "time": _hm(d),
            "day": (d.date() - self.reg.date()).days,
            "kind": kind, "key": key, "step_id": self.key_to_id.get(key) if key else None,
            "type": st.type if st else None,
            "type_label": TYPE_LABEL.get(st.type, st.type) if st else None,
            "title": self.step_text(st) if st else "",
            "by": self._stack[-1] if self._stack else None,
            "text": text, **extra,
        })

    # ── таймеры (вместо APScheduler) ──────────────────────────────────────────

    def _job_id(self, key: str, kind: str = "fire") -> str:
        return f"{key}:{kind}"

    def _schedule_at_delay(self, job_id: str, fn: str, kwargs: dict, delay_s: int | float,
                           meta: dict | None = None) -> None:
        run_at = self.now + timedelta(seconds=delay_s or 0)
        old = self.jobs.get(job_id)
        origin = self._stack[-1] if self._stack else None
        if old is not None and fn == "fire_step" and old.run_at != run_at:
            key = kwargs.get("key")
            self.ev("replaced", key,
                    f"таймер перезаписан вторым путём: был на {_fmt(old.run_at)}"
                    f"{' (из «' + old.origin + '»)' if old.origin else ''}, стал на {_fmt(run_at)}"
                    f"{' (из «' + origin + '»)' if origin else ''} – уйдёт один раз, в новое время",
                    old_at=old.run_at.astimezone(MSK).isoformat(), new_at=run_at.astimezone(MSK).isoformat())
        self._seq += 1
        self.jobs[job_id] = Job(job_id, run_at, fn, kwargs, self._seq, origin, meta or {})
        heapq.heappush(self._heap, (run_at, self._seq, job_id))

    def _next_job(self) -> Job | None:
        """Самый ранний таймер (при равном времени – поставленный раньше)."""
        while self._heap:
            _, seq, jid = self._heap[0]
            job = self.jobs.get(jid)
            if job is None or job.seq != seq:
                heapq.heappop(self._heap)      # отменён или перезаписан
                continue
            return job
        return None

    def _cancel_job(self, job_id: str) -> Job | None:
        return self.jobs.pop(job_id, None)

    def _human(self, name: str, delay_s: int, **kwargs) -> None:
        self._seq += 1
        jid = f"human:{name}:{self._seq}"
        run_at = self.now + timedelta(seconds=delay_s)
        self.jobs[jid] = Job(jid, run_at, name, kwargs, self._seq, None, {})
        heapq.heappush(self._heap, (run_at, self._seq, jid))

    # ── момент «сейчас» у персоны ────────────────────────────────────────────

    def _since(self, flag: bool, after_min: int) -> bool:
        return bool(flag) and self.now >= self.reg + timedelta(minutes=after_min)

    def _webinar_dt(self) -> datetime | None:
        return compose_webinar_dt(self.part.webinar_date, self.part.webinar_time)

    # ── вход (runner.start_for_tg 171–208, _resolve_entry_step 210–260) ─────

    def start(self) -> None:
        router = self.funnel.branch_router
        entry = None
        if router:
            picked = pick_branch_for_now(router, self.now)
            if picked:
                branch, window, wdate = picked
                self.part.branch = branch.id
                self.part.webinar_date = wdate
                self.part.webinar_time = branch.webinar_time
                entry = branch.entry_step
                wdt = self._webinar_dt()
                self.branch_info = {
                    "id": branch.id, "window": f"{window.from_}–{window.to}", "day": window.day,
                    "webinar": wdt.isoformat() if wdt else None,
                    "webinar_label": _fmt(wdt) if wdt else "",
                    "repeat_offset_minutes": branch.repeat_offset_minutes,
                }
            else:
                self.branch_info = {"id": None, "note": "время регистрации не попало ни в одно окно веток – "
                                                        "движок стартует с первого шага"}
        if entry is None:
            first = self.funnel.first_step
            entry = first.key if first else None
        where = ""
        if self.branch_info and self.branch_info.get("id"):
            b = self.branch_info
            where = f" · ветка {b['id']} (окно {b['window']}), эфир {b['webinar_label']}"
        self.ev("start", entry, f"регистрация ({'Telegram' if self.platform == 'tg' else 'MAX'}){where}")
        if not entry:
            self.ev("error", None, "в воронке нет шагов")
            return
        self._persona_timeline()
        st = self.funnel.get_step(entry)
        delay = (st.after_start_seconds if st else None) or 0
        self._schedule_or_fire(entry, delay)

    def _persona_timeline(self) -> None:
        """События персоны на ленте – чтобы было видно, почему условие сработало."""
        p = self.p
        for flag, after, text in (
            (p.vip_paid, p.vip_paid_after_min, "оплатил ВИП"),
            (p.course_paid, p.course_paid_after_min, "купил курс"),
            (p.channel_joined, p.channel_joined_after_min, "вступил в канал"),
            (p.link_clicked, p.link_clicked_after_min, "кликнул ссылку"),
            (p.manager_dm, p.manager_dm_after_min, "написал менеджеру"),
        ):
            if flag and after > 0:
                self._human("human_note", after * 60, text=text)
        wdt = self._webinar_dt()
        if wdt:
            for n in sorted(set(p.attended_days)):
                at = wdt + timedelta(days=n - 1)
                if at > self.now:
                    self._human("human_note", int((at - self.now).total_seconds()),
                                text=f"пришёл на эфир дня {n}")

    def _human_note(self, text: str) -> None:
        self.ev("human", None, text)

    # ── главный цикл ─────────────────────────────────────────────────────────

    def run(self) -> None:
        with clock.use(self.now):
            self.start()
        n = 0
        while self.jobs and not self.truncated:
            job = self._next_job()
            if job is None:
                break
            if job.run_at > self.horizon:
                later = [j for j in self.jobs.values() if j.fn == "fire_step"]
                if later:
                    self.now = self.horizon
                    self.truncated = (f"дальше 14 суток ещё {len(later)} таймер(ов), первый – "
                                      f"«{job.kwargs.get('key', '')}» на {_fmt(job.run_at)}")
                break
            del self.jobs[job.id]
            self.now = job.run_at
            n += 1
            if n > MAX_JOBS:
                self.truncated = "слишком много таймеров – похоже на петлю"
                break
            with clock.use(self.now):
                self._handlers[job.fn](**job.kwargs, **({"meta": job.meta} if job.fn == "fire_step" else {}))
        self._wait_leftovers()
        self._resolve_losses()

    def _resolve_losses(self) -> None:
        """Потеря – только то, что человек так и не получил. Шаг, выпавший на одном пути и
        пришедший другим (кнопка вместо таймера, общий шаг двух веток), не считается."""
        sent = {e["key"] for e in self.events if e["kind"] == "sent"}
        for e in self.events:
            k = e["kind"]
            if k == "parallel_lost":
                real = [x for x in e["lost"] if x not in sent]
                came = [x for x in e["lost"] if x in sent]
                if came:
                    e["text"] += f" (пришли другим путём: {', '.join(came)})"
                e["lost"] = real
                e["lost_ids"] = [self.key_to_id.get(x) for x in real]
                e["resolved"] = not real
            elif k == "claim_cancel":
                chain = self._chain_keys(e["key"])
                if any(c in sent for c in chain):
                    e["resolved"] = True
                    e["text"] = (f"нажатие кнопки сняло таймер «{e['key']}» – человек пошёл по ветке кнопки, "
                                 "дальше пути сходятся, ничего не теряется")
                    e["lost"] = []
                else:
                    e["lost"] = chain
            elif k == "past_skip" and e["key"] in sent:
                e["resolved"] = True
                e["text"] += " – но шаг всё равно пришёл другим путём"

    def _wait_leftovers(self) -> None:
        for kind, a in self.awaiting.items():
            if a.get("stuck"):
                what = "контакт" if kind == "contact" else "почту"
                self.ev("stuck", a["step"], f"ждём {what} – без ответа цепочка дальше не идёт "
                                            "(у шага нет таймаута «не дал»)")

    # ── планирование (runner._schedule_or_fire 1261–1299) ────────────────────

    def _is_timed(self, st: FunnelStep | None) -> bool:
        return st is not None and bool(st.absolute_time_msk or st.webinar_offset_minutes is not None)

    def _schedule_or_fire(self, key: str, delay: int, meta: dict | None = None) -> None:
        st = self.funnel.get_step(key)
        meta = dict(meta or {})
        if self._is_timed(st):
            delay, skipped, target = self._compute_absolute_delay(st, delay)
            if skipped:
                self.ev("past_skip", key, f"время {_fmt(target)} уже прошло – шаг выпадает "
                                          "(«если время прошло – пропустить»)", planned=target.isoformat())
                if st.also_fire:
                    self._parallel_lost(st, "шаг выпал по времени")
                if st.on_done:
                    self._push(key)
                    try:
                        self._schedule_or_fire(st.on_done, st.on_done_after_seconds or 0)
                    finally:
                        self._pop()
                return
            if target is not None and target <= self.now:
                meta["late_from"] = target.isoformat()
                meta["late_s"] = delay
            elif target is not None and (st.min_delay_seconds or 0) > 0 and \
                    delay == st.min_delay_seconds and self.now + timedelta(seconds=delay) > target:
                meta["min_delay_from"] = target.isoformat()
        if st is not None:
            new = apply_dnd_to_delay(delay, self.dnd, st.dnd_exempt)
            if new != delay:
                meta["dnd_from"] = (self.now + timedelta(seconds=delay)).isoformat()
            delay = new
        if delay <= 0:
            self.fire_step(key, meta)
        else:
            self._schedule_at_delay(self._job_id(key), "fire_step", {"key": key}, delay, meta)

    def _compute_absolute_delay(self, st: FunnelStep, fallback: int) -> tuple[int, bool, datetime | None]:
        """runner._compute_absolute_delay_tg 1301–1329 (+ _absolute_base_dt_tg 321–350)."""
        base = self._webinar_dt()
        if base is None:
            # фолбэк движка: день входа, 00:00 МСК
            base = self.part.created_at.astimezone(MSK).replace(hour=0, minute=0, second=0, microsecond=0)
        router = self.funnel.branch_router
        target = resolve_step_target(st, base, branch_by_id(router, self.part.branch))
        if target is None:
            return max(0, fallback), False, None
        delay = int((target.astimezone(pytz.UTC) - self.now.astimezone(pytz.UTC)).total_seconds())
        if delay <= 0:
            if st.skip_if_past:
                return 0, True, target
            return max(PAST_STAGGER_S, st.min_delay_seconds or 0), False, target
        return max(delay, st.min_delay_seconds or 0), False, target

    def _fire_parallel(self, st: FunnelStep) -> None:
        """runner._fire_parallel 688–707 (MAX 553–571 – то же)."""
        self._push(st.key)
        try:
            for pkey, pdelay in (st.also_fire or {}).items():
                target = self.funnel.get_step(pkey)
                if self._is_timed(target):
                    self._schedule_or_fire(pkey, pdelay or 0)
                else:
                    meta = {"parallel_from": st.key}
                    at = self.now + timedelta(seconds=pdelay or 0)
                    if self.dnd.enabled and pdelay and apply_dnd_to_delay(pdelay, self.dnd, False) != pdelay:
                        meta["no_dnd"] = at.isoformat()
                    self._schedule_at_delay(self._job_id(pkey), "fire_step", {"key": pkey}, pdelay or 0, meta)
        finally:
            self._pop()

    def _parallel_lost(self, st: FunnelStep, why: str) -> None:
        keys = list((st.also_fire or {}).keys())
        self.ev("parallel_lost", st.key,
                f"{why} – параллельные стрелки не срабатывают, теряются: {', '.join(keys)}",
                lost=keys, lost_ids=[self.key_to_id.get(k) for k in keys], why=why)

    def _push(self, key: str) -> None:
        self._stack.append(key)

    def _pop(self) -> None:
        self._stack.pop()

    def _next(self, st: FunnelStep, key: str | None, delay: int) -> None:
        """Запланировать следующий шаг «от имени» st."""
        if not key:
            return
        self._push(st.key)
        try:
            self._schedule_or_fire(key, delay)
        finally:
            self._pop()

    # ── анти-дубль (runner 84–163) ───────────────────────────────────────────

    def _recently_fired(self, key: str) -> bool:
        prev = self.recent.get(key)
        return prev is not None and (self.now - prev).total_seconds() < DEDUP_WINDOW_S

    def _dup_recent(self, key: str) -> bool:
        prev = self.recent.get(key)
        self.recent[key] = self.now
        return prev is not None and (self.now - prev).total_seconds() < DEDUP_WINDOW_S

    def _reschedule_if_lost(self, key: str, delay: int) -> None:
        if not key or self._recently_fired(key) or self._job_id(key) in self.jobs:
            return
        self.ev("recovered", key, "повтор: движок восстановил потерянный параллельный таймер")
        self._schedule_or_fire(key, delay or 0)

    def _reschedule_chain_tail(self, key: str | None, delay: int, max_hops: int = 8) -> None:
        cur, cur_delay = key, delay
        for _ in range(max_hops):
            if not cur or self._job_id(cur) in self.jobs:
                return
            if not self._recently_fired(cur):
                self.ev("recovered", cur, "повтор: движок восстановил хвост цепочки")
                self._schedule_or_fire(cur, cur_delay or 0)
                return
            st = self.funnel.get_step(cur)
            if st is None:
                return
            for k, off in (st.also_fire or {}).items():
                self._reschedule_if_lost(k, off or 0)
            cur, cur_delay = st.on_done, st.on_done_after_seconds or 0

    # ── условия ──────────────────────────────────────────────────────────────

    def _attended(self, st: FunnelStep) -> tuple[bool, int]:
        day = int(st.day_offset or 0) + 1
        wdt = self._webinar_dt()
        if day not in self.p.attended_days:
            return False, day
        if wdt is None:
            return True, day
        return self.now >= wdt + timedelta(days=day - 1), day

    def _attendance_room(self, st: FunnelStep) -> str:
        if self.platform == "max":
            return (st.bizon_room_max or self.launch.webinar_bizon_room_max
                    or st.bizon_room or self.launch.air_room or "")
        return st.bizon_room or self.launch.air_room or ""

    def _channel_state(self) -> bool | None:
        joined = self._since(self.p.channel_joined, self.p.channel_joined_after_min)
        if self.platform == "max":
            if not self.launch.channel_max_chat_id:
                return None
            return joined
        # TG: channel_joined_at ставит бот-админ канала; без chat_id проверить нельзя
        if not self.launch.channel_chat_id:
            return None
        return joined

    def _gate(self, st: FunnelStep) -> tuple[str, str] | None:
        """(условие, почему пропущен) или None – шаг уходит. Порядок – как в раннере платформы."""
        checks_tg = ["registered", "attendance", "survey", "not_paid", "paid", "not_course", "course",
                     "lm", "click", "click_pos", "manager", "channel"]
        checks_max = ["registered", "channel", "survey", "not_paid", "paid", "not_course", "course",
                      "lm", "click", "click_pos", "manager", "attendance"]
        for c in (checks_max if self.platform == "max" else checks_tg):
            r = self._check(c, st)
            if r:
                return r
        return None

    def _check(self, c: str, st: FunnelStep) -> tuple[str, str] | None:
        p = self.p
        if c == "registered" and st.skip_if_registered_msk:
            # reg_window.registered_in_window: время не нашли – шлём (fail-open)
            reg = self.registered_msk
            if reg is not None and in_windows(reg, st.skip_if_registered_msk):
                return "registered_msk", (f"зарегистрировался в {reg.strftime('%H:%M')} – в эти часы "
                                          f"({', '.join(st.skip_if_registered_msk)}) шаг не шлётся")
        elif c == "attendance" and st.gated_by_attendance:
            room = self._attendance_room(st)
            if not room:
                return None     # зал не задан – гейт открыт (fail-open)
            was, day = self._attended(st)
            if st.gated_by_attendance == "was" and not was:
                return "attendance", f"только тем, кто был на эфире дня {day}, – а он не был"
            if st.gated_by_attendance == "was_not" and was:
                return "attendance", f"только тем, кто пропустил эфир дня {day}, – а он был"
        elif c == "survey" and st.gated_by_survey_done:
            if self.part.survey_done:
                return "survey_done", "уже прошёл опрос"
        elif c == "not_paid" and st.gated_by_not_paid:
            if self._since(p.vip_paid, p.vip_paid_after_min):
                return "not_paid", "уже оплатил ВИП"
        elif c == "paid" and st.gated_by_paid:
            if not self._since(p.vip_paid, p.vip_paid_after_min):
                return "paid", "только оплатившим ВИП, – а он не оплатил"
        elif c == "not_course" and st.gated_by_not_course_paid:
            if self._since(p.course_paid, p.course_paid_after_min):
                return "not_course_paid", "уже купил курс"
        elif c == "course" and st.gated_by_course_paid:
            if not self._since(p.course_paid, p.course_paid_after_min):
                return "course_paid", "только купившим курс, – а он не купил"
        elif c == "lm" and st.gated_by_has_lm:
            if not p.has_lm:
                return "has_lm", "нет лидмагнита"
        elif c == "click" and st.gated_by_no_link_click:
            if self._since(p.link_clicked, p.link_clicked_after_min):
                return "no_link_click", f"уже кликнул ссылку «{st.gated_by_no_link_click}…»"
        elif c == "click_pos" and self.link_click.get(st.key):
            if not self._since(p.link_clicked, p.link_clicked_after_min):
                return "link_click", (f"только кликнувшим ссылку «{self.link_click[st.key]}…», "
                                      "– а он не кликал")
        elif c == "manager" and st.gated_by_no_manager_dm:
            enabled = self.launch.manager_dm_max if self.platform == "max" else self.launch.manager_dm
            if enabled and self._since(p.manager_dm, p.manager_dm_after_min):
                return "no_manager_dm", "уже написал менеджеру"
        elif c == "channel" and st.gated_by_channel_joined:
            state = self._channel_state()
            need = st.gated_by_channel_joined
            if state is None:
                if self.platform == "max":
                    if self.launch.max_gate_fallback == "send":
                        return None
                    return "channel", ("в MAX подписку проверить нельзя (max_chat_id не задан) – "
                                       "по настройке запуска шаг пропускается")
                return "channel", ("канал не подключён к боту (chat_id = 0) – проверить подписку "
                                   "нельзя, шаг молча пропускается")
            if need == "not_joined" and state is not False:
                return "channel", "уже в канале"
            if need == "joined" and state is not True:
                return "channel", "не вступил в канал"
        return None

    # ── шаг (runner.fire_step 377–686, max_runner.fire_step 258–549) ─────────

    def fire_step(self, key: str, meta: dict | None = None) -> None:
        self._depth += 1
        try:
            if self._depth > MAX_DEPTH:
                self.ev("error", key, "слишком длинная цепочка мгновенных переходов – похоже на петлю")
                return
            st = self.funnel.get_step(key)
            if st is None:
                self.ev("unknown_step", key, f"шага «{key}» нет в воронке – цепочка обрывается молча")
                return
            if self._dup_recent(key):
                self.ev("dup", key, f"шаг уже выполнялся меньше {DEDUP_WINDOW_S} с назад – повтор не шлётся")
                if st.on_done:
                    self._reschedule_chain_tail(st.on_done, st.on_done_after_seconds or 0)
                for k, off in (st.also_fire or {}).items():
                    self._reschedule_if_lost(k, off or 0)
                return
            if self.platform == "max":
                self._fire_max(st, meta or {})
            else:
                self._fire_tg(st, meta or {})
        finally:
            self._depth -= 1

    def _notes(self, st: FunnelStep, meta: dict) -> dict:
        out: dict[str, Any] = {}
        if meta.get("late_from"):
            out["late_from"] = meta["late_from"]
        if meta.get("min_delay_from"):
            out["min_delay_from"] = meta["min_delay_from"]
        if meta.get("dnd_from"):
            out["dnd_from"] = meta["dnd_from"]
        if meta.get("no_dnd"):
            out["no_dnd"] = meta["no_dnd"]
        if meta.get("parallel_from"):
            out["parallel_from"] = meta["parallel_from"]
        return out

    def _sent_text(self, base: str, meta: dict) -> str:
        parts = [base]
        if meta.get("late_from"):
            late = meta.get("late_s") or PAST_STAGGER_S
            why = ("«не раньше чем через …»" if late > PAST_STAGGER_S
                   else "«если время прошло – всё равно отправить»")
            parts.append(f"по плану {_fmt(datetime.fromisoformat(meta['late_from']))} – время прошло, "
                         f"ушло через {_dur(late)} ({why})")
        if meta.get("min_delay_from"):
            parts.append(f"по плану {_fmt(datetime.fromisoformat(meta['min_delay_from']))} – сдвинуто: "
                         "«не раньше чем через …» после постановки")
        if meta.get("dnd_from"):
            parts.append(f"по плану {_fmt(datetime.fromisoformat(meta['dnd_from']))} – сдвинуто ночным режимом")
        if meta.get("no_dnd"):
            parts.append("ночью: параллельная стрелка без своего времени ночной режим не соблюдает")
        return " · ".join(parts)

    def _gated(self, st: FunnelStep, gate: tuple[str, str]) -> None:
        cond, why = gate
        self.ev("gated", st.key, f"пропущен по условию: {why}", condition=cond)
        if st.also_fire:
            self._parallel_lost(st, "шаг пропущен условием")
        self._next(st, st.on_done, st.on_done_after_seconds or 0)

    def _fire_tg(self, st: FunnelStep, meta: dict) -> None:
        key = st.key
        if key in self.launch.tg_skip_steps:
            self.ev("tg_skip", key, "шаг отключён для Telegram в настройках запуска (tg_skip_steps)")
            self._fire_parallel(st)
            self._next(st, st.on_skip or st.on_done, 0)
            return
        if st.type == "jump":
            self.ev("jump", key, f"шаг снят – переход в «{st.jump_to}»" if st.jump_to else "шаг снят, перехода нет")
            if st.also_fire:
                self._parallel_lost(st, "у снятого шага (переход)")
            self._next(st, st.jump_to, 0)
            return
        if st.type == "phone_request":
            self._phone_tg(st, meta)
            return
        if st.type == "email_request":
            self._email(st, meta)
            return
        gate = self._gate(st)
        if gate:
            self._gated(st, gate)
            return
        if st.type in ("call", "sms", "air_pin"):
            label = {"call": "автозвонок", "sms": "SMS", "air_pin": "закреп с отсчётом до эфира"}[st.type]
            self.ev("sent", key, self._sent_text(label, meta), **self._notes(st, meta))
            if st.on_done:
                self._next(st, st.on_done, st.on_done_after_seconds or 0)
            elif st.type != "air_pin":
                self.ev("end", key, "дальше стрелки нет – эта цепочка закончилась")
            self._fire_parallel(st)
            return
        self._send(st, meta)
        if st.survey_qid:
            self._survey_wait(st)
            return
        if st.chain_to_funnel:
            d = st.on_done_after_seconds or 0
            self.ev("chain", key, f"переход в воронку «{st.chain_to_funnel}»"
                                  f"{' через ' + _dur(d) if d else ''} – дальше прогон не идёт"
                                  f"{' (стрелка «по времени» игнорируется)' if st.on_done else ''}")
        elif st.on_done:
            self._next(st, st.on_done, st.on_done_after_seconds or 0)
        else:
            self.ev("end", key, "дальше стрелки нет – эта цепочка закончилась")
        self._fire_parallel(st)

    def _fire_max(self, st: FunnelStep, meta: dict) -> None:
        key = st.key
        if st.type == "jump":
            self.ev("jump", key, f"шаг снят – переход в «{st.jump_to}»" if st.jump_to else "шаг снят, перехода нет")
            if st.also_fire:
                self._parallel_lost(st, "у снятого шага (переход)")
            self._next(st, st.jump_to, 0)
            return
        if st.type == "phone_request":
            self._phone_max(st, meta)
            return
        if st.type == "email_request":
            self._email(st, meta)
            return
        gate = self._gate(st)
        if gate:
            self._gated(st, gate)
            return
        if st.type in ("video_note", "call", "air_pin", "sms"):
            if st.type == "air_pin":
                self.ev("skip_max", key, "в MAX закрепа с отсчётом нет – шаг проходит без сообщения")
            else:
                label = {"video_note": "кружок (в MAX – обычным видео)", "call": "автозвонок", "sms": "SMS"}[st.type]
                self.ev("sent", key, self._sent_text(label, meta), **self._notes(st, meta))
            if st.on_done:
                self._next(st, st.on_done, st.on_done_after_seconds or 0)
            elif st.type in ("call", "sms"):
                self.ev("end", key, "дальше стрелки нет – эта цепочка закончилась")
            self._fire_parallel(st)
            return
        if st.max_subscription_gate:
            sub = self._channel_state()
            if sub is False:
                self.ev("wait", key, "не подписан на канал – вместо шага просьба подписаться; "
                                     "без подписки шаг уйдёт сам через 30 мин")
                self._schedule_at_delay(self._job_id(key, "subfallback"), "force_deliver_gated",
                                        {"key": key}, 30 * 60)
                return
        self._send(st, meta)
        if st.survey_qid:
            self._survey_wait(st)
            return
        if st.chain_to_funnel and not st.on_done:
            self.ev("chain_ignored", key, f"в MAX переход в воронку «{st.chain_to_funnel}» не выполняется – "
                                          "цепочка здесь заканчивается")
        if st.on_done:
            self._next(st, st.on_done, st.on_done_after_seconds or 0)
        elif not st.chain_to_funnel:
            self.ev("end", key, "дальше стрелки нет – эта цепочка закончилась")
        self._fire_parallel(st)

    def _force_deliver_gated(self, key: str) -> None:
        st = self.funnel.get_step(key)
        if st is None:
            return
        self._send(st, {}, note="выдан без подписки (30 мин прошло)")
        if st.on_done:
            self._next(st, st.on_done, st.on_done_after_seconds or 0)
        self._fire_parallel(st)

    def _claim_text(self, st: FunnelStep) -> str | None:
        if self.platform == "max" and st.claim_button_text_max is not None:
            return st.claim_button_text_max or None
        return st.claim_button_text

    def _send(self, st: FunnelStep, meta: dict, note: str = "") -> None:
        label = TYPE_LABEL.get(st.type, st.type)
        if note:
            label += f" · {note}"
        if st.type == "video_note" and self.platform == "tg":
            label = "кружок"
        self.ev("sent", st.key, self._sent_text(f"отправлено: {label}", meta), **self._notes(st, meta))
        claim = self._claim_text(st)
        if claim and st.on_callback and self.p.claim:
            self._human("human_claim", self.p.claim_after_s, key=st.key)

    # ── опрос (services/survey.py, handlers/survey.py) ──────────────────────

    def _survey_wait(self, st: FunnelStep) -> None:
        if st.also_fire:
            self._parallel_lost(st, "вопрос опроса ждёт ответ")
        if self.p.survey_answer and st.survey_next:
            self.ev("wait", st.key, f"ждём ответ на вопрос «{st.survey_qid}» (ответит через {_dur(self.p.survey_after_s)})")
            self._human("human_survey", self.p.survey_after_s, key=st.key)
        else:
            self.ev("wait", st.key, f"ждём ответ на вопрос «{st.survey_qid}» – "
                                    f"{'без ответа опрос стоит' if st.survey_next else 'дальше вопроса нет'}")

    def _human_survey(self, key: str) -> None:
        st = self.funnel.get_step(key)
        if st is None:
            return
        self.ev("human", key, f"ответил на вопрос «{st.survey_qid}»")
        if st.survey_final:
            self.part.survey_done = True
            for k in ("z_survey_followup", "s_survey_followup"):
                if self._cancel_job(self._job_id(k)):
                    self.ev("cancelled", k, "опрос пройден – дожим опроса снят")
        nxt_key = st.survey_next
        nxt = self.funnel.get_step(nxt_key) if nxt_key else None
        if nxt is None:
            return
        self._push(key)
        try:
            if st.survey_edit:
                self.ev("sent", nxt.key, "показан правкой того же сообщения (режим «править сообщение»)")
                if nxt.survey_qid:
                    self._survey_wait(nxt)
                elif nxt.on_done or nxt.also_fire or nxt.chain_to_funnel:
                    self.ev("edit_end", nxt.key,
                            "шаг показан правкой сообщения опроса – его стрелки дальше НЕ срабатывают, "
                            "цепочка стоит (движок так делает только при отправке новым сообщением)")
            else:
                self.fire_step(nxt.key, {})
        finally:
            self._pop()

    # ── кнопка-действие (runner.on_callback_claim 940–957) ───────────────────

    def _human_claim(self, key: str) -> None:
        st = self.funnel.get_step(key)
        if st is None or not st.on_callback:
            return
        self.ev("human", key, f"нажал «{first_line(self._claim_text(st), 60)}»")
        if st.on_done and not st.claim_keeps_chain:
            job = self._cancel_job(self._job_id(st.on_done))
            if job is not None:
                n = self._chain_len(st.on_done)
                self.ev("claim_cancel", st.on_done,
                        f"нажатие кнопки в «{key}» сняло таймер этого шага (был на {_fmt(job.run_at)}) – "
                        f"не придёт он и ещё {n - 1} шаг(ов) цепочки после него "
                        "(нужно «кнопка не обрывает цепочку»)" if n > 1 else
                        f"нажатие кнопки в «{key}» сняло таймер этого шага (был на {_fmt(job.run_at)}) – он не придёт",
                        planned=job.run_at.isoformat(), chain=n)
        self._push(key)
        try:
            self.fire_step(st.on_callback, {})
        finally:
            self._pop()

    def _chain_keys(self, key: str | None) -> list[str]:
        """Цепочка «по времени» начиная с key (без параллельных стрелок)."""
        seen: list[str] = []
        while key and key not in seen:
            seen.append(key)
            st = self.funnel.get_step(key)
            key = st.on_done if st else None
        return seen

    def _chain_len(self, key: str | None) -> int:
        return len(self._chain_keys(key))

    # ── телефон ──────────────────────────────────────────────────────────────

    def _phone_tg(self, st: FunnelStep, meta: dict) -> None:
        """runner._handle_phone_request 994–1055 + handlers/contact.py 195–245."""
        if st.skip_if_user_phone_known and self.part.phone:
            if st.always_send_text:
                self.ev("sent", st.key, self._sent_text("телефон уже известен – текст без кнопки контакта", meta),
                        **self._notes(st, meta))
            else:
                self.ev("skip_known", st.key, "телефон уже известен – запрос не показывается")
            if st.also_fire:
                self._parallel_lost(st, "телефон уже известен (так устроен TG-движок)")
            if st.on_skip_if_phone_known:
                self._next(st, st.on_skip_if_phone_known, st.on_skip_if_phone_known_after_seconds or 0)
            else:
                self.ev("end", st.key, "у шага нет стрелки «уже известен» – цепочка кончилась")
            return
        self.ev("sent", st.key, self._sent_text("запрос телефона (кнопка «поделиться контактом»)", meta),
                **self._notes(st, meta))
        wait = st.on_skip_after_seconds or 3600
        has_skip = bool(st.on_skip_after_seconds and st.on_skip)
        self.awaiting["contact"] = {"step": st.key, "deadline": self.now + timedelta(seconds=wait),
                                    "stuck": not has_skip and self.p.contact != "given"}
        if has_skip:
            self._schedule_at_delay(self._job_id(st.key, "skip"), "_on_phone_skip", {"key": st.key},
                                    st.on_skip_after_seconds)
        self._contact_plan(st, wait, has_skip)
        self._fire_parallel(st)

    def _contact_plan(self, st: FunnelStep, wait: int, has_skip: bool) -> None:
        if self.p.contact == "given":
            self.ev("wait", st.key, f"ждём контакт – человек даст его через {_dur(self.p.contact_after_s)}")
            self._human("human_contact", self.p.contact_after_s, key=st.key)
        elif has_skip:
            self.ev("wait", st.key, f"ждём контакт {_dur(wait)}, потом – по стрелке «не дал»")

    def _on_phone_skip(self, key: str) -> None:
        st = self.funnel.get_step(key)
        if st is None:
            return
        self.ev("timeout", key, f"контакт не дали за {_dur(st.on_skip_after_seconds or 0)} – идём по «не дал»")
        self._next(st, st.on_skip, 0)

    def _human_contact(self, key: str) -> None:
        self.part.phone = True
        a = self.awaiting.pop("contact", None)
        if not a:
            self.ev("human", key, "поделился контактом (воронка его уже не ждёт)")
            return
        st = self.funnel.get_step(a["step"])
        if self.now > a["deadline"]:
            self.ev("human", key, "поделился контактом после дедлайна – сохранён, воронку не двигает")
            return
        self.ev("human", key, "поделился контактом")
        if st is None:
            return
        if self.platform == "max":
            skip_t = st.on_skip or st.on_skip_if_phone_known
            if skip_t:
                self._cancel_job(self._job_id(skip_t))
            target = st.on_contact or st.on_skip or st.on_skip_if_phone_known
        else:
            self._cancel_job(self._job_id(st.key, "skip"))
            target = st.on_contact or st.on_skip
        self._next(st, target, st.on_contact_after_seconds or 0)

    def _phone_max(self, st: FunnelStep, meta: dict) -> None:
        """max_runner.fire_step 289–316 + _fire_manual_phone 905–955 + services/max_phone.py."""
        if not st.manual_phone_max:
            if st.always_send_text:
                self.ev("sent", st.key, self._sent_text("текст шага без кнопки контакта (в MAX номер не "
                                                        "запрашивается)", meta), **self._notes(st, meta))
            else:
                self.ev("skip_known", st.key, "в MAX запрос телефона пропускается – сразу дальше")
            target = st.on_skip_if_phone_known or st.on_skip or st.on_contact
            self._next(st, target, st.on_skip_if_phone_known_after_seconds or 0)
            self._fire_parallel(st)
            return
        if st.skip_if_user_phone_known and self.part.phone:
            if st.text_if_phone_known:
                self.ev("sent", st.key, self._sent_text("телефон уже известен – свой текст", meta),
                        **self._notes(st, meta))
            else:
                self.ev("skip_known", st.key, "телефон уже известен – запрос не показывается")
            self._next(st, st.on_skip_if_phone_known or st.on_contact or st.on_skip,
                       st.on_skip_if_phone_known_after_seconds or 0)
            self._fire_parallel(st)
            return
        self.ev("sent", st.key, self._sent_text("запрос телефона (MAX: кнопка или номер сообщением)", meta),
                **self._notes(st, meta))
        wait = st.on_skip_after_seconds if st.on_skip_after_seconds is not None else 300
        target = st.on_skip or st.on_skip_if_phone_known or st.on_contact
        self.awaiting["contact"] = {"step": st.key, "deadline": self.now + timedelta(seconds=wait),
                                    "stuck": not target and self.p.contact != "given"}
        if target:
            self._next(st, target, wait)
        self._contact_plan(st, wait, bool(target))
        self._fire_parallel(st)

    # ── почта (runner._handle_email_request 1057–1115, max_runner 957–1004) ──

    def _email(self, st: FunnelStep, meta: dict) -> None:
        if st.skip_if_user_email_known and self.part.email:
            if st.text_if_email_known:
                self.ev("sent", st.key, self._sent_text("почта уже известна – свой текст", meta),
                        **self._notes(st, meta))
            else:
                self.ev("skip_known", st.key, "почта уже известна – запрос не показывается")
            target = st.on_skip_if_email_known or st.on_email or st.on_skip
            self._next(st, target, st.on_skip_if_email_known_after_seconds or 0)
            if self.platform == "max":
                self._fire_parallel(st)
            elif st.also_fire:
                self._parallel_lost(st, "почта уже известна (так устроен TG-движок)")
            return
        self.ev("sent", st.key, self._sent_text("запрос почты (ждём обычное сообщение)", meta),
                **self._notes(st, meta))
        wait = st.on_skip_after_seconds if st.on_skip_after_seconds is not None else 300
        has_skip = bool(st.on_skip and wait)
        self.awaiting["email"] = {"step": st.key, "deadline": self.now + timedelta(seconds=wait),
                                  "stuck": not has_skip and not self.p.email}
        if has_skip:
            self._next(st, st.on_skip, wait)
        if self.p.email:
            self.ev("wait", st.key, f"ждём почту – человек пришлёт через {_dur(self.p.email_after_s)}")
            self._human("human_email", self.p.email_after_s, key=st.key)
        elif has_skip:
            self.ev("wait", st.key, f"ждём почту {_dur(wait)}, потом – по стрелке «не прислал»")
        self._fire_parallel(st)

    def _human_email(self, key: str) -> None:
        self.part.email = True
        a = self.awaiting.pop("email", None)
        if not a:
            self.ev("human", key, "прислал почту (воронка её уже не ждёт)")
            return
        if self.now > a["deadline"]:
            self.ev("human", key, "прислал почту после дедлайна – сохранена, воронку не двигает")
            return
        st = self.funnel.get_step(a["step"])
        self.ev("human", key, "прислал почту")
        if st is None:
            return
        skip_t = st.on_skip or st.on_skip_if_email_known
        if skip_t:
            self._cancel_job(self._job_id(skip_t))
        self._next(st, st.on_email or st.on_skip or st.on_skip_if_email_known, st.on_email_after_seconds or 0)


# ── сводка и отметки для холста ──────────────────────────────────────────────

REASON = {
    "past_skip": "время прошло",
    "parallel_lost": "параллельная ветка потеряна",
    "dup": "повтор в окне 90 с",
    "claim_cancel": "таймер снят нажатием кнопки",
    "edit_end": "показан правкой – цепочка стоит",
    "unknown_step": "шага нет",
}


def summarize(sim: Simulator) -> dict[str, Any]:
    evs = sim.events
    sent_keys: dict[str, list[dict]] = {}
    dropped: list[dict] = []
    gated: list[dict] = []
    for e in evs:
        if e["kind"] == "sent" and e["key"]:
            sent_keys.setdefault(e["key"], []).append(e)
        elif e["kind"] in DROP_KINDS:
            if e.get("resolved"):
                continue
            if e["kind"] == "parallel_lost":
                for k in e.get("lost", []):
                    dropped.append({"key": k, "step_id": sim.key_to_id.get(k), "reason": "parallel_lost",
                                    "text": f"параллельная стрелка из «{e['key']}» не сработала: {e.get('why', '')}",
                                    "at": e["at"]})
            elif e["kind"] == "claim_cancel":
                for k in e.get("lost", []):
                    dropped.append({"key": k, "step_id": sim.key_to_id.get(k), "reason": "claim_cancel",
                                    "text": f"цепочка «{e['key']}» снята нажатием кнопки", "at": e["at"]})
            else:
                dropped.append({"key": e["key"], "step_id": e["step_id"], "reason": e["kind"],
                                "text": e["text"], "at": e["at"]})
        elif e["kind"] == "gated":
            gated.append({"key": e["key"], "step_id": e["step_id"], "condition": e.get("condition"),
                          "text": e["text"], "at": e["at"]})
    by_reason: dict[str, int] = {}
    for d in dropped:
        by_reason[d["reason"]] = by_reason.get(d["reason"], 0) + 1
    return {
        "sent": sum(len(v) for v in sent_keys.values()),
        "sent_steps": len(sent_keys),
        "late": sum(1 for e in evs if e["kind"] == "sent" and e.get("late_from")),
        "dnd": sum(1 for e in evs if e["kind"] == "sent" and e.get("dnd_from")),
        "replaced": sum(1 for e in evs if e["kind"] == "replaced"),
        "dropped": dropped,
        "dropped_by_reason": {REASON.get(k, k): v for k, v in by_reason.items()},
        "gated": gated,
        "stuck": [e["text"] for e in evs if e["kind"] == "stuck"],
        "truncated": sim.truncated,
    }


def marks(sim: Simulator) -> dict[str, dict]:
    """Отметки для карточек: step_id → {state, label, title}."""
    out: dict[str, dict] = {}
    reg_date = sim.reg.date()

    def put(key: str | None, state: str, label: str, title: str) -> None:
        sid = sim.key_to_id.get(key or "")
        if not sid:
            return
        cur = out.get(sid)
        rank = {"sent": 3, "dropped": 2, "gated": 1}
        if cur is None or rank[state] > rank[cur["state"]]:
            out[sid] = {"state": state, "label": label, "title": title, "count": 1}
        elif cur["state"] == state:
            cur["count"] += 1

    for e in sim.events:
        if e["kind"] == "sent":
            d = datetime.fromisoformat(e["t"])
            when = d.strftime("%H:%M") if d.date() == reg_date else d.strftime("%d.%m %H:%M")
            put(e["key"], "sent", f"уйдёт {when}", e["text"])
        elif e.get("resolved"):
            continue
        elif e["kind"] in ("parallel_lost", "claim_cancel"):
            for k in e.get("lost", []):
                put(k, "dropped", f"выпадет: {REASON.get(e['kind'], '')}", e["text"])
        elif e["kind"] in DROP_KINDS:
            put(e["key"], "dropped", f"выпадет: {REASON.get(e['kind'], '')}", e["text"])
        elif e["kind"] == "gated":
            put(e["key"], "gated", "не уйдёт: условие", e["text"])
    for m in out.values():
        if m["state"] == "sent" and m["count"] > 1:
            m["label"] += f" (×{m['count']})"
    return out


def day_labels(sim: Simulator) -> list[dict]:
    """Подписи дней ленты: дата, «День регистрации» / «День N», эфиры ветки в этот день."""
    days = sorted({e["day"] for e in sim.events})
    wdt = sim._webinar_dt()
    br = branch_by_id(sim.funnel.branch_router, sim.part.branch)
    max_off = max([int(s.day_offset or 0) for s in sim.funnel.steps] + [0])
    airs: dict[int, list[str]] = {}
    if wdt is not None:
        for i in range(max_off + 1):
            a = wdt + timedelta(days=i)
            airs.setdefault((a.date() - sim.reg.date()).days, []).append(f"эфир {i + 1} в {a.strftime('%H:%M')}")
            if br and br.repeat_offset_minutes:
                r = a + timedelta(minutes=br.repeat_offset_minutes)
                airs.setdefault((r.date() - sim.reg.date()).days, []).append(f"повтор {i + 1} в {r.strftime('%H:%M')}")
    out = []
    for d in days:
        dt = sim.reg.date() + timedelta(days=d)
        out.append({"day": d, "date": dt.strftime("%d.%m"),
                    "title": "День регистрации" if d == 0 else f"День {d}",
                    "airs": airs.get(d, [])})
    return out


def simulate(cfg: FunnelConfig, opts: SimOptions, launch: LaunchSettings,
             key_to_id: dict[str, str] | None = None,
             link_click: dict[str, str] | None = None) -> dict[str, Any]:
    sim = Simulator(cfg, opts, launch, key_to_id, link_click)
    sim.run()
    return {
        "ok": True,
        "registered_at": sim.reg.isoformat(),
        "platform": sim.platform,
        "branch": sim.branch_info,
        "launch": {**launch.model_dump(), "notes": launch.notes(), "dnd_used": bool(sim.dnd.enabled)},
        "events": sim.events,
        "days": day_labels(sim),
        "summary": summarize(sim),
        "marks": marks(sim),
    }
