"""News Radar · Diagnostic Tools

每個工具都是可獨立執行的 CLI 腳本，不產生 token 成本，專責回答
「這一層到底發生什麼事」。

工具清單：
  - diagnose_harvest.py：讀 SQLite 產生健康報告
  - diagnose_feeds.py  ：即時探測每個 RSS / HTML 源的存活狀態
  - replay_item.py     ：對指定 item 重跑清洗 pipeline 觀察卡點

詳細用法見 tools/README.md。
"""
