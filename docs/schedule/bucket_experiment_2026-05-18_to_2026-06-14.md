# Bucket A vs D · 4 週實驗排程

**Period**: 2026-05-18 Mon → 2026-06-14 Sun（28 天 / 28 篇 / 7 篇/週）
**Hypothesis**:
- 平日 D 晚 21:00 > A 早 09:00（D 是 untested upside、long-form 晚間深讀）
- 週末 Sun D 21:00 > Sat A 09:00（投資讀者週日晚 prepare-for-week 高峰）
**Retro date**: 2026-06-15 Mon（scheduled task pending Hsin trigger）

---

## Schedule（照表 publish）

| # | Date | Weekday | Bucket | Time | 完成 |
|---|---|---|---|---|---|
| 1 | 5/18 | Mon | **A 早** | 09:00 | ☐ |
| 2 | 5/19 | Tue | **D 晚** | 21:00 | ☐ |
| 3 | 5/20 | Wed | **A 早** | 09:00 | ☐ |
| 4 | 5/21 | Thu | **D 晚** | 21:00 | ☐ |
| 5 | 5/22 | Fri | **A 早** | 09:00 | ☐ |
| 6 | 5/23 | Sat | **A 早** | 09:00 | ☐ |
| 7 | 5/24 | Sun | **D 晚** | 21:00 | ☐ |
| 8 | 5/25 | Mon | **D 晚** | 21:00 | ☐ |
| 9 | 5/26 | Tue | **A 早** | 09:00 | ☐ |
| 10 | 5/27 | Wed | **D 晚** | 21:00 | ☐ |
| 11 | 5/28 | Thu | **A 早** | 09:00 | ☐ |
| 12 | 5/29 | Fri | **D 晚** | 21:00 | ☐ |
| 13 | 5/30 | Sat | **D 晚** | 21:00 | ☐ |
| 14 | 5/31 | Sun | **A 早** | 09:00 | ☐ |
| 15 | 6/1 | Mon | **A 早** | 09:00 | ☐ |
| 16 | 6/2 | Tue | **D 晚** | 21:00 | ☐ |
| 17 | 6/3 | Wed | **A 早** | 09:00 | ☐ |
| 18 | 6/4 | Thu | **D 晚** | 21:00 | ☐ |
| 19 | 6/5 | Fri | **A 早** | 09:00 | ☐ |
| 20 | 6/6 | Sat | **A 早** | 09:00 | ☐ |
| 21 | 6/7 | Sun | **D 晚** | 21:00 | ☐ |
| 22 | 6/8 | Mon | **D 晚** | 21:00 | ☐ |
| 23 | 6/9 | Tue | **A 早** | 09:00 | ☐ |
| 24 | 6/10 | Wed | **D 晚** | 21:00 | ☐ |
| 25 | 6/11 | Thu | **A 早** | 09:00 | ☐ |
| 26 | 6/12 | Fri | **D 晚** | 21:00 | ☐ |
| 27 | 6/13 | Sat | **D 晚** | 21:00 | ☐ |
| 28 | 6/14 | Sun | **A 早** | 09:00 | ☐ |

---

## Cross-tab 平衡

| | A 09:00 | D 21:00 | Total |
|---|---|---|---|
| 平日 (Mon-Fri) | 10 | 10 | 20 |
| 週六 (Sat) | 2 | 2 | 4 |
| 週日 (Sun) | 2 | 2 | 4 |
| **Total** | **14** | **14** | **28** |

---

## 預測（5/16）

| 排名 | 平日 | 週末 |
|---|---|---|
| #1 | D 晚 | Sun D 晚 |
| #2 | A 早 | Sat A 早 |

理由：
- D 是 untested upside、long-form 晚間深讀符合 Substack newsletter open rate evening peak
- 投資讀者「週日晚 prepare for the week」是 classic pattern
- A 在現有數據已 validated 但部分是 cron supply bias

---

## 6/15 Retro 會檢驗的問題

1. 平日 A vs D 哪個 open rate / view-day / new-subs 更強？
2. 週末 cliff 是「日子問題」還是「時段問題」？
3. Cadence promise（5/16 加進 footer）有沒有提升開信率？
4. Sun D 真的最強嗎？（pre-week prep hypothesis）

---

## 同步: 5/16 加進 footer 的 cadence promise

```
> 「我專門拆解：那些你已經被市場說服、但其實正在害你的共識。」
> 
> 📅 每天 3 分鐘 · 拿走一個被市場藏起來的共識
> 🔄 365 天複利一個眼光
```

每篇文章 footer 自動加（`tools/substack_compose.py:build_footer_block()` 已 patch）。
About page 描述還是 manual web UI 改（python-substack 沒暴露 description endpoint）。
