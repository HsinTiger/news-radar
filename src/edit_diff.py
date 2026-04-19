"""
News Radar · Edit-Diff 模組（Milestone 3）
功能：
1. 掃 drafts/ 資料夾裡所有 .md（人類會在 Finder / 編輯器直接修改這些檔案）。
2. 從 `### 🚀 社群發文預覽` 與 `---` 之間抽出「人類最終版」。
3. 用 `**生成時間**: <ISO8601>` 作為 anchor，反查 DB 的 draft row。
4. 若人類版與 AI 原版（drafts.full_text）有差異，更新 drafts.final_text / reviewer_action。
5. 回傳 [(draft, ai_text, human_text, unified_diff), ...] 給 Reflector 當學習訊號。

設計原則：
- 純 stdlib，不跑 LLM。這一層是確定性程式碼。
- 完全冪等：同一份檔案跑多次只會寫同樣的 final_text。
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from src import db as dbmod

DRAFTS_DIR = Path(__file__).resolve().parent.parent / "drafts"

# 支援多平台的新版正規表示式
RE_BLOCKS = {
    "threads": re.compile(r"### 🧵 Threads 預覽.*?\n\n(.*?)(?=\n\n---|\n\n###|$)", re.DOTALL),
    "fb": re.compile(r"### 📘 FB 預覽.*?\n\n(.*?)(?=\n\n---|\n\n###|$)", re.DOTALL),
    "ig": re.compile(r"### 📸 IG 預覽.*?\n\n(.*?)(?=\n\n---|\n\n###|$)", re.DOTALL),
}
RE_GENERATED_AT = re.compile(r"\*\*生成時間\*\*:\s*([0-9T:\-.+Z]+)")
RE_TITLE_H1 = re.compile(r"^# \[AI Score: [0-9.]+\]\s*(.+)$", re.MULTILINE)


@dataclass
class EditObservation:
    draft_id: str
    title: str
    ai_version: str         # drafts.full_text
    human_version: str      # 從 .md 抽出的文字
    diff_unified: str       # `--- ai / +++ human` 統一 diff
    generated_at: str
    file_path: str


def _extract_preview_block(md_text: str) -> Optional[str]:
    m = RE_PREVIEW_BLOCK.search(md_text)
    if not m:
        return None
    return m.group(1).strip()


def _extract_generated_at(md_text: str) -> Optional[str]:
    m = RE_GENERATED_AT.search(md_text)
    return m.group(1).strip() if m else None


def _extract_title(md_text: str) -> Optional[str]:
    m = RE_TITLE_H1.search(md_text)
    return m.group(1).strip() if m else None


def _find_draft_row(conn, generated_at: Optional[str], title: Optional[str]):
    """優先用 generated_at 精準對應；若對不上再用標題近似匹配。"""
    if generated_at:
        row = conn.execute(
            "SELECT * FROM drafts WHERE generated_at = ? LIMIT 1",
            (generated_at,),
        ).fetchone()
        if row:
            return row

    if title:
        # 容忍 LIKE 匹配，標題可能被檔名截斷所以用部分字
        title_key = title.strip()[:14]
        row = conn.execute(
            "SELECT * FROM drafts WHERE title LIKE ? ORDER BY generated_at DESC LIMIT 1",
            (f"%{title_key}%",),
        ).fetchone()
        return row
    return None


def _normalize(text: str) -> str:
    """比對前的正規化：去 BOM / 統一行尾 / 去首尾空白。"""
    return text.replace("\r\n", "\n").replace("\ufeff", "").strip()


def _make_diff(ai_text: str, human_text: str) -> str:
    diff_lines = difflib.unified_diff(
        ai_text.splitlines(),
        human_text.splitlines(),
        fromfile="ai_original",
        tofile="human_edited",
        lineterm="",
        n=2,
    )
    return "\n".join(diff_lines)


def scan_drafts_folder(conn, drafts_dir: Path = DRAFTS_DIR) -> List[EditObservation]:
    """
    掃描 drafts/ 下所有 .md。
    新版邏輯：逐一從 .md 中抽出 FB/IG/Threads 區塊，並與 platform_drafts 表對比。
    """
    if not drafts_dir.exists():
        print(f"[EditDiff] drafts 目錄不存在: {drafts_dir}")
        return []

    md_files = sorted(drafts_dir.glob("*.md"))
    print(f"[EditDiff] 掃描 {len(md_files)} 份 draft 檔案")

    observations: List[EditObservation] = []

    for md_path in md_files:
        try:
            md_text = md_path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"  ↳ [Skip] 讀檔失敗 {md_path.name}: {e}")
            continue

        generated_at = _extract_generated_at(md_text)
        title_hint = _extract_title(md_text)
        
        main_row = _find_draft_row(conn, generated_at, title_hint)
        if not main_row:
            print(f"  ↳ [Skip] 對不到 DB draft: {md_path.name}")
            continue
            
        draft_id = main_row["id"]
        
        # 逐平台對比
        has_any_edit = False
        for platform_key, regex in RE_BLOCKS.items():
            m = regex.search(md_text)
            if not m:
                continue
            
            human_text = _normalize(m.group(1))
            
            # 從 platform_drafts 撈原始版
            plat_slug = dbmod.PLATFORM_DB_NAME.get(platform_key, platform_key)
            plat_row = conn.execute(
                "SELECT * FROM platform_drafts WHERE draft_id = ? AND platform = ?",
                (draft_id, plat_slug)
            ).fetchone()
            
            if not plat_row:
                continue
                
            ai_text = _normalize(plat_row["full_text"] or "")
            if not ai_text:
                continue
                
            if ai_text != human_text:
                has_any_edit = True
                diff = _make_diff(ai_text, human_text)
                
                # 回寫 platform_drafts 表
                dbmod.update_platform_draft_final_text(
                    conn, draft_id=draft_id,
                    platform=plat_row["platform"],
                    final_text=human_text,
                    reviewer_action="edited"
                )
                
                observations.append(EditObservation(
                    draft_id=f"{draft_id}_{platform_key}",
                    title=f"[{platform_key.upper()}] {main_row['title'] or title_hint or ''}",
                    ai_version=ai_text,
                    human_version=human_text,
                    diff_unified=diff,
                    generated_at=main_row["generated_at"],
                    file_path=str(md_path)
                ))
                print(f"  ↳ [Edited:{platform_key}] {md_path.name}")

        if not has_any_edit:
            print(f"  ↳ [Unchanged] {md_path.name}")

    print(f"[EditDiff] 共偵測到 {len(observations)} 筆人工編輯訊號")
    return observations


if __name__ == "__main__":
    conn = dbmod.get_conn()
    obs = scan_drafts_folder(conn)
    for o in obs:
        print("=" * 60)
        print(f"draft_id: {o.draft_id}")
        print(f"title: {o.title}")
        print("--- diff ---")
        print(o.diff_unified[:1200])
    conn.close()
