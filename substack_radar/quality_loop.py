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


def _same_family(a: str, b: str) -> bool:
    def fam(m: str) -> str:
        m = (m or "").lower()
        for key in ("claude", "gemini", "gpt"):
            if key in m:
                return key
        return m
    return fam(a) == fam(b)


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

    for issue in reconcile(article_md, fact_values or {}, base_currency):
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
    return _logged("gateway", _oss_chat_impl, model, model, prompt, timeout_s)


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

_CRITIQUE_PROMPT = """你是審稿編輯，這一輪只負責「指出問題、給改法」，不要自己改稿。

給你：作者的草稿、管線抓到的原始事實表、程式對帳後確認的違規清單。請做兩件事：
1. 程式抓到的每一條違規，都給一個作者照做就能過關的具體改法：指出要改哪一句、改成什麼方向。
   若你判斷是程式誤報，「建議改法」寫「誤報：理由」，並告訴作者怎麼換個寫法讓程式不再誤判。
2. 另外找出程式抓不到的事實錯誤：比率分母算錯、產業常識過時、專有名詞用錯、
   把一般公司當成資料來源、前後文數字矛盾、把一次性項目當成本業趨勢。風格與觀點不要評論。

=== 程式確認的違規 ===
{violations}

=== 原始事實表（唯一可信的數字來源）===
{facts}

=== 作者草稿 ===
{article}

=== 輸出格式（最高優先）===
只輸出一個 JSON 物件：{{"items": [{{"問題": "…", "原句": "…", "建議改法": "…"}}]}}
完全沒有問題就輸出 {{"items": []}}。不要輸出 JSON 以外的文字。
"""

_REWRITE_PROMPT = """你是這篇文章的作者。審稿編輯把稿子退回來了，附上逐條意見。

請照意見修改你的稿子：
- 每一條意見都要處理；意見寫「誤報」的，照它建議的方式換個寫法。
- 只改有問題的句子與它連帶影響的敘述，其餘保留原文的語氣與結構。
- 不要新增事實表或原文以外的數字、來源；查不到出處的說法寧可刪掉。
- 不要動標題，不要加任何說明文字。

=== 審稿意見（第 {attempt} 次退稿）===
{feedback}

=== 原始事實表 ===
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


def critique(article_md: str, violations: list[Violation], fact_block: str, auditor: str) -> list[dict] | None:
    """稽核模型逐條列出問題與改法。稽核跑不成回 None（跟「跑了沒問題」的 [] 分開）。"""
    listed = "\n".join(f"{i}. {v}" for i, v in enumerate(violations, 1)) or "（無）"
    prompt = _CRITIQUE_PROMPT.format(violations=listed, facts=fact_block[:6000], article=article_md)
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
    return items


def run_writer_text(prompt: str, timeout_s: int) -> tuple[str, str]:
    """寫手重寫：跟寫稿同一套順序 ① agy 寫稿鏈 → ② 閘道 writer → ③ Haiku。"""
    try:
        from src.llm_brain import _agy_model_chain
        chain = [m for m in _agy_model_chain() if not agy_family_dead(m)]
    except Exception:
        chain = []
    for model in chain:
        try:
            return _run_agy_once(prompt, model, timeout_s), f"agy {model}"
        except Exception as exc:
            note_agy_failure(exc, model)
    try:
        return _oss_chat("writer", prompt, timeout_s), "閘道 writer"
    except Exception as exc:
        print(f"[Review] ⚠️ 閘道寫手不可用：{str(exc)[:110]}")
    return run_claude_cli(prompt, timeout_s), "Claude Haiku"


def _feedback(violations: list[Violation], items: list[dict] | None) -> str:
    lines = [f"- [程式檢查] {v.kind}：{v.detail}（{v.fix_hint}）" for v in violations]
    for it in items or []:
        lines.append(f"- [審稿] {it.get('問題', '')}｜原句：{str(it.get('原句', ''))[:120]}"
                     f"｜建議改法：{it.get('建議改法', '')}")
    return "\n".join(lines)


def run_review_loop(article_md: str, *, fact_block: str, auditor: str, eval_kwargs: dict,
                    max_attempts: int = REVIEW_ATTEMPTS) -> tuple[str, LoopReport, int]:
    """回傳 (最終稿, 報告, 退稿次數)。報告的 passed／remaining 只看確定性閘門。"""
    report = LoopReport()
    current = article_md
    rewrites = 0
    for attempt in range(max_attempts + 1):
        violations = evaluate(current, **eval_kwargs)
        report.history.append(violations)
        report.rounds = attempt + 1
        items = critique(current, violations, fact_block, auditor)
        if not violations and not items:
            report.passed = True
            print(f"[Review] ✅ 第 {attempt + 1} 次審稿：程式檢查與審稿編輯都沒有意見，通過")
            return current, report, rewrites
        print(f"[Review] 第 {attempt + 1} 次審稿：程式檢查 {len(violations)} 項、審稿意見 {len(items or [])} 項")
        for line in _feedback(violations, items).splitlines():
            print(f"[Review]   {line[:200]}")
        if attempt == max_attempts:
            break
        prompt = _REWRITE_PROMPT.format(attempt=attempt + 1, feedback=_feedback(violations, items),
                                        facts=fact_block[:6000], article=current)
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
