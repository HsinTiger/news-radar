"""對帳式審稿（2026-11-16，信哥：審稿流程去蕪存菁，維持品質的前提下讓稿子一次就過）。

舊的審稿是「請模型挑毛病」。翻 499 條它自己提的意見：
- 約四成是「素材裡沒有」——它只看得到素材開頭，後面的內容全被當成沒出處，逼寫手把對的東西刪掉；
- 約兩成五是背景知識上的挑剔（「豐田生產方式的核心還包括…」），不影響讀者，改了又引來新的挑剔；
- 真正有價值的只有一類：草稿跟素材寫的不一樣（AWS 的成長率寫成亞馬遜整體、明年底寫成今年底）。

所以分工改成：
- 「有沒有出處」「格式」「篇幅」「開場」→ 程式查（看得到全文、不會累、不會每次答案不同）。
- 模型只做程式做不到的那一件事：找「草稿跟素材矛盾」的句子，而且每一條都要照抄素材原文當證據。
- 證據由程式驗：照抄的那段素材原文真的在素材裡、指的那句真的在草稿裡，才算數。
  驗不過的意見直接丟掉，不會送到寫手面前。

同一份草稿交給兩個不同家的開源模型同時查、取聯集：一個模型單獨查一次會漏
（2026-11-14 那篇第一輪只挑出 3 條，第三輪才又看到另外 5 條），兩個一起查不多花時間。
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time

MAX_ITEMS = int(os.getenv("SUBSTACK_FACT_AUDIT_MAX_ITEMS", "8"))
DEADLINE_S = int(os.getenv("SUBSTACK_FACT_AUDIT_DEADLINE_S", "900"))
# 第一個模型回來之後，最多再等其他模型這麼久。慢的那個趕不上就算了，不讓它拖住整篇稿。
# 2026-11-16 實跑：glm 69 秒就回，mistral 等了 420 秒還沒回，白等 7 分鐘 → 改成 180 秒。
GRACE_S = int(os.getenv("SUBSTACK_FACT_AUDIT_GRACE_S", "180"))

# 模型怎麼選（2026-11-16 毒化測試：在定稿裡埋 5 個跟逐字稿矛盾的錯，同一份 3.6 萬 token 的題目）：
#   glm-5.3-flash（NIM）      5/5，123 秒（跟別人同時跑時 440 秒）
#   mistral-large-4（NIM）    5/5，754 秒
#   gemini-2.5-flash          5/5，54 秒（不是開源模型，放備援：兩個開源的都沒回才用）
#   deepseek-v4.1-flash、step-5-preview（NIM）  超過 16 分鐘沒回——原本的審稿第一、三順位
# 抓錯能力四個都一樣，差在會不會回、多久回，所以預設用兩個會回的開源模型。
DEFAULT_AUDITORS = "glm-5.3-flash,mistral-large-4-1t-a52b"
DEFAULT_FALLBACK = "gemini-2.5-flash"

PROMPT = """你是事實查核員，只做一件事：找出草稿裡「跟素材寫的不一樣」的句子。

怎樣才算：
- 數字、日期、期限寫錯（素材說明年底，草稿寫今年底）
- 張冠李戴（A 說的話寫成 B 說的；部門的數字寫成整間公司的）
- 因果或方向寫反、程度寫過頭（素材說「可能」，草稿寫成已經發生）
- 人名、公司名、產品名寫錯。注意：逐字稿是語音辨識轉的，專有名詞常聽錯；拼法以「研究來源」為準，
  草稿跟研究來源一致、只是跟逐字稿不一樣的，不算錯

每一條都必須附「素材原文」：從下面的素材或研究來源裡**照抄**能證明草稿寫錯的那一段
（15–200 個字，一個字都不要改、不要翻譯、不要摘要）。抄不出原文的就不要列。

不要列：
- 素材裡找不到出處的內容（另外有程式在查）
- 文風、結構、觀點、可以寫得更精確的措辭
- 只靠你自己的背景知識才知道的事（素材沒寫的，你不能確定）

最多 {max_items} 條，最嚴重的排前面。一條都沒有就回空陣列——這很正常，不要硬湊。

=== 素材與研究來源 ===
{pack}

=== 草稿 ===
{article}

=== 輸出格式（最高優先）===
只輸出一個 JSON 物件，不要有其他文字：
{{"items": [{{"原句": "草稿裡寫錯的那一句（照抄）", "素材原文": "照抄的素材原文", "問題": "一句話說哪裡不一樣", "建議改法": "該往哪個方向改"}}]}}
"""


def build_prompt(article: str, pack: str) -> str:
    return PROMPT.format(max_items=MAX_ITEMS, pack=pack, article=article)


def parse(raw: str) -> list[dict]:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0), strict=False)
    except ValueError:
        return []
    items = data.get("items") if isinstance(data, dict) else None
    return [i for i in items or [] if isinstance(i, dict)]


_PUNCT = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "「": '"', "」": '"', "『": '"', "』": '"',
                        "，": ",", "。": ".", "：": ":", "；": ";", "！": "!", "？": "?", "（": "(", "）": ")"})


def _norm(text: str) -> str:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text or "")        # 連結只留文字
    text = text.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")
    text = re.sub(r"[*_`#>]", "", text.translate(_PUNCT))
    return re.sub(r"\s+", "", text).lower()


def coverage(quote: str, haystack_norm: str) -> float:
    """quote 有多少比例找得到（切成小段逐段找，容許模型抄的時候漏一兩個字）。"""
    q = _norm(quote)
    if not q:
        return 0.0
    if q in haystack_norm:
        return 1.0
    cjk = len(re.findall(r"[一-鿿]", q)) > len(q) * 0.3
    size = 8 if cjk else 18
    if len(q) < size:
        return 0.0
    windows = [q[i:i + size] for i in range(0, len(q) - size + 1, max(1, size // 2))]
    return sum(1 for w in windows if w in haystack_norm) / len(windows)


# 「素材裡沒有」不是這一關的工作（程式查得比較準），模型硬要報，附的證據也只是不相干的原文
_ABSENCE = re.compile(r"(素材|原文|來源|逐字稿|節目)[^，。｜]{0,12}(沒有|未|並無|無)(提|說|出現|記載|見|引用|包含)|"
                      r"找不到|無出處|無來源|未經證實|無法(查證|核對|確認)|不在(素材|原文|事實表)")


def verify(items: list[dict], article: str, material: str) -> tuple[list[dict], list[tuple[dict, str]]]:
    """程式驗證審稿意見。回 (算數的, [(被丟掉的, 原因)])。"""
    art, mat = _norm(article), _norm(material)
    kept, dropped, seen = [], [], set()
    for it in items:
        sentence = str(it.get("原句") or "").strip()
        evidence = str(it.get("素材原文") or "").strip()
        if _ABSENCE.search(str(it.get("問題") or "")):
            dropped.append((it, "講的是「素材沒有」，不是矛盾"))
            continue
        if len(_norm(sentence)) < 6 or coverage(sentence, art) < 0.6:
            dropped.append((it, "指的那句不在草稿裡"))
            continue
        if len(_norm(evidence)) < 10:
            dropped.append((it, "沒有附素材原文"))
            continue
        if coverage(evidence, mat) < 0.6:
            dropped.append((it, "附的素材原文在素材裡找不到"))
            continue
        if coverage(evidence, art) >= 0.9 and coverage(sentence, mat) >= 0.9:
            dropped.append((it, "草稿跟素材寫的一樣"))
            continue
        key = _norm(sentence)[:40]
        if key in seen:
            continue
        seen.add(key)
        kept.append(it)
    return kept, dropped


def default_models() -> list[str]:
    return [m.strip() for m in os.getenv("SUBSTACK_FACT_AUDITORS", DEFAULT_AUDITORS).split(",") if m.strip()]


def fallback_models() -> list[str]:
    return [m.strip() for m in os.getenv("SUBSTACK_FACT_AUDIT_FALLBACK", DEFAULT_FALLBACK).split(",") if m.strip()]


def _default_chat(model: str, prompt: str) -> str:
    from substack_radar.quality_loop import _logged, _oss_chat_impl
    return _logged("gateway", _oss_chat_impl, model, model, prompt, DEADLINE_S)


def run(article: str, pack: str, *, models: list[str] | None = None, chat=None,
        deadline_s: int | None = None, grace_s: int | None = None,
        fallback: list[str] | None = None) -> tuple[list[dict], dict]:
    """兩個模型同時查、取聯集、程式驗證。回 (算數的意見, 摘要)。

    摘要 {"answered": [...], "failed": {...}, "raw": n, "dropped": n}；全部模型都沒回應時
    answered 是空的——呼叫端要當成「這一輪沒有第二雙眼睛」，不能當成「查過沒問題」。"""
    if fallback is None:
        fallback = fallback_models() if models is None else []
    models = models or default_models()
    chat = chat or _default_chat
    deadline_s = DEADLINE_S if deadline_s is None else deadline_s
    grace_s = GRACE_S if grace_s is None else grace_s
    prompt = build_prompt(article, pack)
    results: queue.Queue = queue.Queue()

    def work(model: str) -> None:
        t0 = time.time()
        try:
            results.put((model, chat(model, prompt), None, time.time() - t0))
        except Exception as exc:                      # 一個模型掛了不影響另一個
            results.put((model, "", exc, time.time() - t0))

    for model in models:
        threading.Thread(target=work, args=(model,), daemon=True, name=f"fact-audit-{model}").start()

    raw_items: list[dict] = []
    summary = {"answered": [], "failed": {}, "raw": 0, "dropped": 0}
    end = time.time() + deadline_s
    for _ in models:
        try:
            model, raw, exc, secs = results.get(timeout=max(1.0, end - time.time()))
        except queue.Empty:
            break
        if exc is not None:
            summary["failed"][model] = str(exc)[:80]
            print(f"[FactAudit] ⚠️ {model} 沒有回應（{str(exc)[:80]}）")
            continue
        items = parse(raw)
        for it in items:
            it["查核模型"] = model
        raw_items += items
        if not summary["answered"]:
            end = min(end, time.time() + grace_s)     # 有人回了：其他人最多再等 grace_s
        summary["answered"].append(model)
        print(f"[FactAudit] {model} 回了 {len(items)} 條（{secs:.0f} 秒）")
    for model in models:
        if model not in summary["answered"] and model not in summary["failed"]:
            summary["failed"][model] = "太慢，沒等它"
            print(f"[FactAudit] ⏱️ {model} 太慢，不等了")
    # 兩個都沒回：換備援模型查一次，不要讓這篇稿完全沒人對過帳
    for model in ([] if summary["answered"] else fallback):
        t0 = time.time()
        try:
            items = parse(chat(model, prompt))
        except Exception as exc:
            summary["failed"][model] = str(exc)[:80]
            print(f"[FactAudit] ⚠️ 備援 {model} 也沒有回應（{str(exc)[:80]}）")
            continue
        for it in items:
            it["查核模型"] = model
        raw_items += items
        summary["answered"].append(model)
        print(f"[FactAudit] 備援 {model} 回了 {len(items)} 條（{time.time() - t0:.0f} 秒）")
        break

    kept, dropped = verify(raw_items, article, pack)
    for it, why in dropped:
        print(f"[FactAudit] ⏭️ 丟掉（{why}）：{str(it.get('問題') or '')[:60]}")
    summary["raw"], summary["dropped"] = len(raw_items), len(dropped)
    return kept[:MAX_ITEMS], summary
