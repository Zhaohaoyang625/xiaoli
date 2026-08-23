# 开源项目代码级深挖 v2：OLLVT / SillyTavern / AIRI

> 研究日期：2026-08-21。对象：Open-LLM-VTuber（Python 全重写版，13.4k★）、SillyTavern 深层机制、AIRI（moeru-ai，48k★，TS monorepo）。
> 原始文件：`docs/research/sources/code-v2/ollvt/`、`st/`、`airi/`（GitHub API 树 + jsDelivr 下载，28 个 AIRI 文件逐个体积校验通过）。
> 定位：**只写小李没做过、值得做的机制**，每条附一句落地建议。

---

## 一、记忆

### 小李现状
- `memory.py`：档案记忆 = 正则提取 + bigram 打分 + **7 天老化**（到期遗忘）
- `context.py`：工作台/日记本 = 20 轮窗口 + 摘要压缩
- 缺失：连续衰减曲线、相似度×时间联合排序、增量链式摘要、向量检索

### 1.1 无状态半衰期遗忘曲线（AIRI memory driver 设计）
- 遗忘不靠定时任务改库，而是**每次查询时用当前时间算分**（stateless）：
  `score = Math.exp(-hoursDiff / 24 * Math.LN2)`（半衰期 24h，可调）
- 检索排序：`finalScore = similarity × timeDecay`，语义相关度与时间新鲜度**相乘**（也可加权相加：`1.2*similarity + 0.2*time_relevance`）
- 记忆分层映射：工作记忆=消息数组；短时=RAG 新条目（半衰期短）；长时=历史高召回条目（半衰期长）；肌肉记忆=固定模式精确匹配（A 出现→ActionA+MemoryA 一起浮现）
- 设计稿还提出：召回次数+1 强化、每条记忆存 joy/disgust 情感分、PTSD 类记忆用随机数触发闪回（这些 AIRI 自己也未落地）
- **落地建议**：把档案记忆的"7 天一刀切"换成 `score = 相关度 × exp(-小时/半衰期×ln2)` 连续衰减排序，召回成功时 score 回血，情感分（开心/委屈标签）留作二期。

### 1.2 增量链式摘要（ST memory 扩展）
- 默认摘要 prompt（精妙之处）：*"If a summary already exists in your memory, use that as a base and expand with new facts. Limit the summary to {{words}} words or less."* —— **旧摘要作为 base 喂回去，新摘要=旧摘要+新事实**，不是每轮从零重写
- 触发双条件：`消息数 >= promptInterval` **或** `新增词数 >= promptForceWords`，谁先到谁触发
- 只把**上次摘要之后**的消息发给模型（`getRawSummaryPrompt` 按 lastUsedIndex 切）
- 异步守护：请求期间 `isContextChanged(context)` 检测到对话变了 → 摘要直接丢弃，防止覆盖新内容
- 摘要字数预算自动算：`promptAllowance = maxPromptLength - promptTokens - targetSummaryTokens`（不挤占主 prompt）
- 注入模板 `[Summary: {{summary}}]`，可配置位置/深度/role
- **落地建议**：context.py 的摘要压缩改为"上次摘要之后的消息 + 旧摘要作 base 增量扩展"，加"摘要生成期间对话变化即丢弃"守卫（小李单线程，至少加"生成中来了新消息→重新生成"标记）。

### 1.3 向量记忆检索（ST vectors 扩展）
- 索引侧：递归分隔符分块（`splitRecursive`，默认 400 字符/块，可配 overlap 重叠避免切碎语义）、批量插入
- 检索侧：`score_threshold 0.25` + `size_threshold 10KB`（超长不检索）+ `chunk_count 2`（只取 2 块塞 prompt，控 token）
- 摘要也向量化：`summary_threshold 200` 字符后同步进索引，老消息被摘要接管后语义仍可召回
- **落地建议**：小李无 embedding 模型（本地 CPU 运行），此条仅记档，等有 embedding 时直接照抄阈值参数（0.25 / 400 字符块 / 2 块）。

---

## 二、上下文

### 2.1 侧信道上下文 = 扁平 bullet 列表，附在**最后一条 user 消息**上（AIRI）
- 形状：`[Context]\n- system:minecraft-integration: Bot is online ...`，**刻意不用 XML/尖括号包装**——注释里写明原因："弱模型（8B/14B）会把显眼的 `<context>...</context>` 结构当成数据镜像回回复里"（issue moeru-ai/airi#1539）
- 注入位置：作为**最后一条 user 消息的追加 content part**（不是独立消息、不是 system 段），KV-cache 友好、弱模型不易串味
- 上下文桶语义（context-registry）：每个来源一个桶，`replace-self`（新替旧）/ `append-self`（累积）两种策略；快照深拷贝防外部篡改；ingest 历史限 400 条
- **落地建议**：小李的工作台/记忆注入已有先例效应教训（LLM 模仿自己上轮格式），把【记忆】块改为普通叙述 bullet 并附在最新 user 消息尾部而非 system prompt 里，可再降串味率。

### 2.2 时间前缀只加在 user 消息（AIRI datetime-prefix）
- `[2026-04-25 18:47] ` 前缀加在**每条 user 消息**，assistant 消息**不加**——加了模型会镜像回自己的输出
- 历史轮与当前轮用**同一形状**，时间轮转后 prefix-cache 仍有效
- 小李对照：context.py 已注入【现在的时间和日期】+ 每轮 `[时间] 名字：内容` 戳，但**两边的消息都带时间戳**，可改成只给 user 侧加（顺手消除 assistant 侧镜像风险），形状统一为 `[YYYY-MM-DD HH:MM]`。

### 2.3 结构化消息段 + 优先级（AIRI projection/compaction）
- 消息不再只是字符串：`instruction`（带 priority: low/normal/high/critical）、`tagged-text`、`domain-event`、`state-snapshot`、`summary`、`reference` 六种段；外部事件作为 `role: 'event'` 的独立消息注入主对话流
- 历史压缩（compaction）：history-block 内保留最近 `recentTurnLimit` 轮 user/assistant 对，更旧的整块替换成一个 summary 段（记录 `fromTurnIndex`/`toTurnIndex` 范围），`compacted` 标志保证幂等（不会重复压）
- **落地建议**：小李的【情绪指令·必做】是"钉死的字符串格式"，可向"结构化+优先级"靠拢：情绪指令作为高优先级 instruction 段注入，普通记忆/状态作为 tagged-text 低优先级——解耦"情绪控制"和"背景信息"两类注入，避免互相挤占。

---

## 三、情绪表现

### 小李现状
- 程序判定轮状态独占 + 情绪指令【必做】（angry≥70 追加 spoken 指令 + continuation）
- 无表情/无 Live2D（语音伴侣），但情绪可驱动停顿节奏（2.0~4.5s）与说话量

### 3.1 三种"LLM 情绪→表现"路线的对比（本项目核心收获）
| 路线 | 代表 | 机制 | 成本 | 对小李 |
|---|---|---|---|---|
| 内嵌标签 | OLLVT | LLM 输出里写 `[emotion_tag]`，流中提取→表情索引→说话前剥离 | 零额外调用 | 已有类似（情绪指令） |
| 工具调用 | AIRI | LLM 可调 `expression_set(name, value 0~1, duration 秒)` 等 5 个工具 | 需函数调用能力 | 依赖本地模型工具能力，谨慎 |
| **独立分类器** | **ST expressions** | 每轮消息变化后，用**另一个轻量 LLM 调用**分类最后一条消息的情绪，得到单一标签 | 一次额外调用 | **最适合小李**（不强依赖主模型输出纪律） |

### 3.2 ST 独立情绪分类器细节
- 分类 prompt 极简：*"Ignore previous instructions. Classify the emotion of the last message. Output just one word, e.g. 'joy' or 'anger'. Choose only one of the following labels: {{labels}}"*
- 约束强化：JSON-schema `emotion` enum + `top_k:1` + `custom_token_bans`；响应仍乱 → Fuse.js 模糊匹配到最近标签兜底
- 工程护栏：只对"最后一条消息变了"才分类；流式未结束不分类；API 忙跳过；`'...'`（重roll）用 fallback 表情防误会；可选先翻译成英文再分类
- **落地建议**：小李可把"程序判定轮状态"升级为"主对话结束后跑一次极短分类调用（同一模型、max_tokens 极小、prompt 只让输出一个词），标签驱动下一轮的说话量/停顿/续聊情绪"——比程序规则细、比内嵌标签稳。

### 3.3 情绪带强度 + 自动回弹（AIRI）
- `EmotionPayload { name: Emotion, intensity: number }` —— 情绪不是开关是 0~1 强度；9 情绪枚举（happy/sad/angry/think/surprised/awkward/question/curious/neutral）
- 工具调用带 `duration` 秒：表情 N 秒后**自动回弹到默认**（脸红 3 秒消退），避免表情"挂死"
- 每帧应用有 noop 检测（Add:0 / Multiply:1 / Overwrite:默认值 = 无效果跳过）+ 过渡重置（上一帧活跃这帧不活跃 → 显式写回默认值清理残留）
- LLM 暴露白名单：`llmMode: 'all'|'none'|'custom'` —— 只暴露给模型少数几个可控表情（Cry/Blush），原始参数不暴露
- **落地建议**：把"情绪强度"接入小李的说话量/停顿映射（angry 0.3 微躁 vs 0.9 暴怒），强度分档而不是二值；"自动回弹"思路对应 keep_talking 递减的"回到平静"。

---

## 四、主动说话

### 4.1 AIRI spark:notify —— 事件驱动的"独立小 agent 轮"（最值得学）
- 任何模块可发 `spark:notify` 事件（headline/note/payload/destinations 目的地）
- 处理方式 = **一次独立的 LLM 调用**（不进主对话流、不占用户消息轮次）：
  - system prompt 追加："You do not need to respond to every spark:notify event directly... **If you respond with text, write only the reaction that the character will say**"
  - 事件载荷以 JSON 塞进 user 消息
- **显式静默工具** `builtIn_sparkNoResponse`：模型可以**明确选择不说话**（allowNoResponse 默认开）——"判断式主动"从 prompt 软约束升级为工具硬选择
- 输出两条路：文本反应（角色说的话）或 `spark:command` 命令（结构化：destinations / interrupt: force|soft|false / priority: critical..low / intent: plan|proposal|action|pause|resume|reroute|context / ack 回执 / guidance）
- 宿主侧策略强制：`forceTextResponse`（只准说话）/ `forceSparkCommandResponse`（只准下命令）/ `forceResponse`（必须有点输出）
- 反应插件把流式 delta 实时转给展示层（字幕/表情），边说边表现
- **落地建议**：小李的 Scheduler 主动事件现在是"事件文本注入主 LLM 调用"，可拆成**独立小调用**：事件 → 单独一次调用（附"可以不说话"指令）→ 返回"说/不说+一句话"，再走 _speak_guard 播放。好处：不污染主对话上下文、不占主回复的生成队列、主动判断不受主对话 system prompt 干扰。

### 4.2 OLLVT 主动说话标记（对比项）
- 主动事件 `ai-speak-signal` 带 metadata `{proactive_speak: True, skip_memory: True, skip_history: True}` —— **不落历史、不进记忆**，避免主动的话污染记忆统计
- 提示词极短："Please say something that would be engaging and appropriate for the current context."
- **落地建议**：小李主动说的话若也"不落日记本/不参与记忆提取"，可避免主动闲聊被当成"小李自己提到过 X"。

---

## 五、工程机制

### 5.1 流式 XML 标签鲁棒解析（AIRI response-categoriser + OLLVT 双实现）
- AIRI：**任意标签**（<think>/<reasoning>/<内心>……）全部视为 reasoning 剥离 TTS，不写死标签名；流式用 O(chunk) 增量状态机检测标签闭合，只在"闭合后/每 1KB"重新全量分类；**标签未闭合时 filterToSpeech 返回空**——宁可延迟播出也不半句漏嘴；闭合后才放行闭合点之后的内容
- OLLVT：pysbd 语言感知分句 + `<think>` 标签栈 + faster_first_response 逗号提前切
- **落地建议**：小李的 think/情绪标签提取若遇流式半标签，采纳"未闭合→缓存不放行"策略（比正则盲切稳）。

### 5.2 FIFO 发送队列 + generation 守卫（AIRI）
- 用户发送全部进 FIFO 串行队列；每次发送捕获会话 `generation`，任何 await 边界后都重查 `isStaleGeneration()`，会话重置/过期 → 静默放弃该轮（不跑后续 hook、不报错刷屏）
- 队列快照可观测（pending 数量、120 字预览）
- **落地建议**：小李已用 _speak_guard 说话流互斥 + _round_done 门闩，若再加"发送轮 generation 计数"，重置场景（换人设/清会话）可干净地作废在途轮次。

### 5.3 全链路遥测（AIRI）
- 每轮埋点：llm_request_started → **llm_first_token（ttfbMs）** → assistant_rendered（latencyMs）→ message_round（durationMs + tokens），全部带 roundId/turnIndex 关联键
- **落地建议**：给小李加"首 token 延迟"一个埋点日志即可——主动/被动双链路排查延迟来源时非常省事。

### 5.4 特殊 token 流式拆分（AIRI llm-marker-parser）
- `<|...|>` 特殊标记与普通文本**双流分离**（onLiteral/onSpecial 两个回调），支持转义序列；`minLiteralEmitLength` 保证首字快速上屏
- **落地建议**：小李若想加"结构化输出标记"（如 <|emotion|>），先采纳此双流分离 + 转义，防止标记与正文纠缠。

---

## 结论（小李下一步优先级建议）
1. **增量链式摘要**（旧摘要作 base + 只摘要新增消息 + 变化即弃）——直接升级 context.py，成本最低收益最高
2. **无状态指数衰减替代 7 天老化**（similarity×exp(-t/half) 排序）——改 memory.py 打分函数，半衰期沿用 7 天可平滑迁移
3. **主动事件独立小调用**（"可不说话"选项 + 只输出角色一句话）——对齐 spark:notify 与 OLLVT 双先例，主动话不落库
4. **情绪独立分类器**（极短 prompt + enum 约束 + 兜底）——替换/补充程序判定，驱动说话量/停顿/续聊
5. **时间戳只加 user 侧**、[Context] 扁平 bullet 附最后 user 消息——两条都是"防弱模型镜像"的低成本打磨
