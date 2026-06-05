# EchoSpeak AI — AI 英语口语陪练

基于「异构微服务 + 双通道处理」的实时口语陪练系统。Go 负责高并发实时通信，Python 负责 AI 推理。

## 架构

```
前端 (Web) ←→ WebSocket ←→ Go 网关 ←→ gRPC ←→ Python AI 引擎
                                ↕
                             Redis (会话状态)
```

| 层级 | 技术 | 职责 |
|------|------|------|
| 接入层 | Go (Gin + Gorilla WebSocket) | 实时网关、流式路由、打断控制 |
| 推理层 | Python (FastAPI + gRPC) | ASR / LLM / TTS / 发音评测 |
| 存储层 | Redis | 会话上下文、实时打分、临时缓存 |
| 通信 | gRPC + WebSocket | 前后端低延迟 + 异构服务高性能 |

## 快速开始

```bash
# 1. 启动 Redis
docker compose up -d

# 2. 启动 Python AI 引擎
cd python-engine
pip install grpcio grpcio-tools protobuf python-dotenv
python gen_proto.py
python main.py

# 3. 启动 Go 网关
cd go-gateway
go mod tidy
go run .

# 4. 打开前端
# 浏览器打开 frontend/index.html
```

## 项目结构

```
EchoSpeak-AI/
├── go-gateway/               # Go 接入层
│   ├── main.go               # 入口
│   ├── config/config.go      # 配置
│   ├── proto/                # gRPC 协议定义
│   ├── internal/
│   │   ├── ws/               # WebSocket 连接管理 + 流式路由
│   │   ├── session/          # 会话上下文 (context + cancel)
│   │   ├── grpc_client/      # Python gRPC 客户端
│   │   └── redis_client/     # Redis 操作封装
├── python-engine/            # Python AI 推理层
│   ├── main.py               # gRPC Server 入口 (生产)
│   ├── fastapi_server.py     # FastAPI 开发服务器 (ASR/TTS 测试)
│   ├── config.py             # 配置
│   ├── gen_proto.py          # Proto 生成脚本
│   ├── requirements.txt      # Python 依赖
│   ├── services/
│   │   ├── __init__.py       # 导出 ASREngine, TTSEngine
│   │   ├── asr_engine.py     # faster-whisper 语音识别 + 评分
│   │   ├── tts_engine.py     # edge-tts 语音合成
│   │   └── asr_service.py    # (原) FunASR 流式 ASR 骨架
│   └── workers/              # 异步 Worker
├── frontend/                 # 前端测试页面
│   ├── index.html            # WebSocket 连接 + 测试日志
│   ├── test.html             # ASR/TTS 集成测试 (波形+评分)
│   ├── styles.css            # 暗色主题样式
│   └── app.js                # 前端逻辑 (WS 流式 PCM)
└── docker-compose.yml        # Redis
```

## ASR/TTS 开发服务器（快速测试）

无需启动 Redis 或 Go 网关，直接测试语音识别和合成：

```bash
cd python-engine
pip install -r requirements.txt
python fastapi_server.py
# 打开 http://localhost:8000/test
```

| 端点 | 方法 | 说明 |
|------|------|------|
| `GET /` | 浏览器 | 原始测试页 |
| `GET /test` | 浏览器 | ASR/TTS 集成测试页 (推荐) |
| `POST /api/asr/transcribe` | REST | 上传音频文件 → 文字 + 评分 |
| `GET /api/tts/speak` | REST | 文字 → MP3 语音 |
| `GET /api/tts/voices` | REST | 列出 47+ 英语声源 |
| `WS /ws/stream/{id}` | WebSocket | 流式 ASR (PCM → 文字) |
| `WS /ws/tts/{id}` | WebSocket | 流式 TTS (文字 → 语音) |

环境变量参见 `python-engine/config.py`。

## TODO

### ✅ 已完成

- [x] 项目骨架搭建 (Go + Python + 前端)
- [x] gRPC 协议定义 (aiservice.proto)
- [x] WebSocket 消息协议定义
- [x] Go WebSocket 连接管理 (Hub / Client / 读写 Pump)
- [x] Go 会话管理 (context.Context + 级联 Cancel)
- [x] Go ↔ Python gRPC 通信链路
- [x] Go 流式路由骨架 (快通道/慢通道)
- [x] 打断控制骨架 (interrupt → cancel)
- [x] Redis 会话存储封装
- [x] Python gRPC Server + Health Check
- [x] Python ASR 服务骨架
- [x] 前端测试页面 (实时 WS 日志面板)
- [x] 端到端通信验证

### 🔲 待完成

**✅ 已集成 (Xengineer ASR/TTS)**
- [x] Python: ASR 引擎 — faster-whisper (tiny, CPU int8) 本地语音识别
- [x] Python: 发音评分 (0-100, 基于 avg_logprob)
- [x] Python: 流利度评分 (0-100, 基于 WPM + 分段数)
- [x] Python: TTS 引擎 — edge-tts (47+ 英语神经声源)
- [x] Python: FastAPI 开发服务器 (REST + WebSocket 双通道)
- [x] Python: WebSocket 流式 ASR (VAD 静音检测, 1.2s 窗口)
- [x] Python: WebSocket 流式 TTS (MP3 分块)
- [x] 前端: ASR/TTS 测试界面 (波形可视化 + 实时打分)

**核心链路**
- [ ] Go: gRPC stream 真实调用串联 (替换占位代码)
- [ ] Go: 快通道完整流程 (ASR final → LLM stream → TTS stream → 前端)
- [ ] Go: 慢通道异步流程 (纠错结果回写 → 前端高亮)
- [ ] Python: ASR 流式识别 (FunASR / Paraformer streaming)
- [ ] Python: LLM 对话 + 同步纠错 (一次调用同时产出 reply + correction)
- [ ] Python: TTS 流式合成 (CosyVoice / Edge-TTS)

**打断与同步**
- [ ] 打断控制完整实现 (VAD 信号 → context cancel → TTS 流切断 → LLM 任务终止)
- [ ] 前端实时字幕同步 (ASR partial → 逐字显示)
- [ ] 前端纠错高亮标记 (correction → 红色波浪线)

**增值功能**
- [ ] 发音评测异步通道 (音频对齐 + 打分模型 → Redis → 前端)
- [ ] 课后总结报告 (对话历史聚合 → LLM 生成结构化报告)
- [ ] 场景模板配置 (面试/点餐/会议 prompt 模板)
- [ ] 前端音频采集 + VAD (getUserMedia → PCM → WebSocket)
- [ ] 前端 TTS 音频播放队列
- [ ] 前端音频波形可视化

**打磨**
- [ ] 端到端延迟优化
- [ ] 错误处理与降级方案
- [ ] 答辩 Demo 录制
