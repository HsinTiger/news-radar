"""糾錯台（2026-11-21 信哥：「有錯的時候能派糾錯 agent 去協助糾錯發稿，最後把工作紀錄寄給我看」）。

發稿前，稿子若帶「[跟素材不符，還沒修]」：
1. 重新抓文中引用的原始網頁（表格逐列保留，避免像蘋果那篇一樣把 FY2008 表頭跟 FY2026 數字黏在一起看錯）。
2. 派糾錯模型逐條判斷：真錯（給改法）／審稿誤報（附原文）／查不到（改寫成不帶那個數字）。
3. 程式驗證：原句要在稿子裡、引用的原文要真的在網頁上、新寫進去的數字要在原文找得到。
4. 驗證過的才改：本機稿件＋Substack 草稿一起改，改完讀回確認。
任何一條沒處理乾淨，這篇就不發（交給下一篇候選），並把紀錄寄給信哥。

模型只負責「讀」和「提議」，改不改由程式決定（確定性在前、LLM 在後）。
"""
from __future__ import annotations

import html as _html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

FLAG = "[跟素材不符，還沒修]"
FIXED = "[已糾錯]"
CLEARED = "[審稿誤報，已核對原文]"

_LINK = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")
_NUM_UNIT = re.compile(r"(\d+(?:\.\d+)?)\s*(億|兆|%|％|倍)?")
WINDOW = 700
PER_ISSUE_BUDGET = 7000


@dataclass
class FixItem:
    warning: str
    verdict: str = ""          # 真錯／誤報／查不到／未處理
    old: str = ""
    new: str = ""
    basis: str = ""
    status: str = "未處理"      # 已改／已核對／未通過驗證：原因
    url: str = ""


@dataclass
class FixReport:
    items: list[FixItem] = field(default_factory=list)
    model: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.items) and all(i.status in ("已改", "已核對") for i in self.items)

    @property
    def changed(self) -> int:
        return sum(1 for i in self.items if i.status == "已改")


def flagged(warnings) -> list[str]:
    return [str(w) for w in warnings or [] if str(w).startswith(FLAG)]


def _snippet(warning: str) -> str:
    m = re.search(r"（原句：(.*)）\s*$", warning)
    return (m.group(1) if m else "").strip()


# ---------------------------------------------------------------------------
# 原始網頁
# ---------------------------------------------------------------------------

def page_text(raw_html: str) -> str:
    """HTML → 純文字；表格一列一行、儲存格用「｜」隔開（空格也留位置，欄位才對得上表頭）。"""
    t = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", raw_html or "")
    t = re.sub(r"(?i)</t[dh]\s*>", " ｜ ", t)
    t = re.sub(r"(?i)</tr\s*>|<br\s*/?>|</p\s*>|</h\d\s*>|</li\s*>|</div\s*>", "\n", t)
    t = _html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def cited_urls(article: str, limit: int = 10) -> list[str]:
    seen: list[str] = []
    for url in _LINK.findall(article or ""):
        url = url.rstrip(".,，。")
        if url not in seen and not url.endswith((".png", ".jpg")):
            seen.append(url)
    return seen[:limit]


def fetch_pages(urls: list[str], fetch=None) -> dict[str, str]:
    if fetch is None:
        import httpx

        def fetch(u):
            r = httpx.get(u, timeout=25, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 (Macintosh) news-radar-fixdesk"})
            r.raise_for_status()
            return r.text
    pages = {}
    for u in urls:
        try:
            pages[u] = page_text(fetch(u))
        except Exception as exc:
            print(f"[FixDesk] ⚠️ 抓不到 {u[:80]}：{str(exc)[:80]}")
    return pages


def _needles(snippet: str) -> list[str]:
    """原句裡的數字換成來源可能的寫法：1110.3 億 → 1110.3／111.03／111.03b。"""
    out = []
    for m in _NUM_UNIT.finditer(re.sub(r"(?<=\d)[,，](?=\d{3})", "", snippet)):
        num, unit = m.group(1), m.group(2) or ""
        if len(num.replace(".", "")) < 2:
            continue
        out.append(num)
        try:
            v = float(num)
        except ValueError:
            continue
        if unit == "億":
            out += [f"{v / 10:g}", f"{v * 100:g}"]
        elif unit == "兆":
            out += [f"{v * 1000:g}"]
    words = re.findall(r"[A-Za-z][A-Za-z0-9.\-]{2,}|FY\s?\d{4}", snippet)
    return list(dict.fromkeys(out + words))


def evidence_for(snippet: str, pages: dict[str, str], budget: int = PER_ISSUE_BUDGET) -> str:
    chunks, used = [], 0
    for url, text in pages.items():
        flat = re.sub(r"(?<=\d),(?=\d{3})", "", text)
        spans = []
        for nd in _needles(snippet):
            for m in re.finditer(re.escape(nd), flat, re.I):
                spans.append((max(0, m.start() - WINDOW), min(len(flat), m.end() + WINDOW)))
                break
        spans.sort()
        merged: list[list[int]] = []
        for a, b in spans:
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        for a, b in merged:
            piece = flat[a:b]
            if used + len(piece) > budget:
                piece = piece[: max(0, budget - used)]
            if piece:
                chunks.append(f"〔來源 {url}〕\n…{piece}…")
                used += len(piece)
            if used >= budget:
                return "\n\n".join(chunks)
    return "\n\n".join(chunks)


# ---------------------------------------------------------------------------
# 糾錯模型
# ---------------------------------------------------------------------------

PROMPT = """你是電子報的糾錯編輯。審稿員說下面這篇稿子有幾處「跟素材不符」，但審稿員自己也可能看錯
（例如把表格的表頭年份跟數字對錯欄）。你的工作是回到原始網頁逐條核對，判斷誰對。

規則：
- 「判定」只能是：真錯／誤報／查不到。
  - 真錯：稿子確實寫錯。「改成」給改好的完整句子，只改錯的地方，其他字一個不動；新數字必須是原文裡有的。
  - 誤報：稿子是對的，審稿員看錯。「改成」留空。
  - 查不到：原文找不到能確認的資料。「改成」只拿掉有疑問的那個數字或那一小段，其他有出處的數字、
    連結（[文字](網址)）一律原樣保留，不准加任何新數字。
- 「原句」必須從稿子一字不差地複製，要包含整個有問題的句子（到句號為止）。
- 「依據」必須從下面的原始網頁一字不差地複製一小段（30～150 字），證明你的判定；查不到就寫最接近的那段。
- 單位換算要算清楚：英文 B（billion）= 10 億，所以 111.03B = 1110.3 億。
- 只輸出 JSON：{{"items": [{{"編號": 1, "判定": "", "原句": "", "改成": "", "依據": "", "來源": "網址"}}]}}

{issues}

=== 稿子全文 ===
{article}
"""


def build_prompt(article: str, issues: list[tuple[str, str]]) -> str:
    blocks = []
    for n, (warning, ev) in enumerate(issues, 1):
        blocks.append(f"=== 第 {n} 條審稿意見 ===\n{warning.removeprefix(FLAG).strip()}\n\n"
                      f"--- 原始網頁相關段落 ---\n{ev or '（抓不到相關段落）'}")
    return PROMPT.format(issues="\n\n".join(blocks), article=article)


def _number_in(num: str, text_flat: str) -> bool:
    """數值比對（127.60 = 127.6），允許 億↔B／M、兆↔B 換算。"""
    try:
        v = float(num)
    except ValueError:
        return False
    have = set()
    for m in re.findall(r"\d+(?:\.\d+)?", text_flat):
        try:
            have.add(round(float(m), 4))
        except ValueError:
            pass
    return any(round(alt, 4) in have for alt in (v, v / 10, v * 100, v / 1000, v * 10))


def verify(item: dict, article: str, pages: dict[str, str]) -> tuple[str, str, str, str]:
    """回 (status, old, new, url)。status 是「已改」「已核對」或「未通過驗證：原因」。"""
    from substack_radar.fact_audit import _norm, coverage
    from substack_radar.quality_loop import _find_span, _plain_numbers

    verdict = str(item.get("判定", "")).strip()
    old, new = str(item.get("原句", "")).strip(), str(item.get("改成", "")).strip()
    basis, url = str(item.get("依據", "")).strip(), str(item.get("來源", "")).strip()
    span = _find_span(article, old)
    if not span:
        return "未通過驗證：原句在稿子裡找不到", old, new, url
    old = article[span[0]:span[1]]
    corpus = "\n".join(pages.values())
    # 模型抄表格時會把換行抄成字面的「\n」、儲存格分隔符號也不一定照抄：兩邊都拿掉再比
    clean = lambda s: re.sub(r"\\n|｜|\|", " ", s)                 # noqa: E731
    flat = re.sub(r"(?<=\d),(?=\d{3})", "", corpus)
    if verdict in ("真錯", "誤報") and coverage(clean(basis), _norm(clean(corpus))) < 0.7:
        return "未通過驗證：引用的原文在網頁上找不到", old, new, url
    if verdict == "誤報":
        # 誤報要拿得出「同一個數字」的原文，不能隨便貼一段不相干的
        nums = [n for n in _plain_numbers(old) if _number_in(n, re.sub(r"(?<=\d),(?=\d{3})", "", basis))]
        if _plain_numbers(old) and not nums:
            return "未通過驗證：說是誤報，但引用的原文沒有稿子裡的數字", old, "", url
        return "已核對", old, "", url
    if verdict not in ("真錯", "查不到") or not new or new == old:
        return "未通過驗證：沒有給出改法", old, new, url
    added = _plain_numbers(new) - _plain_numbers(old)
    if verdict == "查不到" and added:
        return f"未通過驗證：查不到卻加了新數字 {sorted(added)}", old, new, url
    missing = [n for n in added if not _number_in(n, flat)]
    if missing:
        return f"未通過驗證：新數字 {missing} 在原文找不到", old, new, url
    lost = [u for u in _LINK.findall(old) if u not in new]
    if lost:
        return "未通過驗證：改寫把原句的出處連結拿掉了", old, new, url
    bare = lambda s: _LINK.sub("", s)                     # noqa: E731  長度不算網址
    if len(bare(new)) < len(bare(old)) * 0.5:
        return "未通過驗證：改動太大，不像只改錯處", old, new, url
    return "已改", old, new, url


# ---------------------------------------------------------------------------
# 套用到 Substack 草稿
# ---------------------------------------------------------------------------

def _diff_core(old: str, new: str) -> tuple[int, int, int]:
    """共同前後綴之外的差異區段：old[p:len(old)-s] → new[p:len(new)-s]。"""
    p = 0
    while p < min(len(old), len(new)) and old[p] == new[p]:
        p += 1
    s = 0
    while s < min(len(old), len(new)) - p and old[-1 - s] == new[-1 - s]:
        s += 1
    return p, s, 0


def _text_nodes(node, out):
    if isinstance(node, dict):
        if isinstance(node.get("text"), str):
            out.append(node)
        for child in node.get("content") or []:
            _text_nodes(child, out)
    return out


def place_edit(body: dict, old: str, new: str) -> bool:
    """在草稿的某一個文字節點裡找到「最短、唯一」的片段把 old 換成 new。
    句子常被連結切成好幾個節點，所以從差異處往外擴，擴到在單一節點裡唯一為止。"""
    p, s, _ = _diff_core(old, new)
    nodes = _text_nodes(body, [])
    lo, hi = p, len(old) - s                 # old 的差異區段
    for grow in range(0, len(old) + 1):
        a, b = max(0, lo - grow), min(len(old), hi + grow)
        frag = old[a:b]
        if len(frag) < 4:
            continue
        holders = [n for n in nodes if frag in n["text"]]
        total = sum(n["text"].count(frag) for n in holders)
        if total == 1:
            repl = new[a: len(new) - (len(old) - b)]
            holders[0]["text"] = holders[0]["text"].replace(frag, repl)
            return True
        if total == 0 and a == 0 and b == len(old):
            return False
    return False


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(folder: Path, meta: dict, api, draft_id: str, *, chat=None, fetch=None) -> FixReport:
    report = FixReport()
    warnings = flagged(meta.get("audit_warnings"))
    if not warnings:
        return report
    report.items = [FixItem(warning=w) for w in warnings]
    article_path = folder / "Article_Substack.md"
    article = article_path.read_text(encoding="utf-8")
    pages = fetch_pages(cited_urls(article), fetch)
    if not pages:
        report.error = "文中引用的網頁一個都抓不到，無從核對"
        return report
    issues = [(w, evidence_for(_snippet(w), pages)) for w in warnings]

    if chat is None:
        from substack_radar.quality_loop import run_writer_text
        chat = lambda prompt: run_writer_text(prompt, 900)        # noqa: E731
    from substack_radar.fact_audit import parse

    edits: list[FixItem] = []
    todo = list(range(len(report.items)))
    for round_no in (1, 2):                      # 第二輪：把沒過驗證的原因退回去重做一次
        prompt = build_prompt(article, [issues[i] for i in todo])
        if round_no == 2:
            reasons = "\n".join(f"- 第 {k} 條上一輪被退回：{report.items[i].status}"
                                 for k, i in enumerate(todo, 1))
            prompt = (f"你上一輪的答案有幾條沒通過程式驗證，原因如下。請重做這幾條；"
                      f"真的找不到依據就判「查不到」並改寫成不帶那個數字的句子。\n{reasons}\n\n" + prompt)
        try:
            raw, who = chat(prompt)
        except Exception as exc:
            report.error = f"糾錯模型都不可用：{str(exc)[:120]}"
            return report
        report.model = who if not report.model or report.model == who else f"{report.model}＋{who}"
        answers = parse(raw)
        for k, i in enumerate(todo, 1):
            item = report.items[i]
            ans = next((a for a in answers if str(a.get("編號")) == str(k)), None)
            if ans is None and len(answers) == len(todo):
                ans = answers[k - 1]
            if ans is None:
                item.status = "未通過驗證：糾錯模型沒回這一條"
                continue
            item.verdict, item.basis = str(ans.get("判定", "")), str(ans.get("依據", ""))[:200]
            item.status, item.old, item.new, item.url = verify(ans, article, pages)
            if item.status == "已改":
                edits.append(item)
        # 同一句被兩條意見指出（蘋果那篇：兩條都在講歐洲數字）：把第二條的改動併進第一條
        first: dict[str, FixItem] = {}
        for item in list(edits):
            lead = first.setdefault(item.old, item)
            if lead is item:
                continue
            edits.remove(item)
            p, s, _ = _diff_core(item.old, item.new)
            frag_old, frag_new = item.old[p:len(item.old) - s], item.new[p:len(item.new) - s]
            if item.new == lead.new or (frag_old and frag_new in lead.new):
                item.status = "已改"                      # 第一條已經改到了
            elif frag_old and lead.new.count(frag_old) == 1:
                lead.new = lead.new.replace(frag_old, frag_new)
                item.status = "已改"
            else:
                item.status = "未通過驗證：跟另一條改同一句、改法互相衝突"
        todo = [i for i, it in enumerate(report.items) if it.status not in ("已改", "已核對")]
        if not todo:
            break

    if edits:
        draft = api.get_draft(int(draft_id))
        if draft.get("is_published"):
            report.error = "草稿已經發布，糾錯台不動已發布的文章"
            _record(folder, meta, report)
            return report
        body = json.loads(draft["draft_body"])
        for item in edits:
            if not place_edit(body, item.old, item.new):
                item.status = "未通過驗證：在 Substack 草稿裡定位不到這句"
        failed_old = {i.old for i in edits if i.status != "已改"}
        for item in report.items:                   # 併進去的那條跟著主條的結果走
            if item.status == "已改" and item not in edits and item.old in failed_old:
                item.status = "未通過驗證：在 Substack 草稿裡定位不到這句"
        done = [i for i in edits if i.status == "已改"]
        if done:
            api.put_draft(int(draft_id), draft_body=json.dumps(body, ensure_ascii=False))
            back = api.get_draft(int(draft_id))["draft_body"]
            back_text = "".join(n["text"] for n in _text_nodes(json.loads(back), []))
            for item in done:
                p, s, _ = _diff_core(item.old, item.new)
                core = item.new[max(0, p - 6): len(item.new) - s + 6]
                if core and core not in back_text:
                    item.status = "未通過驗證：寫回 Substack 後讀不到改好的句子"
        for name in ("Article_Substack.md", "Article_Full.md"):
            path = folder / name
            if path.exists():
                text = path.read_text(encoding="utf-8")
                for item in done:
                    if item.status == "已改":
                        text = text.replace(item.old, item.new)
                path.write_text(text, encoding="utf-8")

    _record(folder, meta, report)
    return report


def _record(folder: Path, meta: dict, report: FixReport) -> None:
    by_warning = {i.warning: i for i in report.items}
    new_warnings = []
    for w in meta.get("audit_warnings") or []:
        item = by_warning.get(str(w))
        if item and item.status == "已改":
            new_warnings.append(f"{FIXED} {str(w).removeprefix(FLAG).strip()}")
        elif item and item.status == "已核對":
            new_warnings.append(f"{CLEARED} {str(w).removeprefix(FLAG).strip()}")
        else:
            new_warnings.append(w)
    meta["audit_warnings"] = new_warnings
    meta_path = folder / "metadata.json"
    if meta_path.exists():
        on_disk = json.loads(meta_path.read_text(encoding="utf-8"))
        on_disk["audit_warnings"] = new_warnings
        meta_path.write_text(json.dumps(on_disk, ensure_ascii=False, indent=2), encoding="utf-8")
    log = folder / "fix_log.json"
    history = json.loads(log.read_text(encoding="utf-8")) if log.exists() else []
    history.append({"at": datetime.now().isoformat(timespec="seconds"), "model": report.model,
                    "error": report.error, "items": [i.__dict__ for i in report.items]})
    log.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def work_log(title: str, report: FixReport, outcome: str) -> tuple[str, str]:
    """寄給信哥的工作紀錄：(主旨, 內文)。"""
    cleared = sum(1 for i in report.items if i.status == "已核對")
    bad = len(report.items) - report.changed - cleared
    subject = f"【糾錯紀錄】{title[:30]}｜改 {report.changed} 處、誤報 {cleared} 條" + (f"、沒解決 {bad} 條" if bad else "")
    lines = [f"結果：{outcome}", f"糾錯模型：{report.model or '—'}"]
    if report.error:
        lines.append(f"中止原因：{report.error}")
    for n, i in enumerate(report.items, 1):
        lines += ["", f"第 {n} 條｜{i.status}（判定：{i.verdict or '—'}）",
                  f"審稿意見：{i.warning.removeprefix(FLAG).strip()[:200]}"]
        if i.status == "已改":
            lines += [f"原句：{i.old}", f"改成：{i.new}"]
        if i.basis:
            lines.append(f"原文依據：{i.basis}" + (f"（{i.url}）" if i.url else ""))
    return subject, "\n".join(lines)


def send_work_log(title: str, report: FixReport, outcome: str) -> None:
    try:
        from src.notify import _dispatch

        subject, body = work_log(title, report, outcome)
        _dispatch(subject=subject, body_html=None, body_plain=body, macos_subtitle=outcome[:60])
    except Exception as exc:
        print(f"[FixDesk] ⚠️ 工作紀錄沒寄出：{str(exc)[:120]}")
