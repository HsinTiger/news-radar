"""
News Radar · LLM Brain (Phase 8.19)
====================================
統一的 LLM 呼叫層：claude_cli → gemini → groq → cerebras → None（依能力排序）。

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

# --- Minimal-context mode (2026-05-30, Optimization A) ----------------------
# A trivial `-p` ping costs ~$0.03 / ~12K context tokens because the CLI loads
# the user's CLAUDE.md + skills + plugins + MCP + dynamic env sections on every
# call. For a self-contained JSON-writing task none of that is needed. We strip
# it WITHOUT --bare (which breaks Keychain auth — see _try_claude_cli_once doc)
# by: running from a neutral cwd + `--setting-sources project` (no user/local
# settings) + `--strict-mcp-config` (no MCP) + `--exclude-dynamic-system-prompt-
# sections`. Auth keeps working because credentials live in the macOS Keychain
# and the DEFAULT CLAUDE_CONFIG_DIR is left untouched. Measured: fresh input
# tokens drop ~1650→3; the irreducible ~10K base system prompt stays cached.
# Reversible via LLM_BRAIN_MINIMAL_CONTEXT=0.
LLM_BRAIN_MINIMAL_CONTEXT = os.getenv("LLM_BRAIN_MINIMAL_CONTEXT", "1") == "1"

# Neutral working dir for the subprocess so no project .claude / CLAUDE.md /
# .mcp.json gets picked up. Kept empty (just a settings.json={}).
_MIN_CTX_DIR = Path(
    os.getenv("LLM_BRAIN_MIN_CTX_DIR", str(Path.home() / "news_radar" / ".claude_min"))
)


def _ensure_min_ctx_dir() -> Path:
    """Create (idempotently) the neutral cwd used for minimal-context calls."""
    try:
        _MIN_CTX_DIR.mkdir(parents=True, exist_ok=True)
        settings = _MIN_CTX_DIR / "settings.json"
        if not settings.exists():
            settings.write_text("{}", encoding="utf-8")
    except Exception:
        pass  # fall back to default cwd if we can't create it
    return _MIN_CTX_DIR


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

    # 注意 (2026-05-13): Claude CLI 有時會在 string value 內塞「真實換行字元」
    # （不是合法 JSON 的 \n escape）→ strict 模式的 json.loads 會 reject。
    # 全部用 strict=False（允許 string value 內含 control chars: \n \r \t）。
    # 標準 JSON spec 不允許，但實務上 Claude CLI / LLM 經常這樣輸出。

    # 1) 整段即 JSON
    if stripped.startswith(("{", "[")):
        try:
            json.loads(stripped, strict=False)
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
            json.loads(candidate, strict=False)
            return candidate
        except json.JSONDecodeError:
            pass

    # 3) 第一個 { 到最後一個 }
    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidate = stripped[first_brace:last_brace + 1]
        try:
            json.loads(candidate, strict=False)
            return candidate
        except json.JSONDecodeError:
            pass

    # 4) 第一個 [ 到最後一個 ]（對付 response_model 是 List[...] 的情況）
    first_br = stripped.find("[")
    last_br = stripped.rfind("]")
    if first_br != -1 and last_br > first_br:
        candidate = stripped[first_br:last_br + 1]
        try:
            json.loads(candidate, strict=False)
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


CLAUDE_CLI_MAX_RETRIES = int(os.getenv("CLAUDE_CLI_MAX_RETRIES", "2"))


async def _try_claude_cli(
    *,
    system: str,
    prompt: str,
    response_model: Type[T],
    timeout_s: int,
    disallowed_tools: Optional[tuple] = None,
) -> LLMResult[T]:
    """Retry wrapper around _try_claude_cli_once（2026-05-16 加入）。

    Hsin 5/16 決策：放棄 Gemini fallback、改用 claude_cli retry on transient
    failures。理由：Gemini quota / API 不穩、且 voice / brand 規範對齊 Claude
    模型訓練更熟。LLM provider 切換成本 > 同一 provider 重試成本。

    Retry policy:
    - Retryable: asyncio.TimeoutError, spawn_failed (transient 環境問題)
    - Non-retryable: non-zero exit (auth / permission), JSON parse failure
      (deterministic、retry 也是同樣結果)
    - 預設 2 retries (3 total attempts)、backoff 30s + 60s
    - ENV override: CLAUDE_CLI_MAX_RETRIES (例：=0 關掉 retry)

    歷史 incident: 5/13 + 5/16 兩次 launchctl morning Claude CLI 480s timeout
    → abort → 無 Substack draft。加 retry 後預期 ≥ 90% 自動恢復。
    """
    last_result = None
    for attempt in range(CLAUDE_CLI_MAX_RETRIES + 1):
        if attempt > 0:
            backoff_s = 30 * attempt
            err_preview = (last_result.raw_error or "?")[:80] if last_result else "init"
            print(
                f"[claude_cli] ⟳ retry {attempt}/{CLAUDE_CLI_MAX_RETRIES} "
                f"after {backoff_s}s backoff (prev: {err_preview})"
            )
            await asyncio.sleep(backoff_s)

        result = await _try_claude_cli_once(
            system=system,
            prompt=prompt,
            response_model=response_model,
            timeout_s=timeout_s,
            disallowed_tools=disallowed_tools,
        )

        if result.data is not None:
            if attempt > 0:
                print(
                    f"[claude_cli] ✅ recovered on attempt "
                    f"{attempt + 1}/{CLAUDE_CLI_MAX_RETRIES + 1}"
                )
            return result

        last_result = result
        err = (result.raw_error or "").lower()
        retryable = ("timeout" in err) or ("spawn failed" in err)
        if not retryable:
            return result  # 非 transient 錯誤（auth / parse）、立刻回

    # All retries exhausted
    return last_result


async def _try_claude_cli_once(
    *,
    system: str,
    prompt: str,
    response_model: Type[T],
    timeout_s: int,
    disallowed_tools: Optional[tuple] = None,
) -> LLMResult[T]:
    """試呼叫 claude CLI（單次、無 retry、由 _try_claude_cli wrapper 控制 retry）。

    用官方支援的旗標組合（見 https://code.claude.com/docs/en/cli-reference）：
    - `-p` / `--print`：non-interactive 模式
    - `--output-format json`：回傳 envelope JSON（含 result / usage / total_cost_usd）
    - `--system-prompt <text>`：乾淨地指定 system instruction（取代 default）
    - `--no-session-persistence`：不把 session 存進 ~/.claude/sessions，避免污染
    - user prompt 透過 argv 傳，非 stdin（docs 的 canonical form）

    ⚠️ 不用 `--bare`：實測會把 auth context 跟 hook/skill/plugin 一起剝掉，
    導致即使 `claude login` 成功、`-p` 也會回 "Not logged in"。同理：**不可**
    覆寫 `CLAUDE_CONFIG_DIR`——指到空目錄一樣會回 "Not logged in"（auth 認的是
    Keychain + 預設 config dir）。

    Minimal-context mode (2026-05-30, Optimization A，預設開、env 可關)：
    改用「中性 cwd + setting 旗標」剝掉 per-call 的隱藏 context，auth 不受影響：
    - cwd = 一個空目錄（_MIN_CTX_DIR）→ 不會撈到 project .claude / CLAUDE.md / .mcp.json
    - `--setting-sources project` → 不載 user / local settings（含 user skills / CLAUDE.md）
    - `--strict-mcp-config` → 不載任何 MCP server
    - `--exclude-dynamic-system-prompt-sections` → 不塞 env / git 動態區塊
    實測：fresh input tokens ~1650→3；不可再砍的 base system prompt(~10K)維持 cached。

    disallowed_tools: 額外丟給 `--disallowedTools` 的工具名（例如 Substack composer
    傳 ("WebSearch","WebFetch") 把 agentic 上網查證關掉，改吃預抓好的素材）。

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

    # ⚠️ Arg ordering matters: `--disallowedTools` is variadic (`<tools...>`),
    # so it must NOT sit immediately before the positional prompt or the parser
    # swallows the prompt as another tool name ("Input must be provided…"). We
    # therefore emit all variadic / value flags first and keep the boolean
    # `--no-session-persistence` as the last option before the positional prompt.
    args = [
        CLAUDE_CLI_BIN,
        "-p",
        "--output-format", "json",
    ]
    if disallowed_tools:
        args += ["--disallowedTools", ",".join(disallowed_tools)]

    cwd: Optional[str] = None
    if LLM_BRAIN_MINIMAL_CONTEXT:
        cwd = str(_ensure_min_ctx_dir())
        args += [
            "--setting-sources", "project",
            "--strict-mcp-config",
            "--exclude-dynamic-system-prompt-sections",
        ]

    args += [
        "--system-prompt", system.strip(),
        "--no-session-persistence",  # boolean → safe directly before positional
        prompt.strip(),              # user prompt as positional argv
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
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
        # 2026-05-30: also capture stdout — `claude -p` puts the real failure
        # message (usage limit / refusal / overload) in the JSON envelope on
        # stdout, not stderr. Without this, exit!=0 failures are undiagnosable.
        stdout_text = (stdout_bytes or b"").decode("utf-8", errors="replace")[:900]
        return LLMResult(
            data=None, provider="claude_cli", model=CLAUDE_CLI_BIN,
            raw_error=f"claude CLI exit={proc.returncode}; stderr={stderr_text!r}; stdout={stdout_text!r}",
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

    # 解 + Pydantic validate（strict=False 同上：允許 control chars in string）
    try:
        obj = json.loads(json_blob, strict=False)
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
# OpenAI-compatible path（Groq / Cerebras / 任意 OpenAI-format 免費端點）
# --------------------------------------------------------------------------
# 為什麼 Gemini 走 SDK、這兩家走這個泛用函式：
# - Gemini 有 google-genai 的 structured output（response_schema），品質最穩，留原路。
# - Groq / Cerebras 都是 OpenAI-compatible 的 /chat/completions，差別只有 base_url +
#   key + model → 一個函式覆蓋，多一家只要在 _OPENAI_COMPAT 加一筆 config。
#
# ⚠️ 能力 / 可靠度排序（fallback 觸發順序）：claude_cli → gemini → groq → cerebras。
#   2026-05 免費 tier 實測限制（務必知道，否則會誤判「為什麼長文 fallback 沒生效」）：
#   - Groq free：30 RPM / 6,000 TPM / 1,000 req/day。soul bundle(~17KB) 當 system 的
#     composer 容易撞 TPM；scorer / classifier 這種短 call 沒問題。
#   - Cerebras free：context 上限只有 8,192 tokens → composer 幾乎必超 → 回 400 →
#     fall through。短任務可用。
#   兩家定位都是「Claude + Gemini 同時掛掉」時的最後防線；撞限就記 raw_error、往下一條走。


@dataclass(frozen=True)
class _OpenAICompatProvider:
    """一個 OpenAI-compatible provider 的環境變數 contract。"""
    name: str
    key_env: str
    base_url_env: str
    base_url_default: str
    model_env: str
    model_default: str


_OPENAI_COMPAT: dict[str, _OpenAICompatProvider] = {
    "groq": _OpenAICompatProvider(
        name="groq",
        key_env="GROQ_API_KEY",
        base_url_env="GROQ_BASE_URL",
        base_url_default="https://api.groq.com/openai/v1",
        model_env="GROQ_MODEL",
        model_default="openai/gpt-oss-120b",
    ),
    "cerebras": _OpenAICompatProvider(
        name="cerebras",
        key_env="CEREBRAS_API_KEY",
        base_url_env="CEREBRAS_BASE_URL",
        base_url_default="https://api.cerebras.ai/v1",
        model_env="CEREBRAS_MODEL",
        model_default="zai-glm-4.7",
    ),
}


async def _try_openai_compatible(
    *,
    provider: _OpenAICompatProvider,
    system: str,
    prompt: str,
    response_model: Type[T],
    temperature: float,
    timeout_s: int,
) -> LLMResult[T]:
    """試呼叫任一 OpenAI-compatible /chat/completions 端點。

    與 claude / gemini path 收斂方式一致：抽回的 message.content → _extract_json_blob
    → json.loads(strict=False) → Pydantic model_validate。失敗時 data=None + raw_error。
    """
    api_key = os.getenv(provider.key_env)
    if not api_key:
        return LLMResult(
            data=None, provider=provider.name,
            raw_error=f"{provider.key_env} not set",
        )
    base_url = os.getenv(provider.base_url_env, provider.base_url_default).rstrip("/")
    model = os.getenv(provider.model_env, provider.model_default)

    # OpenAI 的 json_object response_format 要求 prompt 內出現 "json" 字樣，否則某些
    # 端點(含 Groq)會 400。caller 的 prompt 多半已含，但保險補一句。
    user_prompt = prompt.strip()
    if "json" not in (system + prompt).lower():
        user_prompt += "\n\nReturn only a single valid JSON object."

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system.strip()},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "stream": False,
    }

    try:
        import httpx  # 延遲 import：與 google-genai 一樣不拖累無此需求的 caller

        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except Exception as e:
        return LLMResult(
            data=None, provider=provider.name, model=model,
            raw_error=f"{type(e).__name__}: {e}",
        )

    if resp.status_code != 200:
        return LLMResult(
            data=None, provider=provider.name, model=model,
            raw_error=f"http {resp.status_code}: {resp.text[:300]}",
        )

    try:
        body = resp.json()
    except Exception as e:
        return LLMResult(
            data=None, provider=provider.name, model=model,
            raw_error=f"non-JSON body: {type(e).__name__}: {resp.text[:200]}",
        )

    choices = body.get("choices") or []
    if not choices:
        return LLMResult(
            data=None, provider=provider.name, model=model,
            raw_error=f"no choices: {str(body)[:200]}",
        )
    content = (choices[0].get("message") or {}).get("content", "") or ""

    json_blob = _extract_json_blob(content)
    if json_blob is None:
        return LLMResult(
            data=None, provider=provider.name, model=model,
            raw_error=f"no JSON blob in output; first 300 chars: {content[:300]!r}",
        )

    try:
        obj = json.loads(json_blob, strict=False)
        parsed = response_model.model_validate(obj)
    except (json.JSONDecodeError, ValidationError) as e:
        return LLMResult(
            data=None, provider=provider.name, model=model,
            raw_error=f"parse/validate failed: {type(e).__name__}: {str(e)[:300]}",
        )

    usage = body.get("usage") or {}
    return LLMResult(
        data=parsed,
        provider=provider.name,
        model=model,
        input_tokens=int((usage or {}).get("prompt_tokens", 0) or 0),
        output_tokens=int((usage or {}).get("completion_tokens", 0) or 0),
    )


# --------------------------------------------------------------------------
# 對外 API
# --------------------------------------------------------------------------

async def call_for_json(
    *,
    system: str,
    prompt: str,
    response_model: Type[T],
    gemini_model: str = "gemini-2.5-flash",
    temperature: float = 0.2,
    timeout_s: int = 180,
    backends: Optional[tuple] = None,
    disallowed_tools: Optional[tuple] = None,
) -> LLMResult[T]:
    """核心 API：依能力 / 可靠度排序逐一嘗試 backend，第一個成功即交付。

    預設鏈（2026-05-30 擴充）：claude_cli (Max 主腦) → gemini (SDK structured)
        → groq (OpenAI-compatible) → cerebras (OpenAI-compatible)。
        前兩條維持原行為；新增的 groq / cerebras 是「Claude + Gemini 同時不可用」
        時的免費雲端兜底，需設 GROQ_API_KEY / CEREBRAS_API_KEY 才會啟用，
        沒設就自動略過（對既有部署零影響）。各家免費 tier 限制見 _OPENAI_COMPAT
        區塊註解（Cerebras 8K context → 長文 composer 幾乎必 fall through）。

    Phase 8.19b 排序變更（2026-04-20）：
        原本 Gemini primary、Claude CLI fallback，因 Gemini free_tier quota=0
        實測失效 → 反轉成 Claude Max 當 primary brain。Max 訂閱含 CLI 使用，
        無額外 API 費用；額度 5 小時滾動 + 每週上限，以本專案每小時 2 篇、
        每篇 ~4 calls 的量級在限制內綽綽有餘。
        Gemini 保留作備援：哪天 Max 掛掉或使用者換 key，這條 path 還在。

    Phase 9.x (2026-05-12) — selective backend opt-out:
        新 ``backends`` 參數允許呼叫端指定**只用某些 backend**。
        - None (預設)        → 維持 ("claude_cli", "gemini") 行為：Claude 主、Gemini 備
        - ("claude_cli",)    → 只試 Claude CLI；失敗直接回 None，**不退 Gemini**
        - ("gemini",)        → 只試 Gemini；跳過 Claude
        起源：Substack 長文 composer 改為「Claude CLI only」——實測 Gemini
        2.5-flash-lite 在 1500 字 hard cap 跟反 AI 味規則上守不住，Claude CLI
        對 prompt 約束遵守度高很多。Gemini 在 Substack path 等於品質回退，
        不該被當成 fallback 觸發。Caller 寧可看 Claude CLI fail 也不要拿到
        Gemini 的次級輸出。

    Args:
        system: System instruction（全部前綴）
        prompt: User prompt / 新聞內容 / 評閱指令
        response_model: Pydantic BaseModel 類別（例如 NewsScore / MultiPlatformDraft）
        gemini_model: Gemini 模型名稱（備援用），預設 `gemini-2.5-flash`
            （2026-05 實測：2.0-flash-lite 免費額度已歸零 429 limit:0，故改用 2.5-flash）
        temperature: 僅 Gemini 使用（Claude CLI 用系統預設）
        timeout_s: Claude CLI subprocess 硬上限
        backends: 可指定的 backend 順序與白名單（tuple，按序嘗試）。
            None = 預設 ("claude_cli","gemini","groq","cerebras")。
            例：("claude_cli",) 只試 Claude；("gemini","groq") 跳過 Claude、
            先 Gemini 再 Groq。未知名稱會被略過。
        disallowed_tools: 傳給 Claude CLI 的 `--disallowedTools`（例：
            ("WebSearch","WebFetch") 關掉 agentic 上網）。僅 claude_cli path 有效。

    Returns:
        LLMResult，data 為 None 表所有指定 backend 都失敗（呼叫端要自己 skip）。
    """
    allowed = backends or ("claude_cli", "gemini", "groq", "cerebras")
    primary = allowed[0] if allowed else None
    last_error: Optional[str] = None

    # 依 `allowed` 內的順序逐一嘗試（= 能力 / 可靠度排序），第一個成功就回。
    # 預設鏈：claude_cli(Max 主腦) → gemini(SDK structured) → groq → cerebras。
    for name in allowed:
        if name == "claude_cli":
            if not _claude_cli_available():
                print(f"[llm_brain] ℹ️ `{CLAUDE_CLI_BIN}` 不在 PATH，略過 claude_cli。")
                continue
            result = await _try_claude_cli(
                system=system,
                prompt=prompt,
                response_model=response_model,
                timeout_s=timeout_s,
                disallowed_tools=disallowed_tools,
            )

        elif name == "gemini":
            if not _has_gemini_key():
                print("[llm_brain] ℹ️ 無 GEMINI_API_KEY，略過 gemini。")
                continue
            result = await _try_gemini(
                system=system,
                prompt=prompt,
                response_model=response_model,
                model=gemini_model,
                temperature=temperature,
            )

        elif name in _OPENAI_COMPAT:
            provider = _OPENAI_COMPAT[name]
            if not os.getenv(provider.key_env):
                print(f"[llm_brain] ℹ️ 無 {provider.key_env}，略過 {name}。")
                continue
            result = await _try_openai_compatible(
                provider=provider,
                system=system,
                prompt=prompt,
                response_model=response_model,
                temperature=temperature,
                timeout_s=timeout_s,
            )

        else:
            print(f"[llm_brain] ⚠️ 未知 backend `{name}`，略過。")
            continue

        if result.data is not None:
            if name != primary:
                print(f"[llm_brain] ℹ️ 交付來自 fallback：{name}/{result.model or ''}")
            return result

        last_error = result.raw_error
        print(f"[llm_brain] ⚠️ {name} 失敗。reason={result.raw_error}")

    # 所有指定 backend 都失敗
    return LLMResult(
        data=None,
        provider="none",
        raw_error=f"all requested backends failed: {list(allowed)}; last={last_error}",
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
