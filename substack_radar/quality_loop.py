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
AUDIT_TIMEOUT_S = int(os.getenv("SUBSTACK_AUDIT_TIMEOUT_S", "600"))
MAX_ROUNDS = int(os.getenv("SUBSTACK_QUALITY_ROUNDS", "3"))

# 分析師共識 ≠ 公司財測。寫成這些字就是把證據張冠李戴。
_MISATTRIBUTION = ("管理層指引", "管理層的指引", "管理層財測", "管理層誠信",
                   "管理層預估", "公司財測落空", "指引落空", "指引失準")


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
             has_management_guidance: bool = False) -> list[Violation]:
    """只回報程式能確定的違規。判斷題留給稽核 agent。"""
    out: list[Violation] = []

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


def _run_agy(prompt: str, model: str, timeout_s: int) -> str:
    """每次呼叫都是新的 subprocess（cwd=/tmp）＝獨立 session、乾淨 context。"""
    if not os.path.exists(AGY_BIN):
        raise FileNotFoundError(f"agy not found at {AGY_BIN}")
    proc = subprocess.run(
        [AGY_BIN, "-p", prompt, "--model", model, "--print-timeout", f"{timeout_s}s"],
        capture_output=True, text=True, cwd="/tmp", timeout=timeout_s + 60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"agy exit={proc.returncode}: {(proc.stderr or '')[:300]}")
    return proc.stdout or ""


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
                              has_management_guidance=has_management_guidance)
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
                     has_management_guidance=has_management_guidance)
    report.history.append(final)
    report.passed = not final
    if final:
        print(f"[QualityLoop] ❌ 用完 {max_rounds} 輪仍有 {len(final)} 項違規（不靜默放行）")
    return current, report
