# 2025-2026 新开源 AI 伴侣项目对照研究（2026-08-23）

> 研究方式：shields.io 实测 star 数 + jsDelivr/gh-proxy 拉源码原文验证（curl）。
> 原始材料在 `%TEMP%\xlresearch\`（本机临时目录，重装系统会丢，核心结论已在此归档）。
> 落地情况：见 design-log BD 节（睡前回想）与 comparison.md（对比更新）。

## 候选总表（按借鉴价值排序，star 全实测）

| # | 项目 | star | 一句话 | 借鉴价值 |
|---|---|---|---|---|
| 1 | Open-LLM-VTuber | 13k | 语音伴侣标杆（Live2D+情绪标签+主动+打断） | 同品类对照物 |
| 2 | Mem0 | 64k | 记忆层，ADD-only+时间推理 | 记忆引擎对照 |
| 3 | Letta | 24k | sleep-time compute（arXiv:2504.13171） | **已落地睡前回想** |
| 4 | thoughtful-agents | 44 | CHI'25，三层主动性阈值 | 主动调度对照 |
| 5 | CleanS2S | 540 | 中文 S2S，五类拟人响应 | 同栈参考 |
| 6 | Graphiti | 30k | 时间感知图谱（双时态） | 概念借鉴 |

## 关键可借鉴机制（原文引用）

### 1. Open-LLM-VTuber（13k）
- **情绪标签随句流**：`emo_str = " ".join([f"[{key}]," for key in emo_map.keys()])` 注入 prompt，LLM 回复内联 `[joy]`，`extract_emotion()` 逐句扫描、`remove_emotion_keywords()` 剥离——每句一个情绪，比"整段一条指令"更贴表情节奏
- **display/TTS 双文本**：token 流过 `sentence_divider → actions_extractor → display_processor → tts_filter` 四装饰器，`[think]` 内容只显示不朗读
- **打断注入**：`[Interrupted by user]` 可配 system/user role 注入，`handle_interrupt(heard_response)` 带听到的内容，`_interrupt_handled` 防重复注入
- **主动 skip_history=True**：主动说的话不写回历史，防 LLM 自我循环

**小李对照**：双轨 inner_thought/spoken 天然 display/TTS 分离 ✓；keep_talking 走主动链路不伪装用户消息 ✓；打断续说 5 元组带残余 ✓。逐句情绪 = 中期候选。

### 2. Mem0（64k，2026-04 重构）
- **ADD-only 单次调用**：只加不覆盖，检索时靠时间推理选"最新正确版本"——不再有 UPDATE/DELETE 决策
- **三信号融合**：语义 + BM25 + 实体匹配并行打分（旧版权重 semantic 0.6/episodic 0.3/procedural 0.1）
- 实测：每对话 ~7K token，p50 延迟 0.9-1.1s

**小李对照**：merge_fact 同内容只更新重要度不覆盖 = ADD-only ✓。BM25 关键词融合 = 可选（人名/日期类记忆向量弱）。

### 3. Letta Sleep-time Compute（24k，arXiv:2504.13171）
- 双 agent：主 agent 管实时对话 + sleep-time agent 异步整合记忆（默认每 5 步触发）
- 收益：推理成本最高降 5 倍、状态型基准 +18%
- 实践：睡眠期用便宜模型、memory_rethink 大块重写、避免过度整合破坏粒度

**小李落地**：睡前回想（context.sleep_time_integrate，23 点后整合 daily+summary，两阶段提交，见 design-log BD）。

### 4. thoughtful-agents（44，CHI'25《Proactive Conversational Agents with Inner Thoughts》）
- 三层主动性：Overt（system1_prob）/ Covert（im_threshold 1-5）/ Tonal（proactive_tone）+ 独立 interrupt_threshold
- **发言阈值和打断阈值分开设**（示例 im_threshold 3.2 / interrupt_threshold 4.5）
- 回合分配引擎（Sacks 会话分析）：`predict_turn_taking_type()` / `decide_next_speaker_and_utterance()`——轮空时 AI 自选接话
- System1/System2 双速思考：即时直觉 vs 深度反思，`evaluate_thought()` 打 1-5 动机分

**小李对照**：判断式主动 + 每日配额 2 次 + 闲聊保险 = 动机阈值/轮空接话已覆盖 ✓；打断阈值分离 = 可选（不做"她打断用户"，用户没抱怨过现有设计）。

### 5. CleanS2S（540，OpenDILab 上海 AI Lab）
- 五类人类响应模式：①中断用户 ②明确拒绝 ③敷衍 ④拉黑（访问控制）⑤标准回应
- 记忆三维度：时间信号 + 历史交互 + 关键事实
- 技术栈与小李同构（DeepSeek API + CosyVoice + FunASR）

**小李对照**："一整天不理她" = 现成拉黑模式 ✓。

### 6. Graphiti（30k）
- 双时态：`valid_at` / `invalid_at`（事实成立时间段）+ `expired_at`（系统发现失效时间）——矛盾事实不删除只标记失效，查询可回溯
- episodes 事件片段 + provenance 来源追溯；semantic + BM25 + 图谱距离 RRF 融合
- 代价：Neo4j/FalkorDB + LLM 抽取，单机 8G 显卡偏重

**小李对照**：概念借鉴（标失效不删可"翻旧账"）；不引入图数据库。

## 明确排除
- cuiole/openhui（OLV fork，仅 2 star，无实现细节）
- Somnia / Moodweaver / Virtual-Kimi / MrRuicy 系（star 过少、工程深度不足）
- Khoj（30k+ 但通用助手，非伴侣，无情绪/人设机制）

## 行动结论（已按此执行）
1. **睡前回想落地**（Letta）✓ 451 单测全绿
2. 三项对照验证达标（ADD-only / skip_history / display-TTS 分离）✓ 无需改动
3. 中期候选：逐句情绪标签（OLV）、BM25 关键词检索（Mem0）、打断阈值分离（thoughtful）——等用户拍板
