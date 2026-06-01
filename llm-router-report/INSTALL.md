# 🔧 安裝指南 — CCR + LiteLLM + news_radar

> 已安裝的檔案位置、啟動方式、維護指令。**不需要重新安裝** — 這些檔案已經在你的 Mac 上了。

---

## 1. CCR（已安裝）

### 檢查狀態

```bash
# 檢查 ccr 是否在運行
ps aux | grep "ccr code" | grep -v grep
# 或
cat ~/.claude-code-router/.claude-code-router.pid
# 應該輸出: 29001

# 檢查 port 3456 是否 listen
lsof -i :3456
```

### 啟動 / 停止

```bash
# 啟動（手動）
ccr code

# 停止（CTRL+C 或 kill）
kill $(cat ~/.claude-code-router/.claude-code-router.pid)

# 重啟
kill $(cat ~/.claude-code-router/.claude-code-router.pid)
ccr code
```

### 設定位置

| 檔案 | 路徑 | 說明 |
|------|------|------|
| Binary | `/opt/homebrew/bin/ccr` | Homebrew 安裝 |
| Config | `~/.claude-code-router/config.json` | providers + router 設定 |
| PID | `~/.claude-code-router/.claude-code-router.pid` | 運行中 PID |
| Logs | `~/.claude-code-router/logs/` | 默認關閉（LOG=false） |

---

## 2. LiteLLM Gateway（已安裝 + 開機自啟）

### 檢查狀態

```bash
# 健康檢查
curl http://127.0.0.1:4000/health/readiness

# 檢查 launchd 服務
launchctl list | grep litellm

# 看 log
tail -50 ~/litellm-gateway/proxy.log
```

### 啟動 / 停止 / 重載

```bash
# 手動啟動
launchctl start com.hsin.litellm-gateway

# 停止
launchctl stop com.hsin.litellm-gateway

# 重載設定（修改 config.yaml 後）
launchctl unload -w ~/Library/LaunchAgents/com.hsin.litellm-gateway.plist
launchctl load -w ~/Library/LaunchAgents/com.hsin.litellm-gateway.plist
```

### 設定位置

| 檔案 | 路徑 | 說明 |
|------|------|------|
| 專案目錄 | `~/litellm-gateway/` | Python 3.11 venv |
| Config | `~/litellm-gateway/config.yaml` | providers + router 設定 |
| 啟動腳本 | `~/litellm-gateway/start.sh` | 405 bytes |
| 環境變數 | `~/litellm-gateway/.env` | API keys |
| Log | `~/litellm-gateway/proxy.log` | 運行日誌 |
| LaunchDaemon | `~/Library/LaunchAgents/com.hsin.litellm-gateway.plist` | 開機自啟 |

---

## 3. news_radar Pipeline（已安裝）

### 手動執行

```bash
# 完整 compose 流程（含 harvest + compose + publish）
bash ~/bin/news_radar_compose.sh

# 或直接
cd ~/news_radar
source .venv/bin/activate
python run_pipeline.py --harvest-now --compose-only --buffer-target 2

# 只看 LLM brain 測試
cd ~/news_radar
source .venv/bin/activate
python -c "
import asyncio
from src.llm_brain import call_for_json
from pydantic import BaseModel

class Test(BaseModel):
    result: str

async def test():
    r = await call_for_json(
        system='Answer terse',
        prompt='Say hello in 3 words',
        response_model=Test,
    )
    print(f'Provider: {r.provider}, Model: {r.model}')
    print(f'Data: {r.data}')

asyncio.run(test())
"
```

### 設定位置

| 檔案 | 路徑 | 說明 |
|------|------|------|
| Source code | `~/news_radar/src/` | Python pipeline |
| LLM brain | `~/news_radar/src/llm_brain.py` | 5 條 fallback 鏈 |
| 環境變數 | `~/news_radar/.env` | API keys |
| 寫作規範 | `~/news_radar/config/news_radar_soul.md` | ~17KB soul bundle |
| Compose script | `~/bin/news_radar_compose.sh` | launchd 入口 |
| Engagement script | `~/bin/news_radar_engagement.sh` | 互動排程 |
| Snapshot script | `~/bin/news_radar_weekly_snapshot.sh` | 每週快照 |

---

## 4. 環境變數彙總

本機有三個地方有 API keys:

### `~/.claude-code-router/config.json`
```json
{
  "gemini.api_key": "<your-gemini-api-key>",
  "gemini2.api_key": "<your-gemini2-api-key>",
  "groq.api_key": "<your-groq-api-key>",
  "cerebras.api_key": "<your-cerebras-api-key>",
  "opencode.api_key": "<your-opencode-api-key>"
}
```

### `~/litellm-gateway/.env`
```
GEMINI_API_KEY=<第一把 key>
GEMINI_API_KEY_2=<第二把 key>
```

### `~/news_radar/.env`
```
GEMINI_API_KEY=<key1>,<key2>  # 逗號分隔多把
GEMINI_API_KEY_2=<另外一把>
GROQ_API_KEY=<key>
CEREBRAS_API_KEY=<key>
OPENCODE_API_KEY=<key>
```

---

## 5. 常見維護指令

### 檢查所有服務健康

```bash
# 1. CCR 是否活著
curl -s http://127.0.0.1:3456/health || echo "❌ CCR not running"

# 2. LiteLLM 是否活著
curl -s http://127.0.0.1:4000/health/readiness || echo "❌ LiteLLM not running"

# 3. Claude CLI 是否可用
which claude && claude --version

# 4. 檢查 Python venv
ls ~/news_radar/.venv/bin/python && ~/news_radar/.venv/bin/python --version
```

### 查看歷史 log

```bash
# compose log
ls -lt ~/news_radar_snapshots/_compose_logs/ | head -5
cat ~/news_radar_snapshots/_compose_logs/$(ls -t ~/news_radar_snapshots/_compose_logs/ | head -1)

# LiteLLM log
tail -100 ~/litellm-gateway/proxy.log | grep -E "error|429|fallback|key"

# ccr log（如果 LOG=true）
tail -50 ~/.claude-code-router/logs/*.log
```
