# 反面教材研究报告：大项目放弃/移除/拒绝的功能（2026-08-23）

> 研究方法（用户提出）：10k+ star 项目没做的功能，可能是**测试过效果不好才放弃的**——
> 研究"为什么不做"比学"做了什么"更有价值。证据全部来自 jsDelivr CDN 拉取的
> 源码/文档原文 + gh-proxy 代理的 GitHub API（releases/issue），非二手转述。
> 网络坑：GitHub 无法直连，gh-proxy 多个出口 token 轮流被限流，jsDelivr 单文件可靠。

## 一、各项目放弃项清单

### 1. Mem0（64k）——证据最扎实（代码+文档+用户 issue 三方印证）

**① 放弃"LLM 四选一自动决策记忆操作"（ADD/UPDATE/DELETE/NONE → ADD-only）**
- 旧版 0.1.123 prompt："You can perform four operations: (1) add, (2) update, (3) delete, (4) no change... For each new fact, decide whether to ADD / UPDATE / DELETE / NONE"——两轮 LLM 调用（抽取 + 决策）
- 新版 2.0.18 changelog："Single-Pass Extraction: replaced 2-LLM-call pipeline with additive extraction... no more UPDATE/DELETE events (#4805)"
- 原因：成本（两轮砍一轮）+ 可靠性（LLM 自动改删记忆误改误删不可控）+ 可解释性
- **代价（用户反噬，issue 仍挂）**：issue #4956/#5867——"我在 A 公司"→"我现在在 B 公司"两条矛盾记忆共存冲突，用户请求"automatic compaction"补偿

**② 放弃外部图数据库集成（Neo4j/Memgraph/Kuzu/AGE/Neptune）**
- 旧版 `enable_graph` flag + `graph_store` 配置块；2.0.18 移除，"Graph Memory is built in. There is no Neo4j... to deploy"——外部图库运维成本太高，图记忆变付费 Platform 专属

### 2. Open-LLM-VTuber（13k）——与小李最同类

**① 长时记忆/RAG 移除，且"coming back soon"一年没回来**：0.5.2 "~~RAG on chat history~~ (temporarily removed)"→ v1.0.0 删除 RAG_ON 等配置 → 1.2.0 才用 Letta 换血回归（不是重建 RAG）
**② MemGPT/Letta 集成标注 "Probably broken" / "not great" 后移除**：上游改名+API 大改 → 集成漂移失效 + 重型依赖（要强大 LLM、token 大、慢）——第三方记忆集成死亡路径
**③ v1.0.0 重写砍掉的边缘功能**：CLI 模式、退出词、SAY_SENTENCE_SEPARATELY（被并发多段合成替代）、sounddevice/playsound3 依赖、Live2D 2.1 模型支持（为 5.0 砍旧格式）

### 3. Letta / MemGPT（24k）
**① V1 整个 Python server 退役**（archive 分支，维护模式、无安全更新）→ TypeScript V2
**② 废弃清单**：Docker 镜像（不再支持）、Harness Hooks（→ Mods）、memory-block CLI（"让 agent 自己配置"）、built-in explore subagent、**periodic memory-sync reminder injection**（定时注入记忆同步提醒被删）、3-day cron auto-expiry、--system-append flag

### 4. SillyTavern（10k+）
**① 1.10.6 一次砍 10 个内置扩展**（转 downloadable add-on）：Dynamic Audio / HypeBot / Idle / **Speech Recognition** / Chat Variables / Parameter Randomizer / Smart Context-ChromaDB / RVC / Objective / D&D Dice。代码实证：26 扩展目录 → 12 个；语音识别至今未回归

## 二、跨项目重复出现的坑（多个项目同时放弃 = 大概率是坑）

1. **让 LLM 自动改/删用户记忆 → 全部翻车**：Mem0 四选一 → ADD-only（用户投诉冲突累积）；Letta 砍 memory-block CLI；OLVT 的 MemGPT/Mem0 集成全 broken 后移除。**行业收敛方向："只增不改 + 显式更新"**
2. **外部图数据库/重基础设施记忆方案被砍**：Mem0 移除 Neo4j、OLVT 移除 chromadb RAG——单机对话产品经不起额外基础设施
3. **第三方记忆/agent 集成漂移即死**：上游改名/换 API = 你的集成被判死刑
4. **大版本重写 = 边缘功能集中陪葬，"coming back soon"往往不回来**
5. **语音/播放依赖反复更换**：sounddevice/playsound3 被砍、embedder 更换、播放库重做——播放链是反复重做部位

## 三、对小李的启示（别做/慎做清单）

1. **别让 LLM 自动更新/删除记忆**——小李"增加+频率衰减+M7 封顶"正确；矛盾修正走显式规则（见下），不做 LLM 决策
2. **慎加图数据库**——bge-small 本地嵌入方案已验证正确
3. **"暂时移除"= 长期移除**——砍功能要么彻底砍要么立刻给替代
4. **重写先保核心链路**——语音链路+情绪状态机+记忆引擎是核心资产；网页形象/终端命令属第一波被牺牲对象
5. **AI 主动说话保持"有限主动"**——小李每日 2 次配额+夜间静默已是行业更激进收敛方向，别再扩大
6. **外部依赖一键降级链**——TTS 本地→火山→edge 三层正确；DeepSeek 若改版需同样兜底

## 四、已落地：矛盾记忆消解（Mem0 issue #4956 教训 → 小李 v2 M9）

**问题**：ADD-only 代价——"我在腾讯上班"→"我跳槽去字节了"两条冲突事实共存，召回时 LLM 看到矛盾版本（Mem0 400+ issue 至今未解决）。
**方案**（保守版，memory.py `_maybe_supersede`）：新事实带转变/否定词（不/没/别/戒/改/换/现在/再）+ 与旧事实共享核心词（bigram）→ 旧事实标 `superseded`（历史保留可追溯，召回不再注入 = Graphiti valid_at/invalid_at"标失效不删"轻量版）。
**关键实测**（为什么不用 bge 向量）：反义句"爱吃香菜"vs"再也不吃香菜"余弦只有 **0.65**，而并存不冲突的"喜欢奶茶"vs"喜欢果茶"却 **0.856**——向量相似度分不清"矛盾"和"相似"，纯向量阈值漏真矛盾、误伤真并存。**教训：语义消解不能靠相似度，要靠显式否定信号**。
**宁可漏**（"跳槽去字节"无共享词不消解）**不可误伤**（"两个都喜欢"被消成一个=丢信息）。扫描窗口最近 20 条（同主题新声明只可能最近；全扫存记忆会慢）。

## 五、来源 URL（供复验）

- Mem0 旧版四选一 prompt / 新版 ADD-only 代码 / changelog / 图记忆变更 / v1.0 迁移指南 / issue #4956 #5867 #5850：`cdn.jsdelivr.net/gh/mem0ai/mem0@0.1.123|2.0.18|1.0.0/...` + `github.com/mem0ai/mem0/issues/...`
- OLVT 0.4.4/0.5.2/1.2.1 README + v1.0.0/1.2.0 release notes：`cdn.jsdelivr.net/gh/Open-LLM-VTuber/Open-LLM-VTuber@...` + `github.com/Open-LLM-VTuber/Open-LLM-VTuber/releases/tag/...`
- Letta 退役声明 / AGENTS.md / 废弃页 / changelog：`cdn.jsdelivr.net/gh/letta-ai/letta@main|archive/...` + `docs.letta.com/reference/deprecated/...`
- SillyTavern 1.10.6 release notes + 1.10.5 vs 1.10.6 扩展目录对比：`data.jsdelivr.com/v1/packages/gh/SillyTavern/SillyTavern@1.10.5?structure=flat`

## 六、验证达标清单（小李已有，不需做）

- ✅ ADD-only（merge_fact 同内容只更新重要度不覆盖原文）
- ✅ 无外部基础设施（全本地）
- ✅ 有限主动（配额 2 次/日 + 夜间静默 + 保险丝）
- ✅ 降级链（TTS 三层）
- ✅ 逐句合成（SAY_SENTENCE_SEPARATELY 被并发多段合成替代 = 小李流式朗读同路线）
- ✅ 本地语音识别（ST 的 speech-recognition 扩展至今未回归，小李有 whisper）
