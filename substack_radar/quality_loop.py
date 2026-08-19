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

import os
import re
import subprocess
from dataclasses import dataclass, field

from substack_radar.fact_reconcile import ScaleIssue, reconcile

AGY_BIN = os.path.expanduser(os.getenv("AGY_BIN", "~/.local/bin/agy"))
# 寫手是 Gemini 3.6 Flash (High)。稽核刻意換家族＋最高推理強度：
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
_CN_NUMERAL_LIMIT = int(os.getenv("SUBSTACK_CN_NUMERAL_LIMIT", "3"))


def chinese_numerals(article_md: str) -> list[str]:
    return _CN_QUANTITY.findall(article_md or "")


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
             fact_block: str = "") -> list[Violation]:
    """只回報程式能確定的違規。判斷題留給稽核 agent。"""
    out: list[Violation] = []

    # 證據法則：歸屬給外部來源的數字要指得出處（見 evidence_gate）。
    from substack_radar.evidence_gate import check as _evidence_check
    from substack_radar.evidence_gate import stale_claims as _stale_claims

    for issue in _evidence_check(article_md, sources=sources or [], fact_block=fact_block):
        out.append(Violation(kind=issue.rule, detail=issue.detail,
                             fix_hint=f"原句：{issue.sentence.strip()[:80]}"))
    # E4：來源活著、也切題，但過期。目標價／評等／股價／市值有保鮮期。
    for issue in _stale_claims(article_md, sources or []):
        out.append(Violation(kind=issue.rule, detail=issue.detail,
                             fix_hint=f"原句：{issue.sentence.strip()[:80]}"))

    for issue in reconcile(article_md, fact_values or {}):
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


def _run_agy_once(prompt: str, model: str, timeout_s: int) -> str:
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


def _run_agy(prompt: str, model: str, timeout_s: int) -> str:
    """依鏈逐個試。第一個成功就回；全掛才 raise。"""
    chain = [model] + [m for m in AUDIT_MODEL_CHAIN if m != model]
    last = None
    for index, candidate in enumerate(chain):
        try:
            result = _run_agy_once(prompt, candidate, timeout_s)
            if index:
                print(f"[QualityLoop] ℹ️ 稽核改用後備模型 {candidate}（前 {index} 個不可用）")
            return result
        except Exception as exc:
            last = exc
            print(f"[QualityLoop] ⚠️ {candidate} 不可用：{str(exc)[:110]}")
    raise RuntimeError(f"稽核鏈全部不可用；最後一個錯誤：{last}")


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
                              sources=sources, fact_block=fact_block)
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
                     sources=sources, fact_block=fact_block)
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
