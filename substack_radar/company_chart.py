"""公司稿的財報趨勢圖（2026-10-17，對標曼報 Pro 每篇都有附來源的圖）。

只畫 yfinance 抓回來的財報事實，跟寫手拿到的是同一份數字，不另外估算：
長條是營收，折線是營業利益與淨利。有 3 年以上年度資料就畫年度，否則畫近幾季。
畫不出來（資料太少、幣圈沒有損益表）就回 None，文章照常發。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.cover_renderer import FONT_BRAND_PATH, FONT_TITLE_PATH

SOURCE_NOTE = "資料來源：公司財報（經 Yahoo Finance 整理）"


def _series(data: dict) -> Optional[tuple[list[str], list, list, list, str]]:
    years, rev = data.get("years") or [], data.get("revenue_b") or []
    if len([v for v in rev if v is not None]) >= 3:
        n = len(rev)
        return ([str(y) for y in years[:n]][::-1], rev[::-1],
                (data.get("op_income_b") or [None] * n)[:n][::-1],
                (data.get("net_income_b") or [None] * n)[:n][::-1], "年度")
    q, qrev = data.get("quarters") or [], data.get("q_revenue_b") or []
    if len([v for v in qrev if v is not None]) >= 3:
        n = len(qrev)
        labels = [f"{s[:4]}Q{(int(s[5:7]) - 1) // 3 + 1}" for s in q[:n]][::-1]
        return (labels, qrev[::-1], (data.get("q_op_income_b") or [None] * n)[:n][::-1],
                (data.get("q_net_income_b") or [None] * n)[:n][::-1], "季度")
    return None


def render_financial_chart(data: dict, out_path: Path, company_label: str) -> Optional[tuple[Path, str]]:
    """畫圖並回傳 (圖檔, 圖說)。圖說含期間、單位、幣別與資料來源。"""
    series = _series(data or {})
    if not series:
        return None
    labels, rev, op, ni, period = series
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager as fm
    except Exception:
        return None

    body = fm.FontProperties(fname=str(FONT_BRAND_PATH))
    bold = fm.FontProperties(fname=str(FONT_TITLE_PATH))
    cur = data.get("currency") or ""
    unit = f"十億{ {'USD': '美元', 'TWD': '台幣', 'EUR': '歐元', 'JPY': '日圓'}.get(cur, cur) }"

    fig, ax = plt.subplots(figsize=(10, 5.6), dpi=160)
    fig.patch.set_facecolor("white")
    x = range(len(labels))
    bars = ax.bar(x, [v or 0 for v in rev], color="#C9D6E8", width=0.6, label="營收", zorder=2)
    for b, v in zip(bars, rev):
        if v is not None:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height(), f"{v:,.1f}", ha="center", va="bottom",
                    fontproperties=body, fontsize=10, color="#44546A")
    if any(v is not None for v in op):
        ax.plot(x, [v if v is not None else float("nan") for v in op], color="#1F4E79", marker="o",
                linewidth=2.4, label="營業利益", zorder=3)
    if any(v is not None for v in ni):
        ax.plot(x, [v if v is not None else float("nan") for v in ni], color="#D9822B", marker="o",
                linewidth=2.4, label="淨利", zorder=3)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontproperties=body, fontsize=11)
    for t in ax.get_yticklabels():
        t.set_fontproperties(body)
    ax.set_ylabel(unit, fontproperties=body, fontsize=11, color="#44546A")
    ax.grid(axis="y", color="#E5E9F0", zorder=0)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.set_title(f"{company_label}：營收與獲利（{period}）", fontproperties=bold, fontsize=16, loc="left", pad=14)
    ax.legend(prop=body, frameon=False, loc="upper left", ncol=3)
    fig.text(0.01, 0.01, SOURCE_NOTE, fontproperties=body, fontsize=9, color="#7A8699")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    caption = f"圖：{company_label}{period}營收、營業利益與淨利（單位：{unit}）。{SOURCE_NOTE}"
    return out_path, caption


def insert_chart(body_md: str, image_path: Path, caption: str) -> str:
    """把圖插在第一個小標前面（開場講完、正文開始的地方）；沒有小標就放開場第一段後。"""
    # 路徑用 <…> 包起來：資料夾名可能有括號（例如「嬌生…(下)賺錢有道」），不包的話 Markdown 連結在「)」就斷，
    # 圖不會上傳、讀者看到破圖（2026-10-18 實際發生）。
    block = f"![{caption}](<{image_path}>)\n\n*{caption}*\n\n"
    lines = body_md.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("## "):
            return "\n".join(lines[:i]) + "\n" + block + "\n".join(lines[i:])
    parts = body_md.split("\n\n", 1)
    return parts[0] + "\n\n" + block + (parts[1] if len(parts) > 1 else "")
