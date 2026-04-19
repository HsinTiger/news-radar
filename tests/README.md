# News Radar · Tests

## 執行

```bash
# 首次安裝
pip install pytest pytest-asyncio

# 跑全部
pytest

# 只跑 unit
pytest tests/unit -v

# 只跑 integration
pytest tests/integration -v

# 跑單一檔
pytest tests/unit/test_cleaner.py -v
```

## 結構

```
tests/
├── conftest.py           # 共用 fixtures（fixtures_dir, sample_html_en, tmp_db ...）
├── fixtures/             # 寫實但精簡的測試素材
│   ├── sample_blog_en.html
│   └── sample_paywall.html
├── unit/                 # 純函式、零 I/O
│   ├── test_cleaner.py
│   ├── test_fetcher_helpers.py
│   └── test_schema.py
└── integration/          # 會動到 tmp 檔案/DB
    └── test_db_roundtrip.py
```

## 設計原則

1. **Unit ≠ 打網路**：任何需要網路的測試屬於 integration，而且要能 skip。
2. **Fixtures 是寫實的**：`sample_blog_en.html` 仿 Simon Willison 風格，故意寫進
   `Anthropic / OpenAI / Jensen Huang` 三個關鍵字，好讓 cleaner 的白名單能命中，測得到「通過」路徑。
3. **tmp_db fixture**：每個整合測試都拿自己的 SQLite，不可汙染 `data/01_harvest/news_radar.db`。
4. **Bug fix-first**：改完 cleaner/fetcher 後，第一件事是補一個能重現舊 bug 的
   unit test → 紅 → 改 → 綠。這樣每一次改動都擴大測試覆蓋。

## 新增測試的 checklist

- [ ] 檔名 `test_<target>.py`
- [ ] 每個測試只 assert 一個 behavior
- [ ] 需要 DB → 用 `tmp_db` fixture
- [ ] 需要 HTML → 丟一份進 `fixtures/` 而不是 inline string
- [ ] 非同步函式 → `asyncio.run(...)` 即可（如果之後引入 pytest-asyncio 再改 `@pytest.mark.asyncio`）
