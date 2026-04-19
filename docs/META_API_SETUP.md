# 📘 Meta API 從零設置手冊（FB Page + Threads）

> 這份文件引導你完成**一次性**的 Meta Developer 設置，讓 news_radar 能自動發文到你的 FB 粉絲專頁與 Threads 帳號。
> **預估時間：30–60 分鐘**。全部在瀏覽器操作，這部分我沒辦法代勞（需要登入你本人的 Meta 帳號）。
> 完成後把取得的 Token 寫進 `.env`，整套自動化就能運轉。

---

## ⚠️ 先決條件檢查

開始前請確認以下都已成立：

- [ ] 你有一個個人 Facebook 帳號（已驗證手機／信箱）
- [ ] 你有一個 **FB 粉絲專頁**（不是個人頁）。沒有的話先到 [fb.com/pages/create](https://www.facebook.com/pages/create) 建一個
- [ ] 你有一個 **Threads 帳號**（綁定 Instagram 帳號即可，Threads 沒有獨立帳號系統）
- [ ] 你的 Instagram 是「專業帳號」(Professional Account)，且已連結到上方 FB 粉專

**重要**：Threads Graph API 要求你的 IG 必須是「商業」或「創作者」類型的專業帳號，且已連到 FB Page。如果你還是個人帳號，打開 IG → 設定 → 切換成專業帳號 → 綁粉專。

---

## Step 1：註冊 Meta Developer 帳號（5 分鐘）

1. 打開 [developers.facebook.com](https://developers.facebook.com)
2. 右上角點「我的應用程式」→ 「註冊」
3. 跟著流程走：同意條款 → 驗證手機 → 選「開發人員」角色
4. 完成後你會進到 Developer Dashboard

---

## Step 2：建立 App（5 分鐘）

1. Dashboard 右上角「建立應用程式」
2. **用途**：選「其他」
3. **應用程式類型**：選「商業」(Business)
4. **應用程式名稱**：填 `news_radar` 或你喜歡的名字（不會公開）
5. **聯絡電子郵件**：你的 email
6. 建立完成後進入 App Dashboard，**把左上角的 App ID 抄下來** → 待會寫進 `.env` 的 `META_APP_ID`
7. 左側選單 →「設定」→「基本資料」→ 找到 **「應用程式密鑰」**（點「顯示」會要密碼），**抄下來** → 寫進 `.env` 的 `META_APP_SECRET`

---

## Step 3：加入產品模組（3 分鐘）

在 App Dashboard 左側選單最下方「新增產品」，分別加入：

1. ✅ **Facebook Login for Business**（必要）
2. ✅ **Threads API**（必要）

加入後左側會出現對應選單。

---

## Step 4：取得 FB Page Access Token（15 分鐘｜最繁瑣）

這一步最麻煩，但**只做一次**。最終目標是拿到「永不過期」的 Page Access Token。

### 4.1 用 Graph API Explorer 拿短效 User Token

1. 打開 [Graph API Explorer](https://developers.facebook.com/tools/explorer/)
2. 右上角 App 選你剛建好的 `news_radar`
3. 點「Generate Access Token」
4. 勾選這些權限：
   - `pages_show_list`
   - `pages_read_engagement`
   - `pages_manage_posts`
   - `pages_manage_metadata`
   - `publish_to_groups`（若未來要發社團可加）
5. 點「Generate Access Token」→ 跳出授權視窗，選你要管理的粉專 → 允許
6. 框框會出現一串 Token（這是**短效 User Token**，只能活 1~2 小時）→ **先複製暫存**

### 4.2 換成長效 User Token（60 天）

打開終端機跑：

```bash
curl -G \
  "https://graph.facebook.com/v20.0/oauth/access_token" \
  -d "grant_type=fb_exchange_token" \
  -d "client_id=<你的 META_APP_ID>" \
  -d "client_secret=<你的 META_APP_SECRET>" \
  -d "fb_exchange_token=<剛剛那個短效 Token>"
```

回傳的 JSON 裡 `access_token` 那串，就是**長效 User Token（60 天）**。

### 4.3 換成永不過期 Page Access Token

先找出你的粉專 ID：

```bash
curl -G \
  "https://graph.facebook.com/v20.0/me/accounts" \
  -d "access_token=<長效 User Token>"
```

回傳的 JSON 裡，找到你要的粉專（看 `name`），抄下它的 `id`（數字）和 `access_token`（那串文字）。

**那個 `access_token` 就是永不過期的 Page Access Token** ✅
（Meta 的規則：用長效 User Token 去換 Page Token，換出來的 Page Token 不會過期）

寫進 `.env`：
```env
FB_PAGE_ID=你的粉專數字ID
FB_PAGE_ACCESS_TOKEN=永不過期的那串
```

### 4.4 驗證可以發文

```bash
curl -X POST \
  "https://graph.facebook.com/v20.0/<FB_PAGE_ID>/feed" \
  -d "message=news_radar 測試發文，完成後可刪" \
  -d "access_token=<FB_PAGE_ACCESS_TOKEN>"
```

回 `{"id":"xxx_xxx"}` 就成功。打開你的粉專確認有看到這則貼文後，手動刪掉。

---

## Step 5：取得 Threads Access Token（10 分鐘）

Threads API 在 2024 年中才開放，流程比 FB 簡化：

### 5.1 設定 Threads App Permissions

1. 回到 App Dashboard → 左側 **Threads API**
2. 先在「使用案例 (Use Cases)」加入這兩個權限：
   - `threads_basic`
   - `threads_content_publish`
3. 在「App Settings」→ 新增 OAuth Redirect URI：
   - 填 `https://localhost/callback`（本地測試用）

### 5.2 取得 Threads User Token

最簡單的路徑是用 Meta 的 Graph API Explorer：

1. [Graph API Explorer](https://developers.facebook.com/tools/explorer/)
2. 左上角 API 類型切換成 **Threads API**（如果選單沒有，回 App 設定頁確認 Threads API 已加入產品）
3. 點 Generate Access Token → 勾 `threads_basic` + `threads_content_publish`
4. 複製出現的 Token

### 5.3 換成長效 Token（60 天，可自動續）

```bash
curl -G \
  "https://graph.threads.net/access_token" \
  -d "grant_type=th_exchange_token" \
  -d "client_secret=<META_APP_SECRET>" \
  -d "access_token=<Threads 短效 Token>"
```

回傳的 `access_token` 就是 60 天長效版。

寫進 `.env`：
```env
THREADS_USER_ID=<從 /me endpoint 取得>
THREADS_ACCESS_TOKEN=<60 天長效 Token>
```

取得 Threads User ID：
```bash
curl -G "https://graph.threads.net/v1.0/me?access_token=<Threads Token>"
```

### 5.4 每 59 天自動續 Token

news_radar 的 `publisher.py` 會內建檢查：Token 到期前 24 小時自動打續約 endpoint：

```bash
curl -G "https://graph.threads.net/refresh_access_token" \
  -d "grant_type=th_refresh_token" \
  -d "access_token=<現有 Token>"
```

（這段是程式自動跑，你只要確保 `.env` 有最新的 Token 即可）

### 5.5 驗證可以發 Threads

Threads 發文是「兩步式」：先建 container、再 publish。

```bash
# Step 1: 建立 container
curl -X POST \
  "https://graph.threads.net/v1.0/<THREADS_USER_ID>/threads" \
  -d "media_type=TEXT" \
  -d "text=news_radar 測試發文" \
  -d "access_token=<THREADS_ACCESS_TOKEN>"
# 回傳 {"id":"container_id"}

# Step 2: Publish
curl -X POST \
  "https://graph.threads.net/v1.0/<THREADS_USER_ID>/threads_publish" \
  -d "creation_id=<container_id>" \
  -d "access_token=<THREADS_ACCESS_TOKEN>"
# 回傳 {"id":"thread_id"}
```

---

## Step 6：寫進 `.env` 檔

在 `news_radar/` 目錄下建 `.env`（複製 `.env.example`）：

```env
# Meta App
META_APP_ID=1234567890123456
META_APP_SECRET=abcdef1234567890abcdef1234567890

# Facebook Page
FB_PAGE_ID=9876543210987654
FB_PAGE_ACCESS_TOKEN=EAAJxxxxxxxxxxxxxxxxx

# Threads
THREADS_USER_ID=1234567890
THREADS_ACCESS_TOKEN=THABxxxxxxxxxxxxxxxxx

# AI APIs（Milestone 2 才會用到，現在可空）
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
```

---

## ⚠️ Step 7：App Review（只有要「完全公開」發文才需要）

**好消息：如果你只發到自己的粉專、用自己的 Token，不需要 App Review。**

Meta 的規則：
- 你是 App 的擁有者 / 開發者角色 → 可以用任何權限發到你自己擁有的資源（粉專、IG、Threads）
- 其他使用者要用你的 App 登入 → 才需要 App Review

也就是說，news_radar 這種「個人自動化」用途，**完全不用送審**。

---

## 🆘 常見問題

**Q：Token 被撤銷了 / 突然不能發文**
A：最常見原因——你改了 FB 密碼，所有 Token 失效。重跑 Step 4 / Step 5 換新 Token。
`publisher.py` 會在每次發文前做 health check，失敗時會印出清楚的除錯訊息。

**Q：粉專訊息數太少，Meta 擋我**
A：新建粉專發太多同質內容會被風控。建議：頭兩週發文頻率控制在 **每天 ≤ 2 篇**，且人工審核確保品質，累積初始互動後再加頻。

**Q：Threads 發文卻不顯示**
A：檢查三件事：(1) 你的 IG 是否為專業帳號、(2) IG 是否綁定 FB Page、(3) Thread container 是否 publish 了（光建 container 不會發出去）。

**Q：Graph API Explorer 找不到 Threads API**
A：到 App Dashboard 的「新增產品」再次確認 Threads API 已加入，有時需要登出重登 Graph Explorer。

---

## ✅ 完成後回報檢查清單

全部跑完後 `.env` 裡應該有以下 6 個值，且都不是空字串：

- [ ] `META_APP_ID`
- [ ] `META_APP_SECRET`
- [ ] `FB_PAGE_ID`
- [ ] `FB_PAGE_ACCESS_TOKEN`
- [ ] `THREADS_USER_ID`
- [ ] `THREADS_ACCESS_TOKEN`

確認後執行：
```bash
python src/verify_meta_tokens.py
```
（Milestone 2 會提供這支驗證腳本，會自動測試兩邊 API 都能正常發文。）
