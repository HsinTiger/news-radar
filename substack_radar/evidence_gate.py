"""證據法則：每一個「歸屬給外部來源的數字」都必須指得出處在哪。

法則（2026-08-18 owner 要求後定的）
------------------------------------
E1 **具名歸屬必須真的存在**
   文中寫「根據 X」的 X，必須是取材清單裡的某個來源（標題或網域），
   或管線自己抓的事實表。2026-08-18 的瑞昱稿把「戴爾電腦」寫成市調機構，
   而戴爾根本不在來源清單裡——這種要擋下來。

E2 **歸屬句裡的數字必須定位得到**
   該數字要出現在某個來源抓回的全文摘錄裡，或出現在事實表。
   定位成功時回報：第幾個來源、在摘錄的第幾個字、前後文——
   這就是「哪篇文章的哪一段第幾個字」。

E3 **定位不到就降級或刪除**
   不得保留「有數字、無出處」的敘述。要嘛補來源，要嘛改寫成不帶數字的
   判斷，要嘛整句刪掉。

刻意不管的
----------
不查沒有歸屬語氣的數字（那些多半是從事實表推導的比率），也不查百分比以外
的常識性數字。誤報會讓人把閘門關掉，寧可漏也不要吵。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 「根據…」「依據…」「…指出」「…統計」：這些字眼把數字掛到了外部權威身上。
_ATTRIBUTION = re.compile(
    r"(根據|依據|援引|引用|參考)([^，。；\n]{2,40})|"
    r"([^，。；\n]{2,40}?)(指出|表示|統計|調查顯示|研究顯示|報告顯示|數據顯示)"
)
_SENTENCE = re.compile(r"[^。！？\n]+[。！？]?")
# 帶單位的量：百分比與金額最常被掛到外部來源身上，也最容易編造。
_QUANTITY = re.compile(r"(\d+(?:\.\d+)?)\s*(%|％|億|兆|奈米|微米|倍)")


@dataclass(frozen=True)
class EvidenceIssue:
    rule: str
    sentence: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.rule}] {self.detail}\n        原句：{self.sentence.strip()[:70]}"


# 全形數字與單位一律折成半形，否則「超過70％」找不到「70%」。
_FOLD = str.maketrans("０１２３４５６７８９％．，", "0123456789%.,")


def _norm(text: str) -> str:
    return re.sub(r"[\s,，]", "", str(text or "").translate(_FOLD))


# 事實表的資料提供者也是合法出處。稿子寫「CoinMetrics 的數據顯示」是對的——
# 那份數字就是管線自己抓的，只是它不在 research_sources 裡（那是延伸來源清單）。
_FACT_PROVIDERS = ("coinmetrics", "coingecko", "mempool.space", "blockchair",
                   "yfinance", "yahoo finance", "defillama")

# 「根據X」的 X 不一定是來源。E1 第一版把「根據傳統的週期經驗」「根據當日…」
# 「初步」這種一般說法都當成具名機構報上來，2026-08-19 的 BTC 稿因此把三輪
# 稽核全燒在假警報上。只有看起來像**具名實體**的才查：含拉丁字母，或帶機構後綴。
_ORG_SUFFIX = ("證券", "銀行", "研究院", "研究所", "週刊", "周刊", "日報", "時報",
               "新聞", "財經", "媒體", "公司", "基金", "資本", "投顧", "交易所",
               "大學", "智庫", "顧問")
# 虛詞／功能詞：出現任何一個就代表這是一段敘述，不是一個名字。
_PHRASE_MARKERS = ("的", "了", "是", "在", "與", "和", "也", "仍", "會", "被",
                   "把", "讓", "使", "就", "都", "而", "但", "雖", "因", "所",
                   "我們", "他們", "你們", "必須", "可以", "嘗試", "沒有", "缺乏",
                   "面對", "一套", "一個", "這", "那", "其", "之", "對於", "如果")
_GENERIC_HEADS = ("傳統", "當日", "初步", "業界", "市場", "歷史", "上述", "目前",
                  "過去", "近期", "經驗", "估算", "說法", "觀察", "支持", "反方",
                  "這項", "這些", "有分析", "有人", "部分", "多數", "一般")


def _looks_like_named_entity(named: str) -> bool:
    """看起來像不像一個「被指名的出處」。

    以一般說法開頭的（傳統／當日／初步／業界…）一律不是；太長的是句子不是名字。
    其餘：有拉丁字母、有機構後綴、或是一段不長的中文詞，都當成具名實體。
    最後那條是為了「戴爾電腦」——沒有拉丁也沒有機構後綴，但它確實是個名字。
    """
    text = (named or "").strip()
    if len(text) < 2:
        return False
    if any(text.startswith(g) for g in _GENERIC_HEADS):
        return False
    # 有拉丁字母或機構後綴＝裡面有個名字，即使正則多抓了幾個字也算。
    # （「CoinShares 研究主管 James Butterfill 的說法」有 36 字元，但它確實是歸屬。）
    if re.search(r"[A-Za-z]{3,}", text) or any(sfx in text for sfx in _ORG_SUFFIX):
        return True
    # 純中文：機構名不含虛詞。「指出／表示」在中文裡本來就是普通動詞
    # （「用一套演算法表示…」），抽象題材的文章滿篇都是，所以光看長度會誤報一片
    # ——2026-08-19 那篇談模擬假說的 podcast 被報了 8 項，全部是假的。
    core = re.sub(r"\s+", "", text)
    if not re.fullmatch(r"[一-鿿]{2,10}", core):
        return False
    return not any(w in core for w in _PHRASE_MARKERS)


def _names_overlap(named: str, known: set[str], min_cjk: int = 3) -> bool:
    """具名來源與清單裡任一來源是不是同一個。

    拉丁字母比**整詞**，中文比 3 字元以上的連續子字串。
    第一版兩者都用 3 字元滑動視窗，於是「CoinShares」靠 `oin` 比對到
    「CoinMetrics」，一個捏造的分析師就被當成合法出處放行（2026-08-19 BTC 稿）。
    中文沒有詞界可用，只能靠長度；拉丁有空白與大小寫，就該用整詞。
    """
    named = (named or "").strip()
    if not named:
        return False
    named_latin = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9.\-]{3,}", named)}
    named_cjk = re.findall(r"[一-鿿]{2,}", named)
    for k in known:
        if not k:
            continue
        k_latin = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9.\-]{3,}", k)}
        if named_latin & k_latin:
            return True
        # 「moneyweekly」對「moneyweekly.com.tw」：同一家，只是一邊帶網域後綴。
        # 用前綴比對而不是子字串——「coinshares」與「coinmetrics」共用 coin，
        # 但誰都不是誰的前綴，仍然分得開。
        for a in named_latin:
            for b in k_latin:
                if len(a) >= 5 and len(b) >= 5 and (a.startswith(b) or b.startswith(a)):
                    return True
        for run in named_cjk:
            for size in range(len(run), min_cjk - 1, -1):
                for i in range(len(run) - size + 1):
                    if run[i:i + size] in k:
                        return True
    return False


def _topic_words(sentence: str) -> set[str]:
    """句子裡的內容詞，用來判斷來源命中的位置是不是在講同一件事。"""
    text = str(sentence or "")
    words = set(re.findall(r"[A-Za-z][A-Za-z0-9.\-]{2,}", text))
    for run in re.findall(r"[一-鿿]{2,}", text):
        words |= {run[i:i + 2] for i in range(len(run) - 1)}
    return {w for w in words if w not in _TOPIC_STOPWORDS}


_TOPIC_STOPWORDS = {"根據", "數據", "機構", "研究", "顯示", "預估", "分別", "約為",
                    "超過", "高達", "以上", "以下", "其中", "另外", "目前", "這個",
                    "我們", "可以", "已經", "並且", "然而", "因此", "所以"}


def locate(value: str, unit: str, sources: list[dict], fact_block: str,
           topic: str = "") -> str | None:
    """在來源摘錄或事實表裡找這個「數字＋單位」，回傳可讀的定位字串。

    **必須連單位一起比對**。第一版只比裸數字，於是「50%」配到了來源裡的
    「1,350 億美元」、「15%」配到「合理本益比可給到 15 倍」——定位成功但
    完全不是同一件事。那種假信心比沒有閘門更危險，因為它會讓人停止懷疑。
    """
    unit_variants = {unit}
    if unit in ("%", "％"):
        unit_variants |= {"%", "％"}
    needles = [_norm(f"{value}{u}") for u in unit_variants]
    topic_terms = _topic_words(topic) if topic else set()
    best: tuple[int, str] | None = None   # (重疊詞數, 定位字串)
    for index, src in enumerate(sources, 1):
        excerpt = str(src.get("excerpt") or "")
        folded = _norm(excerpt)
        pos = next((folded.find(n) for n in needles if folded.find(n) >= 0), -1)
        if pos < 0:
            continue
        raw_pos = next((excerpt.find(f"{value}{u}") for u in unit_variants
                        if excerpt.find(f"{value}{u}") >= 0), -1)
        if raw_pos < 0:
            raw_pos = pos
        lo, hi = max(0, raw_pos - 24), min(len(excerpt), raw_pos + 40)
        ctx = re.sub(r"\s+", " ", excerpt[lo:hi]).strip()
        # 同樣的「N%」在不同段落講的常常是不同的事——實測「50%」命中的是
        # 「50% 以上的汽車搭載網路」、「10%」命中的是 2006 年的出貨比重。
        # 所以命中點附近必須出現原句的內容詞，否則只是巧合。
        overlap = 0
        if topic_terms:
            window = excerpt[max(0, raw_pos - 90):raw_pos + 90]
            overlap = len(topic_terms & _topic_words(window))
            if not overlap:
                continue
        located = (f"來源 #{index}（{src.get('publisher') or src.get('url', '')[:40]}）"
                   f"第 {raw_pos} 字：…{ctx}…")
        # 同一個「N%」可能在好幾個來源出現。挑跟原句重疊最多的那個，
        # 否則會停在第一個巧合上（實測「10%」先命中 2006 年的出貨比重，
        # 真正的出處是另一個來源裡的 Wi-Fi 7 採用率）。
        if best is None or overlap > best[0]:
            best = (overlap, located)
    if best:
        return best[1]
    folded_facts = _norm(fact_block)
    if any(n and n in folded_facts for n in needles):
        return "管線事實表"
    return None


_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def _resolve_inline_links(text: str, sources: list[dict]) -> str:
    """把 markdown 行內連結換成純文字，連結指向清單裡的來源就順便把出處名帶進來。

    稽核 agent 修 E1 的正當做法之一，就是去取材清單找到出處、用行內連結標上
    （2026-08-19 那篇模擬假說的稿子就是這樣修的）。但閘門直接對原始 markdown
    做比對，抓到的名字變成「[Campbell 提出的理論模型](https://www.prlog.or」，
    於是把一個**修對了**的句子繼續報成違規。差點讓我以為稽核在捏造來源。
    """
    known = {s.get("url", ""): (s.get("publisher") or "") for s in sources}

    def repl(m):
        label, url = m.group(1), m.group(2)
        for src_url, publisher in known.items():
            if src_url and (url.startswith(src_url[:60]) or src_url.startswith(url[:60])):
                return f"{label}（{publisher}）"
        return label

    return _MD_LINK.sub(repl, text or "")


def _is_derived_ratio(value: str, unit: str, fact_values: dict) -> bool:
    """這個百分比是不是由事實表裡兩個數字相除得到的。

    2026-08-19 的 Coinbase 稿寫「訂閱與服務營收 5.55 億，佔營收 45.5%」——
    5.55 ÷ 12.2 = 45.5%，算式完全正確，但 45.5 這個數字在事實表與來源裡
    都查不到，E3 因此判它無出處。推導出來的比率是分析，不是引用。
    """
    if unit not in ("%", "％") or not fact_values:
        return False
    try:
        pct = float(value)
    except ValueError:
        return False
    vals = [abs(v) for v in fact_values.values() if isinstance(v, (int, float)) and v]
    for a in vals:
        for b in vals:
            if b and abs(a / b * 100 - pct) <= 0.6:      # 0.6 個百分點內
                return True
    return False


def check(article_md: str, *, sources: list[dict] | None = None,
          fact_block: str = "", fact_values: dict | None = None) -> list[EvidenceIssue]:
    sources = sources or []
    article_md = _resolve_inline_links(article_md, sources)
    known = {_norm(s.get("publisher")) for s in sources}
    known |= {_norm(s.get("title")) for s in sources}
    known |= {_norm(name) for name in _FACT_PROVIDERS}
    known.discard("")
    issues: list[EvidenceIssue] = []

    for sentence in _SENTENCE.findall(article_md or ""):
        m = _ATTRIBUTION.search(sentence)
        if not m:
            continue
        named = (m.group(2) or m.group(3) or "").strip()
        # E1：具名的來源要真的在清單裡（用寬鬆包含比對，容得下「MIC 等研究機構」）
        if named and _looks_like_named_entity(named):
            named_norm = _norm(named)
            if named_norm and not _names_overlap(named_norm, known):
                issues.append(EvidenceIssue(
                    rule="E1 具名來源不在取材清單",
                    sentence=sentence,
                    detail=f"文中歸屬給「{named}」，但取材清單裡沒有對得上的來源。"
                           "補上真正的來源連結，或改寫成不具名的敘述。",
                ))
        # E2/E3：歸屬句裡的量要定位得到
        for value, unit in _QUANTITY.findall(sentence):
            if _is_derived_ratio(value, unit, fact_values or {}):
                continue
            where = locate(value, unit, sources, fact_block, topic=sentence)
            if where is None:
                issues.append(EvidenceIssue(
                    rule="E3 數字無法定位",
                    sentence=sentence,
                    detail=f"「{value}{unit}」在任何來源摘錄與事實表裡都找不到。"
                           "補來源、改寫成不帶數字的判斷，或整句刪除。",
                ))
    return issues


def report(article_md: str, *, sources: list[dict] | None = None,
           fact_block: str = "") -> str:
    """人看的稽核報告：逐條列出定位結果。"""
    lines: list[str] = []
    for sentence in _SENTENCE.findall(article_md or ""):
        if not _ATTRIBUTION.search(sentence):
            continue
        for value, unit in _QUANTITY.findall(sentence):
            where = locate(value, unit, sources or [], fact_block, topic=sentence)
            lines.append(f"  {'✅' if where else '❌'} {value}{unit} → "
                         f"{where or '找不到出處'}")
    return "\n".join(lines) or "  （文中沒有具名歸屬的數字）"


# --- E4 時效性 -------------------------------------------------------------
# 目標價、股價、市值、評等這類數字有保鮮期。2026-08-18 的聯發科稿引用了
# 2025-11 的報導（9 個月前）裡的「大摩目標價 1,288 元、高盛 1,400 元」，
# 而當時股價 3,885——等於暗示外資看空 65%。來源活著、也切題，就是過期。
_TIME_SENSITIVE = ("目標價", "評等", "調降", "調升", "降評", "升評", "股價",
                   "市值", "本益比", "outperform", "neutral", "buy", "sell")
_STALE_DAYS = 120
# E4 專用：時效性敘述裡的數字常帶「元」，而且會有千分位逗號（1,288 元）。
# 共用的 _QUANTITY 沒有「元」、也不吃逗號，所以 E4 第一版整個沒觸發。
_TS_QUANTITY = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(元|億|兆|%|％|倍)")


def stale_claims(article_md: str, sources: list[dict], *, max_age_days: int = _STALE_DAYS):
    """回傳「引用了過期來源的時效性敘述」。需要 sources 帶 age_days。"""
    from substack_radar.source_dates import age_days

    stale = []
    for src in sources or []:
        age = src.get("age_days")
        if age is None:
            age = age_days(str(src.get("url") or ""), str(src.get("excerpt") or ""))
        if age is not None and age > max_age_days:
            stale.append((src, age))
    if not stale:
        return []

    issues = []
    for sentence in _SENTENCE.findall(article_md or ""):
        if not any(k in sentence.lower() for k in _TIME_SENSITIVE):
            continue
        for value, unit in _TS_QUANTITY.findall(sentence):
            for src, age in stale:
                excerpt = str(src.get("excerpt") or "")
                folded = _norm(excerpt)
                pos = folded.find(_norm(f"{value}{unit}"))
                if pos < 0:
                    continue
                # 跟 locate() 一樣的教訓：裸數字比對會撞出巧合。瑞昱稿的
                # 49% 毛利率、30% ROE 來自 yfinance 事實表（當前資料），
                # 只是數字剛好也出現在舊來源裡，第一版 E4 全都誤報成過期。
                window = excerpt[max(0, pos - 90):pos + 90]
                if not (_topic_words(sentence) & _topic_words(window)):
                    continue
                if True:
                    issues.append(EvidenceIssue(
                        rule="E4 引用過期來源的時效性數字",
                        sentence=sentence,
                        detail=f"「{value}{unit}」出自 {src.get('publisher')}（{age} 天前）。"
                               "目標價／評等／股價／市值有保鮮期——改寫成明確的歷史敘述"
                               f"（例：「{src.get('published_on') or f'{age // 30} 個月前'}時外資給的目標價是…」）"
                               "並對照當前數字，或整段刪除。",
                    ))
                    break
    return issues
