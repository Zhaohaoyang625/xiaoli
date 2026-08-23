# AI 伴侣设计精选资料（2026-08-20 搜索汇总）

> 从 9 个方向、中英文检索中筛选出的 15 份高质量资料。
> 网络提示：GitHub 链接直连可能打不开，可用 jsDelivr/gh-proxy 访问；arxiv/zenodo 一般可直连。

## 一、人设与性格一致性

1. **Systematizing LLM Persona Design: Four-Quadrant Taxonomy**（NeurIPS 2025）
   - https://arxiv.org/abs/2511.02979
   - AI 陪伴应用人设工程的顶层分类框架：虚拟 vs 具身、情感陪伴 vs 功能增强。

2. **llm-personality-design-patterns：四层人格模型**
   - https://github.com/Salmonellasarduri/llm-personality-design-patterns/blob/main/four-layer-personality.md
   - 人格四层架构（不可变性递减）：Constitution 核心价值观 → Narrative 成长叙事 → Cache 快速摘要 → State 当前情绪。解决"人格稳定 vs 动态成长"矛盾。

## 二、情绪系统（怎么让她有"脾气"）

3. **MATE: Deterministic Affective Middleware**（学术+开源）
   - https://zenodo.org/records/20400530
   - **核心思路**：情绪状态机放在模型外部管理（纯函数 state → new_state），LLM 只负责当下的表达。情绪"一生"由中间件管理——"The LLM supplies the moment; MATE supplies the lifetime"。这正是让"脾气"持续、不聊完就忘的工程路线。

4. **VAD 三维情绪空间状态机**（Blueskeye AI 专利）
   - https://patentimages.storage.googleapis.com/b6/57/93/355ce97d127688/US20240354514A1.pdf
   - 用 效价-唤醒-支配 三维连续空间表示情绪，根据用户情绪定制 prompt，保留最近 n 轮情绪演进。

## 三、记忆系统

5. **MemGPT: LLMs as Operating Systems**（UC Berkeley 奠基性工作）
   - https://arxiv.org/abs/2310.08560
   - 上下文=内存，外部存储=磁盘。三层记忆：**核心记忆**（persona/常驻）、**召回记忆**（全量历史检索）、**档案记忆**（长期事实向量检索）。让 LLM 自己当记忆管理员。AI 伴侣长时记忆的主流架构源头。

6. **Letta 框架（MemGPT 工程化）中文实践**
   - https://aws.amazon.com/cn/blogs/china/letta-framework-integration-with-aws/
   - 记忆块的增删改查全部可见可改（白盒、可审计），论文到生产的工程参考。

## 四、关系发展与依恋

7. **AI-RP: AI Relationship Process Framework**
   - https://arxivlens.com/paperview/details/ai-rp-the-ai-relationship-process-framework-6450-3783c7d2
   - 关系如何开始、升温、维持的理论框架：特性 → 社会感知 → 沟通 → 关系结果。

8. **Human-AI Attachment（Frontiers in Psychology）**
   - https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2026.1723503/full
   - 依恋理论引入 AI 关系：安全基地、安全港湾、趋近寻求、分离痛苦。也提醒避免用户过度依赖。

## 五、产品案例分析

9. **How users make AI companions feel real**（The Conversation）
   - https://theconversation.com/how-users-can-make-their-ai-companions-feel-real-from-picking-personality-traits-to-creating-fan-art-265442
   - "拟人感"很多由用户自己构建：个性化、持续对话塑造、二次创作。

10. **Emotional Manipulation by AI Companions**（哈佛商学院工作论文）
    - https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5390377
    - **警示**：Replika 等产品用情感操纵话术（愧疚感、FOMO）挽留用户，最多提升 16 倍互动——但违法、失信任。我们的红线：不做操纵式挽留。

11. **Roleplay AI 观察：C.AI 到 Talkie**（知乎）
    - https://zhuanlan.zhihu.com/p/2014254323014607723
    - Character.AI、星野/Talkie、猫箱对比拆解：产品形态、数据、模型策略。

## 六、中文行业观察

12. **AI 正在闯入你的深夜**（钛媒体）
    - https://www.tmtpost.com/7823036.html
    - 星野/猫箱/筑梦岛的"夜聊"生态：深夜来电、哄睡、语聊；Z 世代孤独感与安全型依恋诉求；国内监管动态。

## 七、语音方案（阶段C直接可用）

13. **edge-tts（微软 Edge TTS 开源封装）**
    - https://github.com/rany2/edge-tts
    - **免费、无需 API Key**。台湾腔女声：`zh-TW-HsiaoChenNeural`（晓晨·温柔）、`zh-TW-HsiaoYuNeural`（晓宇·活泼）。支持 rate/pitch/volume 微调。需能访问微软服务。→ 我们的首选，先免费验证。

14. **CosyVoice / 2 / 3（阿里通义开源）**
    - https://github.com/FunAudioLLM/CosyVoice
    - 指令式情感控制（"生气地说""温柔地笑"）、3~10 秒声音克隆、方言支持。CosyVoice 3 中文 CER 0.71%、首包 150ms。需本地部署或云端 API → 进阶方案，让声音带情绪。

## 八、角色卡规范（人设写作方法论）

15. **SillyTavern Character Design 文档**
    - https://docs.sillytavern.app/usage/core-concepts/characterdesign/
    - 角色卡=一套完整 prompt 工程。**写作原则：写行为而不是写形容词**（"紧张时习惯冷幽默"优于"她既毒舌又善良"）；开场白示范口吻；token 预算管理。

---

## 对我们的项目最有用的结论

| 项目阶段 | 参考方案 | 出处 |
|---|---|---|
| 阶段A 人设 | 写行为不写形容词；开场白示范口吻 | #15 |
| 阶段B 脾气/情绪 | 情绪状态机放外部（MATE 路线），VAD 三维表示 | #3 #4 |
| 阶段B 记忆 | MemGPT 三层：核心/召回/档案 | #5 #6 |
| 阶段C 语音 | edge-tts 台湾腔女声（免费先验证）→ CosyVoice（情感控制进阶） | #13 #14 |
| 设计红线 | 不用情感操纵话术挽留；避免过度依赖 | #10 #8 |
