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

同一份草稿交給兩個不同家的模型同時查、取聯集：一個模型單獨查一次會漏
（2026-11-14 那篇第一輪只挑出 3 條，第三輪才又看到另外 5 條），兩個一起查不多花時間。
寫手同一家的模型不會被派去審自己。模型怎麼選見下面。
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
#   glm-5.3-flash（NIM）      5/5，31–145 秒（四個 NIM 請求同時跑時 440 秒）
#   gemini-2.5-flash          5/5，54 秒
#   gemini-3.5-flash          5/5，20 秒
#   mistral-large-4（NIM）    5/5，754 秒；拿真的第一稿再測一次，等了 1,529 秒後回空白
#   step-5-preview（NIM）     5/5，1,351 秒
#   deepseek-v4.1-flash（NIM）等了 1,833 秒後回空白——原本審稿的第一順位
#   glm-5.3（NIM）            閘道實際換成 glm-5.3-flash 作答，等於同一個
# 抓錯能力大家一樣，差在會不會回、多久回。NIM 上來得及用的開源模型只有 glm-5.3-flash 一個。
# 信哥 2026-11-16 同意加 gemini-2.5-flash 當常態的第二個查核員（不是開源，但免費、不同家、約一分鐘）：
# 兩個同時查、取聯集。備援 gemini-3.5-flash（配額跟 2.5 分開算）在有人被排除或都沒回時補位。
DEFAULT_AUDITORS = "glm-5.3-flash,gemini-2.5-flash"
DEFAULT_FALLBACK = "gemini-3.5-flash"

PROMPT = """你是事實查核員，只做一件事：找出草稿裡「跟素材寫的不一樣」的句子。

怎樣才算：
- 數字、日期、期限寫錯（素材說明年底，草稿寫今年底）
- 張冠李戴（A 說的話寫成 B 說的；部門的數字寫成整間公司的）
- 因果或方向寫反、程度寫過頭（素材說「可能」，草稿寫成已經發生）
- 人名、公司名、產品名寫錯。注意：逐字稿是語音辨識轉的，專有名詞常聽錯；拼法以「素材標題」和「研究來源」為準，
  草稿跟標題或研究來源一致、只是跟逐字稿不一樣的，不算錯

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


def loads_lenient(raw: str):
    """模型交的 JSON 常常少最後一個括號（2026-11-16：gemini-2.5-flash 同一題跑三次，有一次結尾是
    「…}]」少了最外層的「}」，原本的解析就把五條發現整份當成「沒有問題」）。補得回來就補。"""
    m = re.search(r"\{.*", raw or "", re.S)
    if not m:
        return None
    text = re.sub(r"```\s*$", "", m.group(0).strip()).strip()
    for tail in ("", "}", "]}", '"}]}'):
        try:
            return json.loads(text + tail, strict=False)
        except ValueError:
            continue
    end = text.rfind("}")                      # 尾巴多了別的字：往前找到能解析的地方
    while end > 0:
        try:
            return json.loads(text[:end + 1], strict=False)
        except ValueError:
            end = text.rfind("}", 0, end)
    return None


def salvage(raw: str, key: str) -> list[dict]:
    """整份解析不了時，把裡面一個一個完整的物件撈出來（只要有 key 這個欄位）。"""
    out = []
    for m in re.finditer(r"\{[^{}]*\}", raw or "", re.S):
        try:
            obj = json.loads(m.group(0), strict=False)
        except ValueError:
            continue
        if isinstance(obj, dict) and key in obj:
            out.append(obj)
    return out


def parse(raw: str) -> list[dict]:
    data = loads_lenient(raw)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        items = salvage(raw, "原句")
    return [i for i in items if isinstance(i, dict)]


def unreadable(raw: str) -> bool:
    """有回字、看起來也有發現，卻一條都解析不出來＝格式壞掉。這不能當成「查過沒問題」。"""
    return bool((raw or "").strip()) and not parse(raw) and loads_lenient(raw) is None


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
                      r"(未|並未|沒有|沒)(提及|提到|說到|記載)|"
                      r"找不到|無出處|無來源|未經證實|無法(查證|核對|確認)|不在(素材|原文|事實表)")


_NAME_ISSUE = re.compile(r"人名|公司名|名稱|專有名詞|拼|寫法|縮寫|講者名|來賓名|名字")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9.\-]{1,}")


def _sounds_alike(a: str, b: str) -> bool:
    from difflib import SequenceMatcher
    a, b = a.lower(), b.lower()
    return a.startswith(b) or b.startswith(a) or SequenceMatcher(None, a, b).ratio() >= 0.5


def misheard_name(sentence: str, evidence: str) -> bool:
    """草稿的英文詞跟素材原文裡某個英文詞長得像（GPU／GP、Ara／Era、Cline／Klein）＝在爭拼法。"""
    ev = [w for w in _LATIN.findall(evidence or "") if len(w) >= 2]
    disputed = [w for w in _LATIN.findall(sentence or "") if len(w) >= 2 and w.lower() not in (evidence or "").lower()]
    return any(_sounds_alike(d, e) for d in disputed for e in ev)


def _attested_spelling(sentence: str, evidence: str, material: str) -> bool:
    """草稿裡被說「寫錯」的英文名字，其實在素材別處（影片標題、研究來源、逐字稿其他段落）出現過。

    2026-11-17 智取未來：逐字稿是語音辨識轉的，把 Cline 聽成 Klein、GPU 聽成 GP，兩個查核員都照逐字稿
    叫寫手改錯——影片標題明明寫「Ara Khan, Cline」。草稿的寫法只要在素材裡有出處，就不算寫錯。"""
    ev = (evidence or "").lower()
    mat = (material or "").lower()
    disputed = [w for w in _LATIN.findall(sentence or "") if len(w) >= 2 and w.lower() not in ev]
    return bool(disputed) and all(re.search(rf"(?<![a-z0-9]){re.escape(w.lower())}s?(?![a-z0-9])", mat)
                                  for w in disputed)


def verify(items: list[dict], article: str, material: str) -> tuple[list[dict], list[tuple[dict, str]]]:
    """程式驗證審稿意見。回 (算數的, [(被丟掉的, 原因)])。"""
    art, mat = _norm(article), _norm(material)
    kept, dropped, seen = [], [], set()
    for it in items:
        sentence = str(it.get("原句") or "").strip()
        evidence = str(it.get("素材原文") or "").strip()
        problem = str(it.get("問題") or "")
        if _ABSENCE.search(problem):
            dropped.append((it, "講的是「素材沒有」，不是矛盾"))
            continue
        # 問題講的是名字，或者兩邊的英文詞長得像（模型不一定會說「名字寫錯」，2026-11-17 那條寫的是
        # 「素材原文為 GP operator，草稿改寫為 GPU 運算子」），而草稿的寫法在素材別處有出處
        if ((_NAME_ISSUE.search(problem) or misheard_name(sentence, evidence))
                and _attested_spelling(sentence, evidence, material)):
            dropped.append((it, "草稿的寫法在素材別處有出處（逐字稿多半是語音辨識聽錯）"))
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


def pick_models(writers: list[str]) -> list[str]:
    """查核員不能跟寫手同一家（2026-11-16 新流程第一篇實跑：Haiku 沒額度、寫手退到 glm，
    對帳的也是 glm，等於自己審自己）。被排除掉的位子由備援模型補上，一起同時查。"""
    from substack_radar.quality_loop import _same_family

    def independent(model: str) -> bool:
        return not any(_same_family(model, w) for w in writers if w)

    base = default_models()
    keep = [m for m in base if independent(m)]
    if len(keep) < len(base):
        keep += [m for m in fallback_models() if independent(m) and m not in keep]
    return keep or base


def _default_chat(model: str, prompt: str) -> tuple[str, str]:
    """回 (回答, 實際作答的模型)。閘道在模型逾時時會自己換別的模型頂上，名字不一定是我們指定的那個。"""
    from substack_radar import quality_loop as q
    text = q._logged("gateway", q._oss_chat_impl, model, model, prompt, DEADLINE_S)
    return text, getattr(q._SERVED, "model", "") or model


def run(article: str, pack: str, *, models: list[str] | None = None, chat=None,
        deadline_s: int | None = None, grace_s: int | None = None,
        fallback: list[str] | None = None, writers: list[str] | None = None,
        source_title: str = "") -> tuple[list[dict], dict]:
    """兩個模型同時查、取聯集、程式驗證。回 (算數的意見, 摘要)。

    摘要 {"answered": [...], "failed": {...}, "raw": n, "dropped": n}；全部模型都沒回應時
    answered 是空的——呼叫端要當成「這一輪沒有第二雙眼睛」，不能當成「查過沒問題」。"""
    from substack_radar.quality_loop import _same_family

    writers = [w for w in (writers or []) if w]

    def is_author(model: str) -> bool:
        return any(_same_family(model, w) for w in writers)

    if fallback is None:
        fallback = [m for m in fallback_models() if not is_author(m)] if models is None else []
    models = models or pick_models(writers)
    chat = chat or _default_chat
    deadline_s = DEADLINE_S if deadline_s is None else deadline_s
    grace_s = GRACE_S if grace_s is None else grace_s
    if source_title:
        pack = f"【素材標題】{source_title}\n\n{pack}"      # 標題常是名字的正確寫法（逐字稿會聽錯）
    prompt = build_prompt(article, pack)
    results: queue.Queue = queue.Queue()
    raw_items: list[dict] = []
    summary = {"answered": [], "failed": {}, "raw": 0, "dropped": 0}

    def ask(model: str) -> tuple[str, str]:
        out = chat(model, prompt)
        return (out[0], out[1] or model) if isinstance(out, tuple) else (out, model)

    def work(model: str) -> None:
        t0 = time.time()
        try:
            raw, served = ask(model)
            results.put((model, raw, served, None, time.time() - t0))
        except Exception as exc:                      # 一個模型掛了不影響另一個
            results.put((model, "", model, exc, time.time() - t0))

    def accept(model: str, raw: str, served: str, secs: float, label: str = "") -> bool:
        """收下一個模型的回答。閘道換成寫手同一家、或換成已經答過的模型，都不算獨立的一雙眼睛。"""
        if served != model and is_author(served):
            summary["failed"][model] = f"閘道換成跟寫手同一家的 {served}"
            print(f"[FactAudit] ⏭️ {model} 被閘道換成 {served}（跟寫手同一家），這份不算")
            return False
        if served in summary["answered"]:
            summary["failed"][model] = f"閘道換成已經答過的 {served}"
            return False
        if unreadable(raw):
            summary["failed"][model] = "回答的格式壞掉，讀不出來"
            print(f"[FactAudit] ⚠️ {served} 的回答格式壞掉，讀不出來（不當成沒問題）")
            return False
        items = parse(raw)
        for it in items:
            it["查核模型"] = served
        raw_items.extend(items)
        summary["answered"].append(served)
        via = f"（指定 {model}）" if served != model else ""
        print(f"[FactAudit] {label}{served}{via} 回了 {len(items)} 條（{secs:.0f} 秒）")
        return True

    for model in models:
        threading.Thread(target=work, args=(model,), daemon=True, name=f"fact-audit-{model}").start()

    end = time.time() + deadline_s
    done: set[str] = set()
    for _ in models:
        try:
            model, raw, served, exc, secs = results.get(timeout=max(1.0, end - time.time()))
        except queue.Empty:
            break
        done.add(model)
        if exc is not None:
            summary["failed"][model] = str(exc)[:80]
            print(f"[FactAudit] ⚠️ {model} 沒有回應（{str(exc)[:80]}）")
            continue
        first = not summary["answered"]
        if accept(model, raw, served, secs) and first:
            end = min(end, time.time() + grace_s)     # 有人回了：其他人最多再等 grace_s
    for model in models:
        if model not in done:
            summary["failed"][model] = "太慢，沒等它"
            print(f"[FactAudit] ⏱️ {model} 太慢，不等了")
    # 都沒回：換備援模型查一次，不要讓這篇稿完全沒人對過帳
    for model in ([] if summary["answered"] else [m for m in fallback if m not in models]):
        t0 = time.time()
        try:
            raw, served = ask(model)
        except Exception as exc:
            summary["failed"][model] = str(exc)[:80]
            print(f"[FactAudit] ⚠️ 備援 {model} 也沒有回應（{str(exc)[:80]}）")
            continue
        if accept(model, raw, served, time.time() - t0, label="備援 "):
            break

    kept, dropped = verify(raw_items, article, pack)
    for it, why in dropped:
        print(f"[FactAudit] ⏭️ 丟掉（{why}）：{str(it.get('問題') or '')[:60]}")
    summary["raw"], summary["dropped"] = len(raw_items), len(dropped)
    return kept[:MAX_ITEMS], summary
