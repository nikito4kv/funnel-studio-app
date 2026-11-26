"""Симулятор расписания (этап 2B): правила движка, демо-воронки, матрица веток, API.

Истина о поведении – движок bot-engine (runner.py / max_runner.py). Здесь:
  * синтетические воронки воспроизводят известные правила (41, 55, skip_if_past…);
  * демо-воронки из demo/botrepo – то же правило на воронках целиком: ветки, окна регистрации,
    повтор для тех, кто не пришёл, матрица моментов регистрации.
"""
from __future__ import annotations

import warnings
from datetime import date

import pytest

from studio.convert import import_file
from studio.engine_ref.loader import FunnelConfig
from studio.paths import bot_repo_path
from studio.sim.launch import LaunchSettings, load_launch_settings
from studio.sim.models import Persona, SimOptions
from studio.sim.run import engine_config, matrix, matrix_moments, simulate_graph
from studio.sim.simulator import simulate

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

REPO = bot_repo_path()
AUTO = "launches/autowebinar/funnel.yaml"          # автовебинар на 3 дня, ветки DAY / EVE
NEXTDAY = "launches/autowebinar/funnel_nextday.yaml"   # эфир всегда завтра в 13:00, повтор для не пришедших
WEBINAR = "launches/webinar/funnel.yaml"           # один эфир в фиксированную дату


# ── помощники ────────────────────────────────────────────────────────────────

def cfg(steps: list[dict], **extra) -> FunnelConfig:
    return FunnelConfig(id="t", steps=steps, **extra)


def run(c: FunnelConfig, when: str = "2026-09-24T10:00", platform: str = "tg",
        launch: LaunchSettings | None = None, **persona) -> dict:
    return simulate(c, SimOptions(registered_at=when, platform=platform, persona=Persona(**persona)),
                    launch or LaunchSettings())


def kinds(res: dict, key: str) -> list[str]:
    return [e["kind"] for e in res["events"] if e["key"] == key]


def sent_at(res: dict, key: str) -> list[str]:
    return [e["at"] for e in res["events"] if e["kind"] == "sent" and e["key"] == key]


_graphs: dict[str, object] = {}


def graph(path: str):
    if path not in _graphs:
        _graphs[path] = import_file(REPO / path, repo_root=REPO)
    return _graphs[path]


def sim_real(path: str, when: str, platform: str = "tg", **persona) -> dict:
    return simulate_graph(graph(path), SimOptions(registered_at=when, platform=platform,
                                                  persona=Persona(**persona)), REPO)


# ── правила движка на синтетике ──────────────────────────────────────────────

ROUTER = {"type": "time_of_day_msk", "branches": [
    {"id": "DAY", "entry_step": "a", "webinar_time": "13:00", "repeat_offset_minutes": 300,
     "windows": [{"from": "00:00", "to": "11:59", "day": "today"},
                 {"from": "17:00", "to": "23:59", "day": "tomorrow"}]},
    {"id": "EVE", "entry_step": "a", "webinar_time": "18:00", "repeat_offset_minutes": 1140,
     "windows": [{"from": "12:00", "to": "16:59", "day": "today"}]},
]}


def test_chain_and_delay():
    c = cfg([{"key": "a", "text": "Привет", "on_done": "b", "on_done_after_seconds": 90},
             {"key": "b", "text": "Второе"}])
    res = run(c)
    assert sent_at(res, "a") == ["24.09 10:00:00"]
    assert sent_at(res, "b") == ["24.09 10:01:30"]
    assert res["summary"]["sent"] == 2 and not res["summary"]["dropped"]


def test_rule55_same_absolute_time_second_drops():
    """Два шага цепочки на одно время: второй планируется, когда время уже наступило, – выпадает."""
    c = cfg([{"key": "a", "text": "x", "on_done": "call"},
             {"key": "call", "type": "call", "absolute_time_msk": "11:00", "on_done": "circle"},
             {"key": "circle", "type": "video_note", "video_note": "c.mp4", "absolute_time_msk": "11:00",
              "on_done": "c"},
             {"key": "c", "text": "дальше", "absolute_time_msk": "11:30"}])
    res = run(c)
    assert kinds(res, "circle") == ["past_skip"]
    assert sent_at(res, "c") == ["24.09 11:30:00"], "цепочка идёт дальше по on_done"
    # +1 минута чинит (коммит bot-engine 71cd66a0)
    c.steps[2].absolute_time_msk = "11:01"
    res = run(c)
    assert sent_at(res, "circle") == ["24.09 11:01:00"]


def test_rule41_also_fire_lost_on_gated_step():
    c = cfg([{"key": "a", "text": "x", "on_done": "vip"},
             {"key": "vip", "text": "ВИП", "gated_by_not_paid": True, "on_done": "c",
              "also_fire": {"day2": 60}},
             {"key": "c", "text": "c"},
             {"key": "day2", "text": "день 2"}])
    typical = run(c)
    assert sent_at(typical, "day2") == ["24.09 10:01:00"]
    paid = run(c, vip_paid=True)
    assert kinds(paid, "vip") == ["gated", "parallel_lost"]
    assert not sent_at(paid, "day2")
    assert sent_at(paid, "c"), "on_done у пропущенного шага планируется"
    assert any(d["key"] == "day2" and d["reason"] == "parallel_lost" for d in paid["summary"]["dropped"])


def test_skip_if_past_false_goes_with_120s():
    c = cfg([{"key": "a", "text": "x", "on_done": "m"},
             {"key": "m", "text": "утро", "absolute_time_msk": "09:00", "skip_if_past": False, "on_done": "n"},
             {"key": "n", "text": "полдень", "absolute_time_msk": "09:05"}])
    res = run(c)
    ev = next(e for e in res["events"] if e["kind"] == "sent" and e["key"] == "m")
    assert ev["at"] == "24.09 10:02:00" and ev["late_from"].startswith("2026-09-24T09:00")
    assert kinds(res, "n") == ["past_skip"], "у n skip_if_past по умолчанию – выпадает"


def test_min_delay_seconds_future_and_past():
    """min_delay_seconds (bot-engine 23.09): не раньше чем через N с от постановки."""
    c = cfg([{"key": "a", "text": "x", "on_done": "m", "also_fire": {"f": 0}},
             {"key": "m", "text": "утро", "absolute_time_msk": "09:00", "skip_if_past": False,
              "min_delay_seconds": 360},
             {"key": "f", "text": "скоро", "absolute_time_msk": "10:02", "min_delay_seconds": 360}])
    res = run(c)   # регистрация 10:00
    m = next(e for e in res["events"] if e["kind"] == "sent" and e["key"] == "m")
    assert m["at"] == "24.09 10:06:00" and m["late_from"].startswith("2026-09-24T09:00")
    f = next(e for e in res["events"] if e["kind"] == "sent" and e["key"] == "f")
    assert f["at"] == "24.09 10:06:00" and f["min_delay_from"].startswith("2026-09-24T10:02")
    # пропуск по времени min_delay не отменяет
    c.steps[1].skip_if_past = True
    assert kinds(run(c), "m") == ["past_skip"]
    # далёкое время – как было
    c.steps[2].absolute_time_msk = "15:00"
    assert sent_at(run(c), "f") == ["24.09 15:00:00"]


def test_registered_window_gate_uses_entry_send_time():
    c = cfg([{"key": "a", "text": "x", "on_done": "b"},
             {"key": "b", "text": "только утренним", "skip_if_registered_msk": ["11:00-12:00"],
              "on_done": "c"},
             {"key": "c", "text": "всем"}])
    assert sent_at(run(c, "2026-09-24T10:59"), "b")
    late = run(c, "2026-09-24T11:00")
    assert kinds(late, "b") == ["gated"] and sent_at(late, "c"), "окно [11:00, 12:00), цепочка идёт"
    assert sent_at(run(c, "2026-09-24T12:00"), "b"), "правая граница не входит"


def test_absolute_overrides_edge_delay_and_webinar_anchor():
    c = cfg([{"key": "a", "text": "x", "on_done": "pre", "on_done_after_seconds": 5},
             {"key": "pre", "text": "за час", "webinar_offset_minutes": -60, "on_done": "rep"},
             {"key": "rep", "text": "за 30 до повтора", "webinar_offset_minutes": -30, "anchor": "repeat"}],
            branch_router=ROUTER)
    day = run(c, "2026-09-24T10:00")
    assert day["branch"]["id"] == "DAY"
    assert sent_at(day, "pre") == ["24.09 12:00:00"] and sent_at(day, "rep") == ["24.09 17:30:00"]
    eve = run(c, "2026-09-24T14:00")
    assert eve["branch"]["id"] == "EVE"
    assert sent_at(eve, "pre") == ["24.09 17:00:00"] and sent_at(eve, "rep") == ["25.09 12:30:00"]
    late = run(c, "2026-09-24T20:00")
    assert late["branch"]["id"] == "DAY" and sent_at(late, "pre") == ["25.09 12:00:00"]


def test_dnd_moves_night_timer_and_also_fire_ignores_it():
    c = cfg([{"key": "a", "text": "x", "on_done": "b", "on_done_after_seconds": 7200,
              "also_fire": {"p": 7200}},
             {"key": "b", "text": "через 2 часа"},
             {"key": "p", "text": "параллель"}])
    night = LaunchSettings(dnd_enabled=True)
    res = run(c, "2026-09-24T22:00", launch=night)
    b = next(e for e in res["events"] if e["kind"] == "sent" and e["key"] == "b")
    assert b["at"] == "25.09 09:01:00" and b["dnd_from"]
    p = next(e for e in res["events"] if e["kind"] == "sent" and e["key"] == "p")
    assert p["at"] == "25.09 00:00:00" and p.get("no_dnd"), "also_fire без времени ночной режим не соблюдает"


def test_timer_replaced_by_second_path():
    c = cfg([{"key": "a", "text": "x", "on_done": "b", "on_done_after_seconds": 600,
              "also_fire": {"c": 60}},
             {"key": "c", "text": "c", "on_done": "b", "on_done_after_seconds": 3600},
             {"key": "b", "text": "общий шаг"}])
    res = run(c)
    assert "replaced" in kinds(res, "b")
    assert sent_at(res, "b") == ["24.09 11:01:00"], "выжил последний таймер"


def test_dup_within_90s():
    c = cfg([{"key": "a", "text": "x", "on_done": "b", "also_fire": {"b": 30}},
             {"key": "b", "text": "общий"}])
    res = run(c)
    assert [k for k in kinds(res, "b") if k != "end"] == ["sent", "dup"]


def test_claim_cancels_on_done_unless_keeps_chain():
    steps = [{"key": "a", "text": "x", "claim_button_text": "Смотреть", "on_callback": "v",
              "on_done": "b", "on_done_after_seconds": 60},
             {"key": "v", "text": "видео"}, {"key": "b", "text": "дальше", "on_done": "c"},
             {"key": "c", "text": "ещё"}]
    res = run(cfg(steps), claim_after_s=20)
    assert kinds(res, "b") == ["claim_cancel"] and not sent_at(res, "c")
    steps[0]["claim_keeps_chain"] = True
    res = run(cfg(steps), claim_after_s=20)
    assert sent_at(res, "b") and sent_at(res, "c") and sent_at(res, "v")
    res = run(cfg(steps), claim=False)
    assert not sent_at(res, "v")


def test_phone_contact_skip_and_known():
    steps = [{"key": "ph", "type": "phone_request", "text": "телефон?", "on_contact": "ok",
              "on_skip": "no", "on_skip_after_seconds": 180, "skip_if_user_phone_known": True,
              "on_skip_if_phone_known": "known", "also_fire": {"par": 10}},
             {"key": "ok", "text": "спасибо"}, {"key": "no", "text": "жаль"},
             {"key": "known", "text": "знаем"}, {"key": "par", "text": "параллель"}]
    given = run(cfg(steps))
    assert sent_at(given, "ok") == ["24.09 10:00:15"] and not sent_at(given, "no")
    no = run(cfg(steps), contact="no")
    assert sent_at(no, "no") == ["24.09 10:03:00"]
    known = run(cfg(steps), phone_known=True)
    assert sent_at(known, "known") and "parallel_lost" in kinds(known, "ph"), \
        "TG: при известном телефоне also_fire шага не срабатывает"
    mx = run(cfg(steps), platform="max")
    assert sent_at(mx, "known") and sent_at(mx, "par"), "MAX: запрос пропускается, параллель идёт"


def test_survey_waits_and_edit_mode_stops_chain():
    steps = [{"key": "q1", "text": "вопрос", "survey_qid": "q1", "survey_next": "q2", "survey_edit": True,
              "buttons": [{"text": "да", "callback_data": "x"}]},
             {"key": "q2", "text": "вопрос 2", "survey_qid": "q2", "survey_final": True,
              "survey_next": "thanks", "survey_edit": True, "buttons": [{"text": "да", "callback_data": "x"}]},
             {"key": "thanks", "text": "спасибо", "on_done": "gift"},
             {"key": "gift", "text": "подарок"}]
    res = run(cfg(steps))
    assert "edit_end" in kinds(res, "thanks") and not sent_at(res, "gift")
    steps[1]["survey_edit"] = False
    res = run(cfg(steps))
    assert sent_at(res, "gift")
    silent = run(cfg(steps), survey_answer=False)
    assert not sent_at(silent, "q2")


def test_link_click_gate_studio_only():
    """«Кликнул ссылку» – поле Студии, в yaml его нет: симулятор получает его из графа."""
    from studio.convert import import_data
    g = import_data({"id": "t", "steps": [
        {"key": "a", "text": "x", "on_done": "thanks", "on_done_after_seconds": 60},
        {"key": "thanks", "text": "спасибо за клик"}]}, "t")
    g.steps[1].conditions.link_click = "funnel_a"
    opts = lambda clicked: SimOptions(registered_at="2026-09-24T10:00",  # noqa: E731
                                      persona=Persona(link_clicked=clicked))
    assert sent_at(simulate_graph(g, opts(True), REPO, LaunchSettings()), "thanks")
    res = simulate_graph(g, opts(False), REPO, LaunchSettings())
    ev = [e for e in res["events"] if e["key"] == "thanks"]
    assert [(e["kind"], e["condition"]) for e in ev] == [("gated", "link_click")]
    assert "только кликнувшим ссылку «funnel_a…»" in ev[0]["text"]


def test_channel_gate_without_chat_id_skips_in_tg():
    steps = [{"key": "a", "text": "x", "on_done": "nudge"},
             {"key": "nudge", "text": "вступи в канал", "gated_by_channel_joined": "not_joined"}]
    res = run(cfg(steps), launch=LaunchSettings(channel_chat_id=0))
    assert kinds(res, "nudge") == ["gated"]
    res = run(cfg(steps), launch=LaunchSettings(channel_chat_id=-100))
    assert sent_at(res, "nudge")
    res = run(cfg(steps), launch=LaunchSettings(channel_chat_id=-100), channel_joined=True)
    assert kinds(res, "nudge") == ["gated"]
    res = run(cfg(steps), platform="max", launch=LaunchSettings(max_gate_fallback="send"))
    assert sent_at(res, "nudge")


def test_attendance_gate_by_day_of_step():
    steps = [{"key": "a", "text": "x", "also_fire": {"r1": 0, "r2": 0}},
             {"key": "r1", "text": "не был д1", "webinar_offset_minutes": 120,
              "gated_by_attendance": "was_not", "bizon_room": "room"},
             {"key": "r2", "text": "не был д2", "webinar_offset_minutes": 120, "day_offset": 1,
              "gated_by_attendance": "was_not", "bizon_room": "room"}]
    res = run(cfg(steps, branch_router=ROUTER), attended_days=[1])
    assert kinds(res, "r1") == ["gated"] and sent_at(res, "r2")


def test_horizon_and_event_limit():
    steps = [{"key": "a", "text": "x", "on_done": "b"},
             {"key": "b", "text": "через месяц", "absolute_time_msk": "10:00", "day_offset": 30}]
    res = run(cfg(steps))
    assert res["summary"]["truncated"] and "14 суток" in res["summary"]["truncated"]
    loop = cfg([{"key": "a", "text": "x", "on_done": "b", "on_done_after_seconds": 100},
                {"key": "b", "text": "y", "on_done": "a", "on_done_after_seconds": 100}])
    res = simulate(loop, SimOptions(registered_at="2026-09-24T10:00", max_events=50), LaunchSettings())
    assert len(res["events"]) == 50 and res["summary"]["truncated"]


# ── демо-воронки ─────────────────────────────────────────────────────────────

def test_launch_settings_from_repo():
    s = load_launch_settings(REPO, "webinar")
    assert s.found and s.dnd_enabled and s.channel_chat_id != 0
    a = load_launch_settings(REPO, "autowebinar")
    assert a.found and a.channel_chat_id == 0 and not a.dnd_enabled


@pytest.mark.parametrize("when,branch,webinar", [
    ("2026-09-24T09:00", "DAY", "24.09 13:00"),
    ("2026-09-24T12:30", "EVE", "24.09 18:00"),
    ("2026-09-24T17:30", "DAY", "25.09 13:00"),
    ("2026-09-24T23:30", "DAY", "25.09 13:00"),
])
def test_autowebinar_branches(when, branch, webinar):
    """До 12:00 – 13:00 сегодня; 12–17 – 18:00 сегодня; после 17 – 13:00 завтра."""
    res = sim_real(AUTO, when)
    assert res["branch"]["id"] == branch and res["branch"]["webinar_label"] == webinar
    s = res["summary"]
    assert s["sent"] == 19 and not s["truncated"] and not s["stuck"]
    # три дня: у каждого – сообщение «мы в эфире» ровно в час эфира
    assert len(sent_at(res, "d3_live")) == 1


def test_autowebinar_first_morning_goes_late_not_lost():
    """Кружок 9:00 и «меню» 9:05 первого дня – skip_if_past: false. У кружка min_delay_seconds: 360 –
    не посреди регистрации (+6 мин вместо +2), «меню» идёт за ним своими +120 с."""
    res = sim_real(AUTO, "2026-09-24T10:00")
    assert sent_at(res, "d1_circle") == ["24.09 10:06:00"]
    assert sent_at(res, "d1_menu") == ["24.09 10:08:00"]
    assert res["summary"]["late"] == 2


def test_autowebinar_registration_window_gate():
    """Пришёл 11:00–12:00 – «познакомимся» (reg_intro_a/b) не шлём, цепочка идёт дальше."""
    early = sim_real(AUTO, "2026-09-24T10:00")
    assert sent_at(early, "reg_intro_a") and sent_at(early, "reg_intro_b")
    late = sim_real(AUTO, "2026-09-24T11:30")
    assert kinds(late, "reg_intro_a") == ["gated"] and kinds(late, "reg_intro_b") == ["gated"]
    ev = next(e for e in late["events"] if e["kind"] == "gated" and e["key"] == "reg_intro_a")
    assert ev["condition"] == "registered_msk" and "11:30" in ev["text"]
    assert "reg_channel" in {e["key"] for e in late["events"]}, "дальше цепочка идёт"


def test_autowebinar_evening_branch_keeps_mornings_of_next_days():
    """Вечерняя ветка не теряет утро дня N+1: дни – независимые цепочки, а не одна сквозная."""
    res = sim_real(AUTO, "2026-09-24T14:00")
    for d, day in ((2, "25.09"), (3, "26.09")):
        assert sent_at(res, f"d{d}_morning") == [f"{day} 09:00:00"]
    assert not res["summary"]["dropped"]


def test_autowebinar_per_branch_offsets():
    """webinar_offset_by_branch: подготовка за 170 мин до эфира у DAY и за 130 у EVE."""
    day = sim_real(AUTO, "2026-09-24T09:00")
    eve = sim_real(AUTO, "2026-09-24T12:30")
    assert sent_at(day, "d1_prep") == ["24.09 10:10:00"]
    assert sent_at(eve, "d1_prep") == ["24.09 15:50:00"]


def test_autowebinar_vip_buyer_still_gets_all_days():
    """Якорь дней – первое сообщение, а не оффер под гейтом: купивший получает все три дня."""
    res = sim_real(AUTO, "2026-09-24T10:00", vip_paid=True)
    assert sent_at(res, "d3_live")
    assert len([e for e in res["events"] if e["kind"] == "sent" and e["key"].startswith("d3_")]) >= 3
    assert not sent_at(res, "d1_offer"), "оффер купившему не шлём"


def test_nextday_repeat_for_no_shows():
    """Эфир всегда завтра 13:00; не был – повтор 16:01 → 16:32 кружок → 18:00 + SMS."""
    res = sim_real(NEXTDAY, "2026-09-24T10:00")
    assert res["branch"]["webinar_label"] == "25.09 13:00"
    for key, at in (("w_repeat_promo", "25.09 16:01:00"), ("w_sorry_circle", "25.09 16:32:00"),
                    ("w_repeat_live", "25.09 18:00:00"), ("w_sms_repeat", "25.09 18:00:00")):
        assert sent_at(res, key) == [at], key
    was = sim_real(NEXTDAY, "2026-09-24T10:00", attended_days=[1])
    assert kinds(was, "w_repeat_promo") == ["gated"]
    assert sent_at(was, "w_day2"), "день 2 – своя цепочка, был на эфире дня 1 – всё равно получает"


def test_rule55_call_then_circle_one_minute_apart():
    """Звонок за 2 часа и кружок после него не должны стоять на одной минуте: второй выпадает."""
    g = graph(NEXTDAY)
    ok = simulate_graph(g, SimOptions(registered_at="2026-09-24T10:00"), REPO)
    assert sent_at(ok, "w_2h_circle") == ["25.09 11:01:00"]
    bad = g.model_copy(deep=True)
    step = next(s for s in bad.steps if s.key == "w_2h_circle")
    step.schedule.absolute_time_msk = "11:00"
    res = simulate_graph(bad, SimOptions(registered_at="2026-09-24T10:00"), REPO)
    assert kinds(res, "w_2h_circle") == ["past_skip"]


def test_claim_alternative_path_is_not_a_loss():
    """Кнопка и таймер ведут в один шаг – снятый таймер не потеря."""
    steps = [{"key": "a", "text": "x", "claim_button_text": "Начать", "on_callback": "b",
              "on_done": "b", "on_done_after_seconds": 60},
             {"key": "b", "text": "дальше"}]
    res = run(cfg(steps), claim_after_s=20)
    assert not [d for d in res["summary"]["dropped"] if d["reason"] == "claim_cancel"]
    ev = next(e for e in res["events"] if e["kind"] == "claim_cancel")
    assert ev["resolved"]


def test_matrix_on_autowebinar():
    m = matrix(graph(AUTO), date(2026, 9, 24), repo_root=REPO)
    labels = [r["registered_at"][-5:] for r in m["rows"]]
    assert {"09:00", "12:30", "17:30", "23:30", "11:59", "16:59"} <= set(labels)
    by = {r["registered_at"][-5:]: r for r in m["rows"]}
    assert by["12:30"]["branch"] == "EVE" and by["23:30"]["branch"] == "DAY"
    assert all(not r["truncated"] for r in m["rows"])
    # зарегистрировался за минуту до границы ветки – подготовка «за 170 минут» уже в прошлом и выпадает
    assert {d["key"] for d in by["11:59"]["dropped"]} == {"d1_prep"}
    assert {d["key"] for d in by["16:59"]["dropped"]} == {"d1_prep"}
    assert any(s["key"] == "d1_prep" for s in m["steps"])


def test_matrix_moments_fixed_date():
    c = engine_config(graph(WEBINAR))
    ms = [m["registered_at"] for m in matrix_moments(c, date(2026, 9, 10))]
    assert "2026-09-14T12:00" in ms and "2026-09-17T09:00" in ms


# ── API ──────────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path):
    from studio.server import create_app
    return TestClient(create_app(data_dir=tmp_path / "data", dist_dir=tmp_path / "nodist"))


def test_api_simulate_and_matrix(client):
    name = client.post("/api/import", json={"path": AUTO}).json()["name"]
    r = client.post(f"/api/funnels/{name}/simulate",
                    json={"registered_at": "2026-09-24T10:00", "platform": "tg"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["branch"]["id"] == "DAY" and body["summary"]["sent"] == 19
    assert body["days"][0]["title"] == "День регистрации"
    sid = body["events"][1]["step_id"]
    assert sid and body["marks"][sid]["state"] == "sent"
    # несохранённый граф с холста
    g = client.get(f"/api/funnels/{name}").json()
    r = client.post(f"/api/funnels/{name}/simulate",
                    json={"graph": g, "registered_at": "2026-09-24T14:00", "platform": "max",
                          "persona": {"attended_days": [1, 2], "vip_paid": True}})
    assert r.status_code == 200 and r.json()["branch"]["id"] == "EVE"
    r = client.post(f"/api/funnels/{name}/simulate/matrix", json={"date": "2026-09-24"})
    assert r.status_code == 200 and len(r.json()["rows"]) >= 6
    assert client.post(f"/api/funnels/{name}/simulate", json={"registered_at": "вчера"}).status_code == 422
    assert client.post("/api/funnels/nope__x/simulate", json={"registered_at": "2026-09-24T10:00"}).status_code == 404


def test_api_testrun_needs_bot(client, monkeypatch):
    monkeypatch.delenv("STUDIO_TEST_TG_TOKEN", raising=False)
    monkeypatch.delenv("STUDIO_TEST_TG_CHAT", raising=False)
    st = client.get("/api/testrun").json()
    assert st["ready"] is False and "тестовый бот" in st["hint"]
    name = client.post("/api/import", json={"path": AUTO}).json()["name"]
    r = client.post(f"/api/funnels/{name}/testrun", json={})
    assert r.status_code == 400 and "тестовый бот" in r.json()["message"]


def test_testrun_through_fake_telegram(monkeypatch):
    """Тест-прогон без сети: подменяем HTTP и смотрим, что и в каком виде ушло бы."""
    import asyncio
    import json

    import httpx

    from studio.sim import testrun

    monkeypatch.setenv("STUDIO_TEST_TG_TOKEN", "123:TEST")
    monkeypatch.setenv("STUDIO_TEST_TG_CHAT", "42")
    monkeypatch.delenv("STUDIO_TG_PROXY", raising=False)
    calls: list[tuple[str, dict]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        method = req.url.path.rsplit("/", 1)[-1]
        assert "/bot123:TEST/" in req.url.path
        if req.headers.get("content-type", "").startswith("application/json"):
            calls.append((method, json.loads(req.content)))
        else:
            calls.append((method, {"multipart": True}))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(calls)}})

    res = asyncio.run(testrun.run(graph(NEXTDAY), REPO, "2026-09-24T10:00", Persona(), pause_seconds=0,
                                  limit=8, transport=httpx.MockTransport(handler)))
    assert res["ok"] and res["sent"] == 8
    assert calls[0][0] == "sendMessage" and "Тест-прогон" in calls[0][1]["text"]
    assert any(m == "sendPhoto" for m, _ in calls), "картинки из static запуска уходят файлом"
    texts = [d for m, d in calls if m == "sendMessage"]
    kb = [d["reply_markup"] for d in texts if "reply_markup" in d]
    buttons = [b for m in kb for row in m["inline_keyboard"] for b in row]
    assert all(("url" in b) or b.get("callback_data") == "studio_noop" for b in buttons)
    assert all("{Имя}" not in d["text"] for d in texts)


def test_all_funnels_matrix_runs():
    """Матрица веток на всех воронках демо-репозитория – без исключений, без зависаний."""
    from studio.paths import funnel_files
    n = 0
    for p in funnel_files():
        g = import_file(p, repo_root=REPO)
        m = matrix(g, date(2026, 9, 24), "tg", repo_root=REPO)
        assert m["rows"], p
        for r in m["rows"]:
            assert r["sent"] >= 0 and (not r["truncated"] or "14 суток" in r["truncated"]
                                       or "обрезана" in r["truncated"]), (p, r["truncated"])
        n += 1
    assert n == 5
