"""繁體中文（台灣用法）大陸用語修正 — 單一真相來源。

2026-06-02：原本 `_MAINLAND_TERMS` 只住在 `substack_radar/composer.py`、只作用於
Substack 草稿。meta 三平台（IG/FB/Threads）的貼文與圖卡文字完全沒有這道後處理。
把詞表與修正核心抽到這裡，讓 substack 與 meta 共用同一張表，避免兩套漂移。

設計原則（沿用 substack 既有 Optimization B，2026-05-30）：
  - 只「自動替換」**無歧義**的詞——blind replace 不論語境都正確者。
  - replacement 含「／」(例：互聯網→網際網路／網路) → 不自動換，只在 audit 警告，
    由人/LLM 選正確的那一個。
  - 語境敏感詞 (數據/質量/智能/移動/用戶/文件/視頻邊界…) 一律**不收**，永不誤傷。

2026-06-02 借 `taiwan-traditional-chinese` 詞庫補強，但嚴格 curate：凡在台灣金融／
一般語境會撞詞的一律排除（主板=股市主板、隊列=球隊列、字符=數字符號、溢出=水溢出、
補丁=衣服補丁、評論/社區/設備/通信/文本/字體=台灣本就通用），詳見下方註解。
"""
from __future__ import annotations

import re
from typing import List, Tuple

# (found_term, suggested_replacement, category)
# replacement 含「／」者 = 歧義，只警告不自動換。
MAINLAND_TERMS: List[Tuple[str, str, str]] = [
    # 人名
    ("特朗普",   "川普",            "人名"),
    ("奧巴馬",   "歐巴馬",          "人名"),
    ("默克爾",   "梅克爾",          "人名"),
    ("扎克伯格", "祖克柏",          "人名"),
    ("普京",     "普丁",            "人名"),
    ("澤連斯基", "澤倫斯基",        "人名"),
    ("內塔尼亞胡","納坦雅胡",        "人名"),
    ("默多克",   "梅鐸",            "人名"),
    ("朔爾茨",   "蕭茲",            "人名"),
    ("馬克龍",   "馬克宏",          "人名"),
    # 資訊／網路／軟體（高 priority）
    ("互聯網",   "網際網路／網路",  "資訊"),
    ("視頻",     "影片",            "資訊"),
    ("軟件",     "軟體",            "資訊"),
    ("硬件",     "硬體",            "資訊"),
    ("屏幕",     "螢幕",            "資訊"),
    ("服務器",   "伺服器",          "資訊"),
    ("數據庫",   "資料庫",          "資訊"),
    ("文件夾",   "資料夾",          "資訊"),
    ("程序員",   "工程師",          "資訊"),
    ("算法",     "演算法",          "資訊"),
    ("內存",     "記憶體",          "資訊"),
    ("帶寬",     "頻寬",            "資訊"),
    ("接口",     "介面",            "資訊"),
    ("模塊",     "模組",            "資訊"),
    ("鏈接",     "連結",            "資訊"),
    ("點贊",     "按讚",            "資訊"),
    ("登錄",     "登入",            "資訊"),
    ("賬號",     "帳號",            "資訊"),
    ("賬戶",     "帳戶",            "資訊"),
    ("默認",     "預設",            "資訊"),
    ("缺省",     "預設",            "資訊"),
    ("設置",     "設定",            "資訊"),
    ("兼容",     "相容",            "資訊"),
    ("並發",     "並行",            "資訊"),
    ("性能",     "效能",            "資訊"),
    ("反饋",     "回饋",            "資訊"),
    ("標簽",     "標籤",            "資訊"),
    ("在線",     "線上",            "資訊"),
    ("黑客",     "駭客",            "資訊"),
    # 商業／市場
    ("創始人",   "創辦人",          "商業"),
    ("短信",     "簡訊",            "商業"),
    # 度量
    ("千米",     "公里",            "度量"),
    ("厘米",     "公分",            "度量"),
    ("千克",     "公斤",            "度量"),
    # ---- 2026-06-02 補充（借 taiwan-traditional-chinese 詞庫，嚴格 curate）----
    # 較長的複合詞排在前面，避免子字串先被較短規則吃掉。
    ("字符串",   "字串",            "資訊"),
    ("源代碼",   "原始碼",          "資訊"),
    ("操作系統", "作業系統",        "資訊"),
    ("配置文件", "設定檔",          "資訊"),
    ("解釋器",   "直譯器",          "資訊"),
    ("對話框",   "對話方塊",        "資訊"),
    ("剪貼板",   "剪貼簿",          "資訊"),
    ("快捷鍵",   "快速鍵",          "資訊"),
    ("滾動條",   "捲軸",            "資訊"),
    ("客戶端",   "用戶端",          "資訊"),
    ("分辨率",   "解析度",          "資訊"),
    ("固件",     "韌體",            "資訊"),
    ("鼠標",     "滑鼠",            "資訊"),
    ("光標",     "游標",            "資訊"),
    ("字節",     "位元組",          "資訊"),
    ("哈希",     "雜湊",            "資訊"),
    ("堆棧",     "堆疊",            "資訊"),
    ("遞歸",     "遞迴",            "資訊"),
    ("枚舉",     "列舉",            "資訊"),
    ("插件",     "外掛",            "資訊"),
    ("圖標",     "圖示",            "資訊"),
    ("線程",     "執行緒",          "資訊"),
    # 一般／消費科技
    ("打印機",   "印表機",          "一般"),
    ("打印",     "列印",            "一般"),
    ("智能手機", "智慧型手機",      "一般"),
    ("硅谷",     "矽谷",            "一般"),
    ("充值",     "儲值",            "一般"),
    # 故意排除（語境敏感／會撞台灣用語）：
    #   主板(股市主板)、隊列(球隊列表)、字符(數字符號)、溢出(水溢出)、補丁(衣服補丁)、
    #   變量/常量(改變量/正常量)、評論/社區/設備/通信/文本/字體/渲染(台灣本就通用)、
    #   數據/質量/智能/移動/用戶/文件(一對多，需語境)。
]


_OPENCC = None


def _cc():
    """Lazy OpenCC s2tw converter (Simplified → Traditional, Taiwan variants).
    Returns False if opencc isn't installed so callers degrade to identity."""
    global _OPENCC
    if _OPENCC is None:
        try:
            import opencc  # type: ignore
            _OPENCC = opencc.OpenCC("s2tw")
        except Exception:  # noqa: BLE001
            _OPENCC = False
    return _OPENCC


def to_traditional(text: str) -> str:
    """Deterministic Simplified→Traditional (Taiwan) — the hard backstop so no
    Simplified Chinese ever ships, regardless of which LLM/provider wrote it
    (fallback models sometimes ignore the '繁體' instruction). OpenCC s2tw is a
    character-level conversion (实→實, 远→遠); curated VOCAB stays with
    MAINLAND_TERMS below. Identity if opencc is unavailable."""
    if not text:
        return text
    cc = _cc()
    return cc.convert(text) if cc else text


def fix_mainland_text(text: str) -> Tuple[str, List[str]]:
    """繁化（簡→繁台灣）+ 大陸→台灣用語修正。回傳 (修正後字串, 修正訊息列表)。

    先用 OpenCC s2tw 把簡體字體強制轉成台灣繁體（最後防線），再跑詞表：跳過
    「／」歧義詞；當 found 是自身 repl 的子字串時 (算法 ⊂ 演算法)，用負向後查避免
    重複替換 (演算法 → 演演算法)。語境敏感詞不在表內，永不誤傷。
    """
    if not text:
        return text, []
    fixes: List[str] = []
    trad = to_traditional(text)
    if trad != text:
        fixes.append("[繁化] 簡體→台灣繁體")
        text = trad
    for found, repl, category in MAINLAND_TERMS:
        if "／" in repl:
            continue  # 歧義 — 留給 audit 警告
        if found not in text:
            continue
        if found in repl:
            prefix = repl.split(found)[0]
            if prefix:
                pat = re.compile(f"(?<!{re.escape(prefix)}){re.escape(found)}")
                new_text, cnt = pat.subn(repl, text)
                if cnt:
                    text = new_text
                    fixes.append(f"[TW:{category}]『{found}』×{cnt}→『{repl}』")
                continue
        cnt = text.count(found)
        text = text.replace(found, repl)
        fixes.append(f"[TW:{category}]『{found}』×{cnt}→『{repl}』")
    return text, fixes
