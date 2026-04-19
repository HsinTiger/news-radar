# ============================================================
# News Radar · Makefile
# ------------------------------------------------------------
# 標準化所有常用指令。原則：一個口令一個動作，可打 `make help` 查詢。
# ============================================================

.PHONY: help install test unit integ diag diag-harvest diag-feeds \
        harvest pipeline reflect clean lint replay

PY := python

# ---- 預設目標 ----
help:
	@echo "News Radar · make 指令"
	@echo ""
	@echo "  make install       安裝 requirements.txt + pytest"
	@echo "  make test          跑全部 pytest"
	@echo "  make unit          只跑 unit 測試"
	@echo "  make integ         只跑 integration 測試"
	@echo ""
	@echo "  make diag          一次跑完 harvest + feeds 診斷"
	@echo "  make diag-harvest  只跑 SQLite 診斷（快，不打網路）"
	@echo "  make diag-feeds    只跑 feeds 存活探測（打網路 30-90s）"
	@echo "  make replay ID=<id prefix or url>   重跑單篇清洗"
	@echo ""
	@echo "  make harvest       跑 run_harvest.py 採集一次"
	@echo "  make pipeline      跑 run_pipeline.py"
	@echo "  make reflect       跑 run_reflect.py"
	@echo ""
	@echo "  make clean         清掉 __pycache__ / .pytest_cache"

# ---- 安裝 ----
install:
	$(PY) -m pip install -r requirements.txt
	$(PY) -m pip install pytest pytest-asyncio

# ---- 測試 ----
test:
	pytest

unit:
	pytest tests/unit -v

integ:
	pytest tests/integration -v

# ---- 診斷 ----
diag: diag-harvest diag-feeds
	@echo ""
	@echo "✅ 診斷完成。報告："
	@echo "   data/01_harvest/diagnostic_report.md"
	@echo "   data/01_harvest/feeds_health.md"

diag-harvest:
	$(PY) tools/diagnose_harvest.py

diag-feeds:
	$(PY) tools/diagnose_feeds.py

replay:
ifndef ID
	@echo "用法：make replay ID=<id prefix or url>"
	@exit 1
endif
	$(PY) tools/replay_item.py $(ID)

# ---- 執行 ----
harvest:
	$(PY) run_harvest.py

pipeline:
	$(PY) run_pipeline.py

reflect:
	$(PY) run_reflect.py

# ---- 清理 ----
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache
	@echo "✅ 清理完成"
