# 真人感仿真 · 高 star 项目对照复盘（2026-08-23）

> 用户要求：不要自己瞎改，对照 GitHub 高 star 项目找思路。本文是 2026-08-23 五项实测修复
> （延迟/省略号/打断/情绪/网页）的三线对照研究报告汇总，回答三个问题：
> ① 哪些是"方向对、方法糙"（有更成熟做法）② 哪些是自创但合理（有先例）
> ③ 下一步学什么（按优先级）
> 材料：本地源码（OLLVT/dsh/UTSUWA/SillyTavern）+ 外部调研（Pipecat/sherpa-onnx/HF speech-to-speech/
> CharacterAI/Replika/MATE 论文/openfeelz 等）。研究分三线，各线完整细节见本文对应章节。

---

## 〇、总结论（先看这个）

| 本次改动 | 判定 | 证据 |
|---|---|---|
| 省略号不切句（留句内念） | ✅ **对，有硬先例** | GPT-SoVITS 词表把 `……` 定义为"拖沓的停顿"；edge-tts 官方建议用省略号制造停顿；SillyTavern 截断标点集不含省略号；实证：带标点停顿匹配 89% vs 去标点 35% |
| 半双工改"她说话时继续听、开口即打断" | ✅ **方向对**（全双工监听是 OLLVT/Pipecat/HF s2s/Hume 标准做法）；❌ **方法糙**（检测器落后，见 P0-1） | 我们已有的"生成代"作废机制 = HF speech-to-speech `CancelScope` 同款设计 |
| 哄一轮大降级 + 气自然消 | 🟡 **时序方向对**（分级渐进是共识，C.ai 秒原谅=反面教材）；❌ **缺双向通道**（台词和状态各说各话，正是用户实测 bug 根因） | MATE 论文诊断"表演连续性但不拥有它"；CharacterAI/Replika 双反面（秒原谅崩盘 / 冷冰冰像陌生人） |
| web_search 收敛、网页布局 | ✅ 工程优化，无仿真争议 | — |

**一句话：没有"瞎改"级的错误，但有四个"自己造的轮子不如高 star 现成方案"的点（Silero VAD、起播保护期、台词-状态双向同步、标点感知停顿），和一个真 bug（停顿期嘴一直张着）。**

---

## 一、语音线：打断 / 全双工 / 回声（对照：OLLVT / Pipecat / sherpa-onnx / HF speech-to-speech / Hermes / Hume）

### 1.1 项目做法一览

| 项目 | 打断检测 | 判定延迟 | 全/半双工 | 回声处理 | 打断后 LLM 处理 |
|---|---|---|---|---|---|
| OLLVT | Silero VAD（概率≥0.4 且 dB≥60）+ 5 窗平滑 | ~0.1s（3×32ms） | 全双工（常开监听） | 浏览器原生 AEC | `[Interrupted by user]` system 信号 + 已播文本+"..."入上下文 |
| Pipecat | SileroVADAnalyzer（ONNX），start_secs=0.2s | ~0.2s | 全双工 + InterruptionFrame | WebRTC 靠浏览器 AEC | **只把实际播出的字提交上下文**；带 interrupted 标志 |
| sherpa-onnx | Silero/Ten VAD，minSpeech=0.25s | 0.25s | 半双工原语 | 无内置 | 框架不管 LLM |
| HF speech-to-speech | Silero VAD v5，min_speech_ms=384 | 0.38s | 真全双工（独立线程） | WebRTC 靠浏览器 AEC | **CancelScope 生成代**（=我们 _gen 同款）+ 输出端 discard 守卫 |
| Hermes Agent | VAD + **预校准底噪 × 倍数** + **起播 grace 秒** | 有 grace 抑制期 | 全双工（生成期/播放期都能打断） | 未公开 | 完整双工监听 |
| Hume EVI | 服务端持续处理音频 | 未公开 | 全双工 | 服务端处理 | 停生成+停流式音频+`user_interruption` 事件 |

**共识**：全双工监听是标配；**检测器清一色 Silero VAD（模型），没有一家用纯能量阈值**；回声要么靠浏览器 AEC、要么服务端处理。

### 1.2 我们的差距

| 维度 | 我们 | 主流 | 判定 |
|---|---|---|---|
| 打断检测器 | 纯能量峰值 500/2000 固定常数 | Silero VAD 概率+dB 双条件 | ❌ 唯一用能量阈值的（P0-1） |
| 判定延迟 | 0.3s 连续有声 | 0.1~0.38s | 量级不丢人，但判定质量差 |
| 阈值自适应 | 固定 2000（注释自嘲"不合适就调"） | Hermes 预校准底噪 × 倍数 | ❌ 安静/嘈杂环境都会出错（P0-2） |
| 回声处理 | 无 AEC，调高阈值硬扛 | AEC 消除自家声音，阈值保持正常灵敏度 | ❌ 治标不治本（P1-1） |
| 防误打断 | 0.3s 连续有声（防咳嗽） | Hermes grace_seconds 起播保护期 | ❌ **缺起播保护期**（P0-2，刚改完监听打断最该补） |
| 生成代作废 | ✅ 已有 _gen | HF CancelScope 同款 | ✅ 已对齐 |
| 打断后 LLM | ✅ _unfinished（没说完的句子注入下轮） | OLLVT 已播文本+"..."；Pipecat 只提交实际播出的字 | 🟡 已有基础，缺"正在播那句说到哪了"（P1-3） |

### 1.3 落地清单（语音线）

- **P0-1 换 Silero VAD**（核心，成本最低）：抄 OLLVT 参数——`silero-vad` 包（模型 ~2MB，单窗推理 <1ms 走 CPU 不占显存），16k/512 样本窗，prob≥0.4 且 dB≥60 双条件 + 3×32ms 命中开口 + 0.8s 收尾。**更省事**：whisper_stt.py 里 faster-whisper 内置的就是同一个 Silero ONNX（`vad_filter=True` 已在用），可直接复用做打断。替换后 500/2000 魔法数退役。
- **P0-2 起播保护期**（Hermes grace 同款）：play_speech 每次开始记 `grace_until = now + 0.4s`，打断判定先查过没过期——防她自己的起播声（无 AEC 时）打断她自己。
- **P1-1 AEC**（真消除自家声音）：`pip install pyaec`（Rust 封装 speexdsp，有 Windows wheel）或 speexdsp-ns-vulcanlabs。near=麦克风流，far=voice.py 正在播放的 PCM buffer（不需要 WASAPI loopback）。注意播放/采集时钟漂移——先小测。上 AEC 后 Silero 阈值恢复正常灵敏度。
- **P1-2 底噪自适应**（Hermes 先例）：通话空闲采样 2s 算底噪基线，打断条件改"当前 dB ≥ 底噪+20dB"替代固定值，每 30s 慢更新。
- **P1-3 打断时"正在播那句说到哪了"带给 LLM**：按已播时长/总时长比例估算已说出字数，拼"他打断你时你正说到「今天天气真……」"追加进 _unfinished（已有注入机制，只加一条）。
- P2-1 短词防误打断（Pipecat min_words=3：识别出 ≤2 字不发给 LLM 当一句话）；P2-2 打断冷却 300-500ms（OLLVT 建议）。

---

## 二、情绪线：状态机持续性 / 台词-状态同步（对照：UTSUWA / SillyTavern / CharacterAI / Replika / MATE / ALMA / openfeelz）

### 2.1 关键发现

- **用户实测 bug 根因**（"哄了她、她说原谅了、状态还生气"）：MATE 论文诊断名 **"perform continuity without possessing it"**（表演连续性但不拥有它）——台词和状态各说各话，主流产品通病。
- **但反向教训同样致命**：CharacterAI 纯台词无状态 → **秒原谅崩盘**（RLHF 顺从偏差 + 模型更信最近对话不信状态块）；Replika 脚本化状态 → **空洞道歉**和**冷战像陌生人**。**两个失败态：秒原谅和卡生气都不像人，中间态才有人样。**
- **不是二选一，是双向通道 + 程序仲裁**：状态驱动台词为主干（保住"吃醋真生气"人设），台词回写为承诺（她亲口说原谅=原谅生效），程序当裁判带门闩。UTSUWA 的 `mergeUpdates`（LLM JSON 自报被基线钳制）就是这个仲裁的现成先例。
- **ALMA 分层**：情绪（分钟级）和怨气/心情（小时~天级）必须分开管理——我们现在 angry 一锅炖，"哄好情绪"和"记不记仇"互相打架。
- **时间衰减**：社区默认指数半衰期（openfeelz halfLifeHours=12、快档 ~1h）+ 睡眠衰减；我们是线性 -6/轮 + 12h 硬切（二进制跳变）。

### 2.2 落地清单（情绪线）

- **P0-1 台词-状态双向同步**（用户实测 bug 的完整修法，三步闭环）：
  1. **台词锚定（生成前）**：连珠炮同款机制（消息末尾【情绪指令·必做】）加"台词边界"——jealous/angry 时钉"禁止说'我原谅你了/和好了/没事了'这类完全原谅的话"；content 时放行。依据：C.ai 实证"模型更信最近对话"→ 指令必须贴近输出末尾（连珠炮已验证该位置遵循率最高）。
  2. **台词回写（生成后）**：parse 她台词里的原谅信号（"原谅你/不生气了/和好啦"）→ 沿降级链走一轮（angry→jealous、-35；≤40→content），cause 记"我亲口说了原谅他的话"。**门闩**：只许沿降级链（不许一步 angry→content）；每段小脾气只触发一次回写、回写后 2 轮内不再触发（防秒原谅崩盘）。
  3. **状态变化承认钩子**（UTSUWA strained event 先例）：程序轮和好后注入"你刚刚心软了"的过渡提示，让台词顺着状态演。
- **P0-2 指数半衰期衰减**：angry/jealous 触发记 `mood["since"]` 时间戳，每轮 `V = 45 + (V0-45)·e^(-Δt/τ)`，τ≈2h（与"气不过夜"一致但连续）；保留聊天加速器每轮额外 -6；12h 硬切改指数式；应用时机学 UTSUWA `resolveTimeDecayOnLoad`（时间戳去重防多扣）。
- **P1-1 情绪/怨气分层（ALMA）**：heart.json 加 `grudge`（怨气 0-100，半衰期天级）：吃醋/生气 +15~25；哄降情绪快降怨气慢（-5）；只有台词回写/连续哄/时间能消。describe() 里怨气高 → 翻旧账；情绪强度才是"现在炸不炸"。"哄了还生气"拆成两个真实问题：情绪没消（修）vs 怨气记仇（人设，不该修）。
- **P1-2 哄话强弱分级**：SOOTHE_WORDS 分强（对不起/我错了/消消气，-35 链）与弱（爱你/抱抱/么么，只 -18）——防"随口说爱你"秒消气。
- P2-1 台词-状态一致性检查器（MATE Critic 思路，低频独立调用）；P2-2 状态变化日志（transitions 最近 10 条，终端"心情"可查"什么时候变的"）。

---

## 三、说话节奏线：切句 / 停顿 / 省略号（对照：dsh / OLLVT / Pipecat / sherpa-onnx / edge-tts / GPT-SoVITS / SillyTavern）

### 3.1 关键发现

- **省略号不切 = 对**（详见总结论）：GPT-SoVITS/edge 官方/SillyTavern 三面证据。
- **句间停顿 0.25s 固定 = 糙**：主流是"停顿归 TTS 引擎管、播放层只补引擎不念的地方"（sherpa `silence_scale` 参数化思路）。引擎的标点韵律只对**带标点句尾**生效——我们"尾部无标点也算整句"和硬切断点两类句子引擎不念停顿，0.25s 就是全部 → **机关枪听感重灾区**。
- **<pause/> LLM 标签 = 有先例但非主流**（OLLVT 新版同款；dsh/Pipecat/sherpa 都没有）；双轨（引擎标点韵律为主 + <pause/> 限量补充）恰是成熟组合。
- **真 bug：静音段播放时口型一直张着**——OLLVT 静音=空音频+音量曲线全 0 → 前端嘴不动；我们播零 PCM 但 `speaking_until` 还亮着，<pause/> 越长越明显。
- 连续标点未归一（`！！`），GPT-SoVITS 实测多连标点会干扰模型停顿判断。

### 3.2 落地清单（节奏线）

- **P0-1 静音段口型清零**：`_silence_pcm` 入队前先写 `_speaking_until = 0`（播静音=闭嘴），静音段播完恢复。测试：play_speech("好想你<pause/>真的")，检查静音期间 face_state.js 的 speaking_until 为 0。
- **P0-2 标点感知句间停顿**（sherpa silence_scale 的播放层版）：固定 0.25s → 按句尾字符查表：句号 0.35s（引擎已念 0.5-0.8s，只补衔接）/ 感叹问号 0.30s / **无标点 0.50s**（引擎没念全靠播放层）/ 硬切断点 0.15s（别打断语流）。`_SENTENCE_GAP` 常量变函数。测试：三个用例听感。
- **P1-1 连续标点归一**：speakable() 加 `！！→！`、`？？→？`、`。。→。`、`……（3+）→……`。
- **P1-2 省略号三引擎念法验证**：写脚本三引擎（本地克隆/火山/edge）试听含 … 的同一句；若某引擎不念停顿，播放层给含省略号句尾补 0.3s。
- P1-3 情绪补话间隔与句尾标点联动（问号句等她答，间隔取上沿）。

---

## 四、优先级汇总（等用户拍板）

### 立即做（纯增益小改动，无风险）
1. **C-P0-1** 静音段口型清零（<pause/> 时嘴别张着）
2. **C-P0-2** 标点感知句间停顿（无标点句尾 0.5s，修机关枪听感）
3. **C-P1-1** 连续标点归一（！！→！）
4. **A-P0-2** 起播保护期 0.4s（防她打断自己——刚改完监听打断最该补）

### 核心升级（建议做，工作量中等）
5. **A-P0-1** Silero VAD 换掉能量阈值（复用 faster-whisper 内置 ONNX，几乎零成本；修"打断不灵/误打断"的根本）
6. **B-P0-1** 台词-状态双向同步（锚定+回写+门闩；用户实测 bug 的完整修法）
7. **B-P0-2** 指数半衰期衰减（气不过夜但连续，替代线性 -6）

### 等拍板（改动大 / 有取舍）
8. **A-P1-1** AEC 回声消除（pyaec/speexdsp，near=mic/far=播放 buffer；要小测时钟漂移）
9. **A-P1-2** 底噪自适应阈值（Hermes 预校准思路）
10. **B-P1-1** 情绪/怨气分层（ALMA；heart.json 加 grudge 字段，"哄了还生气"拆成"情绪"和"怨气"两个真实问题）
11. **B-P1-2** 哄话强弱分级（爱你/抱抱=弱消气）
12. **A-P1-3** 打断时"正在播那句说到哪了"带给 LLM

### 长期观察
13. **B-P2** 一致性检查器 + 状态变化日志（可审计）；**A-P2** 短词防误打断 + 打断冷却；**C-P1-2** 省略号三引擎念法验证脚本

---

## 五、参考材料

- 本地源码：docs/research/sources/code-v2/ollvt/（Silero VAD/打断信号）、docs/research/sources/dsh-repo/（切句/播放）、docs/research/sources/utsuwa/（状态机/LLM 自报钳制）、docs/research/sources/sillytavern/（截断标点/角色卡）
- 外部：Pipecat 打断文档、sherpa-onnx VAD、HF speech-to-speech（CancelScope）、Hermes Agent barge_in PR、Hume EVI、GPT-SoVITS 标点 Issue #1260、edge-tts Issue #111、CharacterAI 故障排查/性格分析、Replika 冲突论文、MATE 论文（zenodo）、ALMA 2005、openfeelz、st-Emotion、ScenePulse
