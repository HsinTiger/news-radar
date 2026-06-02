"""
News Radar · Pydantic Schemas
沿用 alpha_pipeline.py 的 Pydantic 強制 JSON 風格
"""
from __future__ import annotations
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, HttpUrl


# ---------- 抓到的原始新聞 ----------
class NewsItem(BaseModel):
    id: str = Field(description="sha1(url)")
    feed_name: str
    feed_tier: str                          # primary / secondary
    source_type: str = "article"            # article / social / video / forum / rss_summary
    url: str
    title: str
    published_at: str                       # ISO8601
    fetched_at: str
    language: Optional[str] = None
    raw_html: Optional[str] = None
    clean_markdown: Optional[str] = None
    word_count: int = 0
    og_image_url: Optional[str] = None
    # Phase 8.16：影片 URL 提取
    # 來源優先序：og:video:secure_url → og:video:url → og:video
    #            → twitter:player:stream → <video src> / <source src>
    #            → RSS enclosure (video/* 或 audio/*)
    # 填入後代表「此素材有隨附媒體檔」；是否為 Meta Graph API 可直用的 .mp4 由
    # `og_video_is_direct` 旗標判定，publisher 端再做最後驗證。
    og_video_url: Optional[str] = None
    og_video_is_direct: bool = False        # True = .mp4/.webm/.mov 等直鏈；False = embed/iframe/player
    tags: List[str] = Field(default_factory=list)
    status: str = "fetched"                 # fetched / scored / drafted / published / dropped
    drop_reason: Optional[str] = None


# ---------- LLM 結構化輸出（composer.py 強制格式）----------
class DraftContent(BaseModel):
    """舊版單一版本草稿（保留以相容舊資料）。Milestone 3.1 之後使用 PlatformVariant。"""
    title: str = Field(description="≤ 28 字的標題")
    hook: str = Field(description="第一段，定錨重要性")
    framework: str = Field(description="第二段，系統拆解")
    validation: str = Field(description="第三段，具體數字 + 背書")
    macro_insight: str = Field(description="第四段，宏觀衝擊")
    ending_question: str = Field(description="結尾開放提問")
    hashtags: List[str] = Field(default_factory=list)
    image_url: Optional[str] = None


# ---------- 平台專屬變體（Milestone 3.1 三平台獨立寫作）----------
class PlatformVariant(BaseModel):
    """單一平台的完整可發文文字。content 必須直接可送入 publisher，
    不再需要任何字串拼接或截斷。
    """
    title: str = Field(description="貼文開頭的標題句 / 一行 hook。")
    body: str = Field(description="正文內容（不含標題、不含 hashtag）。")
    hashtags: List[str] = Field(
        default_factory=list,
        description="hashtag 清單，每一項務必以 # 開頭。數量需符合該平台建議。",
    )
    primary_topic_tag: Optional[str] = Field(
        default=None,
        description=(
            "本貼文的『主題標籤 (topic pill)』。"
            "Threads 會把 hashtags[0] 自動升級為貼文頂部的類目導航 pill，"
            "此欄位即為該 pill 的內容。finalize_variant 時會把它放到 hashtags 最前面，"
            "並確保整份 hashtags 不重複。FB/IG 上此欄位不會被特別處理，"
            "但仍建議填入以便 reflector / scorer 後續單獨統計『哪些 topic tag 帶來最多流量』。"
            "格式：務必以 # 開頭，例如 '#GPTRosalind'。"
        ),
    )
    char_count: int = Field(
        description="實際字元數（含標題、正文、hashtag 與空白）。由 LLM 計算並回填，後續程式會再校驗一次。",
    )


class CarouselCards(BaseModel):
    """2–4 張可滑動社群圖卡的內容（從文章蒸餾），給 IG/FB/Threads carousel 用。
    封面卡用 ig/fb 變體的 title；其餘卡用下面這些欄位。能填就填，缺的卡會自動略過。"""
    insight_statement: Optional[str] = Field(
        default=None, description="一句最反直覺的核心洞察（自己長一句，禁套範例句型）。")
    insight_support: Optional[str] = Field(
        default=None, description="支撐那句洞察的 1–2 句具體說明。")
    stat_number: Optional[str] = Field(
        default=None, description="全篇最有力的單一數字/型號，如 $329、9 億、18%、AM5（沒有就留 null）。")
    stat_caption: Optional[str] = Field(
        default=None, description="那個數字代表什麼，1–2 句。")
    takeaways: List[str] = Field(
        default_factory=list, description="2–3 條讀者可帶走的具體判斷（每條一句）。")


class MultiPlatformDraft(BaseModel):
    """composer.py 的新強制輸出：根據媒介門檻動態生成版本。"""
    fb: Optional[PlatformVariant] = None
    ig: Optional[PlatformVariant] = None
    threads: Optional[PlatformVariant] = None
    image_url: Optional[str] = Field(
        default=None,
        description="共用圖片 URL。三平台目前共用同一張圖，可為原始 og:image 或建議的官方圖。",
    )
    carousel: Optional[CarouselCards] = Field(
        default=None,
        description="2–4 張社群圖卡的蒸餾內容（洞察句、關鍵數字、帶走判斷）。",
    )


class ScoreBreakdown(BaseModel):
    data_density: float = 0.0
    strategic_signal: float = 0.0
    news_novelty: float = 0.0
    persona_fit: float = 0.0


class Draft(BaseModel):
    """完整草稿紀錄（寫入 drafts 表）"""
    id: str
    news_id: str
    persona_version: str
    content: DraftContent
    full_text: str                          # 組裝後的發文文字
    confidence_score: float
    score_breakdown: ScoreBreakdown
    llm_provider: str
    llm_model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    generated_at: str
    status: str = "pending_review"
    # Phase 8.18：雲本混合架構 publish queue 欄位（與 status 正交）
    # status 表達「composer 產出 → 人工審核」的狀態；
    # queue_status 表達「publisher 佇列」的狀態（NULL / queued / published / stale / failed）。
    publish_at: Optional[str] = None        # ISO8601；composer 寫稿時給 cloud publisher 看的「預期發佈時間」
    queue_status: Optional[str] = None      # NULL 表示不在佇列裡；publisher 獨占改動


# ---------- 發布結果 ----------
class PublishResult(BaseModel):
    draft_id: str
    platform: str                           # facebook / threads
    platform_post_id: Optional[str] = None
    posted_at: str
    success: bool
    error_message: Optional[str] = None


# ---------- Harvest 執行報告（給 run_harvest.py 回傳）----------
class HarvestReport(BaseModel):
    started_at: str
    finished_at: str
    feeds_checked: int = 0
    items_found: int = 0
    items_new: int = 0
    items_dropped: int = 0
    drop_reasons: dict = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)
