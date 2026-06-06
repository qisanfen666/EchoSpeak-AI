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

**前置依赖：** Python 3.11+、Redis

```bash
# 1. 启动 Redis（二选一）
docker compose up -d redis          # 有 Docker Desktop
redis-server                        # 无 Docker，需自行安装 Redis

# 2. 配置 API Key
# 在 python-engine/ 下创建 .env，填入 LLM_API_KEY=sk-xxx

# 3. 安装 Python 依赖 & 生成 Proto
cd python-engine
pip install -r requirements.txt
python gen_proto.py

# 4. 启动 Python gRPC 引擎
python main.py                      # 监听 :50051

# 5. 新终端，启动 Go 网关 + 前端
cd go-gateway
./gateway.exe                       # 已编译好，监听 :8080，同时托管前端

# 浏览器打开 http://localhost:8080
```

### 本地开发

如果修改了 Go 代码，重新编译：
```bash
cd go-gateway && go build -o gateway.exe .
```
如果修改了 proto 文件，两端都要重新生成：
```bash
cd go-gateway && protoc --go_out=./proto --go-grpc_out=./proto proto/aiservice.proto
cd python-engine && python gen_proto.py
```

## 项目结构

```
EchoSpeak-AI/
├── go-gateway/               # Go 接入层
│   ├── main.go               # 入口，托管 WebSocket + 前端静态文件
│   ├── config/config.go      # 配置（环境变量）
│   ├── proto/                # gRPC 协议定义 + 生成代码
│   ├── internal/
│   │   ├── ws/               # WebSocket 连接管理 + 流式路由 + 报告生成
│   │   ├── session/          # 会话上下文 (context + cancel) + 对话历史
│   │   ├── grpc_client/      # Python gRPC 客户端
│   │   └── redis_client/     # Redis 操作封装
│   └── gateway.exe           # 预编译二进制（Windows）
├── python-engine/            # Python AI 推理层
│   ├── main.py               # gRPC Server（StreamASR / Chat / Synthesize）
│   ├── fastapi_server.py     # FastAPI 开发服务器（直连前端，不走 Go）
│   ├── config.py             # 配置（环境变量 + 默认值）
│   ├── gen_proto.py          # Proto 生成脚本
│   └── services/
│       ├── asr_engine.py     # ASR：faster-whisper 语音转文字 + 发音/流利度打分
│       ├── llm_engine.py     # LLM：OpenAI 兼容接口流式对话
│       ├── tts_engine.py     # TTS：edge-tts 语音合成
│       └── correction_engine.py  # 纠错：独立 LLM 调用分析语法/表达问题
├── frontend/                 # 前端单页应用
│   └── index.html            # 3 视图（选场景 → 对话 → 报告）+ Chart.js 可视化
└── docker-compose.yml        # Redis 容器（仅用于本地开发）
```

## 功能清单

### ✅ 已完成

**核心对话链路**
- [x] ASR 语音识别（faster-whisper，支持 PCM 流式输入）
- [x] LLM 流式对话（DeepSeek / OpenAI 兼容，4 个预设场景 + 自定义场景）
- [x] TTS 语音合成（edge-tts，流式 MP3 推送前端播放）
- [x] 全双工语音/文字对话（Go gRPC StreamASR + Chat 管线）
- [x] 打断控制（前端 VAD → Go context cancel → 终止 LLM + TTS）

**语法纠错**
- [x] 并行纠错 LLM 调用（独立于对话 LLM，不增加延迟）
- [x] 7 种错误类型识别（grammar / tense / preposition / article / vocabulary / word_choice / expression）
- [x] 前端纠错卡片展示（原文 → 纠正 + 中文解释）
- [x] 纠错结果同步写入会话历史，供课后报告统计

**发音评测**
- [x] 发音准确度打分（基于 Whisper avg_logprob，0-100）
- [x] 流利度打分（基于 WPM + 停顿段数，0-100）
- [x] 评分随 ASR 结果实时推送前端

**课后报告**
- [x] 多维评分展示（语法 / 词汇 / 发音 / 流利，4 色卡片）
- [x] 高频错误统计（按类型排序 + 横向条形图）
- [x] 逐错误详情列表（类型标签 + 原文 → 纠正 + 解释）
- [x] 针对性学习建议（基于 Top-3 高频错误类型）
- [x] 本次学习趋势折线图（逐轮错误数 + 回复耗时，Chart.js）
- [x] 历史对比图表（跨练习的错误总数 / 评分 / 耗时趋势，纯内存）

**前端**
- [x] 3 视图架构（场景选择 → 实时对话 → 课后报告）
- [x] 麦克风采集 + VAD 语音活动检测 + 波形可视化
- [x] TTS 音频播放队列
- [x] AI 回复流式逐字显示

**运维**
- [x] 预编译 Go 二进制（Windows，无需 Go 环境即可运行）
- [x] 环境变量配置（.env，仅需填 API Key）

### 🔲 待完成

- [ ] 发音评测对齐打分模型（当前基于 logprob 估算，可接入专用评测模型）
- [ ] 跨会话数据持久化（当前历史对比仅存浏览器内存）
- [ ] 端到端延迟优化（Whisper 模型预热、gRPC 连接池）
- [ ] 移动端适配 / PWA
- [ ] 单元测试 + 集成测试
