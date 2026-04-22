# Git 詞彙字典 · News Radar 專用精簡版

> 目的：讓 Hsin 真的弄懂每次 agent 叫他輸入的 git 指令到底在做什麼。
> 範圍：只收本專案會用到的，不堆無關教科書內容。
> 建立日期：2026-04-22。看到新術語想收的請補進 §5。

---

## 1. 核心五個名詞（理解其他指令的基礎）

| 名詞 | 一句話解釋 | 在 News Radar 的角色 |
|---|---|---|
| **repo（repository）** | 被 git 追蹤的資料夾，裡面有一個隱藏的 `.git/` 目錄 | 本專案本機只有一份 clone：`~/news_radar`（人類改 + launchd 跑）。遠端鏡像在 GitHub。|
| **remote** | 一個命名的 URL，指向別處的 repo | 預設叫 `origin`，指向 GitHub 上的 news_radar repo |
| **branch** | 一條「commit 鏈」的命名指標 | 本專案有 `main`（程式碼）與 `state`（DB + 狀態）兩條，§S_A §5 |
| **commit** | 一次快照：檔案內容 + 訊息 + 指向上一個 commit 的指標 | 用 SHA（40 碼 hex，常縮寫前 7 碼，例如 `c4bf25e`）識別 |
| **HEAD** | 指「你目前在哪個 commit」的指標；通常指向目前 branch 的最新 commit | `git log HEAD..origin/main` = 「origin/main 有、我還沒有」的 commits |

---

## 2. 日常五個動詞（90% 時間只用這些）

### `git status`
看三件事：
- 現在在哪條 branch
- 有沒有未加入 staging 的修改（working tree）
- 有沒有 staged 但沒 commit 的東西
- 跟 `origin/<branch>` 差幾個 commit（領先/落後）

**沒有副作用**，隨時可以打。

### `git add <檔案>` / `git add .`
把「working tree 的修改」搬進「staging area（暫存區）」。
這步還沒寫進歷史，只是告訴 git「下一次 commit 要包含這些」。

### `git commit -m "訊息"`
把 staging area 的內容**封存成一個 commit**，並產生 SHA。
訊息要寫給未來的自己或 agent 看——為什麼改，不是改了什麼（`git diff` 能看改了什麼）。

### `git push`
把本地的 commits **送去 remote**。預設 push 目前 branch 到 `origin/同名 branch`。
完整寫法：`git push origin main` = 「push 本地的 main 上到 origin 的 main」。

### `git pull`
把 remote 的 commits **拉下來**，同時試著 merge 進本地 branch。
= `git fetch` + `git merge`。
本專案 compose_hourly.sh 改用 `git fetch + git merge --ff-only`，比 `pull` 更安全（§4 解釋）。

---

## 3. 容易搞混的 `origin main` vs `origin/main`

這是一個**很多人卡很久**的點，分清楚以後讀指令會突然順暢：

| 寫法 | 意思 | 出現在哪些指令 |
|---|---|---|
| `origin main` （空白分開） | 兩個參數：remote 名稱 + branch 名稱 | `git push origin main`、`git fetch origin main` |
| `origin/main` （斜線連起） | 單一物件：**本地快取的、origin 在上次 fetch 時的 main**（read-only 指標）| `git merge origin/main`、`git reset --hard origin/main`、`git log origin/main..HEAD` |

記憶法：
- 有**空白** → 動作的目標（push 去哪、fetch 哪條）
- 有**斜線** → 一個已經在本地的「遠端快照名」，可以當 commit 來比對

重點：`origin/main` 不是 GitHub 上的 main，是**你上次 fetch 時 GitHub main 的樣子**。所以 `git status` 說「領先 origin/main 3 commits」意思是「相對於我上次 fetch 下來的版本」——如果你兩天沒 fetch，GitHub 上早就比那個新很多了。

---

## 4. 特殊用法（本專案會看到的進階參數）

### `--ff-only`（fast-forward only）
用在 merge 或 pull 上。意思是「只允許純粹向前的更新」——本地有任何分岔就拒絕合併，回報錯誤。

**為什麼本專案 compose_hourly.sh 用它**：Exec clone 應該永遠是 origin/main 的忠實複本，不該有本地 commit。用 `--ff-only` 可以讓意外本地 commit 立刻被發現，而不是被無聲合併掉。

對照：`git pull`（無 `--ff-only`）遇到分岔會自動生一個 merge commit，把兩邊縫起來——對日常開發 OK，對 Exec clone 這種「只當快取」的場景會留下贅餘歷史。

### `--hard`（給 reset 用的）
`git reset --hard <commit>` = 「把 HEAD 跟 working tree 都搬到那個 commit，沒寫入 staging/commit 的東西全部丟掉」。**破壞性**，要確認沒東西要保留才打。

本專案以前 compose_hourly.sh 用 `git reset --hard origin/main`，現在換成 `git merge --ff-only origin/main`（見 §4 安全性解釋）。

### `--force` / `-f`（給 push 用的）
`git push --force` = 「無視遠端的 commit 歷史，強行用本地覆蓋」。**破壞性**——如果別人在這 branch 上有新 commit，會被你擦掉。

本專案 **state 分支** 每小時都被 force-push（Mac 跟 Cloud runner 都會），因為 state 分支用 **orphan commit 模式**：每次都 `git init -b state` 新建、commit 一次、force-push，沒有歷史可以衝突（§S_A §5.1）。

更安全的變體：`--force-with-lease`——只有在「遠端 state 的 SHA 是你以為的那個」時才 force。本專案的 state 沒用這個（因為 orphan 每次都覆蓋），但一般 branch 想 force-push 時強烈建議用這個。

### `--oneline`（給 log 用的）
`git log --oneline -10` = 用一行一個 commit 的簡短格式列最近 10 筆。平常看歷史的預設。

---

## 5. 本專案最常被 agent 唸的那幾條指令，逐字拆解

### 5.1 「先拉最新的 main 再開工」
```bash
git fetch origin main
git merge --ff-only origin/main
```
- `fetch origin main`：從 origin 下載 main 分支的新 commits，更新本地的 `origin/main` 指標——**不碰你自己的 main**。
- `merge --ff-only origin/main`：把 `origin/main` 的新 commits 套到你目前這條 branch 上；只有「純向前」才接受。

合起來就是安全版 `git pull --ff-only`。

### 5.2 「把我的改動上雲」
```bash
git add .
git commit -m "訊息"
git push
```
- `add .`：把當前目錄所有變動加入 staging。
- `commit -m`：封存。
- `push`：推到 origin/同名 branch。

### 5.3 「看看我有沒有東西還沒 push」
```bash
git log origin/main..HEAD --oneline
```
讀法：「origin/main 沒有、HEAD 有」的 commits。如果輸出是空的，代表你跟 origin/main 同步；有內容代表你 push 前可以先看一眼自己要送什麼。

### 5.4 「看 state 分支上目前是什麼狀態」
```bash
git fetch origin state
git show origin/state:LAST_RUN.txt
```
- `fetch origin state`：把 state 分支下載下來（更新 `origin/state` 指標）。
- `git show origin/state:LAST_RUN.txt`：印出 state 分支上 `LAST_RUN.txt` 的內容，**不簽出、不動你的 working tree**。

### 5.5 「我看有 .git/index.lock，怎麼辦」
這是另一個 git 程序半途斷掉的殘留鎖檔。如果 `ps aux | grep git` 沒看到進行中的 git，可以：
```bash
rm .git/index.lock
```
Sandbox 環境下這個可能會失敗（unlink 權限問題，見 handoff 記錄），要回到真實 shell 處理。

---

## 6. 三個狀態 vs 四個動作的心智圖

這張圖理解以後，上面所有指令都串得起來：

```
    (remote: GitHub origin)
            ▲        │
     push   │        │  fetch
            │        ▼
  ┌─────────┴────────────┐
  │  local .git (歷史)    │  ← commit 寫進來；reset 改 HEAD 指標
  └─────────┬────────────┘
            ▲        │
   commit   │        │  checkout
            │        ▼
  ┌─────────┴────────────┐
  │  staging area (暫存) │  ← add 放進來；restore --staged 拿出去
  └─────────┬────────────┘
            ▲        │
    add     │        │  restore
            │        ▼
  ┌─────────┴────────────┐
  │  working tree (你看的檔案)│
  └──────────────────────┘
```

四個「層」：working tree、staging、local .git、remote。
動作就是在「搬」東西上下層之間移動。弄懂這張圖，`add/commit/push/fetch/pull/reset` 全部變成**搬運方向**的變體。

---

## 7. 不該 Panic 的錯誤訊息

| 錯誤訊息 | 實際意思 | 怎麼辦 |
|---|---|---|
| `Not possible to fast-forward, aborting.` | 本地有 origin 沒有的 commit，`--ff-only` 拒絕合 | 先 `git log origin/main..HEAD` 看本地多了什麼；真的要丟就 `git reset --hard origin/main` |
| `Your branch is behind 'origin/main' by N commits` | 你 fetch 了但還沒 merge | `git merge --ff-only origin/main` |
| `Your branch is ahead of 'origin/main' by N commits` | 你有本地 commit 還沒 push | `git push` |
| `fatal: not a git repository` | 當前目錄不是 git repo（可能 cd 錯路徑） | `cd` 回 `~/news_radar` |
| `rejected ... non-fast-forward` | push 時遠端有新 commit 你沒有 | `git fetch && git merge --ff-only origin/<branch>` 後再 push；若真要強推再 `--force-with-lease` |

---

## 8. 相關文件連結

- 三方 Mac × Cloud × GitHub 架構：[`System_Architecture.md` §5.3](./System_Architecture.md#53-hybrid-三方同步視覺圖mac--cloud--github)
- state 分支 orphan commit 協議：[`System_Architecture.md` §5.1](./System_Architecture.md#51-the-orphan-commit-pattern)
- push_state.sh 的 post-condition 檢查：[`System_Architecture.md` §5.2](./System_Architecture.md#52-what-push_statesh-adds-new-2026-04-22)
