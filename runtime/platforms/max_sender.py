"""Отправка шага в MAX (перенос нужного из bot-engine
`common/launch_engine/platforms/max/sender.py`, коммит f171a355).

Как в движке bot-engine:
  * текст – HTML MAX (`max_html.html_to_max`), лимита подписи 1024 нет:
    медиа и текст с кнопками уходят ОДНИМ сообщением;
  * кружок: круглых видео в MAX нет – уходит обычным видео, текст шага –
    подписью (если его не погасили `content.max.text = ""` или
    `max_video_note_caption: false`); нет ролика – `video_fallback`;
  * видео/кружок без файла: шаг молча пропускается (цепочка идёт дальше),
    с `media_optional` – уходит один текст;
  * картинки ≥ 2 – альбом одним сообщением; картинки нет – уходит текст;
  * file_id Telegram в MAX не годится – считается «файла нет».

Отличия от bot-engine (по устройству):
  * файл заливается в MAX БАЙТАМИ, токен вложения кэшируется в media_cache по
    (sha256, бот, вид «max_image»/«max_video») – как file_id у Telegram.
    В bot-engine MAX скачивал картинку по ссылке с нашего домена при каждой
    отправке; у Студии своего публичного static нет;
  * токен из кэша не принят – кэш сбрасывается, файл заливается заново.
"""
from __future__ import annotations

import logging
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path

from studio.model.funnel import Step, StepType

from .. import repo
from ..media import MediaRef, MediaStore
from ..sender import SendResult
from .max_api import MaxApi, MaxApiError, is_block_error
from .max_html import html_to_max
from studio.model.emptiness import visible

log = logging.getLogger("runtime.max.sender")


@dataclass
class MaxSendResult(SendResult):
    ext_ids: list[str] = field(default_factory=list)


class _Blocked(Exception):
    pass


def keyboard(rows: list[list[dict]]) -> dict | None:
    return {"type": "inline_keyboard", "payload": {"buttons": rows}} if rows else None


def _mid(resp) -> str | None:
    if not isinstance(resp, dict):
        return None
    msg = resp.get("message") or {}
    return (msg.get("body") or {}).get("mid") or msg.get("mid")


class MaxSender:
    def __init__(self, pool, media: MediaStore):
        self.pool = pool
        self.media = media

    async def send(self, api: MaxApi, bot_key: int, uid: int, step: Step, text: str,
                   rows: list[list[dict]], *, static_launch: str) -> MaxSendResult:
        """Исключения наружу не летят: результат говорит, ушло ли и не
        заблокировал ли человек бота."""
        res = MaxSendResult()
        kb = keyboard(rows)
        text = html_to_max(text or "") if visible(text) else ""
        try:
            if step.type in (StepType.video_note, StepType.video, StepType.animation):
                await self._video(api, bot_key, uid, step, text, kb, static_launch, res)
            else:
                await self._photo_or_text(api, bot_key, uid, step, text, kb, static_launch, res)
        except _Blocked as e:
            res.ok, res.blocked, res.error = False, True, str(e)
        except Exception as e:  # noqa: BLE001 – ошибка отправки не рвёт цепочку
            if is_block_error(e):
                res.ok, res.blocked, res.error = False, True, str(e)
            else:
                res.ok, res.error = False, f"{type(e).__name__}: {e}"
        if res.ok and not res.ext_ids and res.note is None:
            res.note = "нечего отправлять"
        return res

    # ── низ ──────────────────────────────────────────────────────────────
    async def _post(self, api, uid, text, attachments, res) -> None:
        try:
            resp = await api.send_message(user_id=uid, text=text, attachments=attachments or None)
        except MaxApiError as e:
            if is_block_error(e):
                raise _Blocked(str(e)) from e
            raise
        mid = _mid(resp)
        if mid:
            res.ext_ids.append(str(mid))

    async def _text(self, api, uid, text, kb, res) -> None:
        if not text.strip() and kb is None:
            return
        await self._post(api, uid, text, [kb] if kb else None, res)

    async def _bytes_of(self, api: MaxApi, ref: MediaRef) -> tuple[bytes, str, str, str]:
        """→ (байты, sha256, имя файла, content-type)."""
        import hashlib
        if ref.kind == "file":
            p = Path(ref.value)
            data = p.read_bytes()
            ct = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            return data, ref.sha256 or hashlib.sha256(data).hexdigest(), p.name, ct
        data, ct = await api.download(ref.value)
        name = ref.value.rsplit("/", 1)[-1].split("?", 1)[0] or "file"
        return data, hashlib.sha256(data).hexdigest(), name, ct

    async def _attach(self, api: MaxApi, bot_key: int, kind: str, ref: MediaRef, *,
                      fresh: bool = False) -> dict:
        """Вложение image/video: токен из кэша или заливка байтами."""
        ckind = f"max_{kind}"
        if ref.kind == "file" and ref.sha256 and not fresh:
            async with self.pool.acquire() as conn:
                tok = await repo.media_get(conn, ref.sha256, bot_key, ckind)
            if tok:
                return {"type": kind, "payload": {"token": tok}, "_cached": ref.sha256}
        data, sha, name, ct = await self._bytes_of(api, ref)
        if not fresh:
            async with self.pool.acquire() as conn:
                tok = await repo.media_get(conn, sha, bot_key, ckind)
            if tok:
                return {"type": kind, "payload": {"token": tok}, "_cached": sha}
        tok = await api.upload(kind, data, name, ct)
        async with self.pool.acquire() as conn:
            await repo.media_put(conn, sha, bot_key, ckind, tok)
        return {"type": kind, "payload": {"token": tok}}

    async def _send_with_media(self, api, bot_key, uid, kind, refs: list[MediaRef], text, kb, res):
        atts = [await self._attach(api, bot_key, kind, r) for r in refs]
        clean = [{k: v for k, v in a.items() if k != "_cached"} for a in atts]
        try:
            await self._post(api, uid, text, clean + ([kb] if kb else []), res)
        except MaxApiError as e:
            cached = [a.get("_cached") for a in atts if a.get("_cached")]
            if not cached or not (400 <= e.code < 500):
                raise
            # токен из кэша не принят (протух / от другого бота) – заливаем заново
            log.info("MAX: токен вложения из кэша не принят (%s) – заливаем файл заново", e)
            async with self.pool.acquire() as conn:
                for sha in cached:
                    await repo.media_drop(conn, sha, bot_key, f"max_{kind}")
            atts = [await self._attach(api, bot_key, kind, r, fresh=True) for r in refs]
            await self._post(api, uid, text, atts + ([kb] if kb else []), res)

    def _resolve(self, path: str | None, static_launch: str) -> MediaRef | None:
        """Файл или ссылка; file_id Telegram и отсутствующий файл → None."""
        ref = self.media.resolve(path, static_launch) if path else None
        if ref is None or ref.kind in ("missing", "file_id"):
            return None
        return ref

    # ── шаги ─────────────────────────────────────────────────────────────
    async def _photo_or_text(self, api, bot_key, uid, step, text, kb, static_launch, res):
        imgs = [r for r in (self._resolve(p, static_launch) for p in (step.media.images or []) if p) if r]
        if step.type == StepType.photo and len(imgs) >= 2:
            try:
                await self._send_with_media(api, bot_key, uid, "image", imgs[:10], text, kb, res)
                return
            except _Blocked:
                raise
            except Exception as e:  # noqa: BLE001 – альбом не собрался – шлём одну картинку
                log.warning("MAX: альбом шага %s не ушёл (%s) – шлём одну картинку", step.key, e)
        ref = self._resolve(step.media.image, static_launch) if step.media.image else None
        if step.media.image and ref is None:
            log.warning("шаг %s: картинки %s для MAX нет – уходит текстом", step.key, step.media.image)
            res.note = f"нет файла {step.media.image}"
        if ref is None and imgs:
            ref = imgs[0]
        if ref is None:
            await self._text(api, uid, text, kb, res)
            return
        await self._send_with_media(api, bot_key, uid, "image", [ref], text, kb, res)

    async def _video(self, api, bot_key, uid, step, text, kb, static_launch, res):
        m = step.media
        src = m.video_note if step.type == StepType.video_note else (
            m.video if step.type == StepType.video else m.animation)
        ref = self._resolve(src, static_launch) or self._resolve(m.video_fallback, static_launch)
        optional = m.media_optional and bool(text.strip())
        if ref is None:
            if optional:
                res.note = f"нет ролика {src} – ушёл один текст"
                await self._text(api, uid, text, kb, res)
            else:
                res.note = f"нет ролика {src} – в MAX шаг пропущен"
                log.info("шаг %s: ролика %s для MAX нет – пропускаем, цепочка идёт дальше",
                         step.key, src)
            return
        try:
            await self._send_with_media(api, bot_key, uid, "video", [ref], text, kb, res)
            if step.type == StepType.video_note:
                res.note = "кружок → видео"
        except _Blocked:
            raise
        except Exception as e:  # noqa: BLE001
            if not optional:
                raise
            log.warning("шаг %s: видео в MAX не ушло (%s) – шлём один текст", step.key, e)
            await self._text(api, uid, text, kb, res)
            res.note = f"видео не ушло ({type(e).__name__}) – ушёл текст"
