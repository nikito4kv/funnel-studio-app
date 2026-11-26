"""Настройки запуска, от которых зависит расписание воронки.

Воронка (funnel*.yaml) – не вся правда о том, что получит человек. Часть
поведения задаёт launch.yaml запуска:

  features.dnd            – ночной режим: таймеры из окна 21:00–09:00 уезжают на утро;
  features.tg_skip_steps  – шаги, отключённые только в Telegram;
  channel.chat_id         – канал подключён к боту? 0 = гейт «вступил / не вступил»
                            проверить нельзя, и в TG такой шаг молча пропускается;
  channel.max_chat_id,
  channel.max_gate_fallback – то же в MAX; при «не проверить» решает fallback;
  broadcast.air_room, webinar.bizon_room_max – залы для гейта «был на эфире»;
  features.manager_dm(_max) – без настройки гейт «написал менеджеру» не работает.

Читаем их из bot-engine (только чтение). Нет файла – разумные умолчания движка.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class LaunchSettings(BaseModel):
    slug: str = ""
    found: bool = False                  # launch.yaml прочитан
    dnd_enabled: bool = False
    dnd_from: str = "21:00"
    dnd_to: str = "09:00"
    dnd_reschedule_to: str = "09:01"
    tg_skip_steps: list[str] = []
    channel_chat_id: int = 0
    channel_max_chat_id: int = 0
    max_gate_fallback: str = "skip"
    air_room: str = ""
    webinar_bizon_room_max: str = ""
    manager_dm: bool = False
    manager_dm_max: bool = False

    def notes(self) -> list[str]:
        """Что из настроек запуска влияет на прогон – человеческими словами."""
        out: list[str] = []
        if not self.found:
            out.append("launch.yaml запуска не найден – настройки запуска по умолчанию движка")
        out.append(f"ночной режим: {self.dnd_from}–{self.dnd_to}, перенос на {self.dnd_reschedule_to}"
                   if self.dnd_enabled else "ночной режим выключен")
        if not self.channel_chat_id:
            out.append("канал не подключён к боту в TG (chat_id = 0): шаги с условием «в канале / "
                       "не в канале» в Telegram молча пропускаются")
        if not self.channel_max_chat_id:
            what = "уходят" if self.max_gate_fallback == "send" else "пропускаются"
            out.append(f"в MAX подписку проверить нельзя (max_chat_id = 0): шаги с условием канала {what}")
        if self.tg_skip_steps:
            out.append("отключены в Telegram: " + ", ".join(self.tg_skip_steps))
        return out


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _ok_config(cfg: Any) -> bool:
    return isinstance(cfg, dict) and bool(str(cfg.get("actions_table") or "").strip()) \
        and bool(cfg.get("actions"))


def load_launch_settings(repo_root: Path | None, launch: str | None) -> LaunchSettings:
    if not repo_root or not launch:
        return LaunchSettings(slug=launch or "")
    p = Path(repo_root) / "launches" / launch / "launch.yaml"
    if not p.is_file():
        return LaunchSettings(slug=launch)
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 – битый yaml: работаем на умолчаниях
        return LaunchSettings(slug=launch)
    feats = d.get("features") or {}
    dnd = feats.get("dnd") or {}
    ch = d.get("channel") or {}
    bc = d.get("broadcast") or {}
    wb = d.get("webinar") or {}
    air = str(bc.get("air_room") or "")
    if not air and bc.get("air_room_env"):
        air = f"(env {bc['air_room_env']})"
    return LaunchSettings(
        slug=str(d.get("slug") or launch),
        found=True,
        dnd_enabled=bool(dnd.get("enabled", False)),
        dnd_from=str(dnd.get("from") or dnd.get("from_") or "21:00"),
        dnd_to=str(dnd.get("to") or "09:00"),
        dnd_reschedule_to=str(dnd.get("reschedule_to") or "09:01"),
        tg_skip_steps=[str(x) for x in (feats.get("tg_skip_steps") or [])],
        channel_chat_id=_int(ch.get("chat_id")),
        channel_max_chat_id=_int(ch.get("max_chat_id")),
        max_gate_fallback=str(ch.get("max_gate_fallback") or "skip"),
        air_room=air,
        webinar_bizon_room_max=str(wb.get("bizon_room_max") or ""),
        manager_dm=_ok_config(feats.get("manager_dm")),
        manager_dm_max=_ok_config(feats.get("manager_dm_max")),
    )
