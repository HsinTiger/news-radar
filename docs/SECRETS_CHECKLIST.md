# News Radar · GitHub Actions Secrets Checklist

> 部署到 GitHub Actions 需要的全部 Secrets 清單。配合 `docs/GITHUB_DEPLOY.md` 使用。

---

## 1. 必填 Secrets（10 個）

這十個沒有就 workflow 跑不起來。

| Secret 名稱 | 用途 | 從哪拿 | 本機對應 `.env` |
|---|---|---|---|
| `META_APP_ID` | Meta App ID（共用給 FB / IG / Threads token 換發） | [Meta for Developers](https://developers.facebook.com/apps/) → 選 App → Settings → Basic | `META_APP_ID` |
| `META_APP_SECRET` | Meta App Secret | 同上（同頁面） | `META_APP_SECRET` |
| `FB_PAGE_ID` | Facebook Page 的數字 ID | FB Page → About → Page ID | `FB_PAGE_ID` |
| `FB_PAGE_ACCESS_TOKEN` | Facebook Page 長效 token | [Graph API Explorer](https://developers.facebook.com/tools/explorer/) 或 `python -m src.token_utils` | `FB_PAGE_ACCESS_TOKEN` |
| `IG_BUSINESS_ACCOUNT_ID` | Instagram Business 帳號 ID | `python -m src.find_ig_id` 會自動抓出 | `IG_BUSINESS_ACCOUNT_ID` |
| `IG_ACCESS_TOKEN` | Instagram Graph API token | 通常等於 `FB_PAGE_ACCESS_TOKEN`（同一棵授權樹） | `IG_ACCESS_TOKEN` |
| `THREADS_USER_ID` | Threads 帳號數字 ID | [Threads API docs](https://developers.facebook.com/docs/threads/getting-started) | `THREADS_USER_ID` |
| `THREADS_ACCESS_TOKEN` | Threads 長效 token | `python -m src.exchange_threads_token` 換發 | `THREADS_ACCESS_TOKEN` |
| `GEMINI_API_KEY` | Google Gemini API key（Scorer、Composer、Reflector 都用） | [Google AI Studio](https://aistudio.google.com/apikey) | `GEMINI_API_KEY` |
| `ANTHROPIC_API_KEY` | Anthropic Claude API key（competitor_agent 用） | [Anthropic Console](https://console.anthropic.com/settings/keys) | `ANTHROPIC_API_KEY` |

---

## 2. 建議填的 Secrets（2 個）

沒填不會壞，但如果 Threads token 到期自動換發會失敗。

| Secret 名稱 | 用途 | 從哪拿 |
|---|---|---|
| `THREADS_APP_ID` | Threads 專屬 App ID（和 Meta App 分開） | Meta for Developers → 建一個 Threads 類型的 App |
| `THREADS_APP_SECRET` | Threads 專屬 App Secret | 同上 |

---

## 3. 自動提供、不需你手動設

GitHub Actions 會自動注入 `GITHUB_TOKEN`（在 workflow 裡可以拿 `${{ secrets.GITHUB_TOKEN }}`），用來 push 回 state branch。不用設。

前提：Settings → Actions → General → Workflow permissions 選 **`Read and write permissions`**。這個一定要勾，不然 state branch push 會被拒。

---

## 4. 一鍵從 .env 灌進 Secrets

最快的做法：

```bash
cd ~/path/to/news_radar

# 前提：.env 已經填好且本機可以跑
while IFS='=' read -r key value; do
  # 跳過註解 / 空行 / 沒值的 key
  [ -z "$key" ] && continue
  [[ "$key" == \#* ]] && continue
  [ -z "$value" ] && continue
  echo "Setting: $key"
  printf '%s' "$value" | gh secret set "$key"
done < .env

# 確認
gh secret list
```

預期輸出 12 行（或 10 行如果你跳過 Threads App ID/Secret）。

---

## 5. 驗證所有 Secret 都齊了

```bash
# 用 gh 比對
EXPECTED=("META_APP_ID" "META_APP_SECRET" "FB_PAGE_ID" "FB_PAGE_ACCESS_TOKEN" \
          "IG_BUSINESS_ACCOUNT_ID" "IG_ACCESS_TOKEN" "THREADS_USER_ID" \
          "THREADS_ACCESS_TOKEN" "GEMINI_API_KEY" "ANTHROPIC_API_KEY")

gh secret list --json name --jq '.[].name' > /tmp/have.txt
for s in "${EXPECTED[@]}"; do
  if grep -qx "$s" /tmp/have.txt; then
    echo "✅ $s"
  else
    echo "❌ $s（缺）"
  fi
done
```

---

## 6. Token 到期處理

Meta 的長效 token 有效期 60 天。到期前 7 天要換發：

```bash
# 本機跑（需要 .env 裡的 META_APP_ID / SECRET）
~/.virtualenvs/news_radar/bin/python -m src.token_utils

# 它會印出新的長效 token → 手動更新 GitHub Secret
echo -n "NEW_TOKEN_HERE" | gh secret set FB_PAGE_ACCESS_TOKEN
echo -n "NEW_TOKEN_HERE" | gh secret set IG_ACCESS_TOKEN
```

Threads token 類似，用 `src.exchange_threads_token`。

未來優化：可以寫一個 `renew_tokens.yml` weekly workflow 自動換發 + 用 [gh API](https://docs.github.com/rest/actions/secrets#create-or-update-a-repository-secret) 自動更新 secret。目前手動 5 分鐘 × 每 2 個月一次 ≈ 一年 30 分鐘，不值得先自動化。

---

## 7. ⚠️ 絕對不要做的事

- ❌ 把 `.env` commit 進 repo（`.gitignore` 已擋，但還是要檢查 `git ls-files | grep .env`）
- ❌ 把 token 直接寫在 workflow yaml 裡
- ❌ 把 token 貼到 Issue / Discussion / PR description
- ❌ 把 token 放進 artifact 上傳（artifact 對 public repo 的所有人開放下載）
- ❌ 在 `run:` step 裡 `echo $FB_PAGE_ACCESS_TOKEN`（log 裡 GitHub 會自動遮，但不保險）

如果不小心洩露：
1. 立刻到對應 provider 撤銷 token
2. 重新生成
3. `gh secret set` 更新
4. 檢查 repo 裡有沒有殘留（`git log -p | grep -i token`）
