# sherpa-onnx 深度研究：流式识别（说一句出半句）

> 2026-08-24 | 研究轮次：语音模块第 1 弹 | 对应：响应延迟优化
> GitHub: k2-fsa/sherpa-onnx（~21k⭐，下一代会 Kaldi 团队，阿里巴巴出品）
> 结论先行：**接入价值高——识别延迟从 2~3s 压到 <0.2s，CPU 就能跑，不动 GPU**

## 一、为什么研究它（痛点对应）

小李现在通话模式的延迟链路：

```
你说完 5 秒 → 静音 0.8s 判停 → 整段送 faster-whisper（GPU 推理 1~2s）→ 文本 → LLM
```

- 每句话都要**等你说完才识别**，再等 GPU 推理 1~2s——一来一回"等"的感觉都在这
- whisper large-v3 是**非流式**模型：不能边说边出结果，只能整段喂

sherpa-onnx 的流式 zipformer 恰好补这个：**边说边出 partial（半句），你说完立刻有全文**，识别推理在说话期间已实时完成。

## 二、核心能力盘点（都支持）

| 能力 | sherpa-onnx | 对小李的用 |
|---|---|---|
| 流式 ASR | ✅ zipformer/paraformer/wenet CTC | 边说边出字，说完整句即全文 |
| 端点检测 | ✅ `is_endpoint()`（内置，尾部静音自动判"说完了"） | 替代 0.8s 静音判停，且可配置 |
| VAD | ✅ Silero VAD（我们 vad.py 已是同一款） | 不需要，流式自带 endpoint |
| 中文模型 | ✅ 多版本（见下节） | 普通话+多方言 |
| 运行 | ✅ CPU（小模型 RTF 0.05）/ CUDA / NPU | 我们的场景 CPU 足够 |
| 平台 | ✅ Windows x64 官方支持 | 直装 |
| 语言绑定 | ✅ Python/C++/JS/Go/C#/Rust… | Python 即可 |
| 热词 | ✅ hotwords 文件 | 可加"小李/宝贝/齁"等热词提权重 |
| 标点 | ✅ 可选（add-punctuation 模型） | 现在靠 LLM 自己加标点，不需要 |

## 三、中文流式模型选型（2026-08-24 实测下载途径）

| 模型 | 打包大小 | 说明 |
|---|---|---|
| `streaming-zipformer-zh-int8-2025-06-30` | **132MB** ✅ 已下载 | 2025 新版，WenetSpeech+多中文数据集，**我们选的** |
| `streaming-zipformer-zh-2025-06-30`（fp32） | 594MB | 同模型非量化 |
| `streaming-zipformer-zh-xlarge-int8-2025-06-30` | 598MB | 更大的 xlarge 版（更准、更慢） |
| `streaming-zipformer-bilingual-zh-en-2023-02-20` | 488MB | 中英双语旧版 |
| `streaming-zipformer-zh-14M-2023-02-23` | 74MB | 边缘设备专用（Cortex A7 都能跑），精度较低 |
| `streaming-zipformer-ctc-multi-zh-hans-2023-12-13` | ~200MB | 多方言 CTC 版 |

**下载途径**（全实测通过）：
- HF 镜像：`hf-mirror.com/csukuangfj/sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30`（700KB/s+，推荐）
- GitHub release：需 gh-proxy 代理（58KB/s，慢 12 倍，不推荐）
- ModelScope：无官方镜像（k2-fsa 没传）

## 四、实测数据（2026-08-24，本机实测）

**测试集**：80 句台湾腔语料（火山 TTS 合成，2.4~4s/句，共 234s）｜模型：`zipformer-zh-int8-2025-06-30`（132MB）｜运行：CPU 4 线程

**RTF（实时率）**：**0.067**——识别 3 秒音频只要 0.2 秒。对比：faster-whisper large-v3 GPU 一次推理 0.5~2s。加载 ~1s（whisper 6~9s）。

**准确率（归一化对比，繁转简+去标点）**：

| 判定 | 条数 | 说明 |
|---|---|---|
| 完全匹配 | 46/80 | 逐字正确 |
| 字形/口语词差异 | 29/80 | 喔/哦、好了/好啦——内容正确 |
| 真错误 | 5/80 | 见下 |

**5 条真错误 & whisper large-v3 对照**（whisper 全部正确）：

| # | 参考 | sherpa 听成 | whisper |
|---|---|---|---|
| 015 | 你明天**要不要**陪我去逛街 | 你明天要陪我去**雀街** | ✅ |
| 022 | 你抱我一下嘛，**就一下下**就好 | 你抱我一下嘛**注意下下**就好 | ✅ |
| 026 | **好啦好啦**，原谅你这次 | 好了好了 | ✅ |
| 032 | 不要又**空腹**喝咖啡了 | 不要有**空蹈**啡了 | ✅ |
| 056 | 去逛**夜市**…吃**蚵仔煎** | 去**雁市**…吃**科仔煎** | ✅ |

**结论**：sherpa 速度碾压（RTF 0.067 vs whisper GPU 0.5~2s/句），whisper large-v3 准确率碾压（短促口语词/台湾专名）。**两者各有所长 → 双通道方案**（见第六节）。注：语料是 TTS 合成音，真人说话的自然度可能更利于 sherpa，待实测。

## 五、能抄的 / 硬件限制的 / 创新点

### 能抄的 ✅（全部已实测验证）
1. **流式 partial + endpoint 闭环**：边说边出字（实测："刚刚"→"刚刚去便利商店买了关东"→全文），静音判停即全文。这是"说一句出半句"的全部
2. **尾部 0.66s padding**：`input_finished()` 前喂 0.66s 静音——**没有它句尾字会丢**（实测修 001/003 的"看嗎/伞耶"截断）
3. **端点规则三档配置**：`rule1_min_trailing_silence=2.4` 默认；可调 0.8s 与现有一致（快速判停）+ 长句兜底规则（说话≥20s 时静音 1.2s 也判停）
4. **热词文件**：hotwords 提权，"蚵仔煎/夜市"等专名可写死提高命中（实测错 5 条里有 2 条是这类）
5. **CPU 流式**：RTF 0.067（CPU 4 线程）→ 几乎零 CPU 占用，不动 GPU；加载 ~1s
6. **包直装**：`pip install sherpa-onnx`（清华源，1.13.6，Python 3.13 有 wheel）
7. **模型下载**：hf-mirror.com 直连 700KB/s+（gh-proxy 58KB/s 慢 12 倍，ModelScope 无）

### 硬件限制的 ⚠️
1. **短促口语词听辨弱**：实测"要不要/就一下/好啦/空腹/蚵仔煎"5 类听错——zipformer-int8 对快速音节不如 whisper large-v3。**不能无脑替换**
2. **xlarge 版**（598MB）：更准但 CPU 实时率下降，我们 8GB 显卡场景没必要
3. **输出简体**：模型输出简体字（我们的语料/回复是繁体）——需要一次字形转换（opencc t2s→s2t，几百 KB）
4. **TTS 合成音 vs 真人音**：测试集是火山合成音；真人说话的听辨难度不同，待用户实测

### 创新点 💡
1. **双通道设计（小李专属）**：通话模式 sherpa 流式优先（延迟敏感）→ 文本即出即送 LLM；**whisper large-v3 保留做精修/降级**——按键说话/网页 whisper 优先（准确率敏感）。两套模型并存按场景分流，一键切换
2. **延迟预算重排**：省下的 1~2s 推理时间可以还给"静音判停更短"（0.8→0.5s）——用户感知的"她回应快"反而更强
3. **热词动态注入**：hotwords 支持动态列表——把用户昵称/常用词（记忆里存的）每次对话前注入，识别率定向提升

## 六、接入小李的方案（改动点）

### 新模块 `xiaoli/sherpa_stream.py`（~80 行）
```python
class SherpaStreamRecognizer:
    """流式识别：0.1s 块喂入 → 实时 partial → 静音判停出全文"""
    def __init__(self):           # 懒加载：模型路径 models/sherpa/xxx/
    def begin(self) -> None       # 新建 stream（每句话一条）
    def feed(self, pcm) -> str    # 喂 0.1s 块 → 返回 partial 文本（空串=没出字）
    def is_endpoint(self) -> bool # 静音判停（0.8s 规则）
    def finalize(self) -> str     # 说完了 → 完整文本
    def close(self) -> None
```

### 改 `xiaoli/call_mode.py`（通话模式，核心）
- `_loop` 里：VAD 开口 → `begin()`；每 0.1s 块 `feed()`（顺带驱动网页实时字幕？第一版不做）
- 判停：现有 SILENCE_BLOCKS=8（0.8s）保留，但**不需要等 whisper**——`finalize()` 即时返回
- `_finish()`：sherpa 结果优先 → whisper 兜底（sherpa 空/加载失败）

### 改 `xiaoli/stt.py`
- `listen_once()`（按键说话）：可以不动（whisper 优先，准确率场景）
- 或统一走 sherpa：等实测数据再定

### 配置
- `config.py` 加 `STT_STREAM`（True=通话模式用 sherpa 流式）
- 模型自动下载：models/sherpa/ 缺失时提示（不做自动下载，靠 README 说明）

### 风险与回滚
- sherpa 结果差 → 一键关 `STT_STREAM` 回到 whisper 路径（代码路径全保留）
- GPU 完全不动：whisper/TTS 训练互不干扰

## 七、验证计划（下班后）
1. ✅ 已做：语料全量 80 条（RTF 0.067 + 75/80 内容正确 + whisper 对照 5 条差异）
2. 接入通话模式后实测：说 3 句话，感受"说完即回"的延迟
3. 真人语音对比：用真实说话录几条（含"要不要/夜市"等易错词），看 sherpa vs whisper 实际差距
4. 热词实测：把"夜市/蚵仔煎/小李"等写进 hotwords 文件，重测错词是否命中

## 八、相关链接
- 仓库：https://github.com/k2-fsa/sherpa-onnx
- 模型文档：https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-transducer/
- HF 模型：https://hf-mirror.com/csukuangfj/sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30
- 参考实现：python-api-examples/online-decode-files.py（本目录 sherpa-stream-example.py）
