"""Хранилище ботов раздела «Боты»: `STUDIO_DATA/bots.json`, права 600.

Что где лежит:
  * про бота – id, @username, имя, последние 4 символа токена, когда добавлен – открытым текстом;
  * сам токен – зашифрован (Fernet: AES-128-CBC + HMAC-SHA256, пакет `cryptography`).

Ключ шифрования – НЕ в папке данных:
  * `STUDIO_SECRET_KEY` из окружения (на сервере – строка в `/opt/stacks/funnel-studio/.env`, 600);
  * нет переменной – файл `STUDIO_SECRET_KEY_FILE` (по умолчанию `~/.funnel-studio/studio_secret.key`,
    создаётся сам со случайным ключом, 600) – для запуска на своём ПК.

Почему шифрование, а не просто файл 600. Папку данных копируют целиком: перенос на сервер
24.09 шёл копией `data/`, её же берут в бэкап и для разбора ошибок. Токены боевых ботов в
открытом виде уехали бы вместе с ней. Зашифрованный файл без ключа из `.env` бесполезен, а
ключ живёт отдельно. От того, у кого есть и `.env`, и `data/` (root на сервере), шифрование
не защищает – от него не защищает ничто, кроме отдельного хранилища секретов (этап 4 – пульт).

Отпечаток ключа хранится в файле: сменили ключ – раздел честно говорит «токены надо добавить
заново», а не падает на расшифровке.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

DEFAULT_KEY_FILE = Path.home() / ".funnel-studio" / "studio_secret.key"
FILE_NAME = "bots.json"


class VaultError(RuntimeError):
    pass


def _fernet(secret: str) -> Fernet:
    # ключ – любая длинная строка: из неё делаем 32 байта ключа Fernet
    raw = hashlib.sha256(("funnel-studio/bots\0" + secret).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def _fingerprint(secret: str) -> str:
    return hashlib.sha256(("fp\0" + secret).encode()).hexdigest()[:12]


def _chmod600(p: Path) -> None:
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass   # Windows: права наследуются от папки пользователя


def load_key() -> tuple[str, str]:
    """→ (секрет, откуда: env / file)."""
    env = os.environ.get("STUDIO_SECRET_KEY", "").strip()
    if env:
        if len(env) < 24:
            raise VaultError("STUDIO_SECRET_KEY слишком короткий – нужна случайная строка от 24 символов")
        return env, "env"
    p = Path(os.environ.get("STUDIO_SECRET_KEY_FILE") or DEFAULT_KEY_FILE)
    try:
        if not p.is_file():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(secrets.token_urlsafe(32), encoding="utf-8")
            _chmod600(p)
        return p.read_text(encoding="utf-8").strip(), "file"
    except OSError as e:
        raise VaultError(f"ключ шифрования токенов недоступен ({type(e).__name__}): задайте "
                         "STUDIO_SECRET_KEY у сервера Студии") from None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BotVault:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / FILE_NAME
        self._lock = threading.Lock()

    # ── файл ──
    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"key": None, "bots": []}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise VaultError(f"файл ботов {self.path.name} не читается: {type(e).__name__}") from None

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        # создаём сразу с правами 600, чтобы токен ни мгновения не лежал открытым для группы
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        _chmod600(tmp)
        tmp.replace(self.path)
        _chmod600(self.path)

    def key_state(self) -> dict[str, Any]:
        """Для интерфейса: есть ли ключ и подходит ли он к записанным токенам."""
        try:
            secret, source = load_key()
        except VaultError as e:
            return {"ok": False, "source": None, "error": str(e)}
        data = self._read()
        if data.get("key") and data["key"] != _fingerprint(secret) and data.get("bots"):
            return {"ok": False, "source": source,
                    "error": "ключ шифрования токенов сменился – сохранённые токены не расшифровать, "
                             "добавьте ботов заново"}
        return {"ok": True, "source": source, "error": None}

    # ── боты ──
    def list(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in b.items() if k != "token"} for b in self._read().get("bots", [])]

    def get(self, bot_id: int) -> dict[str, Any] | None:
        for b in self.list():
            if b["id"] == bot_id:
                return b
        return None

    def token(self, bot_id: int) -> str:
        secret, _ = load_key()
        data = self._read()
        if data.get("key") and data["key"] != _fingerprint(secret):
            raise VaultError("ключ шифрования токенов сменился – добавьте бота заново (токен из @BotFather)")
        for b in data.get("bots", []):
            if b["id"] == bot_id:
                try:
                    return _fernet(secret).decrypt(b["token"].encode()).decode()
                except (InvalidToken, KeyError, ValueError):
                    raise VaultError("токен бота не расшифровать – добавьте бота заново") from None
        raise KeyError(bot_id)

    def put(self, me: dict[str, Any], token: str) -> dict[str, Any]:
        """Добавить или обновить бота (тот же id – новый токен, например после отзыва)."""
        secret, _ = load_key()
        fp = _fingerprint(secret)
        with self._lock:
            data = self._read()
            bots = data.get("bots", [])
            if data.get("key") and data["key"] != fp:
                bots = []          # старые токены всё равно не расшифровать
            rec = next((b for b in bots if b["id"] == me["id"]), None)
            fresh = rec is None
            if fresh:
                rec = {"id": me["id"], "added_at": now_iso()}
                bots.append(rec)
            rec.update({
                "username": me.get("username") or "",
                "first_name": me.get("first_name") or "",
                "last4": token[-4:],
                "token": _fernet(secret).encrypt(token.encode()).decode(),
                "updated_at": now_iso(),
            })
            self._write({"key": fp, "bots": bots})
            out = {k: v for k, v in rec.items() if k != "token"}
            out["fresh"] = fresh
            return out

    def touch(self, bot_id: int, **fields: Any) -> None:
        """Обновить открытые поля (имя после правки), токен не трогаем."""
        with self._lock:
            data = self._read()
            for b in data.get("bots", []):
                if b["id"] == bot_id:
                    b.update({k: v for k, v in fields.items() if k in ("username", "first_name")})
                    self._write(data)
                    return

    def remove(self, bot_id: int) -> bool:
        with self._lock:
            data = self._read()
            bots = [b for b in data.get("bots", []) if b["id"] != bot_id]
            if len(bots) == len(data.get("bots", [])):
                return False
            data["bots"] = bots
            self._write(data)
            return True
