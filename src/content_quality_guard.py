"""
News Radar · Content Quality Guard（Phase 8.20 附帶品）
======================================================
純函式守門員：給一段最終要發出的文字，回傳一串 QualityIssue。
不做 DB I/O、不打通知、不呼叫 LLM——只負責『判斷』。

為什麼獨立一個模組？
  之前 Phase 8.19 把 emergency_template 從程式碼裡拔掉了，但老 draft 已經
  以 queue_status='queued' 的形式卡在 DB；2026-04-19 晚間 run_publish_queue
  就把一篇「【系統代班速報】...#科技戰略 #商業洞察 #數據驅動」發了出去。
  Hsin 的要求：不刪舊資料，但要一條守門員攔在『真的要送到 Meta API 之前』，
  並在 Mac 上跳本地通知讓他知道系統做了 rescue。

系統設計原則：
  - **純 function**：只 return 問題清單，決策（block / warn / notify）交給上層。
  - **單一事實來源**：所有被禁字 / 禁組合都寫在這裡。
    compose-time 和 publish-time 都 import 它，不要兩邊各自 hard-code 字串。
  - **易擴充**：新增規則只要往 _RULES 加一筆，不必改呼叫端。

使用方式：
    from src.content_quality_guard import check_quality, has_blocking_issues
    issues = check_quality(full_text, title)
    if has_blocking_issues(issues):
        ...  # 拒絕發文 + notify

—— 2026-04-21 overnight, Cowork Claude
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, List, Literal, Optional

Severity = Literal["block", "warn"]


@dataclass(frozen=True)
class QualityIssue:
    """單一品質問題。code 穩定（寫進 log / DB 可依賴），message 給人看。"""
    code: str                  # 穩定識別碼，例：'templated_fallback_marker'
    severity: Severity         # block = 拒絕發文；warn = 記錄但放行
    message: str               # 給 log / notification 用的一句話說明
    evidence: str = ""         # 命中的具體字串（debug 用，可能被截斷）


# ---------- 規則資料（唯一事實來源）----------

@dataclass(frozen=True)
class _Rule:
    code: str
    severity: Severity
    message: str
    matcher: Callable[[str, str], Optional[str]]
    """matcher(full_text, title) -> matched_evidence（None 表示沒觸發）"""


def _contains_any(needles: tuple[str, ...]) -> Callable[[str, str], Optional[str]]:
    def _check(full_text: str, _title: str) -> Optional[str]:
        for n in needles:
            if n in full_text:
                return n
        return None
    return _check


def _contains_all(needles: tuple[str, ...]) -> Callable[[str, str], Optional[str]]:
    """全部都出現才算命中（抓特定 boilerplate 組合）。"""
    def _check(full_text: str, _title: str) -> Optional[str]:
        if all(n in full_text for n in needles):
            return " + ".join(needles)
        return None
    return _check


def _title_is_english_only(full_text: str, title: str) -> Optional[str]:
    """標題裡沒有 CJK 字元，且正文也沒把英文標題重寫成中文開頭 → 有鬼。
    合法例外：中文開頭的正文（第一段有中文），就算標題是英文也算 OK——因為
    writer 已經翻譯／改寫了。觸發點是『標題純英 + 正文前 200 字也沒中文』。
    """
    if not title:
        return None
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in title)
    if has_cjk:
        return None
    # 標題是純英文，檢查正文開頭（去掉 emoji / 標題回聲）
    head = full_text[:400]
    head_has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in head)
    if head_has_cjk:
        return None
    return f"title='{title[:80]}'"


# 以下 tuple 集中了所有被 Phase 8.19 前的 emergency_template 塞過的字。
# 新增規則時，請保留原有 tuple（舊 draft 可能還會被偵測到）。
_TEMPLATED_MARKERS = (
    "【系統代班速報】",
    "結構性位移",
    "護城河的定義已從產品轉向生態數據",
    "數據密度高的決策，將會成為未來的勝負點",
    "面對充滿挑戰的市場",  # emergency_template 的另一句
)

# 三個 hashtag 如果同時出現 = 典型 emergency boilerplate。
_TEMPLATED_HASHTAG_BUNDLE = (
    "#科技戰略",
    "#商業洞察",
    "#數據驅動",
)

_RULES: tuple[_Rule, ...] = (
    _Rule(
        code="templated_fallback_marker",
        severity="block",
        message="偵測到 Phase 8.19 前 emergency_template 的招牌詞，判定為系統代班假文",
        matcher=_contains_any(_TEMPLATED_MARKERS),
    ),
    _Rule(
        code="generic_hashtag_bundle",
        severity="block",
        message="『#科技戰略 + #商業洞察 + #數據驅動』三連發是 emergency_template 的指紋",
        matcher=_contains_all(_TEMPLATED_HASHTAG_BUNDLE),
    ),
    _Rule(
        code="untranslated_english_only",
        severity="block",
        message="標題整段英文、正文前 400 字也沒有中文 → writer 可能沒實際產稿",
        matcher=_title_is_english_only,
    ),
    _Rule(
        code="empty_or_too_short",
        severity="block",
        message="正文少於 30 字，幾乎不可能是正常產稿結果",
        matcher=lambda ft, _t: f"len={len(ft)}" if len(ft.strip()) < 30 else None,
    ),
)


# ---------- 公開 API（呼叫端只用這三個）----------

def check_quality(full_text: str, title: str = "") -> List[QualityIssue]:
    """對『最終要送進 Meta API 的那段字』跑所有規則，回傳命中的 issue 列表。
    full_text = 已經組好、含 hashtag 的完整貼文（platform_drafts.full_text 即是）。
    title = 新聞原始標題，用來判定是否沒翻譯。
    """
    ft = full_text or ""
    issues: List[QualityIssue] = []
    for rule in _RULES:
        evidence = rule.matcher(ft, title or "")
        if evidence is not None:
            issues.append(QualityIssue(
                code=rule.code,
                severity=rule.severity,
                message=rule.message,
                evidence=str(evidence)[:120],
            ))
    return issues


def has_blocking_issues(issues: List[QualityIssue]) -> bool:
    return any(i.severity == "block" for i in issues)


def format_issues(issues: List[QualityIssue]) -> str:
    """一行文，給 log / notification 用。"""
    if not issues:
        return "OK"
    bits = [f"[{i.severity}] {i.code}: {i.evidence}" for i in issues]
    return " | ".join(bits)


__all__ = [
    "QualityIssue",
    "check_quality",
    "has_blocking_issues",
    "format_issues",
]
