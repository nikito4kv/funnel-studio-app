"""Тест-прогон: отправить воронку себе через ТЕСТОВОГО бота, в сжатом времени.

Порядок и состав сообщений – из симулятора (Telegram, «типовой человек» или
заданная персона): что человек получил бы за две недели, приходит подряд с
паузой 1–2 с. Перед каждым сообщением – строчка «когда и какой шаг».

  STUDIO_TEST_TG_TOKEN – токен тестового бота (НЕ боевого – боевые токены
                         bot-engine здесь не читаются и не нужны);
  STUDIO_TEST_TG_CHAT  – куда слать (свой id в Telegram; бот должен быть запущен);
  STUDIO_TG_PROXY      – прокси до api.telegram.org, если нужен (http:// или socks5://).

Кнопки-ссылки остаются ссылками, кнопки-действия («Забрать», ответы опроса) –
неактивны: бот тест-прогона ничего не слушает, нажатие ничего не делает.
Картинки берутся из static запуска в bot-engine (или загруженные в Студию),
кружки и видео – строчкой-заменителем: гонять десятки мегабайт ради проверки
порядка незачем.
"""
from __future__ import annotations

import asyncio
import html
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytz

from ..model.funnel import Funnel
from .models import Persona, SimOptions
from .run import engine_config, key_ids, launch_for, link_clicks
from .simulator import simulate

MSK = pytz.timezone("Europe/Moscow")
API = "https://api.telegram.org"
CAPTION_LIMIT = 1024


class TestRunError(RuntimeError):
    pass


def status() -> dict[str, Any]:
    token = os.environ.get("STUDIO_TEST_TG_TOKEN", "").strip()
    chat = os.environ.get("STUDIO_TEST_TG_CHAT", "").strip()
    ready = bool(token and chat)
    return {
        "ready": ready,
        "proxy": bool(os.environ.get("STUDIO_TG_PROXY", "").strip()),
        "hint": "" if ready else "нужен тестовый бот (STUDIO_TEST_TG_TOKEN и STUDIO_TEST_TG_CHAT у сервера)",
    }


_SAMPLE = {"{Имя}": "Тест", "{имя}": "Тест", "{name}": "Тест", "{Name}": "Тест"}


def render(text: str, webinar: datetime | None) -> str:
    out = text or ""
    for k, v in _SAMPLE.items():
        out = out.replace(k, v)
    if webinar is not None:
        out = out.replace("{webinar.time}", webinar.strftime("%H:%M"))
        out = out.replace("{webinar.date}", webinar.strftime("%d.%m"))
        out = out.replace("{webinar.tgtime}", webinar.strftime("%d.%m в %H:%M"))
    return out


def plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or ""))


def keyboard(step, claim: str | None) -> dict | None:
    rows: list[list[dict]] = []
    for b in step.buttons or []:
        if b.url and re.match(r"^(https?://|tg://)", b.url) and "{" not in b.url:
            rows.append([{"text": b.text, "url": b.url}])
        else:
            rows.append([{"text": f"{b.text} (в тест-прогоне неактивна)", "callback_data": "studio_noop"}])
    if claim:
        rows.append([{"text": f"{claim} (неактивна)", "callback_data": "studio_noop"}])
    return {"inline_keyboard": rows} if rows else None


def _media_file(ref: str | None, launch: str, repo_root: Path | None, media_dir: Path | None) -> Path | None:
    """./static/x.jpg, ./static_extra/x.jpg … – файл в папке запуска (или загруженный в Студию)."""
    if not ref or not ref.startswith("./"):
        return None
    if ref.startswith("./static/") and media_dir is not None:
        own = media_dir / ref[len("./static/"):]
        if own.is_file():
            return own
    if repo_root is not None and launch:
        base = (repo_root / "launches" / launch).resolve()
        p = (base / ref[2:]).resolve()
        if base in p.parents and p.is_file():
            return p
    return None


class _Bot:
    def __init__(self, token: str, chat: str, client: httpx.AsyncClient):
        self.base = f"{API}/bot{token}"
        self.chat = chat
        self.c = client

    async def call(self, method: str, data: dict, files: dict | None = None) -> dict:
        try:
            if files:
                form = {k: (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
                        for k, v in data.items()}
                r = await self.c.post(f"{self.base}/{method}", data=form, files=files)
            else:
                r = await self.c.post(f"{self.base}/{method}", json=data)
        except httpx.HTTPError as e:
            raise TestRunError(f"Telegram не отвечает ({type(e).__name__}) – нужен прокси STUDIO_TG_PROXY?") from e
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if not body.get("ok"):
            return {"ok": False, "description": body.get("description") or f"HTTP {r.status_code}"}
        return body

    async def text(self, text: str, kb: dict | None = None) -> dict:
        data: dict[str, Any] = {"chat_id": self.chat, "text": text[:4096], "parse_mode": "HTML",
                                "disable_web_page_preview": True}
        if kb:
            data["reply_markup"] = kb
        res = await self.call("sendMessage", data)
        if not res.get("ok"):   # кривой HTML в тексте – шлём без разметки, чтобы прогон не встал
            data.pop("parse_mode")
            data["text"] = plain(text)[:4096]
            res = await self.call("sendMessage", data)
        return res

    async def photo(self, path: Path, caption: str, kb: dict | None) -> dict:
        data: dict[str, Any] = {"chat_id": self.chat, "caption": caption, "parse_mode": "HTML"}
        if kb:
            data["reply_markup"] = kb
        with path.open("rb") as fh:
            res = await self.call("sendPhoto", data, files={"photo": (path.name, fh.read())})
        return res


async def run(f: Funnel, repo_root: Path | None, registered_at: str | None, persona: Persona,
              pause_seconds: float = 1.5, limit: int = 60, media_dir: Path | None = None,
              transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    token = os.environ.get("STUDIO_TEST_TG_TOKEN", "").strip()
    chat = os.environ.get("STUDIO_TEST_TG_CHAT", "").strip()
    if not token or not chat:
        raise TestRunError(status()["hint"])
    reg = registered_at or datetime.now(MSK).strftime("%Y-%m-%dT%H:%M")
    cfg = engine_config(f)
    launch = launch_for(f, repo_root)
    res = simulate(cfg, SimOptions(registered_at=reg, platform="tg", persona=persona), launch, key_ids(f),
                   link_clicks(f))
    webinar = datetime.fromisoformat(res["branch"]["webinar"]) if (res["branch"] or {}).get("webinar") else None
    sent = [e for e in res["events"] if e["kind"] == "sent"][: max(1, min(limit, 300))]
    proxy = os.environ.get("STUDIO_TG_PROXY", "").strip() or None
    launch_name = f.source.launch if f.source else ""
    out: list[dict] = []
    async with httpx.AsyncClient(timeout=30, proxy=proxy, transport=transport) as client:
        bot = _Bot(token, chat, client)
        head = await bot.text(f"🧪 <b>Тест-прогон «{html.escape(f.title or f.id)}»</b>\n"
                              f"регистрация {reg.replace('T', ' ')} МСК, сообщений: {len(sent)}"
                              f"{' (из ' + str(sum(1 for e in res['events'] if e['kind'] == 'sent')) + ')' if len(sent) < res['summary']['sent'] else ''}")
        if not head.get("ok"):
            raise TestRunError(f"Telegram отказал: {head.get('description')} – проверьте токен и chat id")
        for e in sent:
            st = cfg.get_step(e["key"])
            if st is None:
                continue
            tag = f"<i>⏱ {e['at'][:-3]} · {html.escape(st.key)}</i>"
            body = render(st.text or "", webinar)
            kb = keyboard(st, st.claim_button_text)
            pic = _media_file(st.image or (st.images or [None])[0], launch_name, repo_root, media_dir) \
                if st.type == "photo" else None
            if pic is not None:
                if len(body) + len(tag) + 1 <= CAPTION_LIMIT:
                    r = await bot.photo(pic, f"{tag}\n{body}", kb)
                else:
                    r = await bot.photo(pic, tag, None)
                    if r.get("ok"):
                        r = await bot.text(body, kb)
                if not r.get("ok"):
                    r = await bot.text(f"{tag}\n[картинка {pic.name} не ушла: {r.get('description')}]\n{body}", kb)
            else:
                stub = ""
                if st.type in ("video_note", "video", "animation", "rich") or (st.type == "photo"):
                    ref = st.video_note or st.video or st.animation or st.rich or st.image or ""
                    kind = {"video_note": "кружок", "video": "видео", "animation": "GIF", "rich": "статья",
                            "photo": "картинка"}[st.type]
                    stub = f"[{kind}: {html.escape(ref.split('/')[-1]) or '–'}]\n"
                elif st.type in ("call", "sms"):
                    stub = "[автозвонок]\n" if st.type == "call" else f"[SMS] {html.escape(st.sms_text or '')}\n"
                elif st.type == "phone_request":
                    stub = "[кнопка «поделиться контактом»]\n"
                r = await bot.text(f"{tag}\n{stub}{body}".strip(), kb)
            out.append({"key": st.key, "ok": bool(r.get("ok")), "error": r.get("description")})
            await asyncio.sleep(pause_seconds)
    return {"ok": all(x["ok"] for x in out), "sent": len(out), "results": out,
            "errors": [x for x in out if not x["ok"]]}
