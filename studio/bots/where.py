"""Где работает бот: запуски bot-engine (`launches/*/launch.yaml`) и запуски движка Студии.

bot-engine Студия только читает. У запуска в `launch.yaml`:

    bot:
      tg:  {token_env: WEBINAR_BOT_TOKEN, username: northwind_workshop_bot}
      max: {shared_token_env: MAX_BOT_TOKEN, payload_prefix: webinar_}   # общий (зонтичный) MAX-бот

Сопоставление – по @username (без учёта регистра). Имя общего MAX-бота в yaml не пишется (оно в
.env bot-engine), поэтому берём его из ссылки маркетологам в комментариях `max.ru/<бот>?start=…`, а
нет её – показываем без имени.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_MAX_LINK = re.compile(r"max\.ru/([A-Za-z0-9_]+)\?start=")


def _load(p: Path) -> tuple[dict, str]:
    try:
        text = p.read_text(encoding="utf-8")
        return (yaml.safe_load(text) or {}), text
    except (OSError, yaml.YAMLError):
        return {}, ""


def repo_launches(repo_root: Path) -> list[dict[str, Any]]:
    """Запуски bot-engine с их ботами: slug, название, TG-бот, общий MAX-бот."""
    out: list[dict[str, Any]] = []
    root = Path(repo_root) / "launches"
    if not root.is_dir():
        return out
    for p in sorted(root.glob("*/launch.yaml")):
        cfg, text = _load(p)
        if not isinstance(cfg, dict):
            continue
        bot = cfg.get("bot") or {}
        tg = bot.get("tg") or {}
        mx = bot.get("max") or {}
        item: dict[str, Any] = {
            "slug": cfg.get("slug") or p.parent.name,
            "title": cfg.get("display_name") or cfg.get("slug") or p.parent.name,
            "tg": None, "max": None,
        }
        if isinstance(tg, dict) and tg.get("username") and tg.get("enabled", True) is not False:
            item["tg"] = {"username": str(tg["username"]).lstrip("@"), "token_env": tg.get("token_env") or ""}
        if isinstance(mx, dict) and mx.get("enabled") and mx.get("shared_token_env"):
            m = _MAX_LINK.search(text)
            item["max"] = {"shared": mx["shared_token_env"], "username": m.group(1) if m else "",
                           "prefix": mx.get("payload_prefix") or ""}
        out.append(item)
    return out


def repo_by_username(launches: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    idx: dict[str, list[dict[str, Any]]] = {}
    for x in launches:
        if x["tg"]:
            idx.setdefault(x["tg"]["username"].lower(), []).append(x)
    return idx


def max_groups(launches: list[dict[str, Any]], engine_max: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Общие MAX-боты: какие запуски bot-engine и Студии через них работают. Только показ."""
    groups: dict[str, dict[str, Any]] = {}
    for x in launches:
        m = x["max"]
        if not m:
            continue
        g = groups.setdefault(m["shared"], {"key": m["shared"], "username": "", "launches": [], "studio": []})
        g["username"] = g["username"] or m["username"]
        g["launches"].append({"slug": x["slug"], "title": x["title"], "prefix": m["prefix"]})
    for b in engine_max:
        key = "studio:" + (b.get("username") or "?")
        # тот же бот, что у bot-engine, – в ту же группу (по имени)
        g = next((v for v in groups.values() if b.get("username") and v["username"] == b["username"]), None)
        if g is None:
            g = groups.setdefault(key, {"key": key, "username": b.get("username") or "", "launches": [], "studio": []})
        g["studio"].append({"slug": b["tenant"], "title": b.get("title") or b["tenant"]})
    return sorted(groups.values(), key=lambda g: -len(g["launches"]) - len(g["studio"]))
