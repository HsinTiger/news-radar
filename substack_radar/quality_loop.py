"""品質迴圈：確定性閘門 → agy 稽核 session → 重驗，直到達標或用完輪數。

設計取捨（2026-08-18 稽核之後定的）
----------------------------------
把「兩個 LLM 互相稽核」直接當成品質保證是不夠的。那天在瑞昱稿抓到的錯裡，
最嚴重的三個都不是判斷失誤，而是機械性錯誤：

  * 營業利益寫成 14.39 億（正確 143.9 億，差 10 倍）
  * 主來源連結 404
  * 把分析師共識 EPS 寫成「管理層指引落空」——**管線的欄位標籤本身就寫錯**

第三個尤其說明問題：稽核 agent 讀到同一個錯標籤，只會照著再確認一次。
兩個模型會很有信心地在同一個錯誤上達成共識。

所以順序是：**先讓程式把能算的算完，再把算不出來的交給模型。**
確定性違規當成給稽核 agent 的工單，而不是讓它自由發揮找碴。

迴圈終止條件三選一：零違規（通過）、輪數用盡（帶著違規回報，不靜默放行）、
稽核 agent 連續沒有改善（避免無效燒 token）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field

from substack_radar.fact_reconcile import ScaleIssue, reconcile

AGY_BIN = os.path.expanduser(os.getenv("AGY_BIN", "~/.local/bin/agy"))
# 寫手是 agy 上最新一代的 Gemini（src/agy_models.py 自動判斷）。稽核刻意換家族＋最高推理強度：
# 同一個模型讀自己的輸出，會重現同一組盲點。
AUDIT_MODEL = os.getenv("SUBSTACK_AUDIT_MODEL", "Claude Opus 4.6 (Thinking)")
# 寫手鏈的鏈尾也是 Claude Opus 4.6。寫手若一路 fallback 到它，稽核再用同一個
# 模型，「不同模型互稽」的保證就沒了——同一個模型讀自己的輸出會重現同一組盲點。
# 這時改用 Gemini 3.1 Pro (High)：換家族、推理強度仍是該家族最高的一檔。
AUDIT_FALLBACK_MODEL = os.getenv("SUBSTACK_AUDIT_FALLBACK_MODEL", "Gemini 3.1 Pro (High)")
# 稽核也要有鏈。2026-08-19 台積電那篇就是這樣掉的：agy 的 Claude 供應端整批
# 掛掉（Opus 與 Sonnet 都回 "Agent execution terminated due to error"，跟 .env
# 裡 8/10 記錄的同一個故障），稽核直接放棄，稿子未經修訂就出去。寫手早就有
# AGY_MODEL_CHAIN 當後備，稽核卻只有一個模型——這是漏掉的一半。
AUDIT_MODEL_CHAIN = [m.strip() for m in os.getenv(
    "SUBSTACK_AUDIT_MODEL_CHAIN",
    "Claude Opus 4.6 (Thinking),Gemini 3.1 Pro (High),Gemini 3.7 Flash (High)",
).split(",") if m.strip()]


def _family(model: str) -> str:
    """模型屬於哪一家。寫手的標示長得像「gateway · 模型 writer→glm-5.3-flash」，所以用包含比對。"""
    m = (model or "").lower()
    for key in ("claude", "gemini", "gpt", "glm", "deepseek", "mistral", "step-", "qwen", "llama", "kimi"):
        if key in m:
            return key
    return m


def _same_family(a: str, b: str) -> bool:
    return _family(a) == _family(b)


def auditor_for(writer_model: str | None, *, preferred: str = AUDIT_MODEL) -> str:
    """挑一個跟寫手不同家族的稽核模型。"""
    if writer_model and _same_family(writer_model, preferred):
        return AUDIT_FALLBACK_MODEL
    return preferred
AUDIT_TIMEOUT_S = int(os.getenv("SUBSTACK_AUDIT_TIMEOUT_S", "600"))
MAX_ROUNDS = int(os.getenv("SUBSTACK_QUALITY_ROUNDS", "3"))

# 分析師共識 ≠ 公司財測。寫成這些字就是把證據張冠李戴。
_MISATTRIBUTION = ("管理層指引", "管理層的指引", "管理層財測", "管理層誠信",
                   "管理層預估", "公司財測落空", "指引落空", "指引失準")



# 中文數字：財經內容寫「三千八百八十五元」「一千零三十五億」比阿拉伯數字難讀
# 一個量級。2026-08-18 三篇實測，同一個管線的分佈是 0／7／81 處——不是規則
# 問題，是模型每次心情不同，所以要有閘門而不是只靠 prompt。
# 只抓「數字＋度量單位」，放過「第一」「一次」「三大」「兩者」這類正常中文。
_CN_DIGITS = "零一二三四五六七八九十百千萬"
_CN_QUANTITY = re.compile(
    rf"[{_CN_DIGITS}]{{2,}}(?:點[{_CN_DIGITS}]+)?\s*(?:億|兆|萬|元|倍|%|％|個百分點|奈米|美元)"
)
# 「八成六毛利」「兩成」也是中文數字，但前面只有一位數字，上面那條抓不到。
# 「一成不變」是成語不是比例，排除。
_CN_PERCENT = re.compile(r"(?!一成不變)[一二三四五六七八九兩]成(?:[一二三四五六七八九])?")
_CN_NUMERAL_LIMIT = int(os.getenv("SUBSTACK_CN_NUMERAL_LIMIT", "3"))


def chinese_numerals(article_md: str) -> list[str]:
    text = article_md or ""
    return _CN_QUANTITY.findall(text) + _CN_PERCENT.findall(text)


@dataclass
class Violation:
    kind: str
    detail: str
    fix_hint: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.detail}\n      → {self.fix_hint}"


@dataclass
class LoopReport:
    rounds: int = 0
    passed: bool = False
    history: list[list[Violation]] = field(default_factory=list)
    patches: int = 0                                   # 小修了幾句（只換那幾句，其他沒動）
    auditors: list[str] = field(default_factory=list)  # 實際有回應的查核模型
    unresolved: list[str] = field(default_factory=list)  # 驗證過、但用完次數還沒修掉的矛盾

    @property
    def remaining(self) -> list[Violation]:
        return self.history[-1] if self.history else []


def evaluate(article_md: str, *, fact_values: dict[str, float] | None = None,
             source_urls: list[str] | None = None,
             has_management_guidance: bool = False,
             sources: list[dict] | None = None,
             fact_block: str = "",
             base_currency: str = "TWD") -> list[Violation]:
    """只回報程式能確定的違規。判斷題留給稽核 agent。"""
    out: list[Violation] = []

    # 證據法則：歸屬給外部來源的數字要指得出處（見 evidence_gate）。
    from substack_radar.evidence_gate import check as _evidence_check
    from substack_radar.evidence_gate import stale_claims as _stale_claims

    for issue in _evidence_check(article_md, sources=sources or [], fact_block=fact_block,
                                 fact_values=fact_values or {}):
        out.append(Violation(kind=issue.rule, detail=issue.detail,
                             fix_hint=f"原句：{issue.sentence.strip()[:80]}"))
    # E4：來源活著、也切題，但過期。目標價／評等／股價／市值有保鮮期。
    for issue in _stale_claims(article_md, sources or []):
        out.append(Violation(kind=issue.rule, detail=issue.detail,
                             fix_hint=f"原句：{issue.sentence.strip()[:80]}"))

    from substack_radar.evidence_gate import locate as _locate

    for issue in reconcile(article_md, fact_values or {}, base_currency):
        # 數字本來就出自外部來源（例如同業合計營收），只是剛好約等於某個事實的 10 倍，不是寫錯量級
        # （2026-11-15 Pfizer：「同業合計營收 6,210.87 億」被配到輝瑞自己的營收，報成 10 倍誤植）。
        if sources and _locate(f"{issue.written:g}", issue.unit, sources, "", topic=issue.context):
            continue
        out.append(Violation(
            kind="數字量級",
            detail=str(issue),
            fix_hint=f"把它改成 {issue.expected / 1e8:,.4g} 億（事實表：{issue.fact_label}）；"
                     "並檢查同段落其他數字與比率是否一致。",
        ))

    if not has_management_guidance:
        hits = sorted({m for m in _MISATTRIBUTION if m in article_md})
        if hits:
            out.append(Violation(
                kind="證據張冠李戴",
                detail=f"文中出現 {'、'.join(hits)}，但事實表只有分析師共識 EPS，沒有公司自己的財測。",
                fix_hint="改寫成「低於分析師共識」「賣方預估與實際脫節」；"
                         "不得推論管理層誠信或指引可信度，也不要拿它當證偽條件。",
            ))

    # AEO：讓文章被 AI 引用得到（依據 2026-08-19 萃取的兩支影片，見 aeo_gate）
    from substack_radar.aeo_gate import check as _aeo_check

    for issue in _aeo_check(article_md):
        out.append(Violation(kind=issue.rule, detail=issue.detail,
                             fix_hint=f"例：{issue.sample[:70]}"))

    hits = chinese_numerals(article_md)
    if len(hits) > _CN_NUMERAL_LIMIT:
        sample = "、".join(dict.fromkeys(hits))[:80]
        out.append(Violation(
            kind="中文數字",
            detail=f"有 {len(hits)} 處把數量寫成中文數字（例：{sample}）。",
            fix_hint="全部改成阿拉伯數字（三千八百八十五元 → 3,885 元；"
                     "六十四倍 → 64 倍；十五點八 → 15.8）。"
                     "序數與慣用語（第一、一次、三大、兩者）保持中文。",
        ))

    for url in source_urls or []:
        if not re.match(r"^https?://", url or ""):
            out.append(Violation(kind="來源格式", detail=f"不是可點擊的網址：{url!r}",
                                 fix_hint="移除或換成完整 http(s) 網址。"))
    return out


def _audit_prompt(article_md: str, violations: list[Violation], fact_block: str) -> str:
    numbered = "\n".join(f"{i}. {v}" for i, v in enumerate(violations, 1))
    return (
        "你是一份中文財經電子報的事實稽核與修訂編輯。下面有一篇已完成的草稿、"
        "管線抓到的原始事實表，以及程式對帳後**已確認**的違規清單。\n\n"
        "你的工作：**只修正清單上的問題，以及修正它們所連帶影響的敘述**。\n"
        "不要重寫風格、不要換標題、不要增刪章節、不要添加新的數據或來源。\n\n"
        f"=== 已確認的違規（每一條都必須處理）===\n{numbered}\n\n"
        f"=== 管線抓到的原始事實表（唯一可信的數字來源）===\n{fact_block[:6000]}\n\n"
        f"=== 草稿全文 ===\n{article_md}\n\n"
        "=== 輸出格式（最高優先）===\n"
        "只輸出修訂後的完整 Markdown 全文，用下面兩行包起來，中間不要有任何說明：\n"
        "<<<ARTICLE>>>\n（修訂後全文）\n<<<END>>>\n"
        "若某一條違規你判斷程式誤報、不該改，仍要輸出全文，並在 <<<END>>> 之後"
        "用一行「NOTE: 第 N 條不改，理由」說明。"
    )


# 同一個行程裡，agy 一旦回過「帳號層級不可用」（額度用完／掛住），後面每一次呼叫
# 都還要再等它一輪——2026-09-20 的 FB 重寫每一輪都先在死掉的 agy 上耗掉 5 分鐘。
# 五個模型共用同一個訂閱，所以第一次就足以判定。
# 2026-10-15 修正：額度是一家一份（agy 的 Gemini 沒 credits 時 Claude Opus 照常可用），
# 所以按家族記，不再一家掛掉就整條 agy 放棄。
_AGY_DEAD_FAMILIES: set = set()


def agy_family_dead(model: str) -> bool:
    from src.llm_brain import _agy_family, _dead_families_snapshot

    return _agy_family(model) in (_AGY_DEAD_FAMILIES | _dead_families_snapshot())


def agy_is_dead() -> bool:
    # 跟寫稿那段（src.llm_brain）共用判斷：同一個行程裡，寫稿時已確認 agy 各家沒額度，
    # 稽核就不必再試一輪，也要讓 Haiku 頂第一位（2026-10-17 兩份紀錄分開，稽核改用了開源模型）。
    try:
        from src.llm_brain import _dead_families_snapshot
        known = _AGY_DEAD_FAMILIES | _dead_families_snapshot()
    except Exception:
        known = _AGY_DEAD_FAMILIES
    return {"claude", "gemini"} <= known


def note_agy_failure(error, model: str = "") -> None:
    from src.llm_brain import _agy_family, _agy_quota_or_hang

    if _agy_quota_or_hang(str(error)):
        if model:
            _AGY_DEAD_FAMILIES.add(_agy_family(model))
        else:
            _AGY_DEAD_FAMILIES.update({"claude", "gemini"})
        if {"claude", "gemini"} <= _AGY_DEAD_FAMILIES:
            # 跟寫稿那段共用跨行程記號，下一個排程直接跳過 agy
            from src import llm_brain
            llm_brain._AGY_DEAD_SNAPSHOT.update(_AGY_DEAD_FAMILIES)
            llm_brain._set_agy_exhausted()


def _run_agy_once_impl(prompt: str, model: str, timeout_s: int) -> str:
    """一次呼叫＝一個新的 subprocess（cwd=/tmp）＝獨立 session、乾淨 context。"""
    if not os.path.exists(AGY_BIN):
        raise FileNotFoundError(f"agy not found at {AGY_BIN}")
    proc = subprocess.run(
        [AGY_BIN, "-p", prompt, "--model", model, "--print-timeout", f"{timeout_s}s"],
        capture_output=True, text=True, cwd="/tmp", timeout=timeout_s + 60,
    )
    out = proc.stdout or ""
    err = proc.stderr or ""
    # agy 的供應端故障會走 returncode=0 但 stdout 寫錯誤訊息這條路，所以兩邊都看。
    if proc.returncode != 0 or "Agent execution terminated" in out or "Agent execution terminated" in err:
        raise RuntimeError(f"agy exit={proc.returncode}: {(err or out)[:200]}")
    return out


# --- 寫稿紀錄：每次叫模型都記一筆（logs/writing/llm_calls.csv，2026-10-18） -----------
_LAST_USAGE: dict = {}


def _logged(provider: str, impl, model_label: str, *args):
    from src import run_log
    _LAST_USAGE.clear()
    t = run_log.Timer()
    try:
        out = impl(*args)
    except Exception as exc:
        run_log.log_call(provider=provider, model=_LAST_USAGE.get("model") or model_label, ok=False,
                         seconds=t.seconds, note=str(exc))
        raise
    run_log.log_call(provider=provider, model=_LAST_USAGE.get("model") or model_label, ok=True, seconds=t.seconds,
                     input_tokens=_LAST_USAGE.get("input"), output_tokens=_LAST_USAGE.get("output"))
    return out


def _run_agy_once(prompt: str, model: str, timeout_s: int) -> str:
    return _logged("antigravity_cli", _run_agy_once_impl, model, prompt, model, timeout_s)


def run_claude_cli(prompt: str, timeout_s: int) -> str:
    return _logged("claude_cli", _run_claude_cli_impl, "haiku", prompt, timeout_s)


def _oss_chat(model: str, prompt: str, timeout_s: int) -> str:
    """閘道回空白時改指名其他模型重試（LiteLLM 把「200＋空白」當成功，不會備援；2026-11-03）。"""
    try:
        return _logged("gateway", _oss_chat_impl, model, model, prompt, timeout_s)
    except RuntimeError as exc:
        if "回空字串" not in str(exc):
            raise
        last = exc
    for fb in [m.strip() for m in os.getenv("GATEWAY_EMPTY_FALLBACKS",
                                            "deepseek-v4.1-flash,step-5-preview,gemini-2.5-flash").split(",") if m.strip()]:
        if fb == model:
            continue
        print(f"[QualityLoop] ↻ 閘道 {model} 回空白，改指名 {fb} 再試")
        try:
            return _logged("gateway", _oss_chat_impl, fb, fb, prompt, timeout_s)
        except RuntimeError as exc:
            last = exc
    raise last


_oss_chat_with_fallback = _oss_chat   # 測試用的固定別名（conftest 會把 _oss_chat 換成擋網路的假函式）


def _run_claude_cli_impl(prompt: str, timeout_s: int) -> str:
    """agy 全掛時的稽核後備（信哥 2026-09-20 指定）。

    走的是付費 API，所以只在 agy 整條鏈都不可用時才會到這裡——2026-09-20 週報
    那輪就是 agy 全模型 429，稽核直接沒跑，稿子等於沒有第二個模型看過。"""
    binary = shutil.which(os.getenv("CLAUDE_CLI_BIN", "claude"))
    if not binary:
        raise FileNotFoundError("claude 不在 PATH")
    # 信哥的排程模型順序最後一棒是 Haiku（CLAUDE_MODEL=haiku），不是付費的 Opus。
    args = [binary, "-p", "--output-format", "json",
            "--model", os.getenv("CLAUDE_MODEL") or "haiku",
            "--tools", "", "--strict-mcp-config", "--setting-sources", "",
            "--system-prompt", "照使用者指定的格式輸出，不要多餘的說明。",
            "--no-session-persistence"]
    effort = os.getenv("CLAUDE_EFFORT", "")
    if effort:
        args += ["--effort", effort]
    from src.llm_brain import claude_cli_env   # Haiku 關掉思考：便宜 5 倍、快 4 倍（2026-10-17）
    proc = subprocess.run(args, input=prompt, capture_output=True, text=True,
                          cwd="/tmp", timeout=timeout_s + 60, env=claude_cli_env())
    try:
        data = json.loads(proc.stdout or "{}")
    except Exception as exc:
        raise RuntimeError(f"claude 輸出不是 JSON：{str(proc.stdout)[:120]}") from exc
    usage = data.get("usage") or {}
    _LAST_USAGE.update(input=usage.get("input_tokens"), output=usage.get("output_tokens"),
                       model=next(iter(data.get("modelUsage") or {}), "haiku"))
    if data.get("is_error") or not data.get("result"):
        raise RuntimeError(f"claude 失敗：{str(data.get('result'))[:160]}")
    return str(data["result"])


_SERVED = threading.local()     # 閘道會自己換模型備援；對帳要知道「實際是誰答的」才能判斷有沒有自己審自己


def _oss_chat_impl(model: str, prompt: str, timeout_s: int) -> str:
    """本機 LiteLLM 閘道（設定由 src.llm_brain 載入）。"""
    import httpx

    import src.llm_brain  # noqa: F401  載入閘道環境變數

    base = os.getenv("OSS_GATEWAY_BASE_URL", "http://127.0.0.1:4000/v1")
    key = os.getenv("OSS_GATEWAY_KEY", "")
    if not key:
        raise RuntimeError("沒有開源模型閘道的金鑰")
    # 能力優先、不管速度（2026-10-17）：閘道內部會一路備援，用戶端至少等 1 小時；
    # 用串流，NIM 才不會在 300 秒時回 504。
    timeout_s = max(int(timeout_s), int(os.getenv("GATEWAY_TIMEOUT_S", "3600")))
    from src.gateway_stream import collect_sse

    with httpx.stream("POST", f"{base}/chat/completions", timeout=httpx.Timeout(timeout_s, connect=30),
                      headers={"Authorization": f"Bearer {key}"},
                      # 修訂要回傳整篇文章；不指定上限時閘道預設很短，全文會被截斷（2026-10-17）。
                      json={"model": model, "temperature": 0.2,
                            "max_tokens": int(os.getenv("GATEWAY_MAX_TOKENS", "32000")),
                            "stream": True, "stream_options": {"include_usage": True},
                            "messages": [{"role": "user", "content": prompt}]}) as resp:
        if resp.status_code != 200:
            raise RuntimeError(f"閘道 {model} HTTP {resp.status_code}: {resp.read()[:120]!r}")
        text, _usage = collect_sse(resp.iter_lines())
        _LAST_USAGE.update(input=(_usage or {}).get("prompt_tokens"), output=(_usage or {}).get("completion_tokens"))
    if not text.strip():
        raise RuntimeError(f"閘道 {model} 回空字串")
    served = (resp.headers.get("x-litellm-model-id") or "").split(":")[-1]
    _LAST_USAGE["model"] = f"{model}→{served}" if served and served != model else model
    _SERVED.model = served or model          # 這條執行緒剛剛那次呼叫，實際是誰答的
    if served and served != model:
        print(f"[QualityLoop] ℹ️ 閘道 {model} 實際由 {served} 回答")
    return text


# 閘道角色 audit：刻意排成跟 writer 不同家先上（Step-5 → GLM-5.3-Flash → DeepSeek …），
# 避免自己審自己。實際模型清單在 ~/litellm-gateway/models.json。
OSS_FALLBACK_MODELS = ("audit",)


def run_fallback_text(prompt: str, timeout_s: int) -> tuple[str, str]:
    """agy 不能用之後的順序（信哥 2026-10-08 定、10-17 確認）：② LiteLLM 閘道 → ③ Haiku。
    LLM_HAIKU_STANDIN=1 時恢復 10-09 的規則：agy 全掛就讓 Haiku 排第一。"""
    oss = [(f"閘道 {m}", lambda m=m: _oss_chat(m, prompt, timeout_s)) for m in OSS_FALLBACK_MODELS]
    haiku = [("Claude Haiku", lambda: run_claude_cli(prompt, timeout_s))]
    standin = os.getenv("LLM_HAIKU_STANDIN", "0") == "1"
    order = haiku + oss if (standin and agy_is_dead()) else oss + haiku
    last = None
    for label, call in order:
        try:
            return call(), label
        except Exception as exc:
            last = exc
            print(f"[QualityLoop] ⚠️ {label} 不可用：{str(exc)[:110]}")
    raise RuntimeError(f"後備模型全部不可用；最後一個錯誤：{last}")


def _run_agy(prompt: str, model: str, timeout_s: int) -> str:
    """依鏈逐個試。第一個成功就回；agy 全掛才退到 claude CLI（付費）。"""
    chain = [m for m in [model] + [m for m in AUDIT_MODEL_CHAIN if m != model] if not agy_family_dead(m)]
    try:
        # agy 會下架舊模型（2026-10-17：Gemini 3.1 Pro、3.7 Flash 回 invalid model selection）。
        # 清單查得到時只試還在架上的；查不到就照原清單試。
        from src.agy_models import available_models, newest_gemini
        listed = set(available_models())
        if listed:
            chain = [m for m in chain if m in listed]
            # 舊的 Gemini 下架後，稽核鏈只剩 Claude Opus 一個（2026-10-17）。補上架上最新的 Gemini 當第二順位，
            # 排在 Claude 後面：稽核要跟寫手（多半是 Gemini）不同家先上。
            latest = newest_gemini(sorted(listed))
            if latest and latest not in chain and not agy_family_dead(latest):
                chain.append(latest)
    except Exception:
        pass
    last = "agy 本輪已判定不可用" if not chain else None
    for index, candidate in enumerate(chain):
        if agy_family_dead(candidate):
            continue
        try:
            result = _run_agy_once(prompt, candidate, timeout_s)
            if index:
                print(f"[QualityLoop] ℹ️ 稽核改用後備模型 {candidate}（前 {index} 個不可用）")
            return result
        except Exception as exc:
            last = exc
            note_agy_failure(exc, candidate)
            print(f"[QualityLoop] ⚠️ {candidate} 不可用：{str(exc)[:110]}")
            if agy_is_dead():
                print("[QualityLoop] ⛔ agy 各家額度都不可用，本輪不再試 agy")
                break
    try:
        result, label = run_fallback_text(prompt, timeout_s)
        print(f"[QualityLoop] ℹ️ agy 不可用，改由 {label} 稽核")
        return result
    except Exception as exc:
        raise RuntimeError(f"稽核鏈全部不可用（{str(exc)[:100]}）；最後一個 agy 錯誤：{last}")


def _extract_article(raw: str) -> str | None:
    m = re.search(r"<<<ARTICLE>>>\s*(.+?)\s*<<<END>>>", raw or "", re.S)
    if not m:
        return None
    body = m.group(1).strip()
    return body if len(body) > 400 else None


def run_quality_loop(
    article_md: str,
    *,
    fact_values: dict[str, float] | None = None,
    fact_block: str = "",
    source_urls: list[str] | None = None,
    has_management_guidance: bool = False,
    sources: list[dict] | None = None,
    base_currency: str = "TWD",
    max_rounds: int = MAX_ROUNDS,
    model: str = AUDIT_MODEL,
) -> tuple[str, LoopReport]:
    """跑到零違規、輪數用盡、或稽核 agent 不再改善為止。

    **永遠回傳目前最好的版本**——修不完也不會擋掉整篇稿子，但 report.passed
    會是 False，由呼叫端決定要不要照樣送出。靜默放行才是真正的問題。
    """
    report = LoopReport()
    current = article_md
    for round_index in range(max_rounds):
        violations = evaluate(current, fact_values=fact_values, source_urls=source_urls,
                              has_management_guidance=has_management_guidance,
                              sources=sources, fact_block=fact_block,
                              base_currency=base_currency)
        report.history.append(violations)
        report.rounds = round_index + 1
        if not violations:
            report.passed = True
            print(f"[QualityLoop] ✅ 第 {report.rounds} 輪零違規，通過")
            return current, report
        print(f"[QualityLoop] 第 {report.rounds}/{max_rounds} 輪：{len(violations)} 項違規，"
              f"交給 {model} 修訂")
        for v in violations:
            print(f"[QualityLoop]   {v}")
        try:
            raw = _run_agy(_audit_prompt(current, violations, fact_block), model, AUDIT_TIMEOUT_S)
        except Exception as exc:
            print(f"[QualityLoop] ⚠️ 稽核 session 失敗（{type(exc).__name__}: {exc}）；"
                  "保留目前版本並帶著違規回報")
            return current, report
        patched = _extract_article(raw)
        if not patched:
            print("[QualityLoop] ⚠️ 稽核 session 沒有回傳可用的全文；停止迴圈")
            return current, report
        if patched.strip() == current.strip():
            print("[QualityLoop] ⚠️ 稽核 session 沒有做出任何修改；停止迴圈避免空轉")
            return current, report
        current = patched

    final = evaluate(current, fact_values=fact_values, source_urls=source_urls,
                     has_management_guidance=has_management_guidance,
                     sources=sources, fact_block=fact_block,
                     base_currency=base_currency)
    report.history.append(final)
    report.passed = not final
    if final:
        print(f"[QualityLoop] ❌ 用完 {max_rounds} 輪仍有 {len(final)} 項違規（不靜默放行）")
    return current, report


# --- 第二段：開放式領域稽核 ------------------------------------------------
# 確定性閘門只抓得到「能算的」。2026-08-18 的瑞昱稿還有一批錯是算不出來的：
#   * 「營業費用吞噬掉將近四成毛利」——實際吃掉 77% 毛利（四成是對營收）
#   * 製程寫「0.18 微米→0.13 微米→90 奈米」，2026 年的 IC 設計龍頭早就不是
#   * 把 SoIC（台積電 3D 堆疊）說成「系統單晶片」（那是 SoC）
#   * 把「戴爾電腦」當市調機構
# 這些要靠領域知識，正是第二個模型該做的事。刻意放在確定性閘門之後：
# 地基乾淨了，它才不會把注意力浪費在數字與標籤上。

_OPEN_AUDIT_PROMPT = """你是一份中文財經電子報的事實查核編輯，專長是半導體與財報分析。
下面是一篇已經通過數字對帳的草稿，以及管線抓到的原始事實表。

請只找**事實錯誤**，不要評論風格、結構或觀點。特別注意這幾類：
1. 比率算錯分母（例如「費用吃掉四成毛利」但實際是對營收的四成）
2. 產業常識過時或錯誤（製程節點、技術世代、規格名稱）
3. 專有名詞用錯（例如把 SoIC 當成 SoC）
4. 把不是研究機構的公司當成資料來源
5. 前後文自相矛盾的數字或比率

=== 原始事實表 ===
{facts}

=== 草稿全文 ===
{article}

=== 輸出格式（最高優先）===
若找到問題，輸出修訂後的完整 Markdown 全文，包在
<<<ARTICLE>>> 與 <<<END>>> 之間，只改錯的地方，其餘一字不動。
把握不足的地方寧可刪掉整句，也不要換成另一個你不確定的說法。
若通篇沒有事實錯誤，只輸出一行：CLEAN
"""


def _open_audit_once(article_md: str, fact_block: str, model: str,
                     timeout_s: int) -> tuple[str, bool]:
    prompt = _OPEN_AUDIT_PROMPT.format(facts=fact_block[:6000], article=article_md)
    try:
        raw = _run_agy(prompt, model, timeout_s)
    except Exception as exc:
        raise _AuditUnavailable(str(exc)) from exc
    if "CLEAN" in (raw or "")[:200] and "<<<ARTICLE>>>" not in raw:
        return article_md, False
    patched = _extract_article(raw)
    if not patched or patched.strip() == article_md.strip():
        return article_md, False
    return patched, True


class _AuditUnavailable(RuntimeError):
    """稽核沒跑成，跟「跑了但沒發現問題」是兩件事，不可以印成同一句。"""


def open_domain_audit(article_md: str, fact_block: str, *,
                      model: str = AUDIT_MODEL, timeout_s: int = AUDIT_TIMEOUT_S,
                      max_rounds: int = MAX_ROUNDS) -> tuple[str, bool]:
    """一直跑到回 CLEAN 或不再有修改為止。回傳 (文章, 是否曾修改)。

    為什麼要 loop：2026-08-18 實測，第一輪抓到了「四成毛利→八成」「戴爾電腦
    當市調機構」「SoIC→SoC」，卻漏掉同一段裡的「0.18 微米→0.13 微米→90 奈米」
    ——那在 2026 年錯了十幾年。開放式查核一次掃不乾淨是常態：模型改完前面幾處
    就收手了。改完再讀一遍，注意力會落到不同地方。

    失敗一律回目前版本，不擋稿。
    """
    current = article_md
    changed_any = False
    for round_index in range(max_rounds):
        try:
            current, changed = _open_audit_once(current, fact_block, model, timeout_s)
        except _AuditUnavailable as exc:
            # 以前這裡吞掉例外、回 (原文, False)，外層就印出「✅ 未發現事實錯誤」
            # ——稽核根本沒跑，卻報告成通過。2026-08-19 台積電那篇就是這樣出去的。
            print(f"[QualityLoop] ❌ 領域稽核無法執行（{exc}）；"
                  "這篇**沒有經過領域查核**，不是「查過沒問題」")
            return current, changed_any
        if not changed:
            if round_index == 0:
                print("[QualityLoop] ✅ 領域稽核：跑過了，未發現事實錯誤")
            else:
                print(f"[QualityLoop] ✅ 領域稽核第 {round_index + 1} 輪無新問題，收斂")
            return current, changed_any
        changed_any = True
        print(f"[QualityLoop] ✏️ 領域稽核第 {round_index + 1}/{max_rounds} 輪提出修訂並已套用")
    print(f"[QualityLoop] ⚠️ 領域稽核跑滿 {max_rounds} 輪仍在改，不再繼續（保留最後版本）")
    return current, changed_any


# --- 審稿 → 退稿 → 寫手重寫（信哥 2026-10-18） ----------------------------------
# 舊流程是稽核模型「自己動手改」，寫手從來沒拿到「哪裡錯、怎麼改」；專業查證改完之後
# 只複驗不修，嬌生（下）就是這樣帶著 1 條違規被擋下、當天改發 podcast。
# 新流程：每一輪先跑確定性閘門，再請稽核模型逐條寫出「問題／原句／建議改法」，
# 退回給寫手照著改。最多退稿 REVIEW_ATTEMPTS 次；還有閘門違規才算失敗，
# 由 07:30 的發文程式依規則換下一個專欄。

REVIEW_ATTEMPTS = int(os.getenv("SUBSTACK_REVIEW_ATTEMPTS", "3"))

_ANCHOR_STOP = {"https", "http", "html", "that", "this", "with", "from", "have", "will", "what",
                "they", "their", "about", "said", "says", "news", "podcast", "episode"}


def _anchors(article: str) -> tuple[set[str], set[str], set[str]]:
    """草稿裡拿來回素材找出處的錨點：數字、英文專名、常出現的中文詞。"""
    flat = re.sub(r"\]\([^)]*\)", "]", article or "")
    flat = re.sub(r"(?<=\d)[,，](?=\d{3})", "", flat)
    nums = {n for n in re.findall(r"\d+(?:\.\d+)?", flat) if len(n) >= 2}
    latin = ({w.lower() for w in re.findall(r"[A-Za-z][A-Za-z\-']{3,}", flat)}
             | {w.lower() for w in re.findall(r"\b[A-Z]{3}\b", flat)}) - _ANCHOR_STOP
    grams: dict[str, int] = {}
    for run in re.findall(r"[\u4e00-\u9fff]{3,}", flat):
        for i in range(len(run) - 2):
            grams[run[i:i + 3]] = grams.get(run[i:i + 3], 0) + 1
    cjk = {g for g, c in grams.items() if c >= 2}
    return nums, latin, cjk


def _est_tokens(text: str) -> int:
    """粗估 token：中文一個字約一個 token，英文約 3.6 個字元一個。"""
    cjk = len(re.findall(r"[\u3000-\u9fff\uff00-\uffef]", text or ""))
    return int(cjk * 1.1 + (len(text or "") - cjk) / 3.6)


EVIDENCE_TOKENS = int(os.getenv("SUBSTACK_EVIDENCE_TOKENS", "40000"))


def select_evidence(fact_block: str, article: str, budget: int = EVIDENCE_TOKENS, *,
                    chunk: int = 1200, head: int = 6000) -> str:
    """給審稿與重寫的素材（2026-11-16 重做）。

    原本一律只給前 40,000 個「字元」。英文逐字稿 40,000 字元只有約一萬 token：
    〈黃仁勳辭職〉那集 114,144 字，審稿只看得到前三分之一，就把節目 55,000 字處播的 Hassabis 片段、
    61,900 字處的討論判成「素材裡沒有」，逼寫手把對的內容改弱。
    現在上限改用 token 算（預設四萬），英文素材可以給到約 14 萬字元，這種長度整份給。
    還是超過時：開頭照留，其餘先確保草稿裡每個少見的數字與英文專名都找得到出處段落，
    再用剩下的額度補相關段落；照原順序排，略過的地方標出來。"""
    text = fact_block or ""
    if _est_tokens(text) <= budget or not (article or "").strip():
        while _est_tokens(text) > budget:              # 沒有草稿可比對時才會走到：只好從頭截
            text = text[:int(len(text) * 0.9)]
        return text
    nums, latin, cjk = _anchors(article)
    pieces = [(i, text[i:i + chunk]) for i in range(0, len(text), chunk)]
    lows = {i: re.sub(r"(?<=\d),(?=\d{3})", "", c).lower() for i, c in pieces}

    def hits(anchor: str, is_num: bool) -> list[int]:
        if is_num:
            pat = re.compile(rf"(?<![\d.]){re.escape(anchor)}(?![\d])")
            return [i for i, low in lows.items() if pat.search(low)]
        return [i for i, low in lows.items() if anchor in low]

    where = {(a, True): hits(a, True) for a in nums}
    where.update({(a, False): hits(a, False) for a in latin | cjk})
    where = {k: v for k, v in where.items() if v}
    keep = {i for i, _ in pieces if i < head}
    left = budget - sum(_est_tokens(c) for i, c in pieces if i in keep)
    cost = {i: _est_tokens(c) for i, c in pieces}

    def take(i: int) -> bool:
        nonlocal left
        if i in keep:
            return True
        if cost[i] > left:
            return False
        keep.add(i)
        left -= cost[i]
        return True

    # ① 越少見的錨點越先顧：每個至少帶進它出現的頭兩段（Hassabis、Polymarket 這種只出現一兩次的）
    for (anchor, is_num), idx in sorted(where.items(), key=lambda kv: (len(kv[1]), kv[0][0])):
        if len(idx) > max(3, len(pieces) // 8):
            break                                      # 剩下的都是到處出現的詞，交給 ②
        for i in idx[:2]:
            take(i)
    # ② 剩下的額度：錨點越少見權重越高
    import math
    weight = {k: math.log(1 + len(pieces) / len(v)) * (2 if k[1] else 1) * (0.3 if k[0] in cjk else 1)
              for k, v in where.items()}
    score: dict[int, float] = {}
    for k, idx in where.items():
        for i in idx:
            score[i] = score.get(i, 0.0) + weight[k]
    for i in sorted(score, key=lambda j: (-score[j], j)):
        if left <= 0:
            break
        take(i)

    out, last_end = [], 0
    for i, c in pieces:
        if i not in keep:
            continue
        if i > last_end:
            out.append(f"\n……（中間略過約 {i - last_end:,} 字）……\n")
        out.append(c)
        last_end = i + len(c)
    if last_end < len(text):
        out.append(f"\n……（後面略過約 {len(text) - last_end:,} 字）……")
    kept = sum(len(c) for i, c in pieces if i in keep)
    return (f"（素材全文 {len(text):,} 字，這裡只節錄跟草稿內容相關的 {kept:,} 字。"
            "節錄裡找不到，不代表素材裡沒有；只有節錄明確寫著不一樣的內容，才算草稿寫錯。）\n"
            + "".join(out))


def evidence_pack(fact_block: str, sources: list[dict] | None, *, limit: int = EVIDENCE_TOKENS,
                  article: str = "") -> str:
    """給稽核與寫手的完整取材（2026-11-06）：原本只給事實表前 6,000 字，一集 6 萬字的逐字稿
    後段、寫手查到的第一手資料全看不到，稽核就把真的數字判成「不在事實表」叫寫手刪。
    2026-11-16：超過上限時改挑跟草稿相關的段落（見 select_evidence）。"""
    parts = [f"【原始素材】\n{select_evidence(fact_block, article, limit)}"]
    for i, src in enumerate(sources or [], 1):
        if i > 10:
            break
        head = " ｜ ".join(x for x in (str(src.get("publisher") or ""), str(src.get("title") or ""),
                                       str(src.get("url") or "")) if x)
        parts.append(f"【研究來源 #{i}】{head}\n{str(src.get('excerpt') or '')[:1500]}")
    return "\n\n".join(parts)


_CRITIQUE_PROMPT = """你是審稿編輯，這一輪只負責「指出問題、給改法」，不要自己改稿。

給你：作者的草稿、管線抓到的原始事實表、程式對帳後確認的違規清單。請做兩件事：
1. 程式抓到的每一條違規，都給一個作者照做就能過關的具體改法：指出要改哪一句、改成什麼方向。
   若你判斷是程式誤報，「建議改法」寫「誤報：理由」，並告訴作者怎麼換個寫法讓程式不再誤判。
2. 另外找出程式抓不到的事實錯誤：比率分母算錯、產業常識過時、專有名詞用錯、
   把一般公司當成資料來源、前後文數字矛盾、把一次性項目當成本業趨勢。風格與觀點不要評論。
   這一類最多列 6 條，只列會讓讀者被誤導的；定義可以更精確、措辭可以更嚴謹這種不列。

「建議改法」是寫給作者看的方向，不是要貼進文章的句子：
- 讀者看不到你手上的取材。不要要求作者在文章裡交代查證過程（「提供的素材沒有」「逐字稿寫成…」
  「無法核對」「此處不採用」）。查不到的，請他刪掉或改成不帶細節的說法；寫錯的，請他直接改成對的，
  不必向讀者解釋原本哪裡錯。
- 節目裡的人說的話，寫「某某說」就是合格的出處，不必每句都冠上節目名。

=== 程式確認的違規 ===
{violations}

出處的判斷方式（2026-11-06 信哥：查第一手資料是加分，要引導作者標出處，不是叫他刪）：
- 下面的「原始素材」和「研究來源」都是合法出處。作者自己查到的第一手資料（公司申報文件、財報、
  官方統計、原始論文）是加分，不要因為原始素材裡沒有就要求刪掉。
- 英文素材的數字換成中文單位算同一個數字（$80 billion＝800 億美元）。
- 只有兩邊都找不到的數字或說法才算無出處。這時「建議改法」要具體：如果其實在某個研究來源裡，
  告訴作者用 [來源名](網址) 標上；真的哪裡都沒有，才請作者刪掉或改成不帶數字的判斷。

=== 取材（原始素材＋研究來源）===
{facts}

=== 作者草稿 ===
{article}
{scope}
=== 輸出格式（最高優先）===
只輸出一個 JSON 物件：{{"items": [{{"問題": "…", "原句": "…", "建議改法": "…"}}]}}
完全沒有問題就輸出 {{"items": []}}。不要輸出 JSON 以外的文字。
"""

_REWRITE_PROMPT = """你是這篇文章的作者。審稿編輯把稿子退回來了，附上逐條意見。

請照意見修改你的稿子：
- 每一條意見都要處理；意見寫「誤報」的，照它建議的方式換個寫法。
- 只改有問題的句子與它連帶影響的敘述，其餘保留原文的語氣、節奏與小標；改完要像同一個人從頭寫到尾。
- 審稿意見是編輯寫給你的內部溝通，讀者看不到。不要把意見裡的句子抄進文章，文章裡也不要出現
  「素材」「逐字稿」「研究來源」「提供的來源」「無法核對」「不採用」「以…為準」這類查證過程的話。
  查不到的就刪；寫錯的直接改成對的，不用解釋改了什麼。需要讓讀者知道是誰的說法，
  寫「他估計」「公司說」就夠，不要補「還沒被證實」「查不到」「這是我的推論」這類話。
- 讓讀者知道每個數字從哪來：節目名在文章開頭交代一次就夠，之後直接寫「某某說」「主持人問」，
  不要每句都加「在〈節目名〉裡」；研究來源的內容用行內連結 [來源名](網址) 標上
  （網址照下面取材裡的抄，不要自己編）。兩邊都找不到的數字才刪掉。
- 不要動標題，不要加任何說明文字。
- 意見寫「篇幅不足」時，可以新增段落：回到下面的原始素材，把故事與脈絡說完整（起承轉合），
  不是加條列或重複結論；新增內容一樣不得出現素材以外的數字或來源。

=== 審稿意見（第 {attempt} 次退稿）===
{feedback}

=== 取材（原始素材＋研究來源）===
{facts}

=== 你的草稿 ===
{article}

=== 輸出格式（最高優先）===
只輸出修改後的完整 Markdown 全文，用下面兩行包起來：
<<<ARTICLE>>>
（修改後全文）
<<<END>>>
"""


def _parse_items(raw: str) -> list[dict] | None:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0), strict=False)
    except ValueError:
        return None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    return [i for i in items if isinstance(i, dict) and (i.get("問題") or i.get("建議改法"))]


_SCOPE_NOTE = """
=== 這一輪只審這些句子 ===
全文你上一輪已經審過。下面是作者退稿後新寫或改過的句子；第 2 項的事實錯誤只從這些句子裡找，
其他沒動過的地方不要再提新意見（那樣永遠改不完）。
{sentences}
"""
MAX_AUDIT_ITEMS = int(os.getenv("SUBSTACK_AUDIT_MAX_ITEMS", "6"))


def _sentences(article_md: str) -> list[str]:
    body = re.split(r"^-{3,}\s*$", article_md or "", maxsplit=1, flags=re.M)[0]
    return [x.strip() for x in re.split(r"(?<=[。！？?!])|\n+", body) if len(x.strip()) >= 8]


def _skey(sentence: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"\]\([^)]*\)", "]", sentence))


def critique(article_md: str, violations: list[Violation], fact_block: str, auditor: str,
             focus: list[str] | None = None) -> list[dict] | None:
    """稽核模型逐條列出問題與改法。稽核跑不成回 None（跟「跑了沒問題」的 [] 分開）。
    focus：只審這些句子（第二輪起用，見 run_review_loop）。"""
    listed = "\n".join(f"{i}. {v}" for i, v in enumerate(violations, 1)) or "（無）"
    scope = _SCOPE_NOTE.format(sentences="\n".join(f"- {x}" for x in focus[:60])) if focus else ""
    prompt = _CRITIQUE_PROMPT.format(violations=listed, facts=fact_block, article=article_md, scope=scope)
    from src import run_log
    try:
        with run_log.stage("審稿"):
            raw = _run_agy(prompt, auditor, AUDIT_TIMEOUT_S)
    except Exception as exc:
        print(f"[Review] ⚠️ 審稿模型無法執行（{str(exc)[:120]}）；這一輪只用程式檢查的結果退稿")
        return None
    items = _parse_items(raw)
    if items is None:
        print("[Review] ⚠️ 審稿意見不是合法 JSON；這一輪只用程式檢查的結果退稿")
        return None
    cap = len(violations) + MAX_AUDIT_ITEMS          # 每條程式違規一個改法，另外最多 6 條
    if len(items) > cap:
        print(f"[Review] ℹ️ 審稿列了 {len(items)} 條，只取前 {cap} 條（多了寫手改不完，下一輪又挑新的）")
        items = items[:cap]
    return items


def _writer_order() -> list[str]:
    """重寫照寫稿的同一個順序（.env 的 SUBSTACK_COMPOSER_BACKEND）。
    2026-11-16：第一稿是 Haiku 寫的，兩次退稿卻都交給閘道 glm 整篇重寫，
    定稿等於換了一個寫手，語氣整個變掉。"""
    known = {"claude_cli": "claude", "antigravity_cli": "agy", "gateway": "gateway"}
    order = [known[b.strip()] for b in os.getenv("SUBSTACK_COMPOSER_BACKEND", "").split(",")
             if b.strip() in known]
    for name in ("agy", "gateway", "claude"):      # 沒設定或漏掉的，照舊順序補在後面
        if name not in order:
            order.append(name)
    return order


def run_writer_text(prompt: str, timeout_s: int) -> tuple[str, str]:
    """寫手重寫：跟寫稿同一套順序，前一個不可用才換下一個。"""
    last: Exception | None = None
    for name in _writer_order():
        try:
            if name == "claude":
                return run_claude_cli(prompt, timeout_s), "Claude Haiku"
            if name == "gateway":
                text = _oss_chat("writer", prompt, timeout_s)
                return text, f"閘道 writer→{getattr(_SERVED, 'model', '') or '?'}"
            from src.llm_brain import _agy_model_chain
            for model in [m for m in _agy_model_chain() if not agy_family_dead(m)]:
                try:
                    return _run_agy_once(prompt, model, timeout_s), f"agy {model}"
                except Exception as exc:
                    note_agy_failure(exc, model)
                    last = exc
        except Exception as exc:
            last = exc
            print(f"[Review] ⚠️ 寫手 {name} 不可用：{str(exc)[:110]}")
    raise last or RuntimeError("沒有可用的寫手")


def _feedback(violations: list[Violation], items: list[dict] | None) -> str:
    lines = [f"- [程式檢查] {v.kind}：{v.detail}（{v.fix_hint}）" for v in violations]
    for it in items or []:
        lines.append(f"- [審稿] {it.get('問題', '')}｜原句：{str(it.get('原句', ''))[:120]}"
                     f"｜建議改法：{it.get('建議改法', '')}")
    return "\n".join(lines)


def length_feedback(article_md: str, word_range: tuple[int, int] | None) -> list[dict]:
    """字數不在範圍內 → 一條退稿意見（信哥 2026-10-19：寫太短只剩結論、沒有故事）。
    略超上限時標「結構=False」，走小修整句刪除；差距太大才標「結構=True」整篇重寫。"""
    if not word_range:
        return []
    from substack_radar.composer import _count_chinese_chars
    n, (lo, hi) = _count_chinese_chars(article_md), word_range
    if n < lo * 0.9:
        return [{"問題": f"篇幅不足：目前約 {n} 字，要寫到 {lo}–{hi} 字",
                 "原句": "（全文）",
                 "建議改法": "回到原始素材，把故事和脈絡說完整：來賓／主角是誰、在什麼處境、事情怎麼一步步發展、"
                             "他舉的例子與原話、哪裡出現轉折。用段落敘事補，不要加條列、不要重複結論。",
                 "結構": True}]
    if n > hi * 1.25:
        return [{"問題": f"篇幅過長：目前約 {n} 字，上限 {hi} 字", "原句": "（全文）",
                 "建議改法": "刪掉不影響主線的支線與重複的論證，保留故事主線。",
                 "結構": True}]
    if n > hi:
        return [{"問題": f"篇幅略長：目前約 {n} 字，上限 {hi} 字", "原句": "（全文）",
                 "建議改法": f"請整句刪掉約 {n - hi} 字（把要刪的原句「改成」留空字串）。"
                             "優先刪重複的論證和支線，數字、引句、出處連結不能刪。",
                 "結構": False}]
    return []


def _log_rejections(round_no: int, violations, audit_items, too_short, style_items) -> None:
    """退稿原因記帳（logs/writing/rejections.csv），給 substack_radar.lessons 用。失敗不影響寫稿。"""
    try:
        from src import run_log
        for v in violations:
            run_log.log_rejection(round_no=round_no, source="程式", kind=str(v.kind), quote=str(v.detail))
        for it in audit_items:
            run_log.log_rejection(round_no=round_no, source="審稿", kind=str(it.get("問題", "")),
                                  quote=str(it.get("原句", "")))
        for it in too_short:
            run_log.log_rejection(round_no=round_no, source="篇幅", kind=str(it.get("問題", "")))
        for it in style_items:
            run_log.log_rejection(round_no=round_no, source="文風", kind=str(it.get("問題", "")),
                                  quote=str(it.get("原句", "")))
    except Exception:
        pass


def run_review_loop(article_md: str, *, fact_block: str, auditor: str, eval_kwargs: dict,
                    max_attempts: int = REVIEW_ATTEMPTS,
                    word_range: tuple[int, int] | None = None,
                    writer: str = "", source_title: str = "") -> tuple[str, LoopReport, int]:
    """回傳 (最終稿, 報告, 整篇重寫次數)。報告的 passed／remaining 只看確定性閘門。

    預設走「對帳式」（2026-11-16）；SUBSTACK_REVIEW_MODE=legacy 改回舊的「請模型挑毛病」。"""
    if os.getenv("SUBSTACK_REVIEW_MODE", "ledger") == "legacy":
        return _run_review_loop_legacy(article_md, fact_block=fact_block, auditor=auditor,
                                       eval_kwargs=eval_kwargs, max_attempts=max_attempts, word_range=word_range)
    return _run_review_loop_ledger(article_md, fact_block=fact_block, eval_kwargs=eval_kwargs,
                                   max_attempts=max_attempts, word_range=word_range, writer=writer,
                                   source_title=source_title)


_PATCH_PROMPT = """你是這篇文章的作者。編輯標出下面幾個地方要修。請只修這幾句，文章其他地方一個字都不要動。

每一處回一個替換：
- 「原句」從草稿照抄要換掉的那一句（完整的一句，一字不差，程式要靠它找到位置）。
- 「改成」寫新的句子；整句該刪就留空字串。

規則：
- 照意見的方向改，語氣跟前後文一致，像同一個人寫的。
- 「跟素材不符」那類意見是另一個模型查的，也可能查錯（例如把語音辨識聽錯的名字當成正確寫法）。
  你確定原句是對的，就不要列那一條。
- 意見是編輯寫給你的內部溝通：不要把意見裡的話抄進文章，新句子不要出現「素材」「逐字稿」
  「研究來源」「無法核對」這類查證過程的話。
- 新句子不能出現原句和「素材原文」裡都沒有的數字。
- 說法有誤就直接改成對的，或整句刪掉；不要改成「影片說……但尚未核實」「這只是他的說法」這種記帳句。
- 出處對不上的：改成下面研究來源裡真的有的出處（寫成 [來源名](網址)），或改成不具名的敘述。

=== 要修的地方 ===
{issues}

=== 可以引用的研究來源 ===
{sources}

=== 草稿 ===
{article}

=== 輸出格式（最高優先）===
只輸出一個 JSON 物件，不要有其他文字：{{"patches": [{{"原句": "…", "改成": "…"}}]}}
"""


def _plain_numbers(text: str) -> set[str]:
    flat = re.sub(r"(?<=\d)[,，](?=\d{3})", "", text or "")
    return {n for n in re.findall(r"\d+(?:\.\d+)?", flat) if len(n) >= 2}


def _find_span(article: str, sentence: str) -> tuple[int, int] | None:
    sentence = (sentence or "").strip()
    if len(sentence) < 4:
        return None
    if article.count(sentence) == 1:
        i = article.find(sentence)
        return i, i + len(sentence)
    # 模型照抄時常把空白抄掉或多抄：逐字比對、空白隨意
    pat = r"\s*".join(re.escape(ch) for ch in re.sub(r"\s+", "", sentence))
    hits = list(re.finditer(pat, article))
    return hits[0].span() if len(hits) == 1 else None


def apply_patches(article: str, patches: list[dict], allowed_numbers: set[str]) -> tuple[str, int, list[str]]:
    """把「原句→改成」逐條換進文章。回 (新文章, 換了幾句, 沒換的原因)。

    只動被點名的句子，所以其他句子不用重審——這是小修能一輪收斂的原因。
    找不到原句、同一句出現多次、新句子帶進素材沒有的數字或查證用語，那一條就不換。"""
    from substack_radar.style_gate import _HEDGE, _PROCESS_LEAK

    out, applied, skipped = article, 0, []
    for p in patches or []:
        old, new = str(p.get("原句") or "").strip(), str(p.get("改成") or "").strip()
        span = _find_span(out, old)
        if span is None:
            skipped.append(f"找不到原句：{old[:30]}")
            continue
        original = out[span[0]:span[1]]
        if new == original:
            continue
        extra = _plain_numbers(new) - _plain_numbers(original) - allowed_numbers
        if extra:
            skipped.append(f"新句子帶進素材沒有的數字 {sorted(extra)[:3]}：{old[:30]}")
            continue
        if _PROCESS_LEAK.search(re.sub(r"\]\([^)]*\)", "]", new)):
            skipped.append(f"新句子有查證用語：{old[:30]}")
            continue
        if _HEDGE.search(new) and not _HEDGE.search(original):
            # 2026-11-20：對帳修正常把錯句改成「影片說……但尚未核實」，正文越修越像稽核報告
            skipped.append(f"新句子加了查證口吻：{old[:30]}")
            continue
        if len(new) > len(original) * 3 + 120:
            skipped.append(f"新句子長太多：{old[:30]}")
            continue
        out = out[:span[0]] + new + out[span[1]:]
        applied += 1
    if applied:
        out = re.sub(r"[ \t]+\n", "\n", out)
        out = re.sub(r"\n{3,}", "\n\n", out)
    return out, applied, skipped


_LEAD_PROMPT = """你是這篇文章的作者。文章的開場（第一個小標之前）太長了：{detail}

請只重寫開場，第一個小標之後的內容一個字都不會動。
- 最多 {max_sentences} 句。先寫讀者心裡會問的那個問題（或一個具體的場景、反差）把讀者帶進來，
  再讓讀者看出這篇在談哪個問題、你傾向哪邊、這個傾向在什麼條件下成立。
- 判斷要長在敘事裡，不要寫「這篇要回答的是…」「我的看法是：」「我的判斷是：」這種宣告句。
- 分成兩到三段，每段不超過 200 字。
- 多出來的背景直接刪掉或濃縮成一句，不要搬到後面（後面不會動）。但原本開場裡解釋過、後面段落還會用到的名詞，
  新開場要保留那句解釋（後面不會再解釋一次）。
- 只能用原本開場和下面素材裡有的事實與數字。不要出現「素材」「逐字稿」「研究來源」這類查證過程的話。

=== 原本的開場 ===
{lead}

=== 第一個小標之後（只給你看前後文，不要改）===
{rest}

=== 素材（查數字用）===
{pack}

=== 輸出格式（最高優先）===
只輸出新的開場，用下面兩行包起來：
<<<OPENING>>>
（新的開場）
<<<END>>>
"""


def _split_lead(article: str) -> tuple[str, str]:
    m = re.search(r"^#{2,4}\s+", article or "", flags=re.M)
    return (article, "") if not m else (article[:m.start()], article[m.start():])


def _lead_round(current: str, violations, pack: str) -> tuple[str, bool]:
    """開場太長或貼了標籤（A1／A1b）：只重寫第一個小標之前那一段（2026-11-17 智取未來：
    兩次整篇重寫都沒把開場改短，整篇被擋、當天沒發文）。改完程式驗：句數夠短、沒有標籤句、
    沒帶進素材沒有的數字、沒有查證用語，才換進去。"""
    from substack_radar.aeo_gate import check as aeo_check
    from substack_radar.style_gate import _PROCESS_LEAK

    lead, rest = _split_lead(current)
    if not rest.strip() or len(lead.strip()) < 40:
        return current, False
    detail = "；".join(f"{v.kind}：{v.detail}" for v in violations if re.match(r"A1", str(v.kind)))
    prompt = _LEAD_PROMPT.format(detail=detail[:600], max_sentences=10, lead=lead.strip(),
                                 rest=rest[:2500], pack=pack[:60000])
    try:
        from src import run_log
        with run_log.stage("改開場"):
            raw, who = run_writer_text(prompt, AUDIT_TIMEOUT_S)
    except Exception as exc:
        print(f"[Review] ⚠️ 改開場沒跑成（{str(exc)[:100]}）")
        return current, False
    m = re.search(r"<<<OPENING>>>\s*(.+?)\s*<<<END>>>", raw or "", re.S)
    new_lead = (m.group(1).strip() if m else "")
    if len(new_lead) < 30:
        print(f"[Review] ⚠️ {who} 沒有交出可用的新開場")
        return current, False
    candidate = new_lead + "\n\n" + rest.lstrip()
    left = [v.rule for v in aeo_check(candidate) if v.rule.startswith("A1")]
    extra = _plain_numbers(new_lead) - _plain_numbers(lead) - _plain_numbers(pack)
    if left or extra or _PROCESS_LEAK.search(re.sub(r"\]\([^)]*\)", "]", new_lead)):
        why = left or (f"多了素材沒有的數字 {sorted(extra)[:3]}" if extra else "有查證用語")
        print(f"[Review] ⚠️ {who} 交的新開場沒過（{why}），不換")
        return current, False
    print(f"[Review] 🩹 {who} 重寫了開場（其他段落沒動）")
    return candidate, True


def _patch_round(current: str, violations, audit_items, style_items, pack: str,
                 sources: list[dict] | None, length_items: list[dict] | None = None) -> tuple[str, int]:
    issues = [f"- [程式檢查] {v.kind}：{v.detail}（{v.fix_hint}）" for v in violations]
    issues += [f"- [跟素材不符] {it.get('問題', '')}｜原句：{it.get('原句', '')}"
               f"｜素材原文：{str(it.get('素材原文', ''))[:300]}｜方向：{it.get('建議改法', '')}" for it in audit_items]
    issues += [f"- [文風] {it.get('問題', '')}｜原句：{it.get('原句', '')}｜方向：{it.get('建議改法', '')}"
               for it in style_items]
    issues += [f"- [篇幅] {it.get('問題', '')}｜原句：{it.get('原句', '')}｜方向：{it.get('建議改法', '')}"
               for it in (length_items or [])]
    listed = "\n".join(f"- {s.get('publisher') or ''}｜{s.get('title') or ''}｜{s.get('url') or ''}"
                       for s in (sources or [])[:10]) or "（沒有）"
    prompt = _PATCH_PROMPT.format(issues="\n".join(issues), sources=listed, article=current)
    try:
        from src import run_log
        with run_log.stage("小修"):
            raw, who = run_writer_text(prompt, AUDIT_TIMEOUT_S)
    except Exception as exc:
        print(f"[Review] ⚠️ 小修沒跑成（{str(exc)[:100]}）")
        return current, 0
    from substack_radar.fact_audit import loads_lenient, salvage
    data = loads_lenient(raw)
    patches = data.get("patches") if isinstance(data, dict) else None
    if not isinstance(patches, list):
        patches = salvage(raw, "改成") or None      # 少個括號之類的：把完整的替換一條一條撈出來
    if not isinstance(patches, list):
        print(f"[Review] ⚠️ 寫手（{who}）交的小修不是合法格式")
        return current, 0
    allowed = _plain_numbers(pack) | _plain_numbers(" ".join(str(it.get("素材原文", "")) for it in audit_items))
    revised, applied, skipped = apply_patches(current, patches, allowed)
    for why in skipped:
        print(f"[Review]   ⏭️ 沒換：{why}")
    if applied:
        print(f"[Review] 🩹 {who} 小修了 {applied} 句（其他句子沒動，不用重審）")
    return revised, applied


def _run_review_loop_ledger(article_md: str, *, fact_block: str, eval_kwargs: dict,
                            max_attempts: int, word_range: tuple[int, int] | None,
                            writer: str = "", source_title: str = "") -> tuple[str, LoopReport, int]:
    """對帳式審稿（2026-11-16）。目標：品質不降，第一稿盡量一次就過。

    每一關只留真的在保護讀者的東西：
    1. 程式檢查（出處、數字、開場、篇幅、文風）——看得到全文，結果每次一樣。
    2. 兩個開源模型同時對帳，只找「草稿跟素材矛盾」的句子，證據由程式驗（見 fact_audit）。只在
       第一稿做一次；之後除非整篇重寫，不再請模型重看。
    3. 有問題先「小修」：寫手只交出要換的那幾句，程式換進去，其他句子不動、不用重審。
       只有結構問題（太短、開場太長、插了名詞清單）才整篇重寫。"""
    from substack_radar import fact_audit
    from substack_radar.style_gate import style_feedback

    report = LoopReport()
    current, rewrites = article_md, 0
    sources = eval_kwargs.get("sources")
    pending: list[dict] = []
    need_audit, prev_norm = True, ""
    authors = [writer] if writer else []          # 動過筆的人都不能當查核員
    audited_once = False
    for attempt in range(max_attempts + 1):
        violations = evaluate(current, **eval_kwargs)
        report.history.append(violations)
        report.rounds = attempt + 1
        pack = evidence_pack(fact_block, sources, article=current)
        too_short = length_feedback(current, word_range)
        # 小標交給標題台重寫（只換那一行），不值得為它退稿
        style_items = [i for i in style_feedback(current) if not str(i.get("問題", "")).startswith("小標像標籤")]
        if os.getenv("SUBSTACK_COLUMN") == "hsin" and len(sources or []) >= 3:
            from substack_radar.style_gate import retelling_feedback
            style_items += retelling_feedback(current)      # 有延伸資料卻還在重述來源＝要重寫，不是小修
        # 整篇重寫只留給「改幾句改不好」的：太短／過長（結構=True）、插了名詞清單、還在轉述原素材。
        # 開場太長／貼標籤只改開場（_lead_round），其他段落不動；篇幅略長（結構=False）走小修。
        structural = (any(it.get("結構", True) for it in too_short)
                      or any(re.search(r"專有名詞註解|還在轉述原素材", str(i.get("問題", ""))) for i in style_items))
        lead_fix = any(str(v.kind).startswith("A1") for v in violations)
        if attempt == 0:
            _shadow_grounding(current, fact_block, sources)
        # 先結構、後對帳（2026-11-16 新流程第一篇實跑：稿子太短，對完帳才整篇重寫，重寫完又得再對一次）。
        # 反正要整篇重寫，就等重寫完再對帳，一篇只對一次。
        defer_audit = need_audit and (structural or lead_fix) and attempt < max_attempts
        if defer_audit:
            print("[Review] 這一稿要先動" + ("結構（篇幅等），等重寫完" if structural else "開場，等改完") + "再對帳")
            pending = []
        elif need_audit:
            # 動過筆的人（第一稿的寫手、後來重寫的人）都不能當查核員
            found, summary = fact_audit.run(current, pack, writers=authors, source_title=source_title)
            report.auditors = list(dict.fromkeys(report.auditors + summary["answered"]))
            if not summary["answered"]:
                print("[Review] ⚠️ 查核模型都沒回應：這一篇只有程式檢查，沒有第二雙眼睛對過素材")
            if audited_once:                        # 對過帳之後才整篇重寫的：只收新寫或改過的句子裡的問題
                found = [it for it in found if fact_audit.coverage(str(it.get("原句") or ""), prev_norm) < 0.9]
            pending, need_audit, audited_once = found, False, True
        else:                                       # 小修之後：原句還在＝那一條沒修到
            art_norm = fact_audit._norm(current)
            pending = [it for it in pending if fact_audit.coverage(str(it.get("原句") or ""), art_norm) >= 0.9]
        _log_rejections(attempt + 1, violations, pending, too_short, style_items)
        if not (violations or pending or too_short or style_items):
            report.passed = True
            how = "第一稿一次就過" if attempt == 0 else ("小修後通過" if not rewrites else "重寫後通過")
            print(f"[Review] ✅ 第 {attempt + 1} 次審稿：程式檢查與對帳都沒有問題，{how}")
            return current, report, rewrites
        print(f"[Review] 第 {attempt + 1} 次審稿：程式檢查 {len(violations)} 項、跟素材不符 {len(pending)} 項、"
              f"篇幅 {len(too_short)} 項、文風 {len(style_items)} 項")
        merged = ([{"問題": f"跟素材不符：{it.get('問題', '')}", "原句": it.get("原句", ""),
                    "建議改法": f"{it.get('建議改法', '')}（素材原文：{str(it.get('素材原文', ''))[:200]}）"}
                   for it in pending] + too_short + style_items)
        for line in _feedback(violations, merged).splitlines():
            print(f"[Review]   {line[:200]}")
        if attempt == max_attempts:
            break
        if not structural:
            changed = False
            if lead_fix:
                current, changed = _lead_round(current, violations, pack)
                if changed:
                    report.patches += 1
            rest_violations = [v for v in violations if not str(v.kind).startswith("A1")]
            length_patches = [it for it in too_short if not it.get("結構", True)]
            if rest_violations or pending or style_items or length_patches:
                revised, applied = _patch_round(current, rest_violations, pending, style_items, pack, sources,
                                                length_items=length_patches)
                if applied:
                    current, changed = revised, True
                    report.patches += applied
            if changed:
                continue
            print("[Review] ⚠️ 小修沒有換到任何一句，改成整篇重寫")
        prompt = _REWRITE_PROMPT.format(attempt=rewrites + 1, feedback=_feedback(violations, merged),
                                        facts=pack, article=current)
        try:
            from src import run_log
            with run_log.stage(f"退稿重寫#{rewrites + 1}"):
                raw, who = run_writer_text(prompt, AUDIT_TIMEOUT_S)
        except Exception as exc:
            print(f"[Review] ⚠️ 寫手重寫失敗（{str(exc)[:120]}）；保留目前版本")
            break
        revised = _extract_article(raw)
        if not revised or revised.strip() == current.strip():
            print(f"[Review] ⚠️ 寫手（{who}）沒有交出可用的修改稿；保留目前版本")
            break
        prev_norm = fact_audit._norm(current)
        current, need_audit = revised, True
        authors.append(who)
        rewrites += 1
        print(f"[Review] ✏️ 整篇重寫第 {rewrites} 次（{who}）：{'篇幅或結構要動' if structural else '小修沒成功'}")
    if not audited_once:
        # 等重寫完再對帳，結果重寫沒成功：定稿還是要對一次，不能整篇沒人對過就放出去
        found, summary = fact_audit.run(current, evidence_pack(fact_block, sources, article=current), writers=authors,
                                        source_title=source_title)
        report.auditors = list(dict.fromkeys(report.auditors + summary["answered"]))
        pending = found
    report.passed = not report.remaining
    report.unresolved = [f"{it.get('問題', '')}（原句：{str(it.get('原句', ''))[:60]}）" for it in pending]
    if report.passed and not pending:
        print("[Review] ✅ 程式檢查已全部通過（還有文風或篇幅建議，但已用完修改次數）")
    elif report.passed:
        print(f"[Review] ⚠️ 程式檢查通過，但還有 {len(pending)} 處跟素材不符沒修掉，會列在通知信裡")
    else:
        print(f"[Review] ❌ 修改 {rewrites + (1 if report.patches else 0)} 輪後仍有 {len(report.remaining)} 項程式檢查違規（不靜默放行）")
    return current, report, rewrites


def _shadow_grounding(article: str, fact_block: str, sources: list[dict] | None) -> None:
    """影子檢查（只記帳、不影響結果）：草稿裡的英文專名與數字，全文素材和研究來源裡完全找不到的。
    先觀察一兩週誤報多不多，再決定要不要升成正式的程式檢查，取代模型「有沒有出處」那一類判斷。"""
    try:
        from src import run_log
        hay = (fact_block or "") + " " + " ".join(
            f"{s.get('publisher', '')} {s.get('title', '')} {s.get('url', '')} {s.get('excerpt', '')}"
            for s in sources or [])
        low = re.sub(r"(?<=\d)[,，](?=\d{3})", "", hay).lower()
        flat = re.sub(r"\]\([^)]*\)", "]", article or "")
        names = {w for w in re.findall(r"\b[A-Z][A-Za-z]{3,}(?:\s+[A-Z][A-Za-z]{2,})*\b", flat)}
        missing_names = sorted(n for n in names if n.lower() not in low)
        hay_nums = set(re.findall(r"\d+(?:\.\d+)?", low))
        missing_nums = sorted(n for n in _plain_numbers(flat) if n not in hay_nums and not re.fullmatch(r"(19|20)\d\d", n))
        for n in missing_names[:12]:
            run_log.log_rejection(round_no=1, source="影子", kind="專名在素材找不到", quote=n)
        for n in missing_nums[:12]:
            run_log.log_rejection(round_no=1, source="影子", kind="數字在素材找不到", quote=n)
        print(f"[Review] （影子檢查，不影響結果）素材找不到的英文專名 {len(missing_names)} 個、數字 {len(missing_nums)} 個")
    except Exception:
        pass


def _run_review_loop_legacy(article_md: str, *, fact_block: str, auditor: str, eval_kwargs: dict,
                            max_attempts: int = REVIEW_ATTEMPTS,
                            word_range: tuple[int, int] | None = None) -> tuple[str, LoopReport, int]:
    """舊版：請模型挑毛病、整篇重寫。回傳 (最終稿, 報告, 退稿次數)。"""
    report = LoopReport()
    current = article_md
    rewrites = 0
    # 2026-11-16 翻 32 篇記錄：審稿每一輪都重審全文、固定挑出約 7 條（8.2→7.2→6.7→6.8），
    # 從不收斂，28 篇把重寫次數用到上限。現在第一輪審全文，之後只審改過的句子；
    # 沒改過的句子上一輪已經審過，不再重挑。SUBSTACK_REAUDIT_FULL=1 可改回每輪審全文。
    reaudit_full = os.getenv("SUBSTACK_REAUDIT_FULL", "0") == "1"
    audited: set[str] = set()
    for attempt in range(max_attempts + 1):
        violations = evaluate(current, **eval_kwargs)
        report.history.append(violations)
        report.rounds = attempt + 1
        pack = evidence_pack(fact_block, eval_kwargs.get("sources"), article=current)
        sentences = _sentences(current)
        fresh = [x for x in sentences if _skey(x) not in audited]
        if attempt == 0 or reaudit_full:
            audit_items = critique(current, violations, pack, auditor)
        elif fresh or violations:
            print(f"[Review] 第 {attempt + 1} 次審稿只看改過的 {len(fresh)} 句（全文 {len(sentences)} 句）")
            audit_items = critique(current, violations, pack, auditor, focus=fresh or None)
        else:
            audit_items = []
        audited |= {_skey(x) for x in sentences}
        too_short = length_feedback(current, word_range)
        items = (audit_items or []) + too_short if (audit_items or too_short) else audit_items
        # 文風（套話、沒翻譯的英文、重複、標籤小標）：當退稿意見給寫手，不影響通過與否
        # 只在第一輪或本來就要退稿時附上，免得每篇都為了用詞把 3 次重寫用完、拖過發文時間
        style_items: list[dict] = []
        if attempt == 0 or violations or items:
            from substack_radar.style_gate import style_feedback
            style_items = style_feedback(current)
            items = (items or []) + style_items
        _log_rejections(attempt + 1, violations, audit_items or [], too_short, style_items)
        if not violations and not items:
            report.passed = True
            print(f"[Review] ✅ 第 {attempt + 1} 次審稿：程式檢查與審稿編輯都沒有意見，通過")
            return current, report, rewrites
        print(f"[Review] 第 {attempt + 1} 次審稿：程式檢查 {len(violations)} 項、審稿意見 {len(items or [])} 項")
        for line in _feedback(violations, items).splitlines():
            print(f"[Review]   {line[:200]}")
        if attempt == max_attempts:
            break
        # 要補篇幅時給寫手更多原始素材（podcast 的逐字稿），故事才補得出來
        prompt = _REWRITE_PROMPT.format(attempt=attempt + 1, feedback=_feedback(violations, items),
                                        facts=pack, article=current)
        try:
            from src import run_log
            with run_log.stage(f"退稿重寫#{attempt + 1}"):
                raw, who = run_writer_text(prompt, AUDIT_TIMEOUT_S)
        except Exception as exc:
            print(f"[Review] ⚠️ 寫手重寫失敗（{str(exc)[:120]}）；保留目前版本")
            break
        revised = _extract_article(raw)
        if not revised or revised.strip() == current.strip():
            print(f"[Review] ⚠️ 寫手（{who}）沒有交出可用的修改稿；保留目前版本")
            break
        current = revised
        rewrites += 1
        print(f"[Review] ✏️ 第 {rewrites} 次退稿，已由 {who} 依意見重寫")
    report.passed = not report.remaining
    if report.passed:
        print("[Review] ✅ 程式檢查已全部通過（審稿編輯仍有建議，但已用完退稿次數）")
    else:
        print(f"[Review] ❌ 退稿 {rewrites} 次後仍有 {len(report.remaining)} 項程式檢查違規（不靜默放行）")
    return current, report, rewrites
