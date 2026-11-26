"""Импорт из Miro: разбор HTML и стикеров, раскладка доски, сверка с эталоном, повторный импорт.

Фикстура – выдуманный снимок фрейма (ответ Miro API без картинок): tests/fixtures/miro/workshop.json,
«ЗАПУСК 17 СЕНТЯБРЯ» – регистрация на практикум, 79 элементов и 27 стрелок, диалект «Запуск»
(зелёные сообщения, белые кнопки, стикеры времени). Правила разбора – русскоязычные, поэтому
тексты на доске русские.
"""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

import httpx
import pytest

from studio.miro.build import build_funnel, miro_step_id
from studio.miro.client import FrameRef, MiroClient, MiroError, parse_link
from studio.miro.html import to_tg_html
from studio.miro.merge import merge_reimport
from studio.miro.stickers import ClaudeParser, RuleParser, default_parser
from studio.model.funnel import EdgeKind, Funnel, Position
from studio.paths import bot_repo_path
from studio.server.roundtrip import export_and_check

FIX = Path(__file__).parent / "fixtures" / "miro"
BOARD = "uXjDemoWorkshop="


def snap(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def build(name: str, **kw):
    return build_funnel(snap(name), launch="webinar", funnel_id="miro", **kw)


def by_key(f: Funnel):
    return {s.key: s for s in f.steps}


def out_edges(f: Funnel, key: str):
    k = {s.id: s.key for s in f.steps}
    s = by_key(f)[key]
    return sorted((e.kind.value, k.get(e.to_step), e.delay_seconds) for e in f.edges if e.from_step == s.id)


# ── HTML и стикеры ──

def test_html_to_telegram():
    src = ('<p><strong>Имя, подтвердите</strong></p><p><br />Нажмите — так&#x1f447;</p>'
           '<p><span style="color:red">x &lt; y</span> <a href="https://t.me/+A">https://t.me/+A</a></p>'
           '<p><strong style="c">&#x1f449; </strong><strong><a href="https://v">Забрать</a></strong></p>')
    out = to_tg_html(src)
    assert out.splitlines()[0] == "<b>Имя, подтвердите</b>"
    assert "Нажмите — так👇" in out
    assert "x &lt; y" in out and "span" not in out
    assert '<b>👉 <a href="https://v">Забрать</a></b>' in out
    assert "\n\n\n" not in out


@pytest.mark.parametrize("text,delay,event,conds,skip", [
    ("через 3 минуты если не перешел в канал", 180, None, {"channel": "not_joined"}, None),
    ("10 секунд если не хочет оставлять контакт", 10, "no_contact", {}, None),
    ("сразу если оставляет контакт", 0, "contact", {}, None),
    ("если контакт ранее оставлял -- пропускаем этот этап", None, None, {}, "phone"),
    ("через 4 часа если НЕ нажал на кнопку випа", 14400, None, {"not_paid": True}, None),
    ("через час", 3600, None, {}, None),
    ("10секунд", 10, None, {}, None),
    ("СРАЗУ", 0, None, {}, None),
    ("через 1 час 30 минут", 5400, None, {}, None),
])
def test_rule_parser(text, delay, event, conds, skip):
    m = RuleParser().parse(text)
    assert (m.delay_seconds, m.event, m.conditions, m.skip_if_known) == (delay, event, conds, skip)
    assert m.confidence == 1.0


def test_rule_parser_not_sure():
    assert RuleParser().parse("чатик с лизой тг").confidence == 0.0
    m = RuleParser().parse("через 2 минуты если остановился")
    assert m.unsupported and m.delay_seconds == 120


# ── Claude API: фальшивый клиент ──

class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Resp:
    def __init__(self, data, stop="end_turn"):
        self.content = [_Block(json.dumps(data))]
        self.stop_reason = stop


class FakeAnthropic:
    def __init__(self, data, stop="end_turn"):
        self.calls = []
        self.messages = self
        self._data, self._stop = data, stop

    def create(self, **kw):
        self.calls.append(kw)
        return _Resp(self._data, self._stop)


def test_claude_parser_only_when_rules_unsure():
    fake = FakeAnthropic({"delay_seconds": 600, "event": "none", "condition": "not_in_channel",
                          "skip_if_known": "none", "unsupported": "", "confidence": 0.9})
    p = ClaudeParser(client=fake)
    sure = p.parse("через 3 минуты если не перешел в канал")
    assert sure.source == "rules" and not fake.calls
    got = p.parse("минут через десять, если так и не заглянул в канал")
    assert fake.calls, "неуверенное правило должно уйти в Claude"
    assert got.source == "claude" and got.delay_seconds == 600 and got.conditions == {"channel": "not_joined"}
    assert got.confidence == 0.9
    req = fake.calls[0]
    assert req["model"] == "claude-opus-5"
    assert req["output_config"]["format"]["type"] == "json_schema"


def test_claude_parser_falls_back_on_refusal():
    fake = FakeAnthropic({}, stop="refusal")
    got = ClaudeParser(client=fake).parse("что-то непонятное")
    assert got.source == "rules" and got.confidence == 0.0


def test_default_parser_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(default_parser(), RuleParser)


# ── клиент Miro ──

def test_parse_link():
    r = parse_link("https://miro.com/app/board/uXjDemoWorkshop=/?moveToWidget=7000000000000000316&cot=14")
    assert r == FrameRef(BOARD, "7000000000000000316")
    assert parse_link("https://miro.com/app/board/uXjDemoWorkshop%3D/?focusWidget=12").item_id == "12"
    assert parse_link("uXjDemoWorkshop= 7000000000000000316").item_id == "7000000000000000316"
    with pytest.raises(MiroError):
        parse_link("https://example.com/")


def fake_miro(s: dict, page: int = 20):
    """Фальшивый Miro API по снимку: страницы с курсором, картинки с redirect=false."""
    items = s["items"]
    frame = s["frame"]

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer t0k"
        assert req.method == "GET"
        path, q = req.url.path, dict(req.url.params)
        if path.endswith(f"/items/{frame['id']}"):
            return httpx.Response(200, json=frame)
        if path.endswith("/items") or path.endswith("/connectors"):
            data = items if path.endswith("/items") else s["connectors"]
            if q.get("parent_item_id") and q["parent_item_id"] != str(frame["id"]):
                data = []
            start = int(q.get("cursor") or 0)
            chunk = data[start:start + page]
            nxt = str(start + page) if start + page < len(data) else None
            return httpx.Response(200, json={"data": chunk, **({"cursor": nxt} if nxt else {})})
        if "/resources/images/" in path:
            assert q.get("format") == "original" and q.get("redirect") == "false"
            return httpx.Response(200, json={"url": "https://files.example/img.png"})
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)


def test_client_fetch_frame_pages():
    s = snap("workshop")
    with MiroClient("t0k", transport=fake_miro(s)) as cl:
        got = cl.fetch_frame(FrameRef(BOARD, str(s["frame"]["id"])))
    assert len(got["items"]) == len(s["items"])
    assert len(got["connectors"]) == len(s["connectors"])
    with pytest.raises(MiroError):
        MiroClient("")


# ── раскладка: workshop ──

def test_workshop_structure():
    r = build("workshop")
    f = r.funnel
    s = by_key(f)
    assert [x.key for x in f.steps][:3] == ["m01", "m02", "m03"]
    # вход – запрос телефона; «уже известен» – сразу в кружок
    assert s["m01"].type.value == "phone_request" and s["m01"].contact.skip_if_known
    assert s["m01"].contact.contact_button == "ОТПРАВИТЬ КОНТАКТ"
    assert out_edges(f, "m01") == [("contact", "m02", 0), ("skip", "m02", 10), ("skip_if_phone_known", "m02", 0)]
    # кружок: сценарий – в «что говорит спикер», в MAX подпись скрыта
    assert s["m02"].type.value == "video_note" and s["m02"].content.tg.text.startswith("Ребят, привет!")
    assert s["m02"].content.max.text == ""
    assert s["m02"].title.startswith("Кружок: Ребят, привет!")
    # приветствие: две параллельные стрелки, ссылки – плейсхолдеры
    assert out_edges(f, "m03") == [("also_fire", "m04", 180), ("also_fire", "m05", 1800)]
    assert {b.url for b in s["m03"].content.tg.buttons} == {"{channel.link}"}
    assert s["m04"].conditions.channel == "not_joined"
    assert "{channel.link}" in s["m04"].content.tg.text and "—" not in s["m04"].content.tg.text
    # ВИП: условие «не оплатил», кнопка – {vip_link}, заглушка «[ССЫЛКА НА VIP]» убрана
    assert s["m06"].conditions.not_paid and s["m06"].content.tg.buttons[0].url == "{vip_link}"
    assert "[ССЫЛКА НА VIP]" not in s["m06"].content.tg.text
    assert out_edges(f, "m06") == [("done", "m08", 14400)]
    # стрелки «ВИП-дожим → опрос» на доске нет – проведена по стикеру «через час»
    assert out_edges(f, "m08") == [("done", "m09", 3600)]
    assert any(d.code == "edge_inferred" for d in s["m09"].miro.doubts)
    # опрос: claim → q1 → q2 (несколько) → q3 (последний, без правки – дальше почта)
    assert s["m09"].content.tg.claim_button_text == "ПРОЙТИ ОПРОС"
    assert out_edges(f, "m09") == [("callback", "q1", None)]
    assert s["q2"].survey.multi and s["q2"].survey.done_button == "✅ Готово, дальше"
    assert s["q3"].survey.final and not s["q3"].survey.edit
    assert s["m10"].type.value == "email_request" and s["m10"].email.skip_if_known
    assert out_edges(f, "m10") == [("email", "m11", 0), ("skip", "m11", 600), ("skip_if_email_known", "m11", 0)]
    # письмо после оплаты – не сообщение бота
    assert not any("Тема:" in x.content.tg.text for x in f.steps)
    assert r.stats["messages"] == 15


def test_workshop_doubts_and_ids():
    r1, r2 = build("workshop"), build("workshop")
    assert [s.id for s in r1.funnel.steps] == [s.id for s in r2.funnel.steps]
    first = r1.funnel.steps[0]
    assert first.id == miro_step_id(BOARD, first.miro.item_id)
    codes = {d["code"] for d in r1.doubts()}
    assert {"video_note_file", "nudge", "back_to_survey", "edge_inferred", "second_channel"} <= codes
    # дожим посреди опроса не встроен в цепочку (как в эталоне – его шлёт game-модуль)
    nudge = next(s for s in r1.funnel.steps if s.content.tg.text.startswith("<b>Осталось совсем"))
    assert not any(e.to_step == nudge.id for e in r1.funnel.edges)


def test_workshop_exports_to_engine():
    res = export_and_check(build("workshop").funnel, bot_repo_path())
    assert res["ok"], res


def test_images_fetched_through_callback():
    got = []

    def fetch(it):
        got.append(it.id)
        return f"./static/{it.id}.jpg"
    f = build("workshop", fetch_image=fetch).funnel
    assert len(got) == 7
    assert all(s.media.image.startswith("./static/") for s in f.steps if s.media.image)


# ── повторный импорт ──

def _edit_board(s: dict) -> dict:
    s = copy.deepcopy(s)
    for it in s["items"]:
        if it["id"] == "7000000000000000008":     # второй дожим по каналу – другой текст
            it["data"]["content"] = it["data"]["content"].replace("пока ты читаешь", "пока ты ЧИТАЕШЬ")
        if it["id"] == "7000000000000000526":     # стикер «через 3 минуты» → 5 минут
            it["data"]["content"] = "<p>через 5 минут если не перешел в канал</p>"
    # дожим посреди опроса убрали с доски
    s["items"] = [i for i in s["items"] if i["id"] not in ("7000000000000000120", "7000000000000000239")]
    # новое сообщение в конце: зелёный прямоугольник со стрелкой от подарка
    s["items"].append({"id": "999", "type": "shape", "data": {"shape": "rectangle",
                       "content": "<p>Новое сообщение с доски</p>"}, "style": {"fillColor": "#adf0c7"},
                       "geometry": {"width": 1309, "height": 400},
                       "position": {"x": 29500, "y": 4200, "origin": "center"}})
    s["connectors"].append({"id": "998", "startItem": {"id": "7000000000000000022"},
                            "endItem": {"id": "999"}, "shape": "straight"})
    s["items"].append({"id": "997", "type": "sticky_note", "data": {"content": "<p>через 10 минут</p>"},
                       "style": {"fillColor": "blue"}, "geometry": {"width": 368, "height": 422},
                       "position": {"x": 28300, "y": 4150, "origin": "center"}})
    return s


def test_reimport_keeps_manual_edits():
    cur = build("workshop").funnel
    s = by_key(cur)
    s["m04"].content.tg.text += "\nРУЧНАЯ ПРАВКА"
    s["m03"].key = "welcome"
    s["m02"].media.video_note = "./static/circle1.mp4"     # ролик загрузили в Студии
    manual_id = uuid.uuid4()
    for i, x in enumerate(cur.steps):
        cur.layout.positions[x.id] = Position(x=i * 400, y=0)
    fresh = build_funnel(_edit_board(snap("workshop")), launch="webinar", funnel_id="miro").funnel
    merged, rep = merge_reimport(cur, fresh)
    m = by_key(merged)
    assert "welcome" in m and m["welcome"].id == s["m03"].id          # key и id сохранились
    assert m["m04"].content.tg.text.endswith("РУЧНАЯ ПРАВКА")         # ручная правка текста
    assert m["m02"].media.video_note == "./static/circle1.mp4"         # поле, которого нет на доске
    assert "ЧИТАЕШЬ" in m["m07"].content.tg.text                       # изменение доски
    assert rep["removed"] == ["m12"] and not any(x.key == "m12" for x in merged.steps)
    assert rep["added"], "новое сообщение с доски добавлено"
    new = next(x for x in merged.steps if x.content.tg.text == "Новое сообщение с доски")
    assert new.id in merged.layout.positions                           # и получило место на холсте
    delay = next(e.delay_seconds for e in merged.edges if e.to_step == m["m04"].id)
    assert delay == 300
    assert manual_id not in {x.id for x in merged.steps}
    Funnel.model_validate(merged.model_dump(mode="json", by_alias=True))


def test_reimport_conflict_takes_board():
    cur = build("workshop").funnel
    by_key(cur)["m07"].content.tg.text = "моё"
    fresh = build_funnel(_edit_board(snap("workshop")), launch="webinar", funnel_id="miro").funnel
    merged, rep = merge_reimport(cur, fresh)
    s = by_key(merged)["m07"]
    assert "ЧИТАЕШЬ" in s.content.tg.text
    assert any(d.code == "merge_conflict" for d in s.miro.doubts)
    assert rep["conflicts"] == [{"key": "m07", "field": "content.tg.text"}]


def test_reimport_keeps_manual_steps_and_edges():
    cur = build("workshop").funnel
    from studio.model.funnel import Edge, Step
    extra = Step(id=uuid.uuid4(), key="my_step", content={"tg": {"text": "руками"}})
    cur.steps.append(extra)
    last = by_key(cur)["m11"]
    cur.edges.append(Edge(id=uuid.uuid4(), from_step=last.id, kind=EdgeKind.done, to_step=extra.id,
                          delay_seconds=60))
    merged, _ = merge_reimport(cur, build("workshop").funnel)
    assert any(s.key == "my_step" for s in merged.steps)
    assert any(e.to_step == extra.id and e.delay_seconds == 60 for e in merged.edges)
