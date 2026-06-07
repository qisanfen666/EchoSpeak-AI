# EchoSpeak AI — AI 英语口语陪练

基于 Go + Python 异构微服务的实时英语口语陪练系统，支持语音/文字全双工对话、语法纠错、发音打分、课后报告。

```
浏览器 ←→ WebSocket ←→ Go 网关 (:8080) ←→ gRPC ←→ Python AI 引擎 (:50051)
                              ↕
                           Redis (:6379)
```

## 功能

- 语音/文字实时对话，VAD 打断控制，对话上下文记忆
- 4 个预设场景 + 自定义话题，3 档难度，9 种 AI 口音
- 实时/一问一答双模式，AI 回复中文翻译
- 7 类语法纠错 + 地道表达建议
- 发音准确度 + 流利度打分
- 课后报告：评分/错误统计/趋势图表/学习建议，支持导出 HTML
- 跨练习趋势对比，语音重播

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
