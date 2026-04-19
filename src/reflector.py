"""
News Radar · Reflector 模組（Milestone 3）
功能：吃三種學習訊號 → 呼叫 Gemini 綜合 → 把「新規則」追加到 news_radar_soul.md。

三種訊號：
  1. drafts/*.md 的人工編輯 (src.edit_diff)
  2. drafts_for_review.csv 的 decision 欄 (src.csv_decisions)
  3. 已發布貼文在三平台的互動數 (engagement_stats)

輸出：
  - 對 news_radar_soul.md 的 Ⅸ. Iteration Log 區塊做 append-only 增修
  - 在 reflection_events 表留下完整紀錄（token 用量、rationale）

保護機制：
  - 樣本 < MIN_SAMPLES（預設 3）直接跳過，避免用太少資料亂學
  - 每輪呼叫 LLM 嚴格限縮 input token（diff 只保留前 N 行）
  - 寫回 soul 前會檢查「Ⅸ. Iteration Log」區塊存在與否，缺就退而 append 到檔尾
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from src import db as dbmod
from src.edit_diff import EditObservation, scan_drafts_folder
from src.csv_decisions import CsvDecision, read_decisions
from src import engagement as engagement_mod

# ---------- 基礎設定 ----------
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOUL_PATH = PROJECT_ROOT / "config" / "news_radar_soul.md"

# 可調參數
MIN_SAMPLES = int(os.getenv("REFLECTOR_MIN_SAMPLES", "3"))   # 低於此數量就跳過
DIFF_SNIPPET_LINES = 20          # 每筆 diff 最多送 N 行給 LLM
ENGAGEMENT_SAMPLE_SIZE = 15      # 最多送 N 筆互動紀錄
SOUL_ITERATION_ANCHOR = "## Ⅸ. Iteration Log"


# ---------- LLM Pydantic 輸出 ----------
class ReflectionPatch(BaseModel):
    observations: List[str] = Field(
        description="2-5 條從訊號裡觀察到的具體模式。必須可驗證、避免抽象形容。"
    )
    rules_added: List[str] = Field(
        description="1-3 條新規則，寫給未來的 composer.py 看。每條以動詞開頭（如『刪除』『保留』『替換』）。"
    )
    patch_markdown: str = Field(
        description="完整 markdown 片段，會被直接追加到 news_radar_soul.md 的 Ⅸ. Iteration Log 下方。包含日期、觀察、規則。"
    )
    strategic_recommendations: List[str] = Field(
        description="對新聞來源 (Feeds) 或領域的具體優化建議（如：加強科普、調降硬體更新）。將被寫入 strategic_directives.md。"
    )
    rationale: str = Field(
        description="一段簡述：這些新規則與戰略建議是從哪些訊號導出的、信心度多高。"
    )


@dataclass
class ReflectionBundle:
    edits: List[EditObservation] = field(default_factory=list)
    decisions: List[CsvDecision] = field(default_factory=list)
    engagement_rows: List[dict] = field(default_factory=list)  # sqlite Row → dict
    analyst_feedback: Optional[dict] = None # 新增：數據先知的結構化反饋

    @property
    def sample_count(self) -> int:
        return len(self.edits) + len(self.decisions) + len(self.engagement_rows)

    def signals_summary(self) -> dict:
        return {
            "edits": len(self.edits),
            "csv_decisions": len(self.decisions),
            "engagement_samples": len(self.engagement_rows),
            "has_analyst_feedback": self.analyst_feedback is not None
        }


# ---------- 訊號蒐集 ----------

async def gather_signals(conn, refresh_engagement: bool = True) -> ReflectionBundle:
    """整合三種訊號。refresh_engagement=True 會先去 Meta API 拉最新互動數。"""

    # 1. 先抓一輪最新互動數（寫 engagement_stats）
    if refresh_engagement:
        try:
            await engagement_mod.sync_all_posts(conn)
        except Exception as e:
            print(f"[Reflector] engagement 同步失敗但繼續: {e}")

    # 2. 掃 drafts 資料夾
    edits = scan_drafts_folder(conn)

    # 3. 讀 CSV decisions
    decisions = read_decisions(conn)

    # 4. 撈最新每貼文的互動
    rows = dbmod.latest_engagement_per_post(conn)
    engagement_rows = [dict(r) for r in rows][:ENGAGEMENT_SAMPLE_SIZE]

    # 5. 讀取數據先知 (Analyst) 的結構化報告
    analyst_feedback = None
    feedback_path = PROJECT_ROOT / "config" / "analyst_feedback.json"
    if feedback_path.exists():
        try:
            analyst_feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
        except:
            pass

    bundle = ReflectionBundle(
        edits=edits, 
        decisions=decisions, 
        engagement_rows=engagement_rows,
        analyst_feedback=analyst_feedback
    )
    print(f"[Reflector] 訊號盤點 → {bundle.signals_summary()}")
    return bundle


# ---------- 訊號 → Prompt ----------

def _snip_diff(diff_text: str, max_lines: int = DIFF_SNIPPET_LINES) -> str:
    lines = diff_text.splitlines()
    if len(lines) <= max_lines:
        return diff_text
    return "\n".join(lines[:max_lines]) + f"\n... (diff 已截斷，原有 {len(lines)} 行)"


def _build_prompt(bundle: ReflectionBundle, current_soul: str) -> str:
    parts: List[str] = []
    parts.append("你是 News Radar 的「自我反思官」。你的任務是：")
    parts.append("比對下列三種訊號，歸納 Hsin（品牌主）對社群短文的偏好，")
    parts.append("再把新規則 append 到 news_radar_soul.md 的 Ⅸ. Iteration Log。")
    parts.append("規則必須具體、可執行、能指導未來 composer.py 的產出。")
    parts.append("禁止抽象形容詞；每條規則以動詞開頭，盡量含「保留 X / 刪除 Y / 替換 A→B」的結構。")
    parts.append("")
    parts.append("=== 目前 soul（節錄「Ⅳ. 語氣與用詞規範」與「Ⅵ. 範例輸出」作為風格錨點）===")
    parts.append(current_soul[:4000])
    parts.append("")

    # 人工編輯 diff
    if bundle.edits:
        parts.append(f"=== 訊號 A：人工編輯 diff（{len(bundle.edits)} 筆）===")
        for i, e in enumerate(bundle.edits, 1):
            parts.append(f"[edit #{i}] draft_id={e.draft_id[:10]}  title={e.title[:60]}")
            parts.append(_snip_diff(e.diff_unified))
            parts.append("")
    else:
        parts.append("=== 訊號 A：人工編輯 diff（無，使用者尚未在 drafts/ 修改）===")
        parts.append("")

    # CSV 決策
    if bundle.decisions:
        parts.append(f"=== 訊號 B：CSV decision（{len(bundle.decisions)} 筆）===")
        for i, d in enumerate(bundle.decisions, 1):
            flag = "✅ POSITIVE" if d.decision == "positive" else "❌ NEGATIVE"
            parts.append(f"[csv #{i}] {flag}  title={d.title[:60]}  note={d.note or '—'}")
            if d.ai_version:
                parts.append("  AI 原文：")
                parts.append("  " + d.ai_version[:300].replace("\n", " "))
            parts.append("")
    else:
        parts.append("=== 訊號 B：CSV decision（無，CSV 尚未勾選）===")
        parts.append("")

    # 數據先知洞察 (M6.0)
    if bundle.analyst_feedback:
        parts.append("=== 訊號 D：數據先知的結構化洞察 (Analyst Agent) ===")
        parts.append(f"演算法趨勢點評: {bundle.analyst_feedback.get('summary_of_algorithm_shift')}")
        for i, obs in enumerate(bundle.analyst_feedback.get("observations", []), 1):
            parts.append(f"[obs #{i}] 平台: {obs.get('platform')} | 題材: {obs.get('topic_tag')}")
            parts.append(f"  成效: {obs.get('performance_verdict')}")
            parts.append(f"  依據: {obs.get('rationale')}")
            parts.append(f"  建議: {obs.get('suggested_action')}")
            parts.append("")
    else:
        parts.append("=== 訊號 D：數據先知洞察（尚未產出報告）===")
        parts.append("")

    parts.append("")
    parts.append("請用 ReflectionPatch 的 JSON schema 回答。")
    parts.append("patch_markdown 要以 `[YYYY-MM-DD]` 開頭，夾在一個 ```diff 或 ```text code block 中。")
    parts.append("strategic_recommendations 請針對『哪些來源或主題應多抓/少抓』給出具體、可落實的建議。")
    return "\n".join(parts)


# ---------- LLM 呼叫 ----------

def _get_gemini_client():
    # 延遲 import：在 dry-run 模式下不需要 google.genai
    from google import genai
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 未設定，無法執行 Reflector LLM 呼叫")
    return genai.Client(api_key=api_key)


async def synthesize_patch(bundle: ReflectionBundle, current_soul: str) -> Optional[ReflectionPatch]:
    if bundle.sample_count < MIN_SAMPLES:
        print(f"[Reflector] 樣本不足 ({bundle.sample_count} < {MIN_SAMPLES})，跳過 LLM")
        return None

    prompt = _build_prompt(bundle, current_soul)
    client = _get_gemini_client()

    def _sync_call():
        return client.models.generate_content(
            model="gemini-flash-latest",
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_schema": ReflectionPatch,
                "system_instruction": (
                    "你是嚴謹、會計師性格的編輯官。拒絕空洞形容詞，"
                    "寧可產出 1 條扎實規則，也不要產出 5 條模糊規則。"
                ),
            },
        )

    # genai Python SDK 的 generate_content 是同步呼叫，用 to_thread 避免卡住 asyncio
    response = await asyncio.to_thread(_sync_call)
    return response.parsed


# ---------- Soul 檔讀寫 ----------

def _current_soul_version(conn) -> str:
    """根據 reflection_events 表的筆數決定版本號：1.0 + (n) → 1.n。"""
    n = conn.execute("SELECT COUNT(*) FROM reflection_events WHERE status='completed'").fetchone()[0]
    return f"1.{n}"


def apply_patch_to_soul(patch: ReflectionPatch, soul_path: Path = SOUL_PATH) -> None:
    text = soul_path.read_text(encoding="utf-8")
    block = patch.patch_markdown.strip()
    if not block:
        return
    if SOUL_ITERATION_ANCHOR in text:
        new_text = text.rstrip() + "\n\n" + block + "\n"
    else:
        new_text = text.rstrip() + f"\n\n{SOUL_ITERATION_ANCHOR}\n\n" + block + "\n"
    soul_path.write_text(new_text, encoding="utf-8")

    # 寫入戰略指令檔
    directive_path = PROJECT_ROOT / "config" / "strategic_directives.md"
    content = f"# 🏹 最新戰略指令 ({datetime.now().strftime('%Y-%m-%d')})\n\n"
    content += "\n".join([f"- {r}" for r in patch.strategic_recommendations])
    content += f"\n\n> 依據：{patch.rationale}"
    directive_path.write_text(content, encoding="utf-8")


# ---------- 主入口 ----------

async def run_reflection(dry_run: bool = False) -> dict:
    print("=" * 60)
    print(f"[Reflector] 啟動 | dry_run={dry_run}")
    print("=" * 60)

    conn = dbmod.get_conn()
    try:
        bundle = await gather_signals(conn, refresh_engagement=not dry_run)

        summary = {
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "signals": bundle.signals_summary(),
            "samples_used": bundle.sample_count,
            "status": "unknown",
            "patch_preview": "",
        }

        if bundle.sample_count < MIN_SAMPLES:
            print(f"[Reflector] 樣本不足，本次跳過（僅紀錄 skipped）")
            dbmod.log_reflection_event(
                conn,
                ran_at=summary["ran_at"],
                signals_summary=bundle.signals_summary(),
                samples_used=bundle.sample_count,
                soul_version_before=_current_soul_version(conn),
                soul_version_after=_current_soul_version(conn),
                patch_markdown=None,
                rules_added=[],
                rationale=f"樣本不足（{bundle.sample_count} < {MIN_SAMPLES}）",
                status="skipped_low_samples",
            )
            summary["status"] = "skipped_low_samples"
            return summary

        soul_text = SOUL_PATH.read_text(encoding="utf-8")
        version_before = _current_soul_version(conn)

        if dry_run:
            print("[Reflector] dry-run 模式：只印 prompt，不呼叫 LLM、不改 soul。")
            prompt = _build_prompt(bundle, soul_text)
            print("-" * 60)
            print(prompt[:4000])
            print("-" * 60)
            summary["status"] = "dry_run_ok"
            return summary

        patch = await synthesize_patch(bundle, soul_text)
        if not patch:
            summary["status"] = "llm_empty"
            return summary

        # 寫 soul
        apply_patch_to_soul(patch)
        version_after = _current_soul_version(conn)  # 尚未 +1（還沒寫入 event）

        # 紀錄 event（寫入後 version 才 +1）
        dbmod.log_reflection_event(
            conn,
            ran_at=summary["ran_at"],
            signals_summary=bundle.signals_summary(),
            samples_used=bundle.sample_count,
            soul_version_before=version_before,
            soul_version_after=f"1.{int(version_after.split('.')[1]) + 1}",
            patch_markdown=patch.patch_markdown,
            rules_added=patch.rules_added,
            rationale=patch.rationale,
            status="completed",
        )

        summary["status"] = "completed"
        summary["patch_preview"] = patch.patch_markdown[:400]
        summary["rules_added"] = patch.rules_added
        print("[Reflector] 完成，soul 已更新")
        return summary
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="News Radar Reflector")
    parser.add_argument("--dry-run", action="store_true", help="只蒐集訊號、不改 soul")
    parser.add_argument("--skip-engagement", action="store_true",
                        help="略過 engagement API 呼叫（離線測試用）")
    args = parser.parse_args()

    async def _main():
        if args.skip_engagement:
            # Monkey patch：讓 engagement 同步 no-op
            async def _noop(conn, max_posts: int = 50):
                print("[Engagement] 已略過（--skip-engagement）")
                return {"total": 0, "ok": 0, "failed": 0, "failures": []}
            engagement_mod.sync_all_posts = _noop  # type: ignore
        result = await run_reflection(dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    asyncio.run(_main())
