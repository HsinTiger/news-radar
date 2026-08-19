"""數字對帳：把稿子裡的數字跟管線給模型的事實表比對，專抓量級錯誤。

為什麼需要這一道
----------------
2026-08-18 的瑞昱稿寫「2025 年的營業利益卻僅有 **14.39 億**新臺幣」，正確是
**143.9 億**——差 10 倍，而且跟它自己上一段講的營益率 11.7% 互相矛盾
（1,227 億 × 11.7% ≈ 144 億）。

稽核當下我在回報時把它更正成「1,439 億」，**同樣錯了 10 倍**，只是方向相反。
一個人、一個模型盯著同一串數字都會滑掉；兩個 LLM 只會一起點頭。這種錯要靠
程式對帳，不能靠再讀一遍。

設計原則：**只在「這個數字明顯是某個已知事實的錯誤量級」時才報**。
對不上任何已知事實的數字一律放過（可能來自推論、外部來源、常識），
因為誤報一次就會讓人把整個閘門關掉。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 中文金額單位 → 絕對倍率。稿子習慣用「億」，事實表用原始數值。
_UNITS = {"兆": 1e12, "千億": 1e11, "百億": 1e10, "億": 1e8, "千萬": 1e7, "百萬": 1e6, "萬": 1e4}
_UNIT_RE = "|".join(sorted(_UNITS, key=len, reverse=True))
# lookbehind 要連逗號一起排除，數字本身也要吃得下「1,521.83」這種千分位＋小數。
# 舊版把「第二季營收 1,521.83 億元」切成「521.83 億」，然後拿去跟年營收比對，
# 報出一個不存在的 10 倍誤植（2026-08-18 聯發科第三次實跑）。
# 負號要一起吃進來。季度序列進事實表之後（2026-08-19），虧損是常態：
# 稿子寫「營業利益轉負為 -0.3 億美元」是對的（Q2 = -0.03B），但正則只抓到
# 「0.3 億」＝ +3e7，對不上事實裡的 -3e7，接著誤配到另一季的 +3e8，
# 報出一個不存在的 10 倍誤植。虧損的公司整篇都會這樣。
_VALUE_WITH_UNIT = re.compile(
    rf"(?<![\d.,])([-−﹣－]?\s?)(\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*({_UNIT_RE})"
)

_TOLERANCE = 0.03          # 3%：判定「錯誤量級」時要嚴，才不會亂報
# 判定「這個數字是對的」時要寬。市值與股價每天在動，稿子寫「超過 6 兆」對上
# 6.2 兆是正常寫法；舊版用 3% 判斷，差 3.24% 就被推去比對錯誤量級，於是報出
# 「6 兆對到 2025 營收、差 0.1 倍」這種荒謬結果。
_MATCH_TOLERANCE = 0.15
# 只查 10 倍的誤植。真正的量級災難就是「十億 vs 億」這一種（14.39 → 143.9）。
# 原本連 100 倍、1000 倍也查，結果 2026-08-18 的聯發科稿把「120 億美元」的
# ASIC 市場規模比對到台幣淨利 1,181 億（×10 差 1.6%，在容差內）、把「10 億
# 美元」比對到營業利益（×100）。純屬數字巧合，卻白燒掉一輪稽核。
_WRONG_SCALES = (0.1, 10.0)
# 外幣金額不比對：稿子裡的外幣多半在講別人的市場規模或同業，跟本位幣比對
# 只會撞出巧合。但**哪個是外幣要看公司本位幣**——第一版寫死「美元＝外幣」，
# 對台股沒錯，對 Coinbase（本位幣就是 USD）就等於把整篇的金額全部略過，
# 「Q2 營收 122 億美元」這種 10 倍誤植完全抓不到（2026-08-19 實測）。
_CURRENCY_WORDS = {
    "TWD": ("台幣", "臺幣", "新台幣", "新臺幣", "NTD", "TWD"),
    "USD": ("美元", "美金", "USD"),
    "JPY": ("日圓", "日元", "JPY"),
    "EUR": ("歐元", "EUR"),
    "CNY": ("人民幣", "CNY", "RMB"),
    "HKD": ("港幣", "HKD"),
}


def _foreign_words(base_currency: str) -> tuple:
    base = (base_currency or "TWD").upper()
    return tuple(w for code, words in _CURRENCY_WORDS.items()
                 if code != base for w in words)


# 事實標籤裡的指標名。稿子那句話要提到同一個指標，數字比對才算數——
# 「平均 USDC 餘額創下 200 億美元新高」跟「2022 營業利益 -19.5 億」在數學上
# 差剛好 10 倍，但它們根本不是同一件事（2026-08-19 Coinbase 稿實測）。
_METRIC_WORDS = ("營收", "營業利益", "淨利", "毛利", "市值", "價格", "成交額",
                 "流入", "流出", "發行", "獲利", "虧損", "收入")


def _metric_of(label: str) -> str:
    for word in _METRIC_WORDS:
        if word in (label or ""):
            return word
    return ""


@dataclass(frozen=True)
class ScaleIssue:
    written: float
    unit: str
    absolute: float
    expected: float
    fact_label: str
    context: str

    def __str__(self) -> str:
        ratio = abs(self.expected / self.absolute) if self.absolute else 0
        return (f"「{self.written:g} {self.unit}」對到事實「{self.fact_label}」，"
                f"但量級差 {ratio:g} 倍（應為 {abs(self.expected) / _UNITS[self.unit]:,.4g} {self.unit}）"
                f"｜…{self.context}…")


def article_amounts(text: str, base_currency: str = "TWD") -> list[tuple[float, str, float, str]]:
    """回傳 (寫出來的數字, 單位, 絕對值, 上下文)。``base_currency`` 決定哪些算外幣。"""
    out = []
    for m in _VALUE_WITH_UNIT.finditer(text or ""):
        try:
            v = float(m.group(2).replace(",", ""))
        except ValueError:
            continue
        if v == 0:
            continue
        if m.group(1).strip():          # 負號
            v = -v
        unit = m.group(3)
        # 單位後面緊接著外幣就跳過（「120 億美元」不是本位幣金額）。
        tail = text[m.end():m.end() + 4]
        if any(cur in tail for cur in _foreign_words(base_currency)):
            continue
        lo, hi = max(0, m.start() - 16), min(len(text), m.end() + 16)
        context = re.sub(r"\s+", " ", text[lo:hi]).strip()
        # 中文常用詞代替負號：「虧損 6.7 億」「淨流出 4.1 億」「減少 3 億」。
        if v > 0 and re.search(r"(虧損|淨損|流出|減少|下滑|負)\s*$",
                               text[max(0, m.start() - 8):m.start()]):
            v = -v
        out.append((v, unit, v * _UNITS[unit], context))
    return out


def reconcile(article_md: str, fact_values: dict[str, float],
              base_currency: str = "TWD") -> list[ScaleIssue]:
    """``fact_values``：標籤 → 絕對數值（例如 {"2025 營業利益": 1.439e10}）。

    只有當稿子裡的金額「用錯誤倍率對上某個已知事實」時才回報。
    正確的數字、以及跟事實表無關的數字，都不會出現在結果裡。
    """
    facts = {k: float(v) for k, v in (fact_values or {}).items()
             if isinstance(v, (int, float)) and v}
    if not facts:
        return []
    issues: list[ScaleIssue] = []
    # 一律比絕對值。這個閘門查的是**量級**，正負號不是它的事——而且季度序列
    # 進來之後，虧損公司整篇都是負數，靠正則猜正負號只會製造新的誤報
    # （「最近 3 季分別虧損 6.7 億、3.9 億與 3.6 億」只有第一個數字前面有「虧損」）。
    for written, unit, absolute, context in article_amounts(article_md, base_currency):
        mag = abs(absolute)
        if any(abs(mag - abs(f)) <= abs(f) * _MATCH_TOLERANCE for f in facts.values()):
            continue  # 對得上（含合理的四捨五入與盤中變動），正確
        hit = None
        for scale in _WRONG_SCALES:
            for label, f in facts.items():
                if abs(mag * scale - abs(f)) > abs(f) * _TOLERANCE:
                    continue
                # 同一個指標才算。沒有這一層，任何兩個差 10 倍的數字都會配成一對。
                metric = _metric_of(label)
                if metric and metric not in context:
                    continue
                hit = (label, f)
                break
            if hit:
                break
        if hit:
            issues.append(ScaleIssue(written=written, unit=unit, absolute=absolute,
                                     expected=hit[1], fact_label=hit[0], context=context))
    return issues
