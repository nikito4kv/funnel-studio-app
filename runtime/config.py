"""Настройки процесса движка: всё из переменных окружения.

  STUDIO_RUNTIME_DSN   Postgres движка (по умолчанию – контейнер fs-runtime-pg)
  STUDIO_DATA          папка данных Студии (там data/funnels, data/media)
  BOT_REPO_PATH         монорепо ботов – только чтение (static запусков)
  STUDIO_TG_PROXY      socks5://… для Telegram (необязательно)
  STUDIO_ENV_FILE      файл KEY=VALUE с секретами (по умолчанию
                       ~/.funnel-studio/test_bot.env); уже заданные переменные
                       окружения он не перебивает
  STUDIO_RUNTIME_PORT  health/API движка (8781)
  STUDIO_RUNTIME_HOST  на каком адресе слушать health/API (127.0.0.1; в контейнере
                       0.0.0.0 – иначе порт не опубликовать; /shutdown всё равно
                       принимает только запросы с самой машины)

Токены ботов в базе НЕ хранятся: у бота в таблице `bots` лежит ссылка
`env:ИМЯ_ПЕРЕМЕННОЙ`, сам токен берётся из окружения в момент запуска.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DSN = "postgresql://fs:fsruntime@127.0.0.1:55432/fs_runtime"
DEFAULT_ENV_FILE = Path.home() / ".funnel-studio" / "test_bot.env"


def load_env_file(path: str | Path | None = None) -> list[str]:
    """Подгружает KEY=VALUE в os.environ (не перебивая заданное). → имена ключей."""
    p = Path(path or os.environ.get("STUDIO_ENV_FILE") or DEFAULT_ENV_FILE)
    if not p.is_file():
        return []
    names = []
    for raw in p.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip().lstrip("﻿")
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
        names.append(k)
    return names


def mask(secret: str | None) -> str:
    """Для логов: первые 6 символов и «…». Сам токен не печатается никогда."""
    if not secret:
        return "<пусто>"
    return secret[:6] + "…"


class SecretError(RuntimeError):
    pass


def resolve_secret(ref: str) -> str:
    """`env:ИМЯ` → значение переменной окружения. Другие схемы – ошибка."""
    if not ref.startswith("env:"):
        raise SecretError(f"ссылка на секрет должна быть вида env:ИМЯ (получено «{ref}»)")
    name = ref[4:].strip()
    val = os.environ.get(name, "").strip()
    if not val:
        raise SecretError(f"переменная окружения {name} не задана "
                          f"(ни в окружении, ни в STUDIO_ENV_FILE)")
    return val


@dataclass
class RuntimeConfig:
    dsn: str = field(default_factory=lambda: os.environ.get("STUDIO_RUNTIME_DSN", DEFAULT_DSN))
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("STUDIO_DATA", REPO_ROOT / "data")))
    tg_proxy: str | None = field(default_factory=lambda: os.environ.get("STUDIO_TG_PROXY") or None)
    http_host: str = field(default_factory=lambda: os.environ.get("STUDIO_RUNTIME_HOST", "127.0.0.1"))
    http_port: int = field(default_factory=lambda: int(os.environ.get("STUDIO_RUNTIME_PORT", "8781")))
    # пул соединений на ВЕСЬ процесс (исследование §9.2: один пул, а не 15 × запуск)
    pool_max: int = 10
    # аренда задачи: упал процесс – задача вернётся в очередь через столько секунд
    lease_seconds: int = 120
    # сколько задач держать взятыми одновременно (на все запуски)
    max_held: int = 64

    def bot_repo_path(self) -> Path:
        from studio.paths import bot_repo_path
        return bot_repo_path()
