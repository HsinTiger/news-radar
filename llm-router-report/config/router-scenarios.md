# CCR 路由场景大全 — 配置参考手册

> 本文档收录 ccr (Claude Code Router) 在 news_radar / substack pipeline 中可能用到的**所有路由场景**，每种场景附带完整 JSON 配置和适用说明。  
> **更新**: 2026-05-31 — 包含 Gemini 3.5 Flash、Groq Qwen3-32b、Llama-4-Scout-17b 等 May 2026 新模型。

---

## 快速参考表

### 所有 Provider 一览

| Provider | CCR 名称 | 连入方式 | API Base |
|----------|----------|---------|----------|
| **Gemini** (Key 1) | `gemini` | Google GenAI API | `https://generativelanguage.googleapis.com/v1beta/models/` |
| **Gemini** (Key 2 + Pro) | `gemini2` | 同上（另一把 key） | 同上 |
| **Groq** | `groq` | OpenAI-compatible | `https://api.groq.com/openai/v1/chat/completions` |
| **Cerebras** | `cerebras` | OpenAI-compatible | `https://api.cerebras.ai/v1/chat/completions` |
| **OpenCode Zen** | `opencode` | OpenAI-compatible | `https://opencode.ai/zen/v1/chat/completions` |
| **LiteLLM Gateway** | `litellm` | OpenAI-compatible (local) | `http://127.0.0.1:4000/v1/chat/completions` |

### 所有可用模型 (含 May 2026 新模型)

#### Gemini (gemini / gemini2)

| 模型 | 上线时间 | Context | 免费配额 | RPM | 备注 |
|------|---------|---------|---------|-----|------|
| gemini-2.5-flash | 2026 Q1 | **1M** | ~20 req/day per key | 未公开 | 主力, 免费 tier 最高 context |
| gemini-2.5-pro | 2026 Q1 | >200K | ~20 req/day per key | 未公开 | 更强推理, 适合复杂任务 |
| **gemini-3.5-flash** | **2026-05** | 1M (预估) | ~20 req/day per key | 未公开 | 新一代 Flash, 更快更强 |
| **gemini-3.1-flash-lite** | **2026-05** | 1M (预估) | ~20 req/day per key | 未公开 | 低成本轻量版, 适合短任务 |
| ~~gemini-2.0-flash~~ / ~~gemini-2.0-flash-lite~~ | 2024-2025 | — | 已归零 | — | 2026-06-01 shutdown, 弃用 |

**注意**: 实际免费配额仅 ~20 req/day per project per model (Google 文件宣称的 1,500 不适用于此 project 的用量模式)。两把 key (gemini + gemini2) 合计 ~40 req/day。

#### Groq (groq)

| 模型 | 上线时间 | Context | RPM | RPD | TPM | TPD | 适合 |
|------|---------|---------|-----|-----|-----|-----|------|
| llama-3.3-70b-versatile | 2025 | 131K | 30 | 1,000 | 12K | 100K | 通用任务 (主力) |
| openai/gpt-oss-120b | 2025 | 131K | 30 | 1,000 | 8K | 200K | 通用备选 |
| llama-3.1-8b-instant | 2025 | 131K | 30 | **14,400** | 6K | 500K | **背景/评分批量短任务** |
| **qwen3-32b** | **2026-05** | 131K | 60 | 1,000 | 6K | 500K | **最高 RPM**, scorer 神器 |
| **llama-4-scout-17b** | **2026-05** | 131K | 30 | 1,000 | 30K | 500K | 稍长任务, 高 TPM |
| ~~mixtral-8x7b~~ | 2024-2025 | — | — | — | — | — | Groq 免费已移除 |

**注意**: llama-3.1-8b-instant 有 14,400 RPD (Groq 最高), 是背景批量短任务的最佳选择。qwen3-32b 有 60 RPM (其他模型仅 30)。

#### Cerebras (cerebras)

| 模型 | 参数 | 全 Context | 免费 Context | RPM (免费) | TPM | TPD | 备注 |
|------|------|-----------|-------------|-----------|-----|-----|------|
| zai-glm-4.7 | 355B/32B MoE | 131K | **~65K** | 5 | 30K | 1M | 免费 context ~65K, 长文可用 (但 RPM 5 极低) |
| gpt-oss-120b | 120B Dense | 131K | **~65K** | 5 | 30K | 1M | 免费 context 稍大但 RPM 极低 |

**注意**: Cerebras 免费层的 context (zai-glm-4.7 ~65K, gpt-oss-120b ~65K) 对 news_radar soul bundle (~17K) 足够。真实瓶颈是 **RPM 5**。Developer tier $10/mo 可解锁 500+ RPM + 完整 131K context。

#### OpenCode Zen (opencode)

| 模型 | 真实身份 | Context | 价格 | 稳定性 |
|------|---------|---------|------|--------|
| big-pickle | stealth, maker unknown | ~200K (传闻) | **限免** (for a limited time) | ⚠️ 随时可能转付费或关闭 |

**风险最高**: 适合作为 Claude + Gemini 同时挂掉时的长文兜底, 但不可依赖为长期架构。

#### LiteLLM Gateway (litellm)

| LiteLLM 别名 | 指向真实模型 | 轮换策略 |
|-------------|------------|---------|
| `gemini-flash` | gemini-2.5-flash | 两把 Gemini key load-balance + 撞限换 key |
| `gemini-35-flash` | **gemini-3.5-flash** (May 2026) | 同上, 两把 key |
| `big-pickle` | opencode/big-pickle (stealth, maker unknown) | 单 key |
| `groq-oss` | groq/openai/gpt-oss-120b | 单 key |
| `cerebras-glm` | cerebras/zai-glm-4.7 | 单 key |
| `groq-8b` | **groq/llama-3.1-8b-instant** (May 2026 加入) | 单 key, 高 RPD |
| `groq-qwen3` | **groq/qwen3-32b** (May 2026 加入) | 单 key, 高 RPM |

**LiteLLM fallback 链** (由 `router_settings.fallbacks` 定义):
```
gemini-flash → gemini-35-flash → big-pickle → groq-oss → cerebras-glm → groq-8b → groq-qwen3
```

### 各 Router Mode 适用场景

| Mode | 适用任务 | 推荐 context | 推荐延迟 |
|------|---------|-------------|---------|
| `default` | 日常聊天、编码、写作 | 高 (1M) | 中 |
| `background` | 评分、分类、批量处理 | 低 (~8K) | **极快** (>14K RPD) |
| `think` | 深度推理、架构决策、长文反思 | 中-高 (200K+) | 慢 (可以等) |
| `longContext` | 长上下文回顾、大型重构 | **极高** (1M) | 慢 |
| `webSearch` | 网络搜索相关 | 高 | 中 |

---

## 场景 1: 正常生产路由 (全走 LiteLLM)

> **现状配置** — 所有 Router mode 都走 `litellm,*`。LiteLLM gateway 负责 key 轮换 + provider fallback, ccr 只负责按 mode 分流。

```json
{
  "Router": {
    "default": "litellm,gemini-flash",
    "background": "litellm,groq-oss",
    "think": "litellm,big-pickle",
    "longContext": "litellm,gemini-flash",
    "longContextThreshold": 60000,
    "webSearch": "litellm,gemini-flash",
    "image": ""
  }
}
```

**何时使用**: 日常运行。LiteLLM 由 launchd 管理 (KeepAlive, 死掉 ~6s 自动拉起), 稳定性高。

**流量路径**:
```
claude CLI → ccr(:3456) → LiteLLM(:4000) → Provider API

default / longContext / webSearch:  → gemini-2.5-flash (双 key 轮换)
background:                         → groq/gpt-oss-120b
think:                              → opencode/big-pickle (stealth, ~200K)
```

**Fallback 链** (LiteLLM 自动处理):
```
gemini-flash → gemini-35-flash → big-pickle → groq-oss → cerebras-glm → groq-8b → groq-qwen3
```

**优点**:
- 双 Gemini key 自动 load-balance, 配额的 429 自动换 key
- LiteLLM fallback 链覆盖 6 个平台, 几乎不会完全不可用
- 所有 mode 都有独立 fallback, 互不影响

**缺点**:
- 完全依赖 LiteLLM 存活 → 如果 LiteLLM 挂了, ccr 不会自动降级到直连 provider
- LiteLLM 进程 (~100MB RAM) 比直连多一层开销 (但延迟 ~10ms 可忽略)

---

## 场景 2: LiteLLM 紧急绕过 (ccr 直连 Provider)

> 当 LiteLLM gateway 故障 (非预期的: 配置损坏 / Python 环境坏了 / 端口被占用) 且 launchd KeepAlive 也未能恢复时, 绕过 LiteLLM, ccr 直连各 Provider。

```json
{
  "Router": {
    "default": "gemini2,gemini-2.5-flash",
    "background": "groq,llama-3.1-8b-instant",
    "think": "opencode,big-pickle",
    "longContext": "gemini,gemini-2.5-flash",
    "webSearch": "gemini2,gemini-2.5-flash",
    "image": ""
  }
}
```

**何时使用**: LiteLLM 不在线且你不方便修复它 (凌晨 pipeline 正在跑 / 手动 claude 突然报 proxy error)。

**流量路径**:
```
claude CLI → ccr(:3456) → Provider API (直连, 无 :4000 转发)

default / webSearch:  → gemini2 (第二把 key, gemini-2.5-flash)
background:           → groq/llama-3.1-8b-instant (14,400 RPD 高吞吐)
think:                → opencode/big-pickle stealth, maker unknown, 200K 深度推理)
longContext:          → gemini (第一把 key, gemini-2.5-flash, 1M context)
```

**优点**:
- 移除 LiteLLM 依赖, 减少一层故障点
- 延迟更低 (直连, 不经过 :4000 中转)
- background 改用 llama-3.1-8b-instant (14,400 RPD), 比 LiteLLM 链中的 gpt-oss-120b (1,000 RPD) 更激进

**缺点**:
- **无 key 轮换**: 每个 provider 只有一把 key, 撞 429 后没有备选 key
- **无 fallback**: 如果 gemini2 挂了, default mode 直接报错 — 不会自动退到 groq 或 big-pickle
- longContext 用 gemini (第一把 key), default 用 gemini2 (第二把 key) — 故意错开两把 key 的配额消耗, 减少单把 key 被打满的概率

**恢复 LiteLLM** (适合在紧急模式下同时进行):
```bash
# 检查 LiteLLM 状态
launchctl list | grep litellm

# 手动重启
launchctl unload -w ~/Library/LaunchAgents/com.hsin.litellm-gateway.plist
launchctl load -w ~/Library/LaunchAgents/com.hsin.litellm-gateway.plist

# 验证
curl -s http://127.0.0.1:4000/health/readiness
```

---

## 场景 3: Gemini 3.5 Flash 专项测试

> 测试 May 2026 新推出的 Gemini 3.5 Flash 模型。所有 mode 全部走 Gemini 3.5 Flash, 便于评估速度、质量和 Context 上限。

```json
{
  "Router": {
    "default": "gemini2,gemini-3.5-flash",
    "background": "gemini2,gemini-3.5-flash",
    "think": "gemini2,gemini-3.5-flash",
    "longContext": "gemini,gemini-3.5-flash",
    "webSearch": "gemini2,gemini-3.5-flash",
    "image": ""
  }
}
```

**何时使用**:
- May 2026 新模型刚上架, 需要全面评估其能力 (推理质量、coding 能力、JSON 结构化产出)
- 对比 Gemini 2.5 Flash vs 3.5 Flash 在 news_radar 场景下的表现
- 确认 Gemini 3.5 Flash 在 CCR 直连 + LiteLLM 两种路径下都能正常工作

**变体 — 通过 LiteLLM 测试** (可以享受双 key 轮换):
```json
{
  "Router": {
    "default": "litellm,gemini-35-flash",
    "background": "litellm,gemini-35-flash",
    "think": "litellm,gemini-35-flash",
    "longContext": "litellm,gemini-35-flash",
    "webSearch": "litellm,gemini-35-flash",
    "image": ""
  }
}
```

**注意事项**:
- Gemini 3.5 Flash 的免费配额也是 ~20 req/day per key。如果所有 mode 都用这个模型, 两把 key 约 40 req/day — 建议仅在低负载时测试
- LiteLLM 已预设 `gemini-35-flash` 别名, 双 key 同名 → 自动 load-balance。如果 LiteLLM 用 `gemini-35-flash` 做 fallback (见场景 1 的 LiteLLM fallback 链), 注意它会排在 `gemini-flash` 之后作为第 2 级兜底
- think mode 用 Gemini 3.5 Flash 可能导致深度推理质量下降 (对比 big-pickle 或 Opus)。仅在评估/对比时使用

**回退**:
```bash
# 在 CCR 中强制载入场景 1 的配置即可切换回正常模式
ccr /config set Router.default litellm,gemini-flash
```

---

## 场景 4: 最大免费 tier (不依赖 Claude Max)

> 完全不使用 Claude Max 收费订阅, 全部走免费 Provider。适合 Claude Max 配额用完 (5hrs rolling 耗尽) 或临时不想消耗 Max 额度时使用。

```json
{
  "Router": {
    "default": "gemini2,gemini-2.5-flash",
    "background": "groq,llama-3.1-8b-instant",
    "think": "opencode,big-pickle",
    "longContext": "gemini,gemini-2.5-flash",
    "webSearch": "gemini2,gemini-2.5-flash",
    "image": ""
  }
}
```

**或者通过 LiteLLM (同样全免费, 但享受 fallback)**:
```json
{
  "Router": {
    "default": "litellm,gemini-flash",
    "background": "litellm,groq-8b",
    "think": "litellm,big-pickle",
    "longContext": "litellm,gemini-flash",
    "webSearch": "litellm,gemini-flash",
    "image": ""
  }
}
```

**何时使用**:
- 月底 Claude Max 额度即将耗尽 / 已耗尽
- 运营全免费 pipeline 做测试验证
- 长时间运行非关键任务 (如批量回顾、数据重新分类)

**承载能力估算**:

| 平台 | 每天可处理 | 分配合适的任务 | 够用? |
|------|-----------|--------------|-------|
| Gemini 2 keys (default/longContext/webSearch) | ~40 calls/day | composer (长文), 主脑 | ⚠️ 勉强, 需精打细算 |
| Groq llama-3.1-8b (background) | 14,400 calls/day | scorer (短任务) | ✅ 绰绰有余 |
| OpenCode big-pickle (think) | 未公开 (~200K context) | 深度推理 | ? 未知, 但限免随时消失 |
| Cerebras (fallback) | 1M TPD / 5 RPM | 短~中文本 | 🟡 ~65K 夠但 RPM 5 極低 |

**关键限制**:
- Gemini 2 keys 合计 ~40 calls/day — 如果一天要跑 250 calls, defaut 和 think 之外的调用都要分流到 Groq
- Cerebras free context (~65K) 对 composer (soul bundle ~17K) 足够 — 但 RPM 5 会拖慢长文生成速度
- big-pickle (OpenCode) 是限免状态 — 随时可能消失, 这份配置有单点故障风险

**建议**: 在 Max 额度恢复后尽快切回场景 1。

---

## 场景 5: Groq-heavy (优先 Groq 高 RPD 模型)

> 优先使用 Groq 平台的高 RPD (Requests Per Day) 模型处理背景任务, 同时保留 Gemini 和 big-pickle 做长文。适合需要大量短任务吞吐的场景。

```json
{
  "Router": {
    "default": "groq,qwen3-32b",
    "background": "groq,llama-3.1-8b-instant",
    "think": "opencode,big-pickle",
    "longContext": "gemini,gemini-2.5-flash",
    "webSearch": "groq,qwen3-32b",
    "image": ""
  }
}
```

**或者通过 LiteLLM (能利用 Groq 多模型别名)**:
```json
{
  "Router": {
    "default": "litellm,groq-qwen3",
    "background": "litellm,groq-8b",
    "think": "litellm,big-pickle",
    "longContext": "litellm,gemini-flash",
    "webSearch": "litellm,groq-qwen3",
    "image": ""
  }
}
```

**何时使用**:
- 后台有大量短任务需要执行 (如重新评分数万条历史文章)
- 一次性批量处理 (从凌晨跑到早上, 需要高吞吐)
- Groq 的 qwen3-32b (60 RPM) 可以比其它 Groq 模型快一倍

**Groq 模型对比 (为什么这样分配)**:

| Router Mode | 模型 | RPD | RPM | 原因 |
|------------|------|-----|-----|------|
| default | qwen3-32b | 1,000 | **60** | 日常任务需要最低延迟, 60 RPM 是 Groq 最快 |
| background | llama-3.1-8b-instant | **14,400** | 30 | 批量短任务吃 RPD 配额, 14,400 远超其他模型 |
| think | big-pickle | 未公开 | 未公开 | 深度推理需要 200K context, Groq 最大 131K 不够 |
| longContext | gemini-2.5-flash | ~20/day | 未公开 | 长上下文只有 Gemini (1M) 能胜任 |
| webSearch | qwen3-32b | 1,000 | 60 | 搜索通常配简短 prompt, 快速响应为佳 |

**注意事项**:
- Groq 的 RPD 是 per-organization 的。一个账号下, llama-3.1-8b (14,400 RPD) 和 qwen3-32b (1,000 RPD) 的配额是**独立计算**的, 不互相抢占
- 但如果 default 和 background 都走同一个模型, 配额会叠加消耗 — 所以这里故意用两个不同模型错开
- think 和 longContext 保留非 Groq 的优质长文模型

---

## 场景 6: Think Mode 深度推理 (big-pickle / Gemini 3.5 Flash)

> 将 think mode 配置为最强的免费深度推理模型。big-pickle stealth, maker unknown, ~200K context) 的 context 优于 Groq (131K), 且推理质量高于短模型。Gemini 3.5 Flash 是 May 2026 新加入的备选。

```json
{
  "Router": {
    "default": "litellm,gemini-flash",
    "background": "litellm,groq-8b",
    "think": "opencode,big-pickle",
    "longContext": "litellm,gemini-flash",
    "webSearch": "litellm,gemini-flash",
    "image": ""
  }
}
```

**变体 — 用 Gemini 3.5 Flash 做 think** (对比 big-pickle):
```json
{
  "Router": {
    "default": "litellm,gemini-flash",
    "background": "litellm,groq-8b",
    "think": "gemini2,gemini-3.5-flash",
    "longContext": "litellm,gemini-flash",
    "webSearch": "litellm,gemini-flash",
    "image": ""
  }
}
```

**变体 — 全免费的 big-pickle 深度会话** (适用于长 session 深度编码):
```json
{
  "Router": {
    "default": "opencode,big-pickle",
    "background": "litellm,groq-8b",
    "think": "opencode,big-pickle",
    "longContext": "opencode,big-pickle",
    "webSearch": "litellm,gemini-flash",
    "image": ""
  }
}
```

**何时使用**:
- 编写复杂代码 / 架构决策 / 长反思时, 需要 >131K context 的深度推理
- big-pickle stealth, maker unknown) 的 ~200K context 可以将整个 news_radar 代码库 + 配置一次塞进上下文
- 与 Claude Max 对比 big-pickle 的深度推理质量
- 评估 Gemini 3.5 Flash (May 2026 新模型) 的 think 表现

**big-pickle vs Gemini 3.5 Flash for think**:

| 维度 | big-pickle stealth, maker unknown) | Gemini 3.5 Flash |
|------|---------------------|-------------------|
| Context | ~200K | 1M (预估值) |
| 免费状态 | 限免 (随时消失) | 免费 (Google 稳定提供) |
| 推理质量 | 中等偏上 (对比 GLM-4 系列) | 未知 (May 2026 新模型) |
| 速度 | 未知 | 快 (Flash 系列) |
| 可靠性 | OpenCode 稳定性一般 | Google 基础设施稳定 |

**注意事项**:
- big-pickle 是 OpenCode Zen 的限免模型。如果在 think mode 中深度依赖它, 建议每周执行一次健康检查:
  ```bash
  curl -s https://opencode.ai/zen/v1/models | python3 -c "import sys,json; data=json.load(sys.stdin); print('ok' if any(m.get('id')=='big-pickle' for m in data.get('data',[]) if isinstance(m,dict)) else 'MISSING')"
  ```
- Gemini 3.5 Flash 的免费 context 大概率也是 1M, 但每 key 只有 ~20 req/day — 高负载 think 可能会迅速耗尽配额

---

## 场景 7: Background / Scorer 高吞吐 (llama-3.1-8b-instant)

> Background mode 专门配置为 Groq 的 llama-3.1-8b-instant (14,400 RPD), 适合 scorer 这种短文本批量处理任务。这是所有场景中 RPD 最高的 background 配置。

```json
{
  "Router": {
    "default": "litellm,gemini-flash",
    "background": "groq,llama-3.1-8b-instant",
    "think": "litellm,big-pickle",
    "longContext": "litellm,gemini-flash",
    "webSearch": "litellm,gemini-flash",
    "image": ""
  }
}
```

**通过 LiteLLM 的完整链** (含 groq-8b + groq-qwen3 双备选):
```json
{
  "Router": {
    "default": "litellm,gemini-flash",
    "background": "litellm,groq-8b",
    "think": "litellm,big-pickle",
    "longContext": "litellm,gemini-flash",
    "webSearch": "litellm,gemini-flash",
    "image": ""
  }
}
```

**极极致 — 三个 Groq 模型并行给 background 做 load-balance** (不通过 LiteLLM):
```json
{
  "Router": {
    "default": "gemini2,gemini-2.5-flash",
    "background": "groq,qwen3-32b",
    "think": "opencode,big-pickle",
    "longContext": "gemini,gemini-2.5-flash",
    "webSearch": "gemini2,gemini-2.5-flash",
    "image": ""
  }
}
```

**何时使用**:
- news_radar pipeline 的 scorer 阶段需要大量短文本评分 (每篇文章约 10-20 行, 评 3-5 个维度)
- 历史数据重新分类 (数万条文章需要重新评分)
- Groq qwen3-32b 的 60 RPM 特别适合 "短时间内消化大量积压任务"
- 在其他模式 (default/think/longContext) 不消耗 Groq 配额的情况下, background 独立享用整个 Groq RPD

**Scorer 所需 context 分析**:

```
一篇文章评分: ~500-1,000 tokens input + ~200 tokens output
llama-3.1-8b-instant context: 131K → 一次可批 ~130 篇
qwen3-32b context: 131K → 一样
但 soul bundle (~17K) + instruction (~3-8K) = ~25K overhead
→ 实际每批约 100 篇
```

| 模型 | 每批篇数 | 每日可批 | 用时 (60 RPM) |
|------|---------|---------|--------------|
| llama-3.1-8b-instant | ~100 | 14,400 篇 | ~240 分钟 (4 小时) |
| qwen3-32b | ~100 | 1,000 篇 | ~17 分钟 |
| gpt-oss-120b | ~100 | 1,000 篇 | ~34 分钟 |

**结论**: llama-3.1-8b-instant 适合大量批处理 (14,400 篇/天), qwen3-32b 适合快速消化 (60 RPM, 17 分钟跑完 1,000 篇)。

---

## 附: 场景速查表

| 场景 | 配置文件 | 依赖 LiteLLM? | 依赖 Claude Max? | 日承载量 (calls) | 适合时刻 |
|------|---------|:-------------:|:----------------:|:----------------:|---------|
| 1. 正常生产 | 当前配置 | 是 | 是 | ~250+ | 日常运行 |
| 2. LiteLLM 紧急绕过 | 场景 2 | 否 | 是 | ~250+ | LiteLLM 故障 |
| 3. Gemini 3.5 Flash 测试 | 场景 3 | 可选 | 否 | ~40 | May 2026 新模型评测 |
| 4. 最大免费 | 场景 4 | 可选 | **否** | ~1,000+ | Max 额度耗尽 |
| 5. Groq-heavy | 场景 5 | 可选 | 是 | ~14,400+ (短任务) | 批量历史重跑 |
| 6. Think 深度推理 | 场景 6 | 可选 | 是 | ~40 (longContext 受限) | 架构决策/长反思 |
| 7. Background 高吞吐 | 场景 7 | 可选 | 是 | 14,400 (background) | Scorer 批量执行 |

## 附: 切换配置的方法

```bash
# 1. 临时在 CCR session 中切换 (仅在本次 session 生效)
ccr /config set Router.default litellm,gemini-flash

# 2. 编辑配置文件 (永久生效)
code ~/.claude-code-router/config.json
# 修改后重启 ccr: ctrl+C → ccr code

# 3. 保存多个配置模板, 按需复制
cp ~/.claude-code-router/config.json ~/.claude-code-router/config.normal.json
cp ~/.claude-code-router/config.json ~/.claude-code-router/config.free-tier.json
# 复制对应的 JSON 内容后再重启 ccr 即可

# 4. 如果使用 LiteLLM, 切换 model_list 也需编辑:
code ~/litellm-gateway/config.yaml
# LiteLLM 支持热重载: kill -SIGHUP $(cat ~/litellm-gateway/.pid)
```

## 附: 各平台常见错误码

| 错误 | 含义 | 应对 |
|------|------|------|
| 429 Too Many Requests | 配额耗尽 | 切换 key (LiteLLM 自动做) / 等分钟级冷却 |
| 400 Invalid Argument | 模型不支持某参数 | LiteLLM `drop_params: true` 可自动丢弃 |
| 403 Forbidden | API key 无效 | 检查 `.env` 中的 key |
| 500 Internal Server | 平台故障 | 换平台 (fallback 链自动做) |
| RESOURCE_EXHAUSTED | Gemini 免费配额用尽 | 换下一把 key (LiteLLM 或 `_try_gemini` 自动做) |
| context_length_exceeded | prompt 超过模型限制 | 缩短 prompt / 换更大 context 的模型 |
