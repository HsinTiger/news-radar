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

Severity = Literal["block", "warn", "rewrite"]

# Persisted with every evaluation so dashboard trends remain interpretable when
# rules change. Bump only when rule semantics change, not for comments/tests.
QUALITY_GUARD_VERSION = "2026-07-24.recovery-v3"
# block   = 拒絕發文（嚴重 FP）
# warn    = 記錄但放行（弱訊號）
# rewrite = 請 composer 再寫一次再判定（通常 LLM output 有破綻，但可修）


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


# ---------- Topic-4 redo（2026-04-22）patterns ----------
# 目標：把『LLM 輸出破綻 / 套話堆疊 / 無依據數字 / 過時年份』擋在 Meta API 之前。
# 設計原則：
#   - 有『context-gated』的規則（hyperbole、corporate fluff、uncited stat、wave opener）
#     一定要跟 count / 距離 / year anchor 綁在一起——否則 FP 一下就把 healthy 貼文也 block。
#   - severity='rewrite' 不是拒絕發文，而是給 composer 一個機會重寫；上層可以決定要不要
#     重跑 LLM。這比 block 更溫和，適合『內容結構不好但不算假文』的情況。

# Pattern 1：AI 拒絕／meta 語句——LLM refuses 或自我揭露時的產物
_AI_REFUSAL_MARKERS = (
    "抱歉，我無法",
    "抱歉我無法",
    "作為一個 AI",
    "作為語言模型",
    "作為 AI 助理",
    "I cannot",
    "I'm not able to",
    "I am unable to",
    "As an AI",
    "As a language model",
    "I don't have",
    "我無法提供",
)

# Pattern 2：template expansion 留下的未替換 placeholder
_PLACEHOLDER_MARKERS = (
    "[公司名]",
    "[產品名]",
    "[日期]",
    "{{",
    "}}",
    "XXXX",
    "XXX 公司",
    "TBD",
    "TODO",
    "待補",
    "此處待補",
    "lorem ipsum",
)

# Pattern 3：LLM 掰的示範網址
_FAKE_URL_MARKERS = (
    "example.com",
    "yourwebsite.com",
    "placeholder.io",
    "sample-url",
    "your-site.com",
)

# Pattern 4：過時年份（LLM 用舊 training data 產稿的指紋；單獨出現不判，需 context gate）
_STALE_YEAR_MARKERS = ("2021", "2022", "2023")

# Pattern 5：LLM 虛構的『知名媒體 / 內部消息』佐證
_FAKE_SOURCE_MARKERS = (
    "《某某日報》",
    "《某科技網》",
    "知名科技媒體報導",
    "知名財經媒體指出",
    "根據業內內部消息",
    "匿名內部人士透露",
    "消息人士透露",
)

# Pattern 6：企業套話——單一個不算 fluff，要 ≥3 個才算堆疊
_CORPORATE_FLUFF_TERMS = (
    "賦能", "生態", "賽道", "閉環", "下沉市場", "痛點", "打法",
    "降維打擊", "全鏈路", "抓手", "頂層設計", "護城河", "底層邏輯",
    "戰略縱深", "勢能", "心智占領",
)

# Pattern 7：誇飾詞——單用無礙，疊用 ≥4 次就是 AI 超燃文
_HYPERBOLE_TERMS = (
    "劃時代", "顛覆", "重大突破", "改寫", "前所未有", "史上最",
    "震撼業界", "徹底改變", "革命性", "石破天驚", "翻天覆地",
)

# Pattern 8：數字但無引用來源——要配 citation 近距檢查
_STAT_PATTERN = re.compile(
    r"(?:提升|增長|成長|下降|跌|漲|提高|減少)\s*\d+(?:\.\d+)?\s*%"
    r"|(?:\d+(?:\.\d+)?)\s*(?:億|兆|萬)\s*(?:美元|元|台幣|日圓|人民幣)"
    r"|\d+(?:\.\d+)?\s*倍"
)

# 這些詞表示前後文有引用來源——數字出現在這些詞 ±40 字內就不算 uncited
_CITATION_MARKERS = (
    "來源", "根據", "資料來源", "路透", "彭博", "《", "CNBC", "《華爾街",
    "per ", "according to", "財報", "法說", "官方數據", "調查顯示",
    "白皮書", "研究指出", "公告", "分析師預估",
)

# Recovery mode is deliberately stricter than the long-running legacy feed.
# A bare "根據報導" is not a named source.  The pattern accepts either an
# explicit publisher/institution or a named actor followed by an evidence verb.
_RECOVERY_NAMED_SOURCE_PATTERN = re.compile(
    r"(?:(?:根據|依據|依照)\s*"
    r"(?:《[^》]{2,60}》|[A-Za-z0-9\u4e00-\u9fff·．・\s]{2,60}?)"
    r"(?:的)?(?:報導|公告|數據|資料|報告|調查|統計|財報|法說|說法|指出|表示|證實|回應))"
    r"|(?:(?:路透(?:社)?|彭博|BBC|CNBC|華爾街日報|金融時報|美聯社|中央社|"
    r"公視|自由時報|聯合報|交通部|衛福部|行政院|證交所|金管會)"
    r"(?:報導|公告|指出|表示|證實|回應|資料|數據|報告)?)"
    r"|(?:《[^》]{2,60}》(?:報導|指出|表示|公告))"
    r"|(?:[A-Za-z0-9\u4e00-\u9fff·．・]{2,40}"
    r"(?:報導|財報|法說|白皮書|公告|調查|報告|統計|說法))",
    re.IGNORECASE,
)
_GENERIC_RECOVERY_SOURCE_PATTERN = re.compile(
    r"(?:(?:根據|依據|據)\s*(?:該|這篇|相關)?\s*(?:報導|媒體|新聞|資料|消息))"
    r"|(?<![A-Za-z0-9\u4e00-\u9fff])(?:媒體|新聞|官方|相關人士)"
    r"\s*(?:報導|指出|表示|公告|資料|數據)",
)
_RECOVERY_READER = (
    r"一般人|消費者|使用者|讀者|上班族|投資人|家長|學生|通勤族|"
    r"一般通勤者|通勤者|駕駛|旅客|家庭|企業|你"
)
_RECOVERY_IMPACT_PATTERN = re.compile(
    rf"(?:對(?:{_RECOVERY_READER}).{{0,24}}(?:實際|直接|具體)"
    r"(?:影響|風險|成本)|(?:這|此).{0,20}(?:對你意味著|會直接影響))",
)
_RECOVERY_ACTION_PATTERN = re.compile(
    rf"(?:(?:{_RECOVERY_READER}).{{0,8}}(?:可以|應該|需要|最好|不妨)"
    r"|(?:可以|應該|需要|最好)(?:先|再|立即|優先)?"
    r"(?:查詢|確認|檢查|比較|保留|避開|避免|等待|追蹤|申請|備份|"
    r"諮詢|停止|關閉|更新|調整)|下一步(?:是|可|可以|應))",
)
_RECOVERY_MEASURED_CLAIM_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|％|萬|億|兆|公頃|公里|公尺|人|件|起|"
    r"度|美元|元|倍|顆|噸|GB|TB|MW|GW)",
    re.IGNORECASE,
)
_RECOVERY_ALLEGATION_TERMS = (
    "恐嚇", "威脅提告", "掩蓋", "造假", "圖利", "貪腐", "黑箱",
    "勒索", "詐騙", "收賄", "違法施壓",
)
_RECOVERY_ATTRIBUTION_VERBS = (
    "主張", "質疑", "指控", "聲稱", "表示", "回應", "否認", "判決",
    "起訴", "調查中",
)
_RECOVERY_TRUST_ERODING_FRAMES = (
    "真正的賽局", "信任赤字", "護城河", "底層邏輯", "系統性崩潰",
    "巨大缺口", "信任崩塌", "信任代價", "重創", "迫使各國重新",
)

# Pattern 9：LLM 開場套話——用中間有空白的版本抓 "在 數位化 的 浪潮 中"
_WAVE_PATTERN = re.compile(
    r"在\s*(數位化|AI|快速變動|科技|全球化|變革)\s*的\s*浪潮\s*(中|下|裡|之中|之下)"
)

# 文章若本身含年份錨點，wave opener 就不算干話——算有落地
_YEAR_ANCHORS = ("2024", "2025", "2026", "Q1", "Q2", "Q3", "Q4", "本季", "上季", "本月")


# ---------- Topic-4 helpers ----------

def _count_hits(needles: tuple, text: str) -> int:
    """distinct term count：同一個詞重複只算一次。"""
    return sum(1 for n in needles if n and n in text)


def _hyperbole_overuse(full_text: str, _title: str) -> Optional[str]:
    hits = [n for n in _HYPERBOLE_TERMS if n in full_text]
    if len(hits) >= 4:
        return "hyperbole_count=" + str(len(hits)) + ":" + "/".join(hits[:5])
    return None


def _corporate_fluff_pileup(full_text: str, _title: str) -> Optional[str]:
    hits = [n for n in _CORPORATE_FLUFF_TERMS if n in full_text]
    if len(hits) >= 3:
        return "fluff_count=" + str(len(hits)) + ":" + "/".join(hits[:5])
    return None


def _has_citation_nearby(text: str, stat_pos: int, radius: int = 40) -> bool:
    """stat_pos 前後 radius 字元內若有 citation marker，就算有憑據。"""
    lo = max(0, stat_pos - radius)
    hi = min(len(text), stat_pos + radius)
    window = text[lo:hi]
    return any(m in window for m in _CITATION_MARKERS)


def _uncited_stat(full_text: str, _title: str) -> Optional[str]:
    """抓到任何數字但 ±40 字內都沒有 citation marker。"""
    for m in _STAT_PATTERN.finditer(full_text):
        if not _has_citation_nearby(full_text, m.start()):
            return "stat_no_citation:" + m.group(0)
    return None


def _wave_opener_without_year(full_text: str, _title: str) -> Optional[str]:
    """LLM 最愛的『在 X 的浪潮中』，除非文內有年份錨點才放行。"""
    m = _WAVE_PATTERN.search(full_text)
    if not m:
        return None
    if any(y in full_text for y in _YEAR_ANCHORS):
        return None  # 有年份錨點 → 算落地寫作
    return "wave_opener:" + m.group(0)


def _stale_year_with_recent_context(full_text: str, _title: str) -> Optional[str]:
    """只有當文章同時含 2026 的當前年份 + 2021-2023 舊年份時不算 flag。
    純粹 LLM 拿舊 training data 寫新聞時才 flag（= 沒有當前年份錨點）。"""
    has_stale = any(y in full_text for y in _STALE_YEAR_MARKERS)
    if not has_stale:
        return None
    has_current = any(y in full_text for y in ("2024", "2025", "2026"))
    if has_current:
        return None
    # 只有舊年份、沒當代年份 = LLM 用 outdated data
    stale_hits = [y for y in _STALE_YEAR_MARKERS if y in full_text]
    return "only_stale_years:" + ",".join(stale_hits)


def _generic_recovery_source(full_text: str, _title: str) -> Optional[str]:
    match = _GENERIC_RECOVERY_SOURCE_PATTERN.search(full_text)
    return f"generic_source:{match.group(0)}" if match else None


def _recovery_fact_without_local_source(
    full_text: str, _title: str
) -> Optional[str]:
    for paragraph in re.split(r"\n\s*\n", full_text):
        if not paragraph.strip():
            continue
        factual = "已知事實" in paragraph or _RECOVERY_MEASURED_CLAIM_PATTERN.search(
            paragraph
        )
        named = _RECOVERY_NAMED_SOURCE_PATTERN.search(paragraph)
        generic = _GENERIC_RECOVERY_SOURCE_PATTERN.search(paragraph)
        if factual and (not named or generic):
            return "fact_paragraph_without_named_source:" + paragraph.strip()[:80]
    return None


def _recovery_unattributed_allegation(
    full_text: str, _title: str
) -> Optional[str]:
    for sentence in re.split(r"[。！？\n]+", full_text):
        term = next(
            (item for item in _RECOVERY_ALLEGATION_TERMS if item in sentence),
            None,
        )
        if term is None:
            continue
        attributed = (
            _RECOVERY_NAMED_SOURCE_PATTERN.search(sentence)
            and not _GENERIC_RECOVERY_SOURCE_PATTERN.search(sentence)
        ) or any(
            verb in sentence for verb in _RECOVERY_ATTRIBUTION_VERBS
        )
        if not attributed:
            return f"unattributed_allegation:{term}:{sentence.strip()[:70]}"
    return None


def _recovery_jargon_pileup(full_text: str, _title: str) -> Optional[str]:
    hits = [term for term in _RECOVERY_TRUST_ERODING_FRAMES if term in full_text]
    if len(hits) >= 2:
        return "trust_eroding_frames=" + "/".join(hits[:5])
    return None


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
    # ---- Topic-4 redo：9 條新規則 ----
    _Rule(
        code="ai_refusal_marker",
        severity="block",
        message="偵測到 LLM 拒絕／meta 語句——代表 composer 產稿失敗，不是真的貼文",
        matcher=_contains_any(_AI_REFUSAL_MARKERS),
    ),
    _Rule(
        code="placeholder_marker",
        severity="block",
        message="偵測到未替換的 template placeholder（[公司名]、{{var}} 等）",
        matcher=_contains_any(_PLACEHOLDER_MARKERS),
    ),
    _Rule(
        code="fake_url_marker",
        severity="rewrite",
        message="含 example.com / yourwebsite 類虛構網址——LLM 掰的示範連結",
        matcher=_contains_any(_FAKE_URL_MARKERS),
    ),
    _Rule(
        code="stale_year_without_current",
        severity="warn",
        message="只出現 2021-2023 舊年份、沒有 2024-2026 錨點——可能 LLM 拿舊資料產稿",
        matcher=_stale_year_with_recent_context,
    ),
    _Rule(
        code="fake_source_marker",
        severity="block",
        message="偵測到『《某某日報》』『內部人士透露』等虛構媒體／來源",
        matcher=_contains_any(_FAKE_SOURCE_MARKERS),
    ),
    _Rule(
        code="corporate_fluff_pileup",
        severity="warn",
        message="企業套話堆疊（≥3 個：賦能／生態／賽道／閉環…）——AI 味過重",
        matcher=_corporate_fluff_pileup,
    ),
    _Rule(
        code="hyperbole_overuse",
        severity="rewrite",
        message="誇飾詞 ≥4 個（劃時代／顛覆／重大突破／改寫）——typical AI hype",
        matcher=_hyperbole_overuse,
    ),
    _Rule(
        code="uncited_stat",
        severity="rewrite",
        message="有具體數字但 ±40 字內找不到來源 marker——LLM 可能自己掰的",
        matcher=_uncited_stat,
    ),
    _Rule(
        code="wave_opener_without_year",
        severity="rewrite",
        message="『在 X 的浪潮中』開場 + 無任何年份／季度錨點——純 LLM 空泛開場",
        matcher=_wave_opener_without_year,
    ),
)

_RECOVERY_RULES: tuple[_Rule, ...] = (
    _Rule(
        code="generic_source_attribution",
        severity="rewrite",
        message="Recovery Mode 不接受『根據報導／官方資料』等匿名來源",
        matcher=_generic_recovery_source,
    ),
    _Rule(
        code="missing_source_attribution",
        severity="rewrite",
        message="Recovery Mode 要求具名來源，避免讀者把自動生成內容當成無來源事實",
        matcher=lambda ft, _t: (
            "no_named_source_marker"
            if not _RECOVERY_NAMED_SOURCE_PATTERN.search(ft)
            else None
        ),
    ),
    _Rule(
        code="fact_without_local_source",
        severity="rewrite",
        message="已知事實或具體數字所在段落必須就地標示具名來源",
        matcher=_recovery_fact_without_local_source,
    ),
    _Rule(
        code="unattributed_sensitive_allegation",
        severity="rewrite",
        message="爭議指控必須寫成某人主張／某機關回應，不得當成已證明事實",
        matcher=_recovery_unattributed_allegation,
    ),
    _Rule(
        code="recovery_jargon_pileup",
        severity="rewrite",
        message="策略黑話或戲劇化框架堆疊會侵蝕信任並掩蓋讀者用途",
        matcher=_recovery_jargon_pileup,
    ),
    _Rule(
        code="missing_reader_utility",
        severity="rewrite",
        message="Recovery Mode 每篇必須同時寫出具體讀者影響與可採取的下一步",
        matcher=lambda ft, _t: (
            "no_reader_utility_marker"
            if not (
                _RECOVERY_IMPACT_PATTERN.search(ft)
                and _RECOVERY_ACTION_PATTERN.search(ft)
            )
            else None
        ),
    ),
)


# ---------- 公開 API（呼叫端只用這三個）----------

def check_quality(
    full_text: str,
    title: str = "",
    *,
    recovery: bool = False,
) -> List[QualityIssue]:
    """對『最終要送進 Meta API 的那段字』跑所有規則，回傳命中的 issue 列表。
    full_text = 已經組好、含 hashtag 的完整貼文（platform_drafts.full_text 即是）。
    title = 新聞原始標題，用來判定是否沒翻譯。
    """
    ft = full_text or ""
    issues: List[QualityIssue] = []
    rules = _RULES + (_RECOVERY_RULES if recovery else ())
    for rule in rules:
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


def should_request_rewrite(issues: List[QualityIssue]) -> bool:
    """任一 issue severity='rewrite' → 上層可決定要不要請 composer 再寫一次。
    跟 has_blocking_issues 獨立：一篇可能同時 rewrite 跟 block（block 優先）。"""
    return any(i.severity == "rewrite" for i in issues)


def format_issues(issues: List[QualityIssue]) -> str:
    """一行文，給 log / notification 用。"""
    if not issues:
        return "OK"
    bits = [f"[{i.severity}] {i.code}: {i.evidence}" for i in issues]
    return " | ".join(bits)


__all__ = [
    "QUALITY_GUARD_VERSION",
    "QualityIssue",
    "check_quality",
    "has_blocking_issues",
    "should_request_rewrite",
    "format_issues",
]
