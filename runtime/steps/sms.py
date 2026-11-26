"""Шаг «SMS» (type: sms) – перенос _handle_sms движка bot-engine.

Текст – `sms.text` с плейсхолдерами (`{Имя}`, `{webinar.time}`,
`{stream_url}`, `{utm_*}` …) тем же render_text, что и сообщения.

Короткие ссылки: движок bot-engine заворачивает `{stream_url}` и ссылки на
эфир в прокси `<base_url>/v/<код>` (~30 символов, клик = автовход в Bizon).
У движка Студии прокси-ссылок пока нет – ссылка уходит как есть, в actions
пометка «ссылка без сокращения» (длинная ссылка = SMS из нескольких частей).
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from studio.model.funnel import EdgeKind, Step

from ..planner import plan_also, plan_edge
from .call import provider_step

if TYPE_CHECKING:
    from ..executor import Executor, UserCtx
    from ..graph import CompiledFunnel
    from ..repo import Outcome

_URL = re.compile(r"https?://\S+")


def sms_text(ex: "Executor", u: "UserCtx", g: "CompiledFunnel", step: Step) -> str:
    raw = step.sms.text or ""
    if not raw.strip():
        return ""
    if u.platform == "max":
        # человеку из MAX – MAX-зал эфира ({stream_url} → webinar.stream_url_max, §25)
        from ..platforms import max_steps
        text, _rows = max_steps.render(ex, u, g, step, text_override=raw)
    else:
        text, _kb = ex.render(u, g, step, text_override=raw)
    return (text or "").strip()


async def fire_sms(ex: "Executor", u: "UserCtx", g: "CompiledFunnel", step: Step, job, ctx,
                   out: "Outcome") -> None:
    text = sms_text(ex, u, g, step)
    data = {}
    if _URL.search(text):
        data["links"] = "без сокращения (прокси-ссылок в движке Студии пока нет)"

    async def do(prov, phone):
        return await prov.send(phone, text)

    await provider_step(ex, u, g, step, job, out, "sms", do, what=text, data=data)
    out.specs += plan_edge(g, step, EdgeKind.done, ctx)
    out.specs += plan_also(g, step, ctx)
