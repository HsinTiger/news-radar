"""
News Radar · LLM Brain (Phase 8.19)
====================================
統一的 LLM 呼叫層：Gemini primary → Claude CLI fallback → None。

為什麼獨立一個模組：
- scorer.py / composer.py 都各自呼叫 Gemini；現在要加 Claude CLI fallback
  → 複製邏輯兩份是反模式
- 未來若要再加第三條路（例如本機 llama.cpp），動一處而非散在各 caller
- 測試性：單一模組好 mock，單一 contract 好 assert

決策樹：
    1. GEMINI_API_KEY 有設 → 試 Gemini（google-genai SDK，structured output）
       ├ 成功 → return LLMResult(provider="gemini", data=parsed, ...)
       └ 失敗（429 / 任何 Exception） → print 警告 → go to 2
    2. `claude` CLI 可用（shutil.which("claude")) → 試 claude -p --output-format json
       ├ 成功 → 解 envelope JSON → 抽 result text → 抽內層 JSON → Pydantic validate
       └ 失敗 → print 警告 → go to 3
    3. 兩條都失敗 → return LLMResult(data=None, provider="none")

呼叫端契約：
    result = await call_for_json(
        system="你是...",
        prompt="請分析...",
        response_model=NewsScore,
    )
    if result.data is None:
        # 兩條路都失敗 → 呼叫端自己決定 skip / retry / log
        return None
    # 用 result.data；需要的話看 result.provider 決定後續動作
    return result.data
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError


# --------------------------------------------------------------------------
# 結果 contract
# --------------------------------------------------------------------------

T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMResult(Generic[T]):
    """LLM 呼叫結果。data=None 表示兩條路都失敗（呼叫端要 skip）。"""
    data: Optional[T]
    provider: str  # "gemini" | "claude_cli" | "none"
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    raw_error: Optional[str] = None  # debug 用


# --------------------------------------------------------------------------
# Gemini path
# --------------------------------------------------------------------------

def _has_gemini_key() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


async def _try_gemini(
    *,
    system: str,
    prompt: str,
    response_model: Type[T],
    model: str,
    temperature: float,
) -> LLMResult[T]:
    """試呼叫 Gemini。失敗時 data=None + raw_error 記錯誤。"""
    try:
        # 延遲 import：sandbox 沒有 google-genai 時不拖累其他 caller
        from google import genai  # type: ignore

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return LLMResult(
                data=None, provider="gemini", model=model,
                raw_error="GEMINI_API_KEY not set",
            )

        client = genai.Client(api_key=api_key)

        def _sync_call():
            return client.models.generate_content(
                model=model,
                contents=prompt,
                config={
                    "system_instruction": system,
                    "response_mime_type": "application/json",
                    "response_schema": response_model,
                    "temperature": temperature,
                },
            )

        response = await asyncio.to_thread(_sync_call)
        parsed = response.parsed
        if parsed is None:
            # 極少見：SDK 回了但 parsed 空（通常是回的內容不符 schema）
            return LLMResult(
                data=None, provider="gemini", model=model,
                raw_error="gemini returned empty parsed object",
            )

        # 嘗試抓 usage metadata（若 SDK 版本支援）
        in_tok = out_tok = 0
        try:
            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                in_tok = getattr(usage, "prompt_token_count", 0) or 0
                out_tok = getattr(usage, "candidates_token_count", 0) or 0
        except Exception:
            pass

        return LLMResult(
            data=parsed,
            provider="gemini",
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
        )
    except Exception as e:
        return LLMResult(
            data=None, provider="gemini", model=model,
            raw_error=f"{type(e).__name__}: {e}",
        )


# --------------------------------------------------------------------------
# Claude CLI path
# --------------------------------------------------------------------------

CLAUDE_CLI_BIN = os.getenv("CLAUDE_CLI_BIN", "claude")


def _claude_cli_available() -> bool:
    return shutil.which(CLAUDE_CLI_BIN) is not None


def _extract_json_blob(text: str) -> Optional[str]:
    """從任意文字中抽出 JSON 字串。
    策略（從緊到寬）：
    1. 文字本身就是合法 JSON
    2. markdown code fence：```json ... ``` 或 ``` ... ```
    3. 從第一個 `{` 到最後一個 `}` 的 slice
    4. 從第一個 `[` 到最後一個 `]` 的 slice
    回傳抽到的候選字串（未驗證）；全部失敗則回 None。
    """
    if not text:
        return None

    stripped = text.strip()

    # 1) 整段即 JSON
    if stripped.startswith(("{", "[")):
        try:
            json.loads(stripped)
            return stripped
        except json.JSONDecodeError:
            pass  # 繼續試其他策略

    # 2) markdown code fence
    # 支援 ```json 以及 ``` 兩種
    fence_match = re.search(
        r"```(?:json)?\s*\n?(.*?)\n?```",
        stripped,
        re.DOTALL | re.IGNORECASE,
    )
    if fence_match:
        candidate = fence_match.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    # 3) 第一個 { 到最後一個 }
    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidate = stripped[first_brace:last_brace + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    # 4) 第一個 [ 到最後一個 ]（對付 response_model 是 List[...] 的情況）
    first_br = stripped.find("[")
    last_br = stripped.rfind("]")
    if first_br != -1 and last_br > first_br:
        candidate = stripped[first_br:last_br + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    return None


def _parse_claude_envelope(stdout: str) -> tuple[str, dict]:
    """解 `claude -p --output-format json` 的外層 envelope。
    回傳 (result_text, envelope_meta)。若 envelope 不是預期結構，
    把整段 stdout 當 result_text，envelope_meta={}。
    """
    try:
        env = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout, {}

    if isinstance(env, dict):
        # Claude Code CLI 的典型 envelope：
        # {"type":"result","subtype":"success","result":"...","session_id":"...",
        #  "total_cost_usd":0.123,"usage":{...}}
        result_text = env.get("result", "")
        if not isinstance(result_text, str):
            result_text = str(result_text)
        return result_text, env

    # 不是 dict（可能直接是字串 / array）→ 當純文字處理
    return stdout, {}


async def _try_claude_cli(
    *,
    system: str,
    prompt: str,
    response_model: Type[T],
    timeout_s: int,
) -> LLMResult[T]:
    """試呼叫 claude CLI。

    用官方支援的旗標組合（見 https://code.claude.com/docs/en/cli-reference）：
    - `-p` / `--print`：non-interactive 模式
    - `--output-format json`：回傳 envelope JSON（含 result / usage / total_cost_usd）
    - `--system-prompt <text>`：乾淨地指定 system instruction（取代 default）
    - `--no-session-persistence`：不把 session 存進 ~/.claude/sessions，避免污染
    - user prompt 透過 argv 傳，非 stdin（docs 的 canonical form）

    ⚠️ 不用 `--bare`：實測會把 auth context 跟 hook/skill/plugin 一起剝掉，
    導致即使 `claude login` 成功、`-p` 也會回 "Not logged in"。
    tradeoff: 每次 call 會載整套 context，成本約 $0.094/call（full caching 後），
    但 Claude CLI 只在 Gemini 失敗時才觸發，頻率低，可接受。

    實作細節：
    - 用 asyncio.create_subprocess_exec 避免 block event loop
    - stdin 關閉（不需要 pipe 資料進去）
    - envelope.result 再走 _extract_json_blob 抽 JSON，避免舊版 CLI 或
      skill 干擾時模型在 result 裡加 markdown fence / 前後閒聊
    - 若使用者 CLI 版本太舊不支援新旗標 → 子程序會 exit != 0，走 error path
    """
    if not _claude_cli_available():
        return LLMResult(
            data=None, provider="claude_cli", model=CLAUDE_CLI_BIN,
            raw_error=f"`{CLAUDE_CLI_BIN}` not found on PATH",
        )

    args = [
        CLAUDE_CLI_BIN,
        "-p",
        "--output-format", "json",
        "--system-prompt", system.strip(),
        "--no-session-persistence",
        prompt.strip(),  # user prompt as positional argv
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:
        return LLMResult(
            data=None, provider="claude_cli", model=CLAUDE_CLI_BIN,
            raw_error=f"spawn failed: {type(e).__name__}: {e}",
        )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return LLMResult(
            data=None, provider="claude_cli", model=CLAUDE_CLI_BIN,
            raw_error=f"claude CLI timeout ({timeout_s}s)",
        )

    if proc.returncode != 0:
        stderr_text = (stderr_bytes or b"").decode("utf-8", errors="replace")[:500]
        return LLMResult(
            data=None, provider="claude_cli", model=CLAUDE_CLI_BIN,
            raw_error=f"claude CLI exit={proc.returncode}, stderr={stderr_text!r}",
        )

    stdout_text = (stdout_bytes or b"").decode("utf-8", errors="replace")

    result_text, envelope = _parse_claude_envelope(stdout_text)

    # 抽 JSON blob
    json_blob = _extract_json_blob(result_text)
    if json_blob is None:
        return LLMResult(
            data=None, provider="claude_cli", model=CLAUDE_CLI_BIN,
            raw_error=f"no JSON blob in claude output; first 300 chars: {result_text[:300]!r}",
        )

    # 解 + Pydantic validate
    try:
        obj = json.loads(json_blob)
        parsed = response_model.model_validate(obj)
    except (json.JSONDecodeError, ValidationError) as e:
        return LLMResult(
            data=None, provider="claude_cli", model=CLAUDE_CLI_BIN,
            raw_error=f"parse/validate failed: {type(e).__name__}: {str(e)[:300]}",
        )

    # 從 envelope 抓 token / cost 供 caller log
    in_tok = out_tok = 0
    cost = 0.0
    if isinstance(envelope, dict):
        usage = envelope.get("usage") or {}
        if isinstance(usage, dict):
            in_tok = int(usage.get("input_tokens", 0) or 0)
            out_tok = int(usage.get("output_tokens", 0) or 0)
        cost = float(envelope.get("total_cost_usd", 0.0) or 0.0)

    return LLMResult(
        data=parsed,
        provider="claude_cli",
        model=CLAUDE_CLI_BIN,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=cost,
    )


# --------------------------------------------------------------------------
# 對外 API
# --------------------------------------------------------------------------

async def call_for_json(
    *,
    system: str,
    prompt: str,
    response_model: Type[T],
    gemini_model: str = "gemini-2.0-flash-lite",
    temperature: float = 0.2,
    timeout_s: int = 180,
) -> LLMResult[T]:
    """核心 API：先試 Gemini，失敗才試 Claude CLI。

    Args:
        system: System instruction（全部前綴）
        prompt: User prompt / 新聞內容 / 評閱指令
        response_model: Pydantic BaseModel 類別（例如 NewsScore / MultiPlatformDraft）
        gemini_model: Gemini 模型名稱，預設 `gemini-2.0-flash-lite`
        temperature: 僅 Gemini 使用（Claude CLI 用系統預設）
        timeout_s: Claude CLI subprocess 硬上限

    Returns:
        LLMResult，data 為 None 表兩條路都失敗（呼叫端要自己 skip）。
    """
    # Path 1: Gemini
    if _has_gemini_key():
        r1 = await _try_gemini(
            system=system,
            prompt=prompt,
            response_model=response_model,
            model=gemini_model,
            temperature=temperature,
        )
        if r1.data is not None:
            return r1
        print(
            f"[llm_brain] ⚠️ Gemini ({gemini_model}) 失敗，嘗試 Claude CLI fallback。"
            f" reason={r1.raw_error}"
        )
    else:
        print("[llm_brain] ℹ️ 無 GEMINI_API_KEY，直接嘗試 Claude CLI fallback。")

    # Path 2: Claude CLI
    if _claude_cli_available():
        r2 = await _try_claude_cli(
            system=system,
            prompt=prompt,
            response_model=response_model,
            timeout_s=timeout_s,
        )
        if r2.data is not None:
            return r2
        print(
            f"[llm_brain] ⚠️ Claude CLI 失敗。"
            f" reason={r2.raw_error}"
        )
    else:
        print(f"[llm_brain] ℹ️ `{CLAUDE_CLI_BIN}` 不在 PATH，略過 Claude CLI fallback。")

    # Path 3: 全部失敗
    return LLMResult(
        data=None,
        provider="none",
        raw_error="both gemini and claude_cli unavailable or failed",
    )


# --------------------------------------------------------------------------
# debug entry point
# --------------------------------------------------------------------------

if __name__ == "__main__":
    # 最小示範：定義一個 schema，呼叫 call_for_json
    class Demo(BaseModel):
        headline: str
        confidence: float

    async def _demo():
        r = await call_for_json(
            system="You are a terse news bot. Respond with JSON: {headline, confidence}.",
            prompt="What is a typical headline and confidence for a macroeconomic article?",
            response_model=Demo,
        )
        print(f"provider={r.provider} cost=${r.cost_usd:.4f}")
        print(f"data={r.data}")
        if r.data is None:
            print(f"raw_error={r.raw_error}")

    asyncio.run(_demo())
