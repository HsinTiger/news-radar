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
_VALUE_WITH_UNIT = re.compile(rf"(?<![\d.])(\d{{1,3}}(?:,\d{{3}})+|\d+(?:\.\d+)?)\s*({_UNIT_RE})")

_TOLERANCE = 0.03          # 3%：容得下四捨五入與抓取時間差
# 只查 10 倍的誤植。真正的量級災難就是「十億 vs 億」這一種（14.39 → 143.9）。
# 原本連 100 倍、1000 倍也查，結果 2026-08-18 的聯發科稿把「120 億美元」的
# ASIC 市場規模比對到台幣淨利 1,181 億（×10 差 1.6%，在容差內）、把「10 億
# 美元」比對到營業利益（×100）。純屬數字巧合，卻白燒掉一輪稽核。
_WRONG_SCALES = (0.1, 10.0)
# 外幣金額不比對：事實表是公司自己的本位幣，稿子裡的「億美元」多半在講別人
# 的市場規模或同業，拿來跟本國幣值比對只會撞出巧合。
_FOREIGN_CURRENCY = ("美元", "美金", "USD", "日圓", "日元", "歐元", "EUR", "人民幣", "港幣")


@dataclass(frozen=True)
class ScaleIssue:
    written: float
    unit: str
    absolute: float
    expected: float
    fact_label: str
    context: str

    def __str__(self) -> str:
        ratio = self.expected / self.absolute if self.absolute else 0
        return (f"「{self.written:g} {self.unit}」對到事實「{self.fact_label}」，"
                f"但量級差 {ratio:g} 倍（應為 {self.expected / _UNITS[self.unit]:,.4g} {self.unit}）"
                f"｜…{self.context}…")


def article_amounts(text: str) -> list[tuple[float, str, float, str]]:
    """回傳 (寫出來的數字, 單位, 絕對值, 上下文)。"""
    out = []
    for m in _VALUE_WITH_UNIT.finditer(text or ""):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if v == 0:
            continue
        unit = m.group(2)
        # 單位後面緊接著外幣就跳過（「120 億美元」不是本位幣金額）。
        tail = text[m.end():m.end() + 4]
        if any(cur in tail for cur in _FOREIGN_CURRENCY):
            continue
        lo, hi = max(0, m.start() - 16), min(len(text), m.end() + 16)
        out.append((v, unit, v * _UNITS[unit], re.sub(r"\s+", " ", text[lo:hi]).strip()))
    return out


def reconcile(article_md: str, fact_values: dict[str, float]) -> list[ScaleIssue]:
    """``fact_values``：標籤 → 絕對數值（例如 {"2025 營業利益": 1.439e10}）。

    只有當稿子裡的金額「用錯誤倍率對上某個已知事實」時才回報。
    正確的數字、以及跟事實表無關的數字，都不會出現在結果裡。
    """
    facts = {k: float(v) for k, v in (fact_values or {}).items()
             if isinstance(v, (int, float)) and v}
    if not facts:
        return []
    issues: list[ScaleIssue] = []
    for written, unit, absolute, context in article_amounts(article_md):
        if any(abs(absolute - f) <= abs(f) * _TOLERANCE for f in facts.values()):
            continue  # 對得上，正確
        hit = None
        for scale in _WRONG_SCALES:
            for label, f in facts.items():
                if abs(absolute * scale - f) <= abs(f) * _TOLERANCE:
                    hit = (label, f)
                    break
            if hit:
                break
        if hit:
            issues.append(ScaleIssue(written=written, unit=unit, absolute=absolute,
                                     expected=hit[1], fact_label=hit[0], context=context))
    return issues
