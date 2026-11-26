"""Командная строка движка.

    python -m runtime run                       запустить движок (все запуски, все боты)
    python -m runtime stop                      остановить запущенный движок (корректно)
    python -m runtime hurry <slug> [--uid N]    ТОЛЬКО ДЛЯ ПРОВЕРКИ: ожидающие шаги – сейчас
    python -m runtime status [--tenant slug]    очередь, позиции, версии
    python -m runtime tenant add <slug> …       завести запуск и привязать бота (env-ссылка)
    python -m runtime tenant list
    python -m runtime tenant set <slug> ключ=значение …
    python -m runtime import-repo <запуск>/<funnel.yaml>   воронку из bot-engine → data/funnels Студии
    python -m runtime publish …                 то же, что python -m runtime.publish
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .config import RuntimeConfig, load_env_file


def setup_console() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


class _MskFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):  # noqa: N802
        from datetime import datetime

        from .clock import MSK
        return datetime.fromtimestamp(record.created, MSK).strftime("%d.%m %H:%M:%S МСК")


def setup_logging(level: str = "INFO") -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(_MskFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


# ── tenant ───────────────────────────────────────────────────────────────────

def _settings_from_launch(launch: str) -> dict:
    """Настройки запуска из bot-engine launch.yaml (только чтение)."""
    import yaml
    cfg = RuntimeConfig()
    p = cfg.bot_repo_path() / "launches" / launch / "launch.yaml"
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    ch, wb = raw.get("channel") or {}, raw.get("webinar") or {}
    return {
        "display_name": raw.get("display_name", launch),
        "static_launch": launch,
        "channel": {"link": ch.get("link", ""), "title": ch.get("title", ""),
                    "max_link": ch.get("max_link", "") or "", "vk_link": ch.get("vk_link", "") or "",
                    # chat_id НЕ переносим: тестовый бот не админ чужого канала –
                    # гейт «в канале» работал бы fail-open с предупреждением на каждом шаге
                    "chat_id": 0},
        "webinar": {"datetime_msk": wb.get("datetime_msk", "") or "",
                    "stream_url": wb.get("stream_url", "") or "",
                    "gift_url": wb.get("gift_url", "") or "", "vip_url": wb.get("vip_url", "") or ""},
        "dnd": ((raw.get("features") or {}).get("dnd") or {"enabled": False}),
    }


def _set_path(d: dict, dotted: str, value) -> None:
    node = d
    parts = dotted.split(".")
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value


def _parse_value(v: str):
    try:
        return json.loads(v)
    except ValueError:
        return v


async def _tenant(args) -> int:
    from .db import create_pool, migrate
    from .settings import TenantSettings
    cfg = RuntimeConfig()
    pool = await create_pool(cfg.dsn, max_size=2)
    try:
        await migrate(pool)
        async with pool.acquire() as conn:
            if args.tcmd == "list":
                rows = await conn.fetch(
                    """SELECT t.*, (SELECT string_agg(b.platform || ' ' || b.secret_ref ||
                               COALESCE(' @' || b.username, '') || COALESCE(' id' || b.tg_bot_id, ''), '; ')
                            FROM bots b WHERE b.tenant_id=t.id AND b.platform <> 'max') AS bots,
                           (SELECT string_agg('max ' || b.secret_ref || COALESCE(' @' || b.username, '')
                               || ' метка «' || bt.prefix || '»'
                               || CASE WHEN bt.is_default THEN ' (по умолчанию)' ELSE '' END
                               || CASE WHEN (SELECT count(*) FROM bot_tenants x WHERE x.bot_id=b.id) > 1
                                       THEN ' – общий бот' ELSE '' END, '; ')
                            FROM bot_tenants bt JOIN bots b ON b.id=bt.bot_id
                            WHERE bt.tenant_id=t.id) AS max_bots,
                           (SELECT string_agg(funnel_id || ' v' || v, ', ') FROM
                              (SELECT funnel_id, max(version) v FROM funnel_versions
                               WHERE tenant_id=t.id GROUP BY funnel_id) x) AS funnels
                       FROM tenants t ORDER BY t.id""")
                if not rows:
                    print("запусков нет – python -m runtime tenant add <slug> …")
                for r in rows:
                    print(f"#{r['id']} {r['slug']} «{r['title']}» [{r['status']}]\n"
                          f"   боты: {r['bots'] or '—'}\n"
                          + (f"   MAX: {r['max_bots']}\n" if r['max_bots'] else "")
                          + f"   воронки: {r['funnels'] or '—'}")
                return 0
            if args.tcmd == "add":
                settings = _settings_from_launch(args.from_launch) if args.from_launch else {}
                if args.settings:
                    settings.update(json.loads(Path(args.settings).read_text(encoding="utf-8")))
                for kv in args.set or []:
                    k, v = kv.split("=", 1)
                    _set_path(settings, k, _parse_value(v))
                TenantSettings.model_validate(settings)  # проверка до записи
                row = await conn.fetchrow(
                    """INSERT INTO tenants (slug, title, settings) VALUES ($1, $2, $3)
                       ON CONFLICT (slug) DO UPDATE SET title=EXCLUDED.title,
                            settings=tenants.settings || EXCLUDED.settings, updated_at=now()
                       RETURNING id""", args.slug, args.title or args.slug, settings)
                if args.bot_env:
                    await conn.execute(
                        """INSERT INTO bots (tenant_id, platform, secret_ref) VALUES ($1, 'tg', $2)
                           ON CONFLICT (platform, secret_ref) DO UPDATE SET tenant_id=EXCLUDED.tenant_id,
                                enabled=true""", row["id"], f"env:{args.bot_env}")
                if args.max_bot_env:
                    print(await _link_max_bot(conn, row["id"], args))
                await conn.execute("SELECT pg_notify('fs_tenants', $1)", str(row["id"]))
                print(f"запуск {args.slug} (#{row['id']}) сохранён"
                      + (f", бот: env:{args.bot_env}" if args.bot_env else ""))
                return 0
            if args.tcmd == "set":
                raw = await conn.fetchval("SELECT settings FROM tenants WHERE slug=$1", args.slug)
                if raw is None:
                    print(f"нет запуска {args.slug}", file=sys.stderr)
                    return 1
                for kv in args.pairs:
                    k, v = kv.split("=", 1)
                    _set_path(raw, k, _parse_value(v))
                TenantSettings.model_validate(raw)
                await conn.execute("UPDATE tenants SET settings=$2, updated_at=now() WHERE slug=$1",
                                   args.slug, raw)
                await conn.execute("SELECT pg_notify('fs_tenants', $1)", args.slug)
                print(json.dumps(raw, ensure_ascii=False, indent=1))
                return 0
    finally:
        await pool.close()
    return 2


async def _link_max_bot(conn, tenant_id: int, args) -> str:
    """Привязать MAX-бота к запуску. Бот с той же env-ссылкой уже есть (у
    другого запуска) – это общий бот: добавляется ещё одна метка входа.
    По умолчанию (вход без метки) – первый привязанный запуск или --max-default."""
    ref = f"env:{args.max_bot_env}"
    prefix = args.max_prefix or f"{args.slug}_"
    bot_id = await conn.fetchval(
        """INSERT INTO bots (tenant_id, platform, secret_ref) VALUES ($1, 'max', $2)
           ON CONFLICT (platform, secret_ref) DO UPDATE SET enabled=true
           RETURNING id""", tenant_id, ref)
    clash = await conn.fetchval(
        "SELECT t.slug FROM bot_tenants bt JOIN tenants t ON t.id=bt.tenant_id "
        "WHERE bt.bot_id=$1 AND bt.prefix=$2 AND bt.tenant_id<>$3", bot_id, prefix, tenant_id)
    if clash:
        raise SystemExit(f"метка «{prefix}» у этого MAX-бота уже занята запуском {clash}")
    has_default = await conn.fetchval(
        "SELECT 1 FROM bot_tenants WHERE bot_id=$1 AND is_default AND tenant_id<>$2", bot_id, tenant_id)
    make_default = bool(args.max_default) or not has_default
    async with conn.transaction():
        if make_default:
            await conn.execute("UPDATE bot_tenants SET is_default=false WHERE bot_id=$1", bot_id)
        await conn.execute(
            """INSERT INTO bot_tenants (bot_id, tenant_id, prefix, is_default) VALUES ($1,$2,$3,$4)
               ON CONFLICT (bot_id, tenant_id) DO UPDATE SET prefix=EXCLUDED.prefix,
                    is_default=EXCLUDED.is_default""",
            bot_id, tenant_id, prefix, make_default)
    n = await conn.fetchval("SELECT count(*) FROM bot_tenants WHERE bot_id=$1", bot_id)
    return (f"MAX-бот {ref} (#{bot_id}): метка входа «{prefix}»"
            + (", запуск по умолчанию" if make_default else "")
            + (f"; бот общий на {n} запуск(а)" if n > 1 else ""))


# ── import-repo ───────────────────────────────────────────────────────────────

def _import_repo(args) -> int:
    from studio.convert.from_engine import import_file
    from studio.server.storage import Storage, make_name
    cfg = RuntimeConfig()
    root = cfg.bot_repo_path()
    p = Path(args.path)
    if not p.is_absolute():
        p = root / "launches" / p
    f = import_file(p, repo_root=root)
    name = make_name(p.parent.name, p.stem)
    st = Storage(Path(args.data) if args.data else cfg.data_dir)
    out = st.save(name, f)
    print(f"{p} → {out} ({len(f.steps)} шагов, {len(f.edges)} стрелок); имя для публикации: {name}")
    return 0


# ── run / stop ───────────────────────────────────────────────────────────────

async def _hurry(a) -> int:
    """Ручная проверка без ожидания: ставит ожидающие шаги на «сейчас + gap × номер»
    (порядок сохраняется). На живом запуске не использовать – это перемотка времени."""
    from .db import CH_JOBS, create_pool
    pool = await create_pool(RuntimeConfig().dsn, max_size=1)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """WITH x AS (
                     SELECT j.id, row_number() OVER (ORDER BY j.run_at, j.id) AS n
                     FROM jobs j JOIN tenants t ON t.id=j.tenant_id
                     WHERE t.slug=$1 AND j.status='pending' AND ($2::bigint IS NULL OR j.uid=$2))
                   UPDATE jobs SET run_at = now() + make_interval(secs => x.n * $3)
                   FROM x WHERE jobs.id = x.id RETURNING jobs.step_key, jobs.run_at""",
                a.slug, a.uid, a.gap)
            await conn.execute("SELECT pg_notify($1, '0')", CH_JOBS)
        from .clock import msk
        for r in sorted(rows, key=lambda r: r["run_at"]):
            print(f"  {r['step_key']:<18} → {msk(r['run_at'])}")
        print(f"перемотано задач: {len(rows)}")
        return 0
    finally:
        await pool.close()


def _stop() -> int:
    import urllib.request
    cfg = RuntimeConfig()
    # слушает на 0.0.0.0 (контейнер) – стучимся всё равно на 127.0.0.1: /shutdown только локальный
    host = "127.0.0.1" if cfg.http_host in ("0.0.0.0", "::", "") else cfg.http_host
    url = f"http://{host}:{cfg.http_port}/shutdown"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="POST"), timeout=5) as r:
            print(f"движок останавливается ({r.status}); поллинг бота освободится за ~10 с")
            return 0
    except OSError as e:
        print(f"движок не отвечает на {url}: {e} – похоже, он не запущен", file=sys.stderr)
        return 1

async def _run(args) -> int:
    from .runner import run_forever
    return await run_forever(RuntimeConfig())


def main(argv: list[str] | None = None) -> int:
    setup_console()
    load_env_file()
    ap = argparse.ArgumentParser(prog="python -m runtime", description="Движок воронок Студии")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="запустить движок")
    sub.add_parser("stop", help="корректно остановить запущенный движок (POST /shutdown)")
    hu = sub.add_parser("hurry", help="только для проверки: ожидающие шаги запуска – сейчас, по порядку")
    hu.add_argument("slug")
    hu.add_argument("--uid", type=int, default=None)
    hu.add_argument("--gap", type=int, default=3, help="секунд между шагами (порядок сохраняется)")
    st = sub.add_parser("status", help="очередь, позиции, версии")
    st.add_argument("--tenant", default=None)
    st.add_argument("--json", action="store_true")
    t = sub.add_parser("tenant", help="запуски")
    tsub = t.add_subparsers(dest="tcmd", required=True)
    ta = tsub.add_parser("add", help="завести/обновить запуск")
    ta.add_argument("slug")
    ta.add_argument("--title", default="")
    ta.add_argument("--bot-env", default=None,
                    help="имя переменной окружения с токеном TG-бота (сам токен в базу не пишется)")
    ta.add_argument("--max-bot-env", default=None,
                    help="имя переменной окружения с токеном MAX-бота; тот же бот у второго "
                         "запуска = общий (зонтичный) бот")
    ta.add_argument("--max-prefix", default=None,
                    help="метка входа в MAX-боте (по умолчанию «<slug>_»: ?start=<slug>_reg_…)")
    ta.add_argument("--max-default", action="store_true",
                    help="сделать запуск основным для входа в MAX-бота без метки")
    ta.add_argument("--from-launch", default=None,
                    help="взять канал / эфир / подарок / static из bot-engine launches/<запуск>/launch.yaml")
    ta.add_argument("--settings", default=None, help="JSON-файл настроек")
    ta.add_argument("--set", action="append", metavar="КЛЮЧ=ЗНАЧЕНИЕ")
    tsub.add_parser("list")
    ts = tsub.add_parser("set", help="поменять настройки: channel.chat_id=0 dnd.enabled=true …")
    ts.add_argument("slug")
    ts.add_argument("pairs", nargs="+")
    im = sub.add_parser("import-repo", help="воронка bot-engine → data/funnels Студии")
    im.add_argument("path", help="<запуск>/<funnel.yaml> относительно launches/ или полный путь")
    im.add_argument("--data", default=None)
    pb = sub.add_parser("publish", help="опубликовать (см. python -m runtime.publish -h)")
    pb.add_argument("rest", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    setup_logging()
    if a.cmd == "run":
        try:
            return asyncio.run(_run(a))
        except KeyboardInterrupt:
            return 0
    if a.cmd == "stop":
        return _stop()
    if a.cmd == "hurry":
        return asyncio.run(_hurry(a))
    if a.cmd == "status":
        from .status import main_status
        return asyncio.run(main_status(a.tenant, as_json=a.json))
    if a.cmd == "tenant":
        return asyncio.run(_tenant(a))
    if a.cmd == "import-repo":
        return _import_repo(a)
    if a.cmd == "publish":
        from .publish import _main
        return asyncio.run(_main(a.rest))
    return 2
