#!/bin/bash
# ============================================================
# UltraWork Health Check
# 檢查所有 LLM 服務的運行狀態
# ============================================================
set -u

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=========================================="
echo "  UltraWork LLM Health Check"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="
echo ""

# 1. Check CCR
echo -n "🔍 CCR (:3456) ... "
if curl -sf http://127.0.0.1:3456/health > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Running${NC}"
else
    echo -e "${RED}❌ Not running${NC}"
fi

# 2. Check LiteLLM
echo -n "🔍 LiteLLM (:4000) ... "
if curl -sf http://127.0.0.1:4000/health/readiness > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Running${NC}"
else
    echo -e "${RED}❌ Not running${NC}"
    # 嘗試修復
    echo "   → 嘗試透過 launchctl 啟動..."
    launchctl start com.hsin.litellm-gateway 2>/dev/null
    sleep 2
    if curl -sf http://127.0.0.1:4000/health/readiness > /dev/null 2>&1; then
        echo -e "   ${GREEN}✅ 已恢復${NC}"
    else
        echo -e "   ${RED}❌ 修復失敗（執行 launchctl unload/load）${NC}"
    fi
fi

# 3. Check Claude CLI
echo -n "🔍 Claude CLI ... "
if which claude > /dev/null 2>&1; then
    echo -e "${GREEN}✅ $(claude --version 2>/dev/null || echo 'installed')${NC}"
else
    echo -e "${RED}❌ Not found${NC}"
fi

# 4. Check news_radar venv
echo -n "🔍 news_radar venv ... "
if [ -f ~/news_radar/.venv/bin/python ]; then
    echo -e "${GREEN}✅ $($HOME/news_radar/.venv/bin/python --version)${NC}"
else
    echo -e "${RED}❌ venv not found${NC}"
fi

# 5. Check litellm gateway process
echo -n "🔍 LiteLLM process ... "
if pgrep -f "litellm" > /dev/null 2>&1; then
    echo -e "${GREEN}✅ $(pgrep -f "litellm" | wc -l | tr -d ' ') process(es)${NC}"
else
    echo -e "${RED}❌ No process found${NC}"
fi

# 6. Check .env files exist
echo -n "🔍 news_radar .env ... "
if [ -f ~/news_radar/.env ]; then
    echo -e "${GREEN}✅ Found (${YELLOW}keys masked${GREEN})${NC}"
else
    echo -e "${RED}❌ Missing${NC}"
fi

echo -n "🔍 litellm-gateway .env ... "
if [ -f ~/litellm-gateway/.env ]; then
    echo -e "${GREEN}✅ Found${NC}"
else
    echo -e "${RED}❌ Missing${NC}"
fi

echo ""
echo "=========================================="
echo "  Check complete"
echo "=========================================="
