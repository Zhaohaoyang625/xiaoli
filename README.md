# 小李 💗

住在你手机里的台湾甜妹 AI 伴侣。会说话、会撒娇、会吃醋、会记住你的事、会主动找你聊天，还能看你的照片。

## 她会的

- **聊天**：繁体中文、台湾腔、双轨内心（心里话和说出口的话可以不一样）
- **语音**：本地克隆她自己的声音（火山/edge 降级链），你开口说话她也听得见
- **主动找你**：早安晚安、纪念日、想你的时候，她自己会开口
- **小脾气**：吃醋、真生气、连珠炮，哄两句就心软
- **记忆**：你的事她都记得（生日、喜好、你给她看过的照片），太久不提的会慢慢淡忘
- **看照片**：网页点「📷 传照片」或终端拖图片路径进来，她会像真人一样回应
- **陪你出门**：你吃烧烤/逛街/上班，她像异地恋女友一样参与你的生活
- **上网**：知道最近的热梗、新闻（每天刷一次世界简报）
- **唱歌**：往 `data/songs/` 放歌，她就会唱

## 怎么启动

```bash
# 1.（第一次）存 API 密钥到 Windows 凭据管理器（不落盘明文）
python scripts/setup_keys.py --set deepseek
python scripts/setup_keys.py --set volc

# 2. 启动（也可以双击 start.bat）
python -m xiaoli.chat --voice
```

启动后：
- **终端**直接打字聊天；输入「说」开口说话；「语音开/关」「通话开/关」切模式
- **网页**：浏览器打开 `web/XiaoLi.html`（建议放桌面快捷方式），点🎤说话、📷传照片、🔊开关声音

## 目录速览

| 路径 | 是什么 |
|------|--------|
| `xiaoli/` | 她的"大脑"（persona 人设 / chat 主循环 / voice 语音 / memory 记忆 / heart 情绪 / vision 看照片…） |
| `web/XiaoLi.html` | 网页形象（Live2D 表情 + 打字机字幕） |
| `data/` | 私密数据（聊天记录/记忆/照片收件箱）——**别提交 git** |
| `models/` | 本地模型（7.2G，whisper + 声音克隆） |
| `scripts/` | 运维小工具（密钥管理/显存检查） |
| `docs/design-log.md` | 开发日志（每个功能的坑和结论） |

## 需要什么

- Windows 10/11 + Python 3.11+
- NVIDIA 显卡（推荐）：本地语音（whisper 识别 + 声音克隆）全离线
- DeepSeek API key（她的大脑）、火山引擎 key（可选，TTS 备用）

## 测试

```bash
python -m pytest tests/ -q           # 300+ 单元测试
python tests/final_e2e.py            # 全链路实测（真 API，约 1 分钟）
```

## 备份

```bash
python scripts/backup.py             # 聊天记录/记忆打包到 backups/（自动留最近 5 份）
```
