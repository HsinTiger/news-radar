#!/usr/bin/env python3
"""YouTube 源強化：把「同一主題的多支 YouTube」聚成一個曼報級深度素材包。

曼報「巨人之聲」的深度來自「一個主題 × 多個一手源」（官方影片 + 深度報告 +
財報/訪談）。本工具吃幾個種子 YouTube 網址 → 抓全逐字稿 + metadata → 產出：
  1. 「重點參考資料」清單（曼報開頭那段的格式）
  2. 每支的關鍵數據/人名/機構（從逐字稿粗篩，給寫手對焦）
  3. 合併逐字稿素材（餵給 compose 寫深度文）

用法:
  python scripts/enrich_youtube_sources.py URL1 URL2 ... [--topic "Palantir"] [--out path.md]
"""
from __future__ import annotations
import argparse, os, re, subprocess, sys, json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
import shutil
def _which(*cands):
    for c in cands:
        if shutil.which(c) or Path(c).exists():
            return c
    return cands[-1]
YTDLP = _which(str(REPO / ".venv/bin/yt-dlp"), "yt-dlp", "/Users/hsin/opt/anaconda3/bin/yt-dlp")
FFMPEG = _which("/Users/hsin/opt/anaconda3/envs/tenserMLDesign/bin/ffmpeg",
                "/opt/homebrew/bin/ffmpeg", "ffmpeg")

YT_ID = re.compile(r"(?:v=|/live/|youtu\.be/|/embed/|/watch\?v=)([A-Za-z0-9_-]{11})")


def _meta(url: str) -> dict:
    try:
        out = subprocess.run(
            [YTDLP, "--skip-download", "--no-warnings", "--print",
             "%(id)s\t%(channel)s\t%(duration)s\t%(upload_date)s\t%(title)s", url],
            capture_output=True, text=True, timeout=60).stdout.strip()
        vid, ch, dur, date, title = (out.split("\t") + ["", "", "", "", ""])[:5]
        return {"id": vid, "channel": ch, "duration": dur, "date": date, "title": title, "url": url}
    except Exception:
        m = YT_ID.search(url)
        return {"id": m.group(1) if m else "", "channel": "", "duration": "", "date": "", "title": "", "url": url}


def _transcript(vid: str) -> str:
    """youtube-transcript-api v1.x（新 API：實例 .fetch）。"""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        last = None
        for langs in (["en"], ["en-US", "en-GB"], ["zh-TW", "zh-Hant", "zh"]):
            try:
                f = api.fetch(vid, languages=langs)
                return " ".join(s.text for s in f)
            except Exception as e:
                last = e
                continue
        # 最後手段：列出可用語言抓第一個
        try:
            tl = api.list(vid)
            for t in tl:
                return " ".join(s.text for s in t.fetch())
        except Exception as e:
            last = e
        return f"(逐字稿抓取失敗: {last})"
    except Exception as e:
        return f"(逐字稿抓取失敗: {e})"


AGY_BIN = os.path.expanduser(os.getenv("AGY_BIN", "~/.local/bin/agy"))
AGY_MODEL = os.getenv("AGY_MODEL", "Gemini 3.6 Flash (High)")


def _agy_transcript(vid: str, timeout_s: int = 600) -> str:
    """字幕抓取失敗時的第二條路：agy（Antigravity CLI）讓 Gemini 讀 YouTube。

    ⚠️ 這條路**不能取代 Whisper**。2026-07-26 實測釐清了它的真實能力邊界：
      · youtu.be/Dr8t8xgvOZY（**有**字幕）→ 逐字複述開頭六句與真值一字不差，
        且答對三個只有該片才有的事實（片尾預告、片中的巴菲特對話、頻道名），
        確認不是靠常識作答。
      · youtu.be/_VaEjGnHgOI（**無**字幕）→ 明確回「無法讀取影片」。
    結論：Gemini 讀的是**字幕軌**，不是音訊。所以真正的缺口（無字幕影片）
    它補不上，那裡只有 Whisper。

    那留著它幹嘛？當 youtube_transcript_api 本身失敗（限流、地區封鎖、
    套件與 YouTube 介面脫節）但影片其實有字幕時，這是取得同一份字幕的
    另一條路。純粹是取字幕的韌性備援，不是 ASR 的替代品。

    仍排在字幕 API 之後：有字幕時字幕是真值，LLM 輸出是模型產物。
    只在本機可用（雲端 runner 沒裝 agy），失敗一律回空字串交給 Whisper 接手。
    """
    if not os.path.exists(AGY_BIN):
        return ""
    prompt = (
        f"逐字輸出這支影片的完整逐字稿：https://youtu.be/{vid}\n"
        "規則：① 一字不差，不要改寫、不要總結、不要加旁白或標題；"
        "② 只輸出逐字稿本身，不要任何前言後語；"
        "③ 若你無法讀取這支影片，只輸出六個字：無法讀取影片。"
    )
    try:
        r = subprocess.run([AGY_BIN, "--model", AGY_MODEL, "-p", prompt],
                           capture_output=True, text=True, timeout=timeout_s)
    except Exception:
        return ""
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out or "無法讀取影片" in out[:40]:
        return ""
    return out


def _whisper_transcript(vid: str, model_size: str = "base", max_minutes: int = 0) -> str:
    """無字幕影片的後備：yt-dlp 抓音檔 → faster-whisper ASR 轉字幕（免費、本機）。"""
    import tempfile, os
    base = tempfile.mktemp()
    pp = ["--postprocessor-args", f"-t {max_minutes*60}"] if max_minutes else []
    try:
        subprocess.run([YTDLP, "-f", "bestaudio", "--extract-audio", "--audio-format", "mp3",
                        *pp, "--ffmpeg-location", FFMPEG, "-o", base + ".%(ext)s",
                        "--no-warnings", f"https://youtu.be/{vid}"],
                       capture_output=True, timeout=900)
    except Exception as e:
        return f"(音檔下載失敗: {e})"
    mp3 = base + ".mp3"
    if not os.path.exists(mp3):
        return "(音檔下載失敗)"
    try:
        from faster_whisper import WhisperModel
        m = WhisperModel(model_size, device="cpu", compute_type="int8")
        # language=None → 自動偵測。原本寫死 "en"，中文影片會被硬當英文轉，
        # 這是中文 ASR 品質長期偏低的原因之一（2026-07-26 修）。
        segs, _ = m.transcribe(mp3, language=None, vad_filter=True)
        text = " ".join(s.text.strip() for s in segs)
    except Exception as e:
        text = f"(Whisper 轉錄失敗: {e})"
    finally:
        try: os.remove(mp3)
        except Exception: pass
    return text


_QUALITY_DOMAINS = ("semianalysis", "stratechery", "ben-thompson", "ft.com", "economist",
                    "bloomberg", "wsj.com", "reuters", "nytimes", ".edu", "arxiv",
                    "newsletter", "blog.", "hbr.org", "sequoiacap", "a16z",
                    # 健康/科學/醫學權威源（2026-06-17）：讓非科技題材（睡眠、營養、神經科學…）
                    # 也能精準配到書面文獻，不只退回逐字稿本身。
                    "pubmed", "ncbi.nlm.nih.gov", "nih.gov", "nature.com", "science.org",
                    "nejm.org", "thelancet", "cell.com", "jamanetwork", "bmj.com",
                    "examine.com", "sciencedirect", "nber.org", "ssrn",
                    # 財經/市場開放深度源（2026-06-17）：FT/Bloomberg 多在付費牆、DDG 排不上，
                    # 補一批免費開放、曼報級的財經電子報，讓市場類題材也配得到真分析。
                    "thediff", "netinterest", "appeconomyinsights", "mostlymetrics",
                    "doomberg", "pitchbook", "cbinsights", "notboring", "generalist",
                    "sherwood.news", "bytebytego", "platformer")


def _find_reports(query: str, n: int = 6) -> list:
    """自動上網找對應的書面深度報告（曼報配 SemiAnalysis/Stratechery 的那層）。免費 DDG。"""
    try:
        from ddgs import DDGS
        seen, out = set(), []
        # 多角度撒網：一般分析 + 科技深度源 + 財經深度電子報 + 學術。高品質域名最後再排序拉前。
        queries = (
            f"{query} analysis report",
            f"{query} SemiAnalysis OR Stratechery OR deep dive",
            f"{query} Net Interest OR The Diff OR equity research OR earnings analysis",
            f"{query} deep dive newsletter",
        )
        for q in queries:
            for r in DDGS().text(q, max_results=n * 2):
                href = r.get("href", "")
                dom = re.sub(r"^https?://(www\.)?", "", href).split("/")[0]
                if dom in seen:
                    continue
                seen.add(dom)
                quality = any(d in href for d in _QUALITY_DOMAINS)
                out.append({"title": r.get("title", ""), "url": href, "quality": quality})
        out.sort(key=lambda x: not x["quality"])  # 高品質域名優先
        return out[:n]
    except Exception:
        return []


# 從逐字稿粗篩「曼報式」素材：帶單位的數據、人名/機構/產品
_FIG = re.compile(r"\$?\d[\d,\.]*\s?(?:%|billion|million|trillion|B|M|hours?|seconds?|x|倍|億|兆)", re.I)
_CAP = re.compile(r"\b([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+){0,2})\b")


def _highlights(text: str) -> dict:
    figs = list(dict.fromkeys(_FIG.findall(text)))[:20] if False else []
    # 直接抓含數據的句子（給寫手對焦更有用）
    sents = re.split(r"(?<=[.!?])\s+", text)
    data_sents = [s.strip() for s in sents if _FIG.search(s) and len(s) < 240][:12]
    caps = {}
    for c in _CAP.findall(text):
        c = _clean_entity(c)                  # 去頭去尾虛詞：The United States→United States、Uh Tucker→Tucker
        if not c:
            continue
        multi = " " in c                      # 多字專有名詞（John Boyd / Air Force）幾乎都是真要角
        if not multi and (c in _STOP or len(c) <= 3):
            continue
        caps[c] = caps.get(c, 0) + 1
    # 多字優先；單字需出現 ≥3 次才算
    ents = [(k, v) for k, v in caps.items() if (" " in k) or v >= 3]
    ents.sort(key=lambda x: (-( " " in x[0]) , -x[1]))
    top_entities = [k for k, _ in ents[:16]]
    return {"data_sentences": data_sents, "entities": top_entities}


_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")
_STOP = {"The","This","That","And","But","We","I","It","Yeah","Like","What","Well","There",
    "They","Could","Please","Also","Okay","Sorry","When","Then","Right","Every","You","He",
    "She","So","Now","Here","How","Why","If","Or","Is","Are","Was","Were","Be","Been","Do",
    "Does","Did","Have","Has","Had","Will","Would","Can","Should","My","Your","Our","Their",
    "His","Her","Its","One","Two","Some","Many","Most","More","Said","Just","Really","Very",
    "Maybe","Yes","No","Uh","Um","Oh","Hey","Let","Get","Got","Go","Going","Make","See","Know",
    "Think","Thing","People","Way","Time","Year","Day","Look","Come","Take","Want","Need","Good",
    "Great","First","Last","Next","Even","Still","Because","About","Into","From","With","Over",
    "After","Before","Through","While","Where","Which","Who","Whatever","Actually","Basically",
    "Obviously","Honestly","Anyway","Exactly","Sure","Totally","Absolutely","Probably"}

# 句首大寫的虛詞/助動詞，去頭去尾用：把 "The United States"→"United States"、
# "Does Israel Yeah"→"Israel"、"Uh Tucker"→"Tucker"、"Call Me"→"" 這類雜訊清掉。
_LEAD_STRIP = _STOP | {"At","In","On","Of","As","To","For","By","An","A","Does","Has","Had",
    "Uh","Um","Oh","Call","Me"}


def _clean_entity(c: str) -> str:
    toks = c.split()
    while toks and toks[0] in _LEAD_STRIP:
        toks.pop(0)
    while toks and toks[-1] in _LEAD_STRIP:
        toks.pop()
    return " ".join(toks)


def _content_len(text: str) -> int:
    """語言中立的長度：CJK 逐字 + 非 CJK 按空白切詞。
    `len(text.split())` 對中文逐字稿會嚴重低估（中文詞間無空白，整段字幕可能只算 1），
    這會① 讓素材包標示的字數失真 ② 更糟的是誤觸下面「< 80 視為無字幕」的 Whisper 閘門，
    把抓到的好字幕丟掉、白跑一次 ASR。故統計時把 CJK 字元單獨計數。"""
    cjk = len(_CJK_RE.findall(text))
    non_cjk = len(_CJK_RE.sub(" ", text).split())
    return cjk + non_cjk


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--topic", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--whisper", action="store_true", help="無字幕時用 faster-whisper ASR 轉錄")
    ap.add_argument("--whisper-model", default="base", help="tiny/base/small/medium")
    ap.add_argument("--no-agy", action="store_true",
                    help="不使用 agy/Gemini 原生讀片，無字幕時直接走 Whisper")
    ap.add_argument("--no-reports", action="store_true", help="不上網找書面報告")
    args = ap.parse_args()

    sources = []
    for url in args.urls:
        m = _meta(url)
        if not m["id"]:
            print(f"  ⚠️ 跳過（解析不到 id）: {url}")
            continue
        print(f"  · 抓取 {m['id']} {m['title'][:40]} …")
        m["transcript"] = _transcript(m["id"])
        m["src"] = "字幕"
        if _content_len(m["transcript"]) < 80 or m["transcript"].startswith("("):
            # 後備順序：agy/Gemini 讀字幕軌 → Whisper ASR。
            # Gemini 排前面是因為它便宜且快；但它讀的是字幕軌不是音訊，
            # 所以真正無字幕的影片它也讀不到，會自己回「無法讀取影片」→ 落到 Whisper。
            # 它的價值在於：影片有字幕、但 youtube_transcript_api 取字幕失敗時的備援。
            if not args.no_agy:
                print(f"    └ 取字幕失敗 → 試 agy/Gemini 讀字幕軌（{m['title'][:24]}）…")
                agy_text = _agy_transcript(m["id"])
                if _content_len(agy_text) >= 80:
                    m["transcript"] = agy_text
                    m["src"] = f"Gemini({AGY_MODEL})"
            if (_content_len(m["transcript"]) < 80 or m["transcript"].startswith("(")) and args.whisper:
                print(f"    └ 改用 Whisper ASR 轉錄中（{m['title'][:24]}）…")
                m["transcript"] = _whisper_transcript(m["id"], args.whisper_model)
                m["src"] = f"Whisper({args.whisper_model})"
        m["hl"] = _highlights(m["transcript"])
        m["words"] = _content_len(m["transcript"])
        sources.append(m)

    # 自動上網找對應的書面深度報告（曼報配 SemiAnalysis/Stratechery 那層）
    reports = []
    if not args.no_reports and (args.topic or sources):
        q = args.topic or sources[0]["title"]
        print(f"  · 搜尋對應書面深度報告：{q[:30]} …")
        reports = _find_reports(q)

    topic = args.topic or (sources[0]["title"][:30] if sources else "untitled")
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:40] or "bundle"
    out = Path(args.out) if args.out else REPO / "data" / "source_bundles" / f"{slug}.md"
    out.parent.mkdir(parents=True, exist_ok=True)

    L = [f"# 深度素材包 · {topic}", ""]
    L.append("## 重點參考資料")
    L.append("**🎥 影音一手源**")
    for s in sources:
        dur = f"{int(s['duration'])//60}分" if str(s['duration']).isdigit() else "?"
        L.append(f"- **{s['title']}** — {s['channel']}（{dur}・{s['words']:,} 字・{s.get('src','字幕')}）  \n  {s['url']}")
    if reports:
        L.append("\n**📄 對應書面深度報告（自動搜尋）**")
        for r in reports:
            tag = "⭐ " if r.get("quality") else ""
            L.append(f"- {tag}{r['title']}  \n  {r['url']}")
    L += ["", "## 各源關鍵數據與要角（寫手對焦用）", ""]
    for s in sources:
        L.append(f"### {s['title']} — {s['channel']}")
        if s["hl"]["entities"]:
            L.append(f"**要角/機構**：{ '、'.join(s['hl']['entities']) }")
        if s["hl"]["data_sentences"]:
            L.append("**含數據句**：")
            for ds in s["hl"]["data_sentences"]:
                L.append(f"- {ds}")
        L.append("")
    L += ["---", "## 完整逐字稿素材", ""]
    for s in sources:
        L += [f"### {s['title']} — {s['channel']}（{s['url']}）", "", s["transcript"], ""]

    out.write_text("\n".join(L), encoding="utf-8")
    total = sum(s["words"] for s in sources)
    print(f"\n✅ 素材包：{out}")
    print(f"   {len(sources)} 源・合計 {total:,} 字逐字稿")
    # JSON 給 pipeline
    out.with_suffix(".json").write_text(json.dumps(
        {"topic": topic, "reports": reports,
         "sources": [{k: v for k, v in s.items() if k != "transcript"} for s in sources]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
