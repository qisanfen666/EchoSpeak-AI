# EchoSpeak AI — AI 英语口语陪练

基于 Go + Python 异构微服务的实时英语口语陪练系统，支持流式语音/文字对话（用户打断）、语法纠错、发音打分、课后报告。

```
浏览器 ←→ WebSocket ←→ Go 网关 (:8080) ←→ gRPC ←→ Python AI 引擎 (:50051)
                              ↕
                           Redis (:6379)
```

### 演示视频

[▶️ 点击观看演示视频](https://b23.tv/gr5RCwe)

直接复制链接：https://b23.tv/gr5RCwe

## 功能亮点

### 🎙️ 实时语音对话

- **流式管线架构**：Go 网关 + gRPC 流式传输 + Python 异步引擎，端到端延迟控制在秒级，用户说完即见回复
- **客户端 VAD 端点检测**：基于能量阈值的语音活动检测，1.2s 预录音回溯 buffer + 句首 400ms 补偿，杜绝"吃字"，自然停顿 1.8s 自动触发识别，无需手动停止录音
- **流式 LLM 逐词推送**：AI 回复按 token 实时推送到前端，用户看到文字逐词生成，不等完整回复，体感延迟接近零
- **流式 TTS 音频**：edge-tts 逐 MP3 chunk 推送，浏览器边收边播，AI 说出第一个词时音频已开始播放
- **对话打断**：用户随时开口说话或打字打断 AI 回复，`asyncio.Task` 即时取消 + TTS 播放队列清空，无缝切换下一轮次（半双工模式：一方说一方听，但用户可主动打断）

### 🧑‍🏫 智能教学能力

- **7 类语法纠错**：grammar / tense / preposition / article / vocabulary / word_choice / expression，独立 LLM 并行分析，不阻塞回复流
- **地道表达建议**：纠错外另启 LLM 给出更地道的表达方式，帮助用户从"说对"到"说好"
- **发音准确度 + 流利度打分**：faster-whisper 转写后逐句评估，实时反馈到前端
- **中文翻译**：AI 回复自带可折叠中文翻译，降低初学者的理解门槛

### 🎬 对话场景与难度

- **5 个预设场景**：餐厅点餐 / 求职面试 / 商务会议 / 旅行出行 / 日常闲聊，AI 进入场景主动打招呼
- **自定义话题**：自由输入任何想练习的话题
- **3 档难度**：Easy（简单词汇短句 + 语速 -25%）/ Medium（日常英语 + 正常语速）/ Hard（高级词汇习语 + 语速 +15%），LLM prompt 和 TTS rate 联动控制
- **9 种 AI 口音**：美式🇺🇸 / 英式🇬🇧 / 澳式🇦🇺，男女声各可选，Microsoft Azure Neural TTS 引擎
- **双模式切换**：实时对话（自动 VAD 连续对话）/ 一问一答（手动控制节奏）

### 📊 课后报告与趋势

- **多维度评分**：语法 / 词汇 / 发音 / 流利度四项能力评分
- **错误统计**：按错误类型汇总高频错误，逐条展示原文→纠正+中文解释+所属轮次
- **趋势图表**：Chart.js 绘制逐轮评分折线图，直观看到进步
- **学习建议**：LLM 根据错误分布生成针对性改进建议
- **HTML 报告导出**：Canvas 图表自动转 PNG 嵌入，独立 HTML 文件浏览器直接打开
- **跨练习趋势对比**：首页 Dashboard 展示历次练习记录，追踪长期进步

### 🔧 工程亮点

- **零依赖分发**：Go 网关预编译为 `gateway.exe`（无需安装 Go），Python 引擎可选 PyInstaller 打包为独立 EXE
- **一键启动**：`start.bat` 自动检测 Redis → 启动 Python → 启动 Go → 打开浏览器
- **微信风格 UI**：仿微信聊天界面，气泡式对话、头像昵称、纠错折叠卡片，操作直觉化
- **语音重播**：点击对话气泡旁的 🔊 重播自己录音或 AI 标准发音
- **对话上下文记忆**：Go 端将历史对话传入 gRPC，LLM 记住上文，多轮对话不丢失语境

## 项目结构

```
├── start.bat                    # 一键启动
├── build-exe.bat                # PyInstaller 打包脚本
├── go-gateway/
│   ├── gateway.exe              # 预编译二进制（无需 Go）
│   ├── proto/aiservice.proto    # gRPC 协议定义
│   └── internal/
│       ├── ws/                  # WebSocket Hub / 流式路由 / 报告
│       ├── session/             # 会话上下文 + 对话历史
│       ├── grpc_client/         # Python gRPC 客户端
│       └── redis_client/        # Redis 封装
├── python-engine/
│   ├── main.py                  # gRPC Server (StreamASR / Chat)
│   ├── gen_proto.py             # Proto 生成脚本
│   └── services/
│       ├── asr_engine.py        # faster-whisper 语音识别 + 打分
│       ├── llm_engine.py        # LLM 流式对话 (OpenAI 兼容)
│       ├── tts_engine.py        # edge-tts 语音合成
│       └── correction_engine.py # 语法纠错
└── frontend/
    └── index.html               # 单页应用 (Chart.js 可视化)
```

## 部署

### 依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | ≥ 3.11 | AI 引擎运行时 |
| Redis | ≥ 7.0 | 会话状态缓存（`start.bat` 自动下载 Windows 版） |
| LLM API Key | - | DeepSeek / OpenAI 兼容接口 |

### 端口

| 端口 | 服务 | 说明 |
|------|------|------|
| 6379 | Redis | 会话缓存 |
| 50051 | Python gRPC | AI 引擎 |
| 8080 | Go 网关 | WebSocket + 前端页面 |

启动前确保以上端口未被占用。

### 首次安装

```bash
# 1. 配置 API Key
#    在 python-engine/ 下创建 .env，写入: LLM_API_KEY=sk-xxx

# 2. 安装 Python 依赖
cd python-engine
pip install -r requirements.txt
python gen_proto.py
```

### 启动

双击 `start.bat`（启动 Redis → Python → Go → 浏览器）

或手动三步：

```bash
redis-server                                    # 终端 1
cd python-engine && python main.py              # 终端 2
cd go-gateway && gateway.exe                    # 终端 3
# http://localhost:8080
```

### 分发（对方无需安装 Python）

在你电脑上运行 `build-exe.bat`，生成 `python-engine/dist/echospeak-engine.exe`（~300MB）。把整个项目文件夹 + 这个 exe 发给对方，对方只需双击 `start.bat`（把其中的 `python main.py` 换成 `echospeak-engine.exe`）。

### 环境变量

在 `python-engine/.env` 中配置：

| 变量 | 必填 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | 是 | - |
| `LLM_BASE_URL` | 否 | `https://api.deepseek.com` |
| `LLM_MODEL` | 否 | `deepseek-chat` |
| `WHISPER_MODEL_SIZE` | 否 | `tiny` |
| `TTS_VOICE` | 否 | `en-US-JennyNeural` |
