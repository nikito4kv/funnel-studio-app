"""Отправка шага в Telegram (перенос нужного из platforms/tg/sender.py bot-engine).

Что повторено из движка:
  * подпись к медиа длиннее 1000 символов → медиа отдельно, текст с кнопками
    следом (лимит caption 1024);
  * кружок: send_video_note; не принялся (у человека запрещены кружки) →
    video_fallback обычным видео → текст шага; текст к кружку в обычном
    случае НЕ шлётся (реплика – в самом кружке);
  * видео – с шириной, высотой и длительностью (§35: без них ролик
    «сплющен»);
  * файлы – байтами, file_id кэшируется на (sha256, бот) (§29, §30);
  * заблокировал бота (Forbidden) – это не «ошибка шага», а стоп человеку.

Чего нет в фундаменте: прокси-ссылки и трекинг кликов, rich-статьи,
админ-оверрайды (их заменяют версии Студии), game-хуки.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter,
)
from aiogram.types import FSInputFile, InputMediaPhoto, Message

from studio.model.funnel import Step, StepType

from . import repo
from .engine_lib.video_meta import probe_mp4
from .media import MediaRef, MediaStore
from studio.model.emptiness import visible

log = logging.getLogger("runtime.sender")

CAPTION_SPLIT = 1000


@dataclass
class SendResult:
    ok: bool = True
    message_ids: list[int] = field(default_factory=list)
    error: str | None = None
    blocked: bool = False
    note: str | None = None
    first_message: Message | None = None


class Blocked(Exception):
    """Человек заблокировал бота / удалил аккаунт."""


def _file_id_of(msg: Message, kind: str) -> str | None:
    try:
        if kind == "photo" and msg.photo:
            return msg.photo[-1].file_id
        obj = getattr(msg, kind, None)
        return getattr(obj, "file_id", None)
    except Exception:
        return None


def _is_bad_file_id(e: Exception) -> bool:
    s = str(e).lower()
    return "file identifier" in s or "wrong file" in s or "file_id" in s or "wrong type" in s


class TgSender:
    def __init__(self, pool, media: MediaStore):
        self.pool = pool
        self.media = media

    # ── обёртка вызова API: RetryAfter – подождать и повторить один раз ──
    async def _api(self, fn: Callable[[], Awaitable]):
        try:
            return await fn()
        except TelegramRetryAfter as e:
            wait = min(int(getattr(e, "retry_after", 1) or 1), 30)
            log.warning("Telegram просит подождать %s с (flood) – повторяем", wait)
            await asyncio.sleep(wait)
            return await fn()
        except TelegramForbiddenError as e:
            raise Blocked(str(e)) from e

    async def _send_file(self, bot: Bot, bot_key: int, kind: str, ref: MediaRef,
                         call: Callable[[object], Awaitable[Message]]) -> Message:
        """Файл – кэшированным file_id, иначе байтами (и запомнить file_id)."""
        if ref.kind == "file":
            async with self.pool.acquire() as conn:
                fid = await repo.media_get(conn, ref.sha256, bot_key, kind)
            if fid:
                try:
                    return await self._api(lambda: call(fid))
                except TelegramBadRequest as e:
                    if not _is_bad_file_id(e):
                        raise
                    log.info("file_id из кэша не принят (%s) – шлём файл заново", e)
                    async with self.pool.acquire() as conn:
                        await repo.media_drop(conn, ref.sha256, bot_key, kind)
            msg = await self._api(lambda: call(FSInputFile(ref.value)))
            fid = _file_id_of(msg, kind)
            if fid:
                async with self.pool.acquire() as conn:
                    await repo.media_put(conn, ref.sha256, bot_key, kind, fid)
            return msg
        return await self._api(lambda: call(ref.value))

    @staticmethod
    def _video_kwargs(ref: MediaRef, *, streaming: bool = True) -> dict:
        if ref.kind != "file":
            return {}
        meta = probe_mp4(ref.value) or {}
        out = {}
        if meta.get("width") and meta.get("height"):
            out["width"], out["height"] = meta["width"], meta["height"]
        if meta.get("duration"):
            out["duration"] = meta["duration"]
        if streaming:
            out["supports_streaming"] = True
        return out

    async def send(self, bot: Bot, bot_key: int, chat_id: int, step: Step, text: str,
                   keyboard, *, static_launch: str) -> SendResult:
        """Отправляет шаг. Исключения наружу не летят (кроме BaseException):
        результат говорит, ушло ли, и не заблокировал ли человек бота."""
        res = SendResult()
        if not visible(text):
            text = ""          # «<b> </b>», пробелы – Telegram такое не примет; пусто – не шлём текст
        try:
            if step.type == StepType.video_note:
                await self._video_note(bot, bot_key, chat_id, step, text, keyboard, static_launch, res)
            elif step.type in (StepType.video, StepType.animation):
                await self._video(bot, bot_key, chat_id, step, text, keyboard, static_launch, res)
            else:
                await self._text_or_photo(bot, bot_key, chat_id, step, text, keyboard, static_launch, res)
        except Blocked as e:
            res.ok, res.blocked, res.error = False, True, str(e)
        except Exception as e:  # noqa: BLE001 – ошибка отправки не должна рвать цепочку
            res.ok, res.error = False, f"{type(e).__name__}: {e}"
        if res.ok and not res.message_ids and res.note is None:
            res.note = "нечего отправлять"
        return res

    def _add(self, res: SendResult, msg: Message | None) -> None:
        if msg is None:
            return
        res.message_ids.append(msg.message_id)
        if res.first_message is None:
            res.first_message = msg

    async def _text(self, bot, chat_id, text, keyboard, res):
        if not (text or "").strip():
            return
        msg = await self._api(lambda: bot.send_message(chat_id, text, reply_markup=keyboard))
        self._add(res, msg)

    async def _text_or_photo(self, bot, bot_key, chat_id, step, text, keyboard, static_launch, res):
        images = [i for i in (step.media.images or []) if i]
        if step.type == StepType.photo and len(images) >= 2:
            await self._album(bot, bot_key, chat_id, images, text, keyboard, static_launch, res)
            return
        ref = self.media.resolve(step.media.image, static_launch) if step.media.image else None
        if ref is not None and ref.kind == "missing":
            log.warning("шаг %s: картинки %s нет ни в Студии, ни в static – уходит текстом",
                        step.key, ref.value)
            res.note = f"нет файла {ref.value}"
            ref = None
        if ref is None:
            await self._text(bot, chat_id, text, keyboard, res)
            return
        if len(text or "") > CAPTION_SPLIT:
            msg = await self._send_file(bot, bot_key, "photo", ref,
                                        lambda p: bot.send_photo(chat_id, photo=p))
            self._add(res, msg)
            await self._text(bot, chat_id, text, keyboard, res)
            return
        msg = await self._send_file(
            bot, bot_key, "photo", ref,
            lambda p: bot.send_photo(chat_id, photo=p, caption=text or None, reply_markup=keyboard))
        self._add(res, msg)

    async def _album(self, bot, bot_key, chat_id, images, text, keyboard, static_launch, res):
        media = []
        for i, path in enumerate(images[:10]):
            ref = self.media.resolve(path, static_launch)
            if ref is None or ref.kind == "missing":
                continue
            src = FSInputFile(ref.value) if ref.kind == "file" else ref.value
            cap = text if (i == 0 and text and len(text) <= 1024 and keyboard is None) else None
            media.append(InputMediaPhoto(media=src, caption=cap))
        if not media:
            await self._text(bot, chat_id, text, keyboard, res)
            return
        msgs = await self._api(lambda: bot.send_media_group(chat_id, media=media))
        for m in msgs or []:
            self._add(res, m)
        if not media[0].caption:
            await self._text(bot, chat_id, text, keyboard, res)

    async def _video(self, bot, bot_key, chat_id, step, text, keyboard, static_launch, res):
        kind = "animation" if step.type == StepType.animation else "video"
        path = step.media.animation if kind == "animation" else step.media.video
        ref = self.media.resolve(path, static_launch) if path else None
        if ref is None or ref.kind == "missing":
            if not step.media.media_optional:
                log.warning("шаг %s: ролика %s нет – уходит только текст", step.key, path)
            res.note = f"нет ролика {path}"
            await self._text(bot, chat_id, text, keyboard, res)
            return
        extra = self._video_kwargs(ref, streaming=kind == "video")
        caption_ok = len(text or "") <= CAPTION_SPLIT
        if kind == "animation":
            call = (lambda p: bot.send_animation(chat_id, animation=p,
                                                 caption=(text or None) if caption_ok else None,
                                                 reply_markup=keyboard if caption_ok else None,
                                                 **extra))
        else:
            call = (lambda p: bot.send_video(chat_id, video=p,
                                             caption=(text or None) if caption_ok else None,
                                             reply_markup=keyboard if caption_ok else None,
                                             **extra))
        msg = await self._send_file(bot, bot_key, kind, ref, call)
        self._add(res, msg)
        if not caption_ok:
            await self._text(bot, chat_id, text, keyboard, res)

    async def _video_note(self, bot, bot_key, chat_id, step, text, keyboard, static_launch, res):
        ref = self.media.resolve(step.media.video_note, static_launch) if step.media.video_note else None
        err: str | None = None
        if ref is not None and ref.kind != "missing":
            meta = probe_mp4(ref.value) if ref.kind == "file" else None
            kw = {}
            if meta:
                kw["length"] = min(meta["width"], meta["height"])
                if meta.get("duration"):
                    kw["duration"] = meta["duration"]
            try:
                msg = await self._send_file(bot, bot_key, "video_note", ref,
                                            lambda p: bot.send_video_note(chat_id, video_note=p, **kw))
                self._add(res, msg)
                return
            except Blocked:
                raise
            except Exception as e:  # noqa: BLE001 – кружки запрещены и т.п.
                err = f"{type(e).__name__}: {e}"
        else:
            err = f"нет файла кружка {step.media.video_note}"
        # запасной путь 1: обычное видео
        fb = self.media.resolve(step.media.video_fallback, static_launch) if step.media.video_fallback else None
        if fb is not None and fb.kind != "missing":
            try:
                extra = self._video_kwargs(fb)
                msg = await self._send_file(bot, bot_key, "video", fb,
                                            lambda p: bot.send_video(chat_id, video=p, **extra))
                self._add(res, msg)
                res.note = f"кружок → видео ({err})"
                log.info("шаг %s: кружок не принят (%s) – ушло видео", step.key, err)
                return
            except Blocked:
                raise
            except Exception as e:  # noqa: BLE001
                err = f"{err}; видео: {type(e).__name__}: {e}"
        # запасной путь 2: текст шага
        if (text or "").strip():
            await self._text(bot, chat_id, text, keyboard, res)
            res.note = f"кружок → текст ({err})"
            return
        res.ok, res.error = False, err
