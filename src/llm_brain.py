"""
News Radar · LLM Brain (Phase 8.19)
====================================
統一的 LLM 呼叫層：claude_cli → gemini → GitHub Models GPT-4.1 mini →
GitHub Models GPT-4o mini → opencode → groq → cerebras → None（依能力排序）。

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

import os
os.environ.setdefault("LITELLM_LOG", "WARNING")
os.environ.setdefault("LITELLM_SUPPRESS_FEEDBACK", "1")

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
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
    """LLM 呼叫結果。data=None 表示所有指定路徑都失敗。"""
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

def _gemini_keys() -> list[str]:
    """收集所有可用 Gemini key，依序輪換（第一把撞 429 配額就換下一把）。

    2026-05-31 加入多 key 輪換：Gemini 免費 tier 限額低（且 per-project 計算），
    多一把獨立帳號的 key 等於多一份免費額度。來源 & 順序：
      1. GEMINI_API_KEY（可逗號分隔塞多把）
      2. GEMINI_API_KEY_2（單獨第二把，例如朋友的帳號）
    去空白、去重、保序。
    """
    raw: list[str] = []
    raw += [k.strip() for k in os.getenv("GEMINI_API_KEY", "").split(",")]
    raw.append(os.getenv("GEMINI_API_KEY_2", "").strip())
    seen: set[str] = set()
    out: list[str] = []
    for k in raw:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _has_gemini_key() -> bool:
    return bool(_gemini_keys())


def _is_quota_error(err: str) -> bool:
    """判斷錯誤是否為配額 / 限流類（值得換下一把 key 重試）。"""
    e = err.lower()
    return ("429" in e) or ("resource_exhausted" in e) or ("quota" in e) or ("rate limit" in e)


async def _try_gemini(
    *,
    system: str,
    prompt: str,
    response_model: Type[T],
    model: str,
    temperature: float,
) -> LLMResult[T]:
    """試呼叫 Gemini，依序輪換多把 key。失敗時 data=None + raw_error 記錯誤。

    輪換規則：第 i 把 key 若回配額 / 限流錯誤（429 / RESOURCE_EXHAUSTED）→ 換下一把；
    其他錯誤（auth / schema / 空 parsed）換 key 也是同結果 → 直接回，不浪費呼叫。
    """
    keys = _gemini_keys()
    if not keys:
        return LLMResult(
            data=None, provider="gemini", model=model,
            raw_error="GEMINI_API_KEY not set",
        )

    last_error = "unknown"
    for idx, api_key in enumerate(keys):
        try:
            # 延遲 import：sandbox 沒有 google-genai 時不拖累其他 caller
            from google import genai  # type: ignore

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
                # 極少見：SDK 回了但 parsed 空（通常是回的內容不符 schema）→ 換 key 無益
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

            if idx > 0:
                print(f"[llm_brain] ℹ️ Gemini 換用第 {idx + 1} 把 key 成功。")

            return LLMResult(
                data=parsed,
                provider="gemini",
                model=model,
                input_tokens=in_tok,
                output_tokens=out_tok,
            )
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            if _is_quota_error(last_error) and idx < len(keys) - 1:
                print(
                    f"[llm_brain] ⟳ Gemini 第 {idx + 1} 把 key 配額用盡，換第 {idx + 2} 把。"
                )
                continue
            # 非配額錯誤，或已是最後一把 → 不再換
            break

    return LLMResult(
        data=None, provider="gemini", model=model,
        raw_error=last_error,
    )


# --------------------------------------------------------------------------
# Gemini CLI path
# --------------------------------------------------------------------------

GEMINI_CLI_BIN = os.getenv("GEMINI_CLI_BIN", "gemini")

def _gemini_cli_available() -> bool:
    return shutil.which(GEMINI_CLI_BIN) is not None

def _gemini_cli_dirs() -> list[str]:
    """多帳號輪替用的 **HOME 目錄**清單（GEMINI_CLI_CONFIG_DIRS，逗號分隔，按優先序）。
    每個 entry 是一個 HOME，其底下的 `~/.gemini` 各自登入一個 Google AI Pro 帳號。
    呼叫端用 env['HOME']=<entry> 切換帳號（gemini CLI 讀 $HOME/.gemini；**不吃
    GEMINI_CONFIG_DIR**，2026-06-01 實測）。任一帳號失敗（配額/未登入）→ 輪到下一個。
    空清單 = 用預設 HOME（現役 ~/.gemini）。"""
    raw = os.getenv("GEMINI_CLI_CONFIG_DIRS", "").split(",")
    dirs = [d.strip() for d in raw if d.strip()]
    return dirs if dirs else [""] # fallback to default HOME

async def _try_gemini_cli(
    *,
    system: str,
    prompt: str,
    response_model: Type[T],
    timeout_s: float,
    model_name: str = "gemini-3.1-pro-preview",
) -> LLMResult[T]:
    """試呼叫 Gemini CLI (Google AI Pro)，依序輪換多組 Config Dirs。失敗時 data=None + raw_error 記錯誤。"""
    schema_json = json.dumps(response_model.model_json_schema())
    # 2026-06-01: 極其嚴格的 JSON 提示，防止 Gemini 3.1 Pro 輸出雜訊或中斷
    full_prompt = (
        f"{system}\n\n"
        f"IMPORTANT: You MUST output a valid JSON object matching the schema below. "
        f"Do NOT include any explanations, markdown code blocks, or thoughts. "
        f"Output ONLY the raw JSON string.\n\n"
        f"SCHEMA:\n{schema_json}\n\n"
        f"--- USER PROMPT ---\n{prompt}"
    )

    cmd = [
        GEMINI_CLI_BIN,
        "-p", full_prompt,
        "-o", "json",
        "-m", model_name,
        "--approval-mode", "plan"
    ]
    
    dirs = _gemini_cli_dirs()
    last_error = "unknown"
    
    for idx, config_dir in enumerate(dirs):
        env = os.environ.copy()
        env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
        if config_dir:
            # HOME is the real account switch (GEMINI_CONFIG_DIR is a no-op). Each
            # entry is a HOME dir whose ~/.gemini is logged into one AI Pro account.
            env["HOME"] = os.path.expanduser(config_dir)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="/tmp", # isolate to avoid agentic side-effects
                env=env
            )

            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                # Timeout is not a quota issue, so we don't automatically rotate
                return LLMResult(
                    data=None, provider="gemini_cli", model=model_name,
                    raw_error=f"Timeout ({timeout_s}s)"
                )

            if proc.returncode != 0:
                err_text = stderr.decode('utf-8', errors='replace')
                last_error = f"CLI failed (exit {proc.returncode}): {err_text}"
                if idx < len(dirs) - 1:
                    # Rotate to the next account on ANY error (quota OR auth/not-set-up),
                    # so a not-yet-logged-in account falls through to the next one.
                    reason = "配額用盡" if _is_quota_error(last_error) else "失敗(可能未登入/auth)"
                    print(f"[llm_brain] ⟳ Gemini CLI 第 {idx + 1} 組帳號{reason}，換第 {idx + 2} 組。")
                    continue
                # 這裡若不是 Quota error 或是最後一組帳號，就跳出輪替
                break
                
            try:
                envelope = json.loads(stdout.decode('utf-8'))
                parsed_text = envelope.get("response", "")
                
                # Remove any potential markdown json block markers
                parsed_text = parsed_text.strip()
                if parsed_text.startswith("```json"):
                    parsed_text = parsed_text[7:]
                if parsed_text.startswith("```"):
                    parsed_text = parsed_text[3:]
                if parsed_text.endswith("```"):
                    parsed_text = parsed_text[:-3]
                parsed_text = parsed_text.strip()
                
                parsed = response_model.model_validate_json(parsed_text)
                
                # Extract usage metadata from gemini cli envelope
                in_tok = out_tok = 0
                stats = envelope.get("stats", {})
                models = stats.get("models", {})
                model_stats = models.get(model_name, {})
                tokens = model_stats.get("tokens", {})
                in_tok = tokens.get("input", 0)
                out_tok = tokens.get("candidates", 0)
                
                if idx > 0:
                    print(f"[llm_brain] ℹ️ Gemini CLI 換用第 {idx + 1} 組帳號成功。")
                
                # 取得實際帳號 Email。config_dir 現在是 HOME，帳號檔在 HOME/.gemini/。
                acct_email = "unknown"
                try:
                    gemini_home = os.path.expanduser(config_dir) if config_dir else os.path.expanduser("~")
                    acct_path = os.path.join(gemini_home, ".gemini", "google_accounts.json")
                    if os.path.exists(acct_path):
                        with open(acct_path, "r") as f:
                            acct_info = json.load(f)
                            acct_email = acct_info.get("active", "unknown").split(" @")[0] # hsin290525 @...
                except Exception:
                    pass

                return LLMResult(
                    data=parsed, provider="gemini_cli", model=f"{model_name} ({acct_email})",
                    input_tokens=in_tok, output_tokens=out_tok
                )
            except Exception as e:
                last_error = f"Failed to parse or validate JSON: {type(e).__name__}: {e}"
                break
                
        except Exception as e:
            last_error = f"Execution error: {type(e).__name__}: {e}"
            break
            
    return LLMResult(
        data=None, provider="gemini_cli", model=model_name,
        raw_error=last_error
    )


# --------------------------------------------------------------------------
# Antigravity CLI (agy) — Gemini CLI 個人版 2026-06-18 被 Google 收掉後的接班。
# 你的 AI Pro 訂閱額度改由 agy 取用：headless `agy -p` 直接拿 gemini-3.1-pro，token-free。
# 注意：agy 走系統 keyring 單一登入（沒有 gemini-cli 那種多 gemhome 帳號輪替）；多帳號待官方支援。
# 只在本機（Mac）可用；雲端 runner 沒裝 agy → _agy_available() 回 False 自動略過。
# --------------------------------------------------------------------------
AGY_BIN = os.path.expanduser(os.getenv("AGY_BIN", "~/.local/bin/agy"))
AGY_MODEL = os.getenv("AGY_MODEL", "Gemini 3.1 Pro (High)")
# agy 內部的模型後備鏈（逗號分隔，依序嘗試）。強模型先、續航模型後。
# 設 AGY_MODEL 時它一律排第一，鏈只當它的後備——這樣單次覆寫
# （AGY_MODEL="..." python compose.py）語意不變，仍然「優先用我指定的」。
# 同一模型的重試次數。agy 偶發輸出不合 schema，重試比換模型便宜也更可能成功。
AGY_RETRIES_PER_MODEL = int(os.getenv("AGY_RETRIES_PER_MODEL", "2"))
AGY_MODEL_CHAIN = os.getenv(
    "AGY_MODEL_CHAIN",
    "Claude Opus 4.6 (Thinking),Gemini 3.6 Flash (High),Gemini 3.1 Pro (High)",
)


def _agy_model_chain() -> list:
    """回傳去重後的嘗試順序。AGY_MODEL 若有設就置頂。"""
    chain = []
    for name in [os.getenv("AGY_MODEL") or ""] + AGY_MODEL_CHAIN.split(","):
        name = name.strip()
        if name and name not in chain:
            chain.append(name)
    return chain or [AGY_MODEL]


def _agy_available() -> bool:
    return os.path.exists(AGY_BIN)


def _agy_home_dirs() -> list:
    """AGY_HOME_DIRS：逗號分隔的 HOME 目錄清單，每個 = 一個登入 agy 的 Google 帳號。
    agy 憑證是 HOME 相對的（實測換 HOME 即要求重新登入），故比照 gemini-cli 用 HOME 切換多帳號。
    空字串項 = 用真實 HOME（預設帳號）。未設則只用真實 HOME（單帳號）。"""
    raw = os.getenv("AGY_HOME_DIRS", "")
    if not raw.strip():
        return [""]
    return [d.strip() for d in raw.split(",")]


def _compact_schema_for_prompt(response_model: Type[T]) -> tuple[str, tuple[str, ...]]:
    """Build the small, writer-visible schema used by the Antigravity CLI.

    ``SkipJsonSchema`` fields are pipeline metadata, so derive visibility and
    requiredness from Pydantic's public JSON schema instead of ``model_fields``.
    """
    import typing as _typing

    public_schema = response_model.model_json_schema()
    visible_names = tuple(public_schema.get("properties", {}).keys())
    required_set = set(public_schema.get("required", ()))
    field_descriptions = []
    for name in visible_names:
        field = response_model.model_fields[name]
        core = field.annotation
        args = _typing.get_args(core)
        if args and type(None) in args:
            core = next((arg for arg in args if arg is not type(None)), str)
            args = _typing.get_args(core)
        if args and all(isinstance(arg, str) for arg in args):
            spec = "|".join(args)
        elif core is int:
            spec = "整數"
        else:
            spec = "字串"
        optional = "" if name in required_set else "（可省略）"
        field_descriptions.append(f'  "{name}": <{spec}>{optional}')
    compact = "{\n" + ",\n".join(field_descriptions) + "\n}"
    required = tuple(name for name in visible_names if name in required_set)
    return compact, required


async def _try_agy(
    *,
    system: str,
    prompt: str,
    response_model: Type[T],
    timeout_s: float,
    model_name: Optional[str] = None,
) -> LLMResult[T]:
    """Antigravity CLI（agy）：AI Pro 訂閱 → gemini-3.1-pro，token-free、headless。
    `agy -p` 輸出純文字（無 -o json envelope），故 stdout 本身就是要解析的 JSON 回應。
    多帳號：依 AGY_HOME_DIRS 逐一換 HOME = 換 Google 帳號（agy 憑證 HOME 相對；比照 gemini-cli）。"""
    model_name = model_name or AGY_MODEL
    # agy 的 -p 約 32K 字截斷（2026-06-21 實測：company prompt 34.5K → 後段被截、agy 看不到任務）。
    # 故用「精簡欄位表」取代冗長的 pydantic json schema dump（5.5K→~0.6K），欄位說明本就在 user prompt。
    schema_compact, required_names = _compact_schema_for_prompt(response_model)
    required = ", ".join(required_names) or "（無）"
    # 把任務與目前 schema 的必填欄位放在最後（最高注意力）。
    full_prompt = (
        f"{system}\n\n"
        f"輸出 JSON 欄位表（值照欄位說明寫；<a|b|c> 表示三選一）：\n{schema_compact}\n\n"
        f"--- 任務素材 / USER PROMPT ---\n{prompt}\n\n"
        f"=== 最終指令（最高優先，覆蓋以上任何衝突）===\n"
        f"1. 只根據上面『任務素材 / USER PROMPT』指定的那一篇來寫；不要把寫作規則或輸出示例當成文章內容。\n"
        f"2. 只輸出一個 raw JSON 物件（無 markdown 圍欄、無任何說明或思考過程）。\n"
        f"3. JSON **必含 schema 全部必填欄位**：{required}。所有欄位遵守上方標示的型別與列舉值。"
    )
    cmd = [
        AGY_BIN, "-p", full_prompt,
        "--model", model_name,
        "--print-timeout", f"{int(timeout_s)}s",
    ]
    dirs = _agy_home_dirs()
    last_error = "unknown"
    for idx, home_dir in enumerate(dirs):
        env = os.environ.copy()
        if home_dir:
            env["HOME"] = os.path.expanduser(home_dir)  # 換 HOME = 換 agy 登入的帳號
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="/tmp",  # 隔離，避免 agent 對專案目錄產生副作用
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s + 20)
            except asyncio.TimeoutError:
                proc.kill()
                return LLMResult(data=None, provider="antigravity_cli", model=model_name,
                                 raw_error=f"Timeout ({timeout_s}s)")
            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")
            # 未登入 / 配額用盡 → 換下一組帳號
            if proc.returncode != 0 or "Authentication required" in out or "Authentication required" in err:
                last_error = f"agy exit {proc.returncode}: {(err or out).strip()[:200]}"
                if idx < len(dirs) - 1:
                    reason = "配額用盡" if _is_quota_error(last_error) else "未登入/失敗"
                    print(f"[llm_brain] ⟳ agy 第 {idx + 1} 組帳號{reason}，換第 {idx + 2} 組。")
                    continue
                break
            text = out.strip()
            for fence in ("```json", "```"):
                if text.startswith(fence):
                    text = text[len(fence):]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            # agy 偶爾在 JSON 前後夾話 → 抽第一個 { 到最後一個 }
            if not text.startswith("{"):
                i, j = text.find("{"), text.rfind("}")
                if i != -1 and j > i:
                    text = text[i:j + 1]
            parsed = response_model.model_validate_json(text)
            if idx > 0:
                print(f"[llm_brain] ℹ️ agy 換用第 {idx + 1} 組帳號成功。")
            return LLMResult(data=parsed, provider="antigravity_cli", model=model_name)
        except Exception as e:
            # 解析/驗證失敗 = 內容問題，換帳號也沒用 → 直接跳出
            last_error = f"agy parse/exec error: {type(e).__name__}: {e}"
            break
    return LLMResult(data=None, provider="antigravity_cli", model=model_name, raw_error=last_error)


# --------------------------------------------------------------------------

CLAUDE_CLI_BIN = os.getenv("CLAUDE_CLI_BIN", "claude")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "opus")
# claude CLI 的推理強度（low/medium/high/xhigh/max）。長文寫作預設拉到 high。
# 空字串＝不傳 --effort，交給 CLI 自己的預設。
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "high")


# --------------------------------------------------------------------------
# Codex CLI path — primary long-form writer on the owner Windows machine.
# --------------------------------------------------------------------------

CODEX_CLI_BIN = os.getenv("CODEX_CLI_BIN", "codex")
CODEX_MODEL = os.getenv("CODEX_MODEL", "gpt-latest")


def _spawnable_cli_command(
    configured: str,
    *,
    platform_name: Optional[str] = None,
) -> Optional[tuple[str, ...]]:
    """Resolve a CLI path that ``create_subprocess_exec`` can spawn directly.

    On Windows, npm shims are commonly found as ``.CMD`` before a native
    ``.exe``. PowerShell can launch the shim, but asyncio's exec transport
    raises ``WinError 5``. Prefer the same-name executable when one exists.
    """
    path = shutil.which(configured)
    if not path:
        return None
    if (platform_name or os.name) == "nt" and Path(path).suffix.lower() in {
        ".cmd",
        ".bat",
    }:
        stem = Path(path).stem.lower()
        shim_root = Path(path).parent
        # Codex's npm shim invokes node + codex.js. The WindowsApps codex.exe
        # can be discoverable yet ACL-blocked, so follow the known shim target.
        if stem == "codex":
            node = shim_root / "node.exe"
            script = (
                shim_root
                / "node_modules"
                / "@openai"
                / "codex"
                / "bin"
                / "codex.js"
            )
            if node.is_file() and script.is_file():
                return (str(node), str(script))
        # Claude's npm shim targets its package-local binary. A separate
        # ``claude.exe`` on PATH may be an unrelated multi-call launcher.
        if stem == "claude":
            bundled = (
                shim_root
                / "node_modules"
                / "@anthropic-ai"
                / "claude-code"
                / "bin"
                / "claude.exe"
            )
            if bundled.is_file():
                return (str(bundled),)
        return None
    return (path,)


def _strict_response_schema(value: Any) -> Any:
    """Return an OpenAI structured-output-compatible JSON Schema copy."""
    if isinstance(value, list):
        return [_strict_response_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    strict = {key: _strict_response_schema(item) for key, item in value.items()}
    if strict.get("type") == "object" or "properties" in strict:
        strict["additionalProperties"] = False
        properties = strict.get("properties")
        if isinstance(properties, dict):
            # Structured outputs require every declared property to be listed.
            # Optional values remain representable through a nullable schema.
            strict["required"] = list(properties)
    return strict


def _codex_cli_available() -> bool:
    return _spawnable_cli_command(CODEX_CLI_BIN) is not None


async def _try_codex_cli(
    *,
    system: str,
    prompt: str,
    response_model: Type[T],
    timeout_s: int,
    disallowed_tools: Optional[tuple] = None,
) -> LLMResult[T]:
    """Run Codex non-interactively with a read-only sandbox and JSON schema."""
    del disallowed_tools  # Codex receives no writable tools in read-only mode.
    if not _codex_cli_available():
        return LLMResult(
            data=None,
            provider="codex_cli",
            model=CODEX_MODEL,
            raw_error=f"`{CODEX_CLI_BIN}` not found on PATH",
        )
    command = _spawnable_cli_command(CODEX_CLI_BIN)
    assert command is not None

    full_prompt = (
        f"{system.strip()}\n\n"
        f"--- 任務素材 / USER PROMPT ---\n{prompt.strip()}\n\n"
        "=== 最終輸出契約 ===\n"
        "只輸出符合指定 JSON Schema 的文章物件；不要輸出 markdown fence、"
        "思考過程、工具指令或任何 JSON 以外文字。"
    )

    try:
        with tempfile.TemporaryDirectory(prefix="news-radar-codex-") as temp_dir:
            temp_root = Path(temp_dir)
            schema_path = temp_root / "response.schema.json"
            output_path = temp_root / "last-message.json"
            schema_path.write_text(
                json.dumps(
                    _strict_response_schema(response_model.model_json_schema()),
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            args = [
                *command,
                "exec",
                "--model",
                CODEX_MODEL,
                "--sandbox",
                "read-only",
                "--ephemeral",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--color",
                "never",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "-",
            ]
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=temp_dir,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(full_prompt.encode("utf-8")),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                proc.kill()
                return LLMResult(
                    data=None,
                    provider="codex_cli",
                    model=CODEX_MODEL,
                    raw_error=f"codex CLI timeout ({timeout_s}s)",
                )

            stdout_text = stdout_bytes.decode("utf-8", errors="replace")
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
            if proc.returncode != 0:
                return LLMResult(
                    data=None,
                    provider="codex_cli",
                    model=CODEX_MODEL,
                    raw_error=(
                        f"codex CLI exit={proc.returncode}; "
                        f"stderr={stderr_text[:500]!r}; stdout={stdout_text[:500]!r}"
                    ),
                )

            raw = (
                output_path.read_text(encoding="utf-8")
                if output_path.is_file()
                else stdout_text
            )
            blob = _extract_json_blob(raw)
            if blob is None:
                return LLMResult(
                    data=None,
                    provider="codex_cli",
                    model=CODEX_MODEL,
                    raw_error=f"no JSON blob in codex output; first 300 chars: {raw[:300]!r}",
                )
            parsed = response_model.model_validate_json(blob)
            return LLMResult(
                data=parsed,
                provider="codex_cli",
                model=CODEX_MODEL,
            )
    except Exception as exc:
        return LLMResult(
            data=None,
            provider="codex_cli",
            model=CODEX_MODEL,
            raw_error=f"codex CLI spawn/parse failed: {type(exc).__name__}: {exc}",
        )

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
    return _spawnable_cli_command(CLAUDE_CLI_BIN) is not None


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
    command = _spawnable_cli_command(CLAUDE_CLI_BIN)
    assert command is not None

    # ⚠️ Arg ordering matters: `--disallowedTools` is variadic (`<tools...>`),
    # so it must NOT sit immediately before the positional prompt or the parser
    # swallows the prompt as another tool name ("Input must be provided…"). We
    # therefore emit all variadic / value flags first and keep the boolean
    # `--no-session-persistence` as the last option before the positional prompt.
    args = [
        *command,
        "-p",
        "--output-format", "json",
        "--model", CLAUDE_MODEL,
    ]
    # 推理強度：Substack 是長文論證，預設 high。之前完全沒傳這個旗標，
    # 等於一直用 CLI 預設強度在寫稿。
    if CLAUDE_EFFORT:
        args += ["--effort", CLAUDE_EFFORT]
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

    # 從 envelope 抓 token / cost / 實際模型供 caller log + provenance。
    in_tok = out_tok = 0
    cost = 0.0
    real_model = CLAUDE_MODEL
    if isinstance(envelope, dict):
        usage = envelope.get("usage") or {}
        if isinstance(usage, dict):
            in_tok = int(usage.get("input_tokens", 0) or 0)
            out_tok = int(usage.get("output_tokens", 0) or 0)
        cost = float(envelope.get("total_cost_usd", 0.0) or 0.0)
        # 實際模型藏在 modelUsage（dict keyed by 真實模型名）。挑 output 最多的當主寫手；
        # 沒有就退回 envelope.model / bin 名。這讓上層知道是 claude-opus / sonnet（原生方案）
        # 還是被 CCR 路由到別家模型（名稱非 claude-*）。
        mu = envelope.get("modelUsage")
        if isinstance(mu, dict) and mu:
            try:
                real_model = max(
                    mu.items(),
                    key=lambda kv: int((kv[1] or {}).get("output_tokens", 0) or 0),
                )[0]
            except Exception:
                real_model = next(iter(mu.keys()), CLAUDE_MODEL)
        elif envelope.get("model"):
            real_model = str(envelope["model"])

    return LLMResult(
        data=parsed,
        provider="claude_cli",
        model=real_model,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=cost,
    )


# --------------------------------------------------------------------------
# OpenAI-compatible path（GitHub Models / Groq / Cerebras / 其他相容端點）
# --------------------------------------------------------------------------
# 為什麼 Gemini 走 SDK、這兩家走這個泛用函式：
# - Gemini 有 google-genai 的 structured output（response_schema），品質最穩，留原路。
# - Groq / Cerebras 都是 OpenAI-compatible 的 /chat/completions，差別只有 base_url +
#   key + model → 一個函式覆蓋，多一家只要在 _OPENAI_COMPAT 加一筆 config。
#
# ⚠️ 能力 / 可靠度排序由 call_for_json 的預設鏈決定。
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
    # GitHub Actions can mint a short-lived GITHUB_TOKEN with ``models: read``.
    # This gives the cloud pipeline a governed fallback without another
    # long-lived third-party secret.  It still returns through the same
    # Pydantic contract and all downstream editorial/source-quality gates.
    "github_models": _OpenAICompatProvider(
        name="github_models",
        key_env="GITHUB_TOKEN",
        base_url_env="GITHUB_MODELS_BASE_URL",
        base_url_default="https://models.github.ai/inference",
        model_env="GITHUB_MODELS_MODEL",
        model_default="openai/gpt-4.1-mini",
    ),
    # GitHub Models quotas are model-specific.  Keep one separate low-tier
    # model pool so a bounded rewrite can finish when gpt-4.1-mini returns 429.
    # This is not a quality bypass: the same schema, numeric grounding, and
    # current-guard requirements still apply.
    "github_models_4o": _OpenAICompatProvider(
        name="github_models_4o",
        key_env="GITHUB_TOKEN",
        base_url_env="GITHUB_MODELS_BASE_URL",
        base_url_default="https://models.github.ai/inference",
        model_env="GITHUB_MODELS_4O_MODEL",
        model_default="openai/gpt-4o-mini",
    ),
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
    # OpenCode Zen 的 "big-pickle"（社群證實 = 智譜 GLM-4.6，200k context /
    # 160k 輸入 / 32k 輸出，2026-05 限免）。大 context → 排在 groq/cerebras 之前，
    # 當長文 composer 在 Claude+Gemini 都掛時的兜底。key 取得：https://opencode.ai/auth
    "opencode": _OpenAICompatProvider(
        name="opencode",
        key_env="OPENCODE_API_KEY",
        base_url_env="OPENCODE_BASE_URL",
        base_url_default="https://opencode.ai/zen/v1",
        model_env="OPENCODE_MODEL",
        model_default="big-pickle",
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
# LiteLLM path (GitHub Actions 主要的 LLM 路徑，2026-06-01)
# --------------------------------------------------------------------------
#
# LiteLLM 統一了 100+ LLM provider（OpenAI / Anthropic / Google / Groq 等）
# 的 OpenAI-compatible /chat/completions 介面。好處：
#   - 一支函式覆蓋所有 provider，不用每加一家 provider 就加一段程式碼
#   - 支援 model 字串路由（"gemini/gemini-2.5-flash"、"groq/llama-3.3-70b"）
#   - 自動 fallback（若設 LITELLM_API_KEY 但沒設想用的 provider key，會主動跳過）
#
# 本路徑在 Cloud (GitHub Actions) 環境是預設首要路徑；Mac 環境保留 claude_cli 優先。
# 啟用條件：LITELLM_API_KEY 有設（可以是任何支援 OpenAI-compatible 的 provider key；
#   也可設 OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY 等，LiteLLM 會自動取用）
LITELLM_MODEL = os.getenv("LITELLM_MODEL", "gemini/gemini-2.5-flash")
_QUOTA_EXHAUSTED_BACKENDS: set[str] = set()


def _quota_family(backend: str) -> set[str]:
    """Return wrappers that consume the same provider quota."""
    if LITELLM_MODEL.lower().startswith("gemini/") and backend in {
        "litellm",
        "gemini",
    }:
        return {"litellm", "gemini"}
    return {backend}


def _litellm_available() -> bool:
    """LiteLLM 可用條件：litellm 已 install 且至少有一把 provider key。

    LiteLLM 不需要專屬 key；取用標準 OpenAI-compatible env vars：
      - OPENAI_API_KEY       → "openai/..."
      - ANTHROPIC_API_KEY    → "claude-..."
      - GEMINI_API_KEY       → "gemini/..."
      - GROQ_API_KEY         → "groq/..."
    只要任一有設、litellm importable 就算可用。
    """
    try:
        import litellm  # noqa: F401
    except ImportError:
        return False
    provider_keys = [
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
        "GROQ_API_KEY", "LITELLM_API_KEY",
    ]
    return any(os.getenv(k) for k in provider_keys)


async def _try_litellm(
    *,
    system: str,
    prompt: str,
    response_model: Type[T],
    temperature: float,
    timeout_s: int,
) -> LLMResult[T]:
    """試呼叫 LiteLLM 統一接口，支援 100+ provider。

    Model 由 LITELLM_MODEL env var 控制（預設 gemini/gemini-2.5-flash）。
    Provider 變更 model 字首即可：
      - "groq/llama-3.3-70b-versatile"
      - "openai/gpt-4o-mini"
      - "gemini/gemini-2.5-flash"
      - "claude/claude-sonnet-4-20250514"

    Steps:
      1. 組裝 OpenAI-compatible payload
      2. litellm.acompletion() 呼叫
      3. 抽 content → _extract_json_blob → Pydantic validate
    """
    if not _litellm_available():
        return LLMResult(
            data=None, provider="litellm",
            raw_error="litellm not installed or no provider key set",
        )

    try:
        from litellm import acompletion
    except ImportError:
        return LLMResult(
            data=None, provider="litellm",
            raw_error="litellm import failed",
        )

    model = LITELLM_MODEL
    user_prompt = prompt.strip()
    if "json" not in (system + prompt).lower():
        user_prompt += "\n\nReturn only a single valid JSON object."

    messages = [
        {"role": "system", "content": system.strip()},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = await acompletion(
            model=model,
            messages=messages,
            temperature=temperature,
            response_format={"type": "json_object"},
            request_timeout=timeout_s,
            num_retries=2,
        )
    except Exception as e:
        return LLMResult(
            data=None, provider="litellm", model=model,
            raw_error=f"{type(e).__name__}: {e}",
        )

    choices = getattr(response, "choices", None) or []
    if not choices or not choices[0].message:
        return LLMResult(
            data=None, provider="litellm", model=model,
            raw_error="no choices in LiteLLM response",
        )

    content = (choices[0].message.content or "").strip()
    if not content:
        return LLMResult(
            data=None, provider="litellm", model=model,
            raw_error="empty LiteLLM response content",
        )

    json_blob = _extract_json_blob(content)
    if json_blob is None:
        return LLMResult(
            data=None, provider="litellm", model=model,
            raw_error=f"no JSON blob in LiteLLM output; first 300 chars: {content[:300]!r}",
        )

    try:
        obj = json.loads(json_blob, strict=False)
        parsed = response_model.model_validate(obj)
    except (json.JSONDecodeError, ValidationError) as e:
        return LLMResult(
            data=None, provider="litellm", model=model,
            raw_error=f"parse/validate failed: {type(e).__name__}: {str(e)[:300]}",
        )

    # 從 LiteLLM response 抽 usage
    usage = getattr(response, "usage", None)
    in_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
    out_tok = getattr(usage, "completion_tokens", 0) if usage else 0

    return LLMResult(
        data=parsed,
        provider="litellm",
        model=model,
        input_tokens=int(in_tok or 0),
        output_tokens=int(out_tok or 0),
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

    預設鏈（2026-07-24 擴充）：claude_cli (Max 主腦) → gemini (SDK structured,
        1M context) → GitHub Models (gpt-4.1-mini → gpt-4o-mini) → opencode
        (big-pickle = GLM-4.6, 200k) → groq → cerebras。
        GitHub Models 使用 Actions 短效 GITHUB_TOKEN；其餘免費兜底需設對應 key
        （OPENCODE_API_KEY / GROQ_API_KEY / CEREBRAS_API_KEY）才會啟用，沒設就自動
        略過。排序綜合能力、可靠度與可用 context：
        長文兜底優先走 context 大的（gemini 1M → opencode 200k），groq(6K TPM)、
        cerebras(8K context) 殿後。各家限制見 _OPENAI_COMPAT 區塊註解。

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
            None = 預設 ("claude_cli","gemini_cli","gemini","github_models",
            "github_models_4o","opencode","groq","cerebras")。
            例：("claude_cli",) 只試 Claude；("gemini","groq") 跳過 Claude、
            先 Gemini 再 Groq。未知名稱會被略過。
        disallowed_tools: 傳給 Claude CLI 的 `--disallowedTools`（例：
            ("WebSearch","WebFetch") 關掉 agentic 上網）。僅 claude_cli path 有效。

    Returns:
        LLMResult，data 為 None 表所有指定 backend 都失敗（呼叫端要自己 skip）。
    """
    use_quota_circuit = backends is None
    if backends is None:
        # 動態預設：Cloud (GitHub Actions) → litellm → gemini → groq；
        # Mac (有 Claude CLI) → claude_cli → gemini → litellm。
        # 由 CLAUDE_CLI_BIN 是否在 PATH 決定——GitHub Actions runner 上沒有 claude CLI。
        if _claude_cli_available():
            backends = (
                "claude_cli", "litellm", "gemini", "gemini_cli",
                "github_models", "github_models_4o", "opencode", "groq",
                "cerebras",
            )
        else:
            backends = (
                "litellm", "gemini", "github_models", "github_models_4o",
                "opencode", "groq", "cerebras",
            )
    allowed = backends
    primary = allowed[0] if allowed else None
    last_error: Optional[str] = None

    # 依 `allowed` 內的順序逐一嘗試（= 能力 / 可靠度排序），第一個成功就回。
    # 預設鏈：claude_cli(Max 主腦) → gemini(SDK structured) → groq → cerebras。
    for name in allowed:
        if use_quota_circuit and name in _QUOTA_EXHAUSTED_BACKENDS:
            print(
                f"[llm_brain] ℹ️ quota circuit 已開啟，略過本 process 已耗盡的 {name}。"
            )
            continue
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

        elif name == "codex_cli":
            if not _codex_cli_available():
                print(f"[llm_brain] ℹ️ `{CODEX_CLI_BIN}` 不在 PATH，略過 codex_cli。")
                continue
            result = await _try_codex_cli(
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

        elif name == "litellm":
            if not _litellm_available():
                print("[llm_brain] ℹ️ litellm 不可用（無 provider key 或未 install），略過。")
                continue
            result = await _try_litellm(
                system=system,
                prompt=prompt,
                response_model=response_model,
                temperature=temperature,
                timeout_s=timeout_s,
            )

        elif name == "antigravity_cli":
            if not _agy_available():
                print(f"[llm_brain] ℹ️ agy 不在 {AGY_BIN}，略過 antigravity_cli。")
                continue
            # agy 內部的模型鏈：強模型先試，撞上限或解析失敗就換下一個，
            # **鏈用完才掉出 agy**。原本只試單一 AGY_MODEL，一失敗就直接掉到
            # 外部 backend（gemini API / opencode…），而那些明顯弱一階——
            # 2026-08-08 MARA 首刷就是這樣掉到 gemini-2.5-flash，產出標題 21 字、
            # 數字取自已不可重現的 TTM 欄位。
            #
            # 預設把 Opus 排前面、Flash 3.6 墊後：Opus 品質較好但額度緊，
            # Flash 3.6 上限高，適合當同一天要連寫多篇時的續航模型。
            result = None
            # 同一模型先重試再換模型。agy 的失敗多半是「輸出不合 schema」，
            # 而那是機率性的，不是能力問題：2026-08-10 排程在研究簡報階段收到
            # 「4 validation errors for EditorialResearchBrief」整條鏈就崩了，
            # 但同一天同一條管線手動跑兩次都一次過。
            # 沒有重試 = 把一個間歇性失敗當成永久失敗，然後掉到結構上更弱的後備。
            for _idx, _agy_model in enumerate(_agy_model_chain()):
                for _attempt in range(AGY_RETRIES_PER_MODEL):
                    result = await _try_agy(
                        system=system,
                        prompt=prompt,
                        response_model=response_model,
                        timeout_s=timeout_s,
                        model_name=_agy_model,
                    )
                    if result.data is not None:
                        break
                    if _attempt + 1 < AGY_RETRIES_PER_MODEL:
                        print(f"[llm_brain] ↻ agy「{_agy_model}」第 {_attempt + 1} 次未產出"
                              f"（{str(result.raw_error)[:60]}），重試。")
                if result.data is not None:
                    break
                _remaining = _agy_model_chain()[_idx + 1:]
                if _remaining:
                    print(f"[llm_brain] ⟳ agy「{_agy_model}」未產出"
                          f"（{str(result.raw_error)[:70]}），改試「{_remaining[0]}」。")

        elif name == "gemini_cli":
            if not _gemini_cli_available():
                print(f"[llm_brain] ℹ️ `{GEMINI_CLI_BIN}` 不在 PATH，略過 gemini_cli。")
                continue
            result = await _try_gemini_cli(
                system=system,
                prompt=prompt,
                response_model=response_model,
                timeout_s=timeout_s,
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
        if (
            use_quota_circuit
            and result.raw_error
            and _is_quota_error(result.raw_error)
        ):
            exhausted = _quota_family(name)
            _QUOTA_EXHAUSTED_BACKENDS.update(exhausted)
            print(
                "[llm_brain] ℹ️ quota circuit 記錄本 process 已耗盡："
                + ",".join(sorted(exhausted))
            )

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
