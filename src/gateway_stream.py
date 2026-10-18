"""閘道（LiteLLM → NIM）用串流呼叫（2026-10-17）。

NIM 對「不串流」的請求大約 300 秒就回 504；長文修訂、能力強但慢的模型都會撞到。
串流時連線一直有資料在流，就不會被切斷——所以閘道一律用串流，再把片段拼回整段文字。
"""
from __future__ import annotations

import json
from typing import Iterable


def collect_sse(lines: Iterable[str]) -> tuple[str, dict]:
    """把 SSE 的 data: 行拼成 (正文, usage)。思考內容（reasoning_content）不收。"""
    parts: list[str] = []
    usage: dict = {}
    for line in lines:
        line = (line or "").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except ValueError:
            continue
        if chunk.get("usage"):
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or choice.get("message") or {}
            if delta.get("content"):
                parts.append(delta["content"])
    return "".join(parts), usage
