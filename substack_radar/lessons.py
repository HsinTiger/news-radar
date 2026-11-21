"""把過去的退稿原因變成下一篇的交稿前自查（2026-11-16，信哥：根據過往經驗讓寫手少被退）。

資料來源是 logs/writing/rejections.csv（審稿迴圈每一輪記的帳）。只取「第一輪」「程式檢查／篇幅／文風」
這三種：它們有固定的名字、固定的避免方法，而且是寫手第一稿就能自己避開的。審稿模型的事實意見
每篇都不一樣，不適合變成通則。

自我修正：某個原因最近不再出現，就會自動從提醒裡消失；提醒最多 4 條，不會越堆越多。
"""
from __future__ import annotations

import csv
import re
from collections import Counter, OrderedDict
from pathlib import Path

MAX_LESSONS = 4
RECENT_RUNS = 12
MIN_RUNS = 2          # 至少在兩篇出現過才算「常犯」

# (辨識用的 pattern, 固定名稱, 怎麼避免)
_KINDS = [
    (r"^E1", "出處對不上", "寫「根據 X」「X 指出」時，X 要是取材清單裡真的有的媒體或機構；"
                         "節目裡的人直接寫名字（「Alex 說」），不要寫成聽起來像外部機構的說法。"),
    (r"^E3|數字無法定位", "數字找不到出處", "帶單位的數字（%、億、倍）要跟素材裡的一模一樣；自己換算的，把算法寫出來。"),
    (r"^A1b|標籤句", "開場貼標籤", "開場不要寫「這篇要回答的是…」「我的判斷是：」這種宣告句，判斷藏在敘事裡。"),
    (r"^A1", "開場太長", "第一個小標之前 10 句內，就要讓讀者看出這篇在談什麼問題、你傾向哪邊。"),
    (r"^A2", "小標後第一句用代名詞", "每個小標後的第一句要寫出主詞（誰、哪家公司），不要用「他」「這」開頭。"),
    (r"^A3", "數據沒標時間", "重要數字要帶年份或季度。"),
    (r"篇幅不足", "寫太短", "字數只算中文字（英文、數字、網址不算）。把主角是誰、在什麼處境、事情怎麼一步步發展、"
                         "具體的例子寫完整，直接寫到字數上限附近。"),
    (r"套話", "AI 套話", "不寫「全集最值得」「我的判斷是」「最後留一個問題給你」這類每篇都有的句子。"),
    (r"英文沒翻譯", "英文沒翻譯", "英文片語翻成自然的中文；要留原文就放括號，只放第一次。"),
    (r"小標像標籤", "小標像標籤", "小標寫成一句完整的話或讀者心裡的疑問，不要兩三個字的標籤。"),
    (r"查證過程", "查證過程寫進文章", "正文不出現「素材」「逐字稿」「研究來源」「提供的來源」；查不到的就不寫。"),
    (r"出現 \d+ 次", "每句都冠節目名", "節目名在開頭講一次，之後寫「他說」「主持人追問」。"),
    (r"專有名詞註解", "名詞清單", "不做「專有名詞註解」清單，名詞第一次出現時在句子裡順口解釋。"),
    (r"同一個論點", "重複論點", "同一個論點只講一次，第二次出現時要推進到下一步。"),
    (r"中文數字|數字量級", "數字寫法", "數字用阿拉伯數字，億與兆的量級寫之前再對一次素材。"),
    (r"對仗", "對仗句太多", "「不是 X，而是 Y」一篇最多用一兩次，其餘直接講 Y。"),
    (r"比較無基準", "比較沒講基準", "寫「更快」「更貴」「大幅」時，講清楚跟什麼比、差多少。"),
    (r"段落過長", "段落太長", "一段超過 260 字就拆開，一段只講一件事。"),
    (r"填充詞|修辭黑名單|收尾 AI 味|破折號|名詞化", "AI 手癖", "少用破折號與填充詞（其實、很清楚、值得注意），動作不要藏進抽象名詞。"),
]


def classify(kind: str) -> tuple[str, str] | None:
    for pat, name, advice in _KINDS:
        if re.search(pat, kind or ""):
            return name, advice
    return None


def _mode(run: str) -> str:
    return (run or "").split(" ")[-1]


def load(path: Path | None = None) -> list[dict]:
    if path is None:
        from src.run_log import LOG_DIR
        path = LOG_DIR / "rejections.csv"
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def top_lessons(mode: str, *, rows: list[dict] | None = None, limit: int = MAX_LESSONS) -> list[tuple[str, str, int, int]]:
    """回 [(名稱, 怎麼避免, 幾篇犯過, 看了幾篇)]，同專欄樣本不到 3 篇就用全部專欄的。"""
    rows = load() if rows is None else rows
    first = [r for r in rows if str(r.get("round")) == "1" and r.get("source") in ("程式", "篇幅", "文風")]

    def tally(pool: list[dict]):
        runs = list(OrderedDict.fromkeys(r.get("run", "") for r in pool))[-RECENT_RUNS:]
        seen: Counter = Counter()
        advice: dict[str, str] = {}
        for run in runs:
            names = set()
            for r in pool:
                if r.get("run") != run:
                    continue
                hit = classify(r.get("kind", ""))
                if hit:
                    names.add(hit[0])
                    advice[hit[0]] = hit[1]
            seen.update(names)
        return runs, seen, advice

    same = [r for r in first if _mode(r.get("run", "")) == mode]
    runs, seen, advice = tally(same)
    if len(runs) < 3:
        runs, seen, advice = tally(first)
    return [(name, advice[name], n, len(runs)) for name, n in seen.most_common() if n >= MIN_RUNS][:limit]


def prompt_block(mode: str, *, rows: list[dict] | None = None) -> str:
    try:
        lessons = top_lessons(mode, rows=rows)
    except Exception:
        return ""
    if not lessons:
        return ""
    lines = [f"- {name}（最近 {total} 篇有 {n} 篇）：{advice}" for name, advice, n, total in lessons]
    return ("=== 交稿前自查（最近幾篇第一稿實際被退的原因，由程式統計）===\n"
            "這幾件事避開了，稿子通常一次就過：\n" + "\n".join(lines) + "\n\n")


# ===========================================================================
# 每週學習迴圈（WO-3）：從糾錯台、審稿警告、退稿帳本學出「教訓」，累積成各角色的教訓清單。
# 不訓練模型，只累積規則；每一條新規則都要通過 accept() 的程式檢查才採用。
# 網路與模型一律用參數注入（chat=），單元測試不連網。
# ===========================================================================
import hashlib
import json
from datetime import date, datetime
from difflib import SequenceMatcher

REPO = Path(__file__).resolve().parents[1]

MAX_LESSON_LINES = 12          # 每個角色的教訓清單最多幾條，超過刪最舊的
MAX_LESSON_CHARS = 80          # 一條教訓最長幾個字
SIMILAR_LIMIT = 0.6            # 跟既有教訓的相似度要低於這個數
MAX_EXAMPLES = 4               # 每類最多附幾個例句給模型
ROLES = ("writer", "researcher")

# 審稿警告只收這幾種開頭，以及內容含「隱藏抽查」的
WARNING_PREFIXES = ("[跟素材不符，還沒修]", "[已糾錯]", "[審稿誤報，已核對原文]", "[品質迴圈未通過]")
WARNING_KEYWORD = "隱藏抽查"

# 有序：用 kind_text 判斷，第一個符合的勝（「其他」放最後兜底）
CATEGORIES: list[tuple[str, str]] = [
    ("數字或年份錯", r"年份|FY|張冠李戴|數字.*錯|寫錯"),
    ("掛錯來源", r"引用的網頁上找不到|出處|歸屬|來源不符"),
    ("單位換算", r"億|兆|billion|million|單位|換算"),
    ("人名或專有名詞", r"人名|名字|拼|名稱"),
    ("意思說反", r"說反|寫反|相反|誤讀|曲解"),
    ("引用整理站", r"D 級|整理站"),
    ("字數", r"字數"),
    ("AI 套話與文風", r"套話|破折號|對仗|填充詞|生硬"),
    ("其他", r"."),
]
_CATEGORY_RE = [(name, re.compile(pat, re.I)) for name, pat in CATEGORIES]

# 哪些類別的教訓放進研究員的清單（其餘給寫手）
RESEARCHER_CATEGORIES = {"掛錯來源", "引用整理站", "人名或專有名詞"}

# 教訓要有具體動作，不能是空泛口號
ACTION_WORDS = ("核對", "標出", "換算", "寫出", "不要", "改成", "附上", "確認", "檢查", "查證", "避免",
                "對照", "補上", "列出", "註明", "標明", "改用", "改寫", "刪掉", "拆開", "不寫", "只寫", "寫成")

# 禁用詞（比對時不分大小寫）
BANNED_WORDS = ("曼報", "Manny", "Claude", "Gemini", "GPT", "Opus", "Sonnet", "Haiku", "agy", "DeepSeek",
                "產文路線")
# 跟口吻規則衝突的寫法（2026-11-21 試跑抓到）：查證留在幕後，不叫寫手把原文、取材清單、比對過程寫進稿子
CONFLICT_PHRASES = ("附上原文", "附上取材", "在稿中附上", "附上原始", "附上連結", "標註出處於文中", "寫出查證")


def _case_id(source: str, kind_text: str, quote: str) -> str:
    return hashlib.sha1(f"{source}{kind_text}{quote}".encode("utf-8")).hexdigest()


def _make_case(day: str, source: str, kind_text: str, quote: str, folder: str) -> dict:
    kind_text, quote = (kind_text or "").strip(), (quote or "").strip()
    return {"id": _case_id(source, kind_text, quote), "date": day, "source": source,
            "kind_text": kind_text, "quote": quote, "folder": folder}


def _parse_day(text: str) -> date | None:
    try:
        return date.fromisoformat(str(text).strip()[:10])
    except ValueError:
        return None


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _warning_quote(text: str) -> str:
    """審稿警告裡常夾著被點名的原句（「…」或 \"…\"），抓第一段當引句；沒有就留空。"""
    m = re.search(r"[「“\"]([^」”\"]{4,200})[」”\"]", text)
    return m.group(1) if m else ""


def collect_cases(since: date, until: date, *, root: Path = REPO) -> list[dict]:
    """把糾錯台紀錄、審稿警告、退稿帳本整理成案例（含頭尾日期），用 id 去重。只讀。"""
    root = Path(root)
    seen: dict[str, dict] = {}

    def add(case: dict) -> None:
        if case["kind_text"]:
            seen.setdefault(case["id"], case)

    drafts = root / "data" / "substack_drafts"
    for day_dir in sorted(drafts.glob("*")) if drafts.is_dir() else []:
        day = _parse_day(day_dir.name)
        if not day_dir.is_dir() or day is None or not since <= day <= until:
            continue
        for folder in sorted(p for p in day_dir.iterdir() if p.is_dir()):
            label = f"{day_dir.name}/{folder.name}"
            fix_log = _read_json(folder / "fix_log.json")
            for entry in fix_log if isinstance(fix_log, list) else []:
                for item in (entry.get("items") or []) if isinstance(entry, dict) else []:
                    if not isinstance(item, dict):
                        continue
                    add(_make_case(day.isoformat(), "fix_log", str(item.get("warning") or item.get("verdict") or ""),
                                   str(item.get("old") or ""), label))
            meta = _read_json(folder / "metadata.json")
            warnings = meta.get("audit_warnings") if isinstance(meta, dict) else None
            for w in warnings if isinstance(warnings, list) else []:
                if isinstance(w, str) and (w.startswith(WARNING_PREFIXES) or WARNING_KEYWORD in w):
                    add(_make_case(day.isoformat(), "warning", w, _warning_quote(w), label))

    for row in load(root / "logs" / "writing" / "rejections.csv"):
        day = _parse_day(row.get("date") or "")
        if day is None or not since <= day <= until:
            continue
        add(_make_case(day.isoformat(), "rejection", row.get("kind") or "", row.get("quote") or "",
                       row.get("run") or ""))
    return list(seen.values())


def category_of(kind_text: str) -> str:
    for name, rx in _CATEGORY_RE:
        if rx.search(kind_text or ""):
            return name
    return "其他"


def categorize(cases: list[dict]) -> dict[str, list[dict]]:
    """依 CATEGORIES 的順序分組，只回有案例的類別。"""
    groups: dict[str, list[dict]] = {}
    for name, _ in CATEGORIES:
        hits = [c for c in cases if category_of(c.get("kind_text", "")) == name]
        if hits:
            groups[name] = hits
    return groups


def role_of(category: str) -> str:
    return "researcher" if category in RESEARCHER_CATEGORIES else "writer"


def _default_chat(prompt: str) -> tuple[str, str]:
    """共用文件組的讀卡函式：agy Gemini 3.8 Flash 優先，閘道 fast 後備。"""
    from substack_radar.filings_desk import _default_chat as shared
    return shared(prompt)


PROPOSE_PROMPT = """你是編輯部的學習員。下面是「{category}」這類錯誤，這一週在我們的稿子裡出現了 {n} 件，附幾個例句：

{examples}

請交回「一條」給寫手的教訓，規則：
- 一句話，{limit} 字以內，繁體中文。
- 要有具體動作，用「核對」「標出」「換算」「寫出」「不要」「改成」「附上」這類動詞，不要空泛口號。
- 不要提任何模型名稱、人名或品牌。
只回 JSON：{{"lesson": "..."}}"""


def _example_lines(cases: list[dict]) -> list[str]:
    """最多 4 個例句，同樣的 kind_text 只取一個，讓例子盡量不同。"""
    out, seen = [], set()
    for c in cases:
        key = c.get("kind_text", "")
        if key in seen:
            continue
        seen.add(key)
        line = key[:120] + (f"｜原句：{c['quote'][:100]}" if c.get("quote") else "")
        out.append(line)
        if len(out) >= MAX_EXAMPLES:
            break
    return out


def _parse_lesson(raw: str) -> str:
    from substack_radar.fact_audit import loads_lenient
    data = loads_lenient(raw)
    if isinstance(data, dict) and isinstance(data.get("lesson"), str):
        return data["lesson"].strip()
    text = (raw or "").strip()
    return text.splitlines()[0].strip(" -「」\"") if text else ""


def propose_lessons(groups: dict[str, list[dict]], *, chat=None, min_count: int = 3) -> list[dict]:
    """每個一週內 ≥ min_count 件的類別，叫一次便宜模型交回一條教訓。模型失敗就跳過那一類。"""
    chat = chat or _default_chat
    out: list[dict] = []
    for category, cases in groups.items():
        if len(cases) < min_count or category == "其他":    # 「其他」湊不出具體教訓（試跑 122 件只得到空話）
            continue
        examples = _example_lines(cases)
        prompt = PROPOSE_PROMPT.format(category=category, n=len(cases), limit=MAX_LESSON_CHARS,
                                       examples="\n".join(f"{i}. {e}" for i, e in enumerate(examples, 1)))
        try:
            raw = chat(prompt)
            raw = raw[0] if isinstance(raw, tuple) else raw
            lesson = _parse_lesson(str(raw or ""))
        except Exception as exc:                   # noqa: BLE001
            print(f"[Lessons] ⚠️ {category} 提案失敗：{str(exc)[:100]}")
            continue
        if lesson:
            out.append({"role": role_of(category), "category": category, "lesson": lesson, "examples": examples})
    return out


_LINE_RE = re.compile(r"^\s*-\s*(.*?)(?:（([^（）]*)，(\d{4}-\d{2}-\d{2})）)?\s*$")


def _lesson_text(line: str) -> str:
    """清單裡一行「- 教訓（類別，日期）」只留教訓本文；已經是純文字就原樣回。"""
    m = _LINE_RE.match(line or "")
    return (m.group(1) if m else (line or "")).strip()


def accept(lesson, existing: list[str]) -> tuple[bool, str]:
    """程式檢查，全部通過才收。回 (是否採用, 原因)。"""
    text = (lesson.get("lesson") if isinstance(lesson, dict) else lesson) or ""
    text = str(text).strip()
    if not text:
        return False, "教訓是空的"
    if len(text) > MAX_LESSON_CHARS:
        return False, f"太長（{len(text)} 字，上限 {MAX_LESSON_CHARS}）"
    low = text.lower()
    for word in BANNED_WORDS:
        if word.lower() in low:
            return False, f"含禁用詞「{word}」"
    for phrase in CONFLICT_PHRASES:
        if phrase in text:
            return False, f"跟口吻規則衝突（查證留在幕後）：含「{phrase}」"
    for old in existing or []:
        old_text = _lesson_text(old)
        ratio = SequenceMatcher(None, text, old_text).ratio()
        if ratio >= SIMILAR_LIMIT:
            return False, f"跟既有教訓太像（相似度 {ratio:.2f}）：{old_text[:30]}"
    if not any(w in text for w in ACTION_WORDS):
        return False, "空泛口號，沒有具體動作詞"
    return True, "通過"


# ---------------------------------------------------------------------------
# 教訓清單檔
# ---------------------------------------------------------------------------

def lesson_path(role: str, *, root: Path = REPO) -> Path:
    if role not in ROLES:
        raise ValueError(f"未知角色：{role}")
    return Path(root) / "substack_radar" / "config" / "lessons" / f"{role}.md"


def read_lessons(role: str, *, root: Path = REPO) -> list[str]:
    """清單檔裡的教訓行（「- 」開頭），沒有檔案就回空。"""
    try:
        text = lesson_path(role, root=root).read_text(encoding="utf-8")
    except OSError:
        return []
    return [ln.rstrip() for ln in text.splitlines() if ln.strip().startswith("- ")]


def _line_date(line: str) -> str:
    m = re.search(r"(\d{4}-\d{2}-\d{2})）\s*$", line)
    return m.group(1) if m else "0000-00-00"


def trim_lines(lines: list[str], limit: int = MAX_LESSON_LINES) -> list[str]:
    """超過上限就刪最舊的（依行尾日期；同日期維持檔案順序，前面的算較舊）。"""
    ordered = sorted(lines, key=_line_date)       # sorted 是穩定排序
    return ordered[-limit:] if len(ordered) > limit else ordered


def save_lessons(role: str, lines: list[str], *, root: Path = REPO) -> Path:
    path = lesson_path(role, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    keep = trim_lines([ln.strip() for ln in lines if ln and ln.strip()])
    path.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    return path


def load_lessons(role: str, *, root: Path = REPO) -> str:
    """給寫稿流程讀的區塊；沒有檔案或沒有教訓就回空字串。"""
    try:
        lines = read_lessons(role, root=root)
    except ValueError:
        return ""
    if not lines:
        return ""
    return ("=== 歷來學到的教訓（每週由程式從失敗紀錄整理）===\n" + "\n".join(lines) + "\n\n")


def format_line(lesson: str, category: str, day: date | str) -> str:
    return f"- {lesson.strip()}（{category}，{day}）"


# ---------------------------------------------------------------------------
# 週報歷史
# ---------------------------------------------------------------------------

def load_history(history_path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        with open(history_path, encoding="utf-8") as f:
            for ln in f:
                try:
                    row = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def recurring_from_rows(rows: list[dict], weeks: int = 3, min_count: int = 3) -> list[str]:
    """最近 weeks 週每週都有 ≥ min_count 件的類別（「其他」不算：沒辦法寫成程式檢查）。"""
    by_week: dict[str, dict] = {}
    for r in rows:                                 # 同一週重跑，以最後一筆為準
        by_week[str(r.get("until") or r.get("date") or len(by_week))] = r
    recent = [by_week[k] for k in sorted(by_week)][-weeks:]
    if weeks <= 0 or len(recent) < weeks:
        return []
    names = None
    for r in recent:
        counts = r.get("counts") if isinstance(r.get("counts"), dict) else {}
        hit = {k for k, v in counts.items() if isinstance(v, (int, float)) and v >= min_count and k != "其他"}
        names = hit if names is None else names & hit
    order = [n for n, _ in CATEGORIES]
    return sorted(names or [], key=lambda n: order.index(n) if n in order else 99)


def recurring(history_path: Path, weeks: int = 3) -> list[str]:
    """讀 data/learning/weekly.jsonl，回傳連續 weeks 週都有 ≥3 件的類別（週報建議改成程式檢查）。"""
    return recurring_from_rows(load_history(Path(history_path)), weeks)


if __name__ == "__main__":
    import sys
    for m in sys.argv[1:] or ["podcast", "podcast/ai", "company", "morning"]:
        print(f"[{m}]\n{prompt_block(m) or '（還沒有足夠的記錄）'}")
