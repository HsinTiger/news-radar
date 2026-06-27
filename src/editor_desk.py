"""總編輯閘（Phase 4）：LLM 副編跑「編審五關」→ 殺稿為主、人一鍵把關。

信哥拍板的總編輯本質：**主要工作是殺稿，不是過稿。預設不登、要登必須掙來。** 對每篇
待發稿跑五關，任一硬傷＝殺；少而好，寧可殺錯也不放過填充物（解「我自己都不想全看」）。

設計鐵律 —— **fail-open（活下去）**：LLM 全鏈不可用 / 解析失敗 / 逾時 → verdict 預設「發」，
**絕不因為總編閘故障而擋下發文**。閘只在「明確判定殺/退」時才擋。整套藏 EDITORIAL_MODE 後。

回傳一張「編審單」：五關各一句 + verdict（發/退/殺）+ 整體理由 + 退稿修法。
"""
from __future__ import annotations

from typing import Optional

try:
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover
    BaseModel = object  # type: ignore


class EditorVerdict(BaseModel):
    verdict: str = Field(description="只能是『發』『退』『殺』三者之一")
    sowhat: str = Field(description="第①關 So-what×需求：一句話")
    verify: str = Field(description="第②關 查證：一句話")
    angle: str = Field(description="第③關 角度/脊椎：一句話")
    readable: str = Field(description="第④關 我會讀完嗎：一句話")
    risk: str = Field(description="第⑤關 風險：一句話")
    reason: str = Field(description="整體判決理由，一句話")
    fix: str = Field(default="", description="若退稿，怎麼修；發/殺可留空")


_SYSTEM = (
    "你是 News Radar 的總編輯。你的工作主要是『殺稿』——預設不登、要登必須自己掙來；"
    "少而好，寧可殺錯也不放過填充物（作者自己都不想讀完的稿）。對這篇待發稿跑編審五關，"
    "任一硬傷＝殺：\n"
    "① So-what×需求：一句話講不出『讀者為什麼在乎 × 為什麼現在 × 為什麼是我們』→ 殺；"
    "題材需求(demand)偏低且角度平庸 → 殺。\n"
    "② 查證：核心宣稱站得住嗎？有沒有明顯造假/誇大/無法查證的關鍵宣稱、或撞到已知謠言？"
    "→ 有則退或殺。\n"
    "③ 角度/脊椎：是反共識洞見還是人云亦云？接得上『清醒非共識投資人』的脊椎嗎？→ 弱則退。\n"
    "④ 我會讀完嗎：作者自己會把它讀完嗎？hook 夠力？長度配得上洞見？聲線專業不小丑"
    "（禁『笑死/完了/你怎麼看/有人也這樣嗎』）？→ 不行則退。\n"
    "⑤ 風險：政治題守中立、不選邊；無誹謗/未審先判/overclaim？→ 高風險則退或殺。\n"
    "判決三選一：發＝五關都過、值得佔版面；退＝有救但要改（指出哪關＋怎麼修）；"
    "殺＝填充物或硬傷、不值得登。**只輸出 JSON，verdict 只能是『發』『退』『殺』。**"
)


def _fail_open(note: str) -> EditorVerdict:
    """LLM 不可用 → 預設放行，絕不因總編閘故障擋發文（活下去）。"""
    return EditorVerdict(
        verdict="發", sowhat="(fail-open)", verify="(fail-open)", angle="(fail-open)",
        readable="(fail-open)", risk="(fail-open)", reason=f"總編閘 fail-open：{note}", fix="",
    )


async def editor_review(
    *, title: str, body: str, topic_category: Optional[str] = None,
    demand_weight: Optional[float] = None, slot: Optional[str] = None,
) -> EditorVerdict:
    """跑編審五關。回傳 EditorVerdict。任何失敗都 fail-open（verdict=發）。"""
    body = (body or "").strip()
    if len(body) < 40:
        # 內容太短沒得審——交回現有 quality guard / freshness 流程處理，不在這裡擋。
        return _fail_open("body 太短，跳過總編閘")
    meta = []
    if topic_category:
        meta.append(f"topic_category={topic_category}")
    if demand_weight is not None:
        meta.append(f"demand_weight={demand_weight:.2f}（>1.2 高需求 / <0.9 低需求）")
    if slot:
        meta.append(f"slot={slot}（market=早午盤面 / politics=晚間政治時事）")
    prompt = (
        f"【待審稿】\n標題：{title}\n"
        + (("脈絡：" + "；".join(meta) + "\n") if meta else "")
        + f"內文：\n{body[:4000]}\n\n請跑編審五關，輸出 JSON 編審單。"
    )
    try:
        from src.llm_brain import call_for_json
        res = await call_for_json(
            system=_SYSTEM, prompt=prompt, response_model=EditorVerdict,
            temperature=0.1, timeout_s=120,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail_open(f"call 例外 {type(exc).__name__}")
    data = getattr(res, "data", None)
    if data is None:
        return _fail_open("LLM 無解析結果")
    if data.verdict not in ("發", "退", "殺"):
        # 模型亂回 verdict → 當作放行（fail-open），但保留它的編審單供記錄。
        data.verdict = "發"
        data.reason = f"(verdict 異常已 fail-open) {data.reason}"
    return data
