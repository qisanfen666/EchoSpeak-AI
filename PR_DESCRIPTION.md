# EchoSpeak AI — 集成 LLM 对话引擎 + 全双工对话

## 功能描述

实现完整的 ASR → LLM → TTS 对话流水线，支持文字输入和语音输入两种方式与 AI 进行英语口语练习。

- **文字输入**：在输入框输入英文，按 Enter 发送，AI 回复文字 + 语音朗读
- **语音输入**：点击「录音」按钮，说话后点击停止，自动识别 → AI 回复 → 语音朗读
- **场景对话**：餐厅点餐、工作面试、商务会议、旅行出行四种场景，切换场景自动重置对话上下文
- **课后报告**：结束会话时生成综合评分报告

## 实现思路

- **LLM 引擎** (`services/llm_engine.py`)：基于 OpenAI 兼容 API，接入 DeepSeek 模型
  - 场景化 System Prompt，每个场景有独立角色设定（waiter/interviewer/team lead/receptionist）
  - 对话历史管理（自动裁剪过长历史，保留最近 20 轮）
  - 网络异常时自动降级为本地回复
- **全双工 WebSocket** (`fastapi_server.py` `/ws` 端点)：
  - 接收文字消息 (`text_message`) 或音频 (`audio_chunk`)
  - 音频支持 WebM/Opus（MediaRecorder 输出）和 PCM 双格式，自动检测 WebM 魔数
  - 流水线：ASR 识别 → LLM 回复 → TTS 语音合成 → 推送文字+音频
- **前端** (`frontend/index.html`)：
  - MediaRecorder API 录制麦克风，输出 WebM 格式
  - 300ms 缓冲收集 TTS 音频再播放，避免播放卡顿
  - 纯 DOM 操作显示对话（不涉及 innerHTML 编码问题）

## 测试方式

```bash
cd python-engine
set LLM_API_KEY=sk-48d86232619544d5a4e05b77dbbdfe3c
python fastapi_server.py
# 打开 http://localhost:8000
```

1. 输入英文文字 → 回车 → 查看 AI 回复（文字显示 + 语音朗读）
2. 点击「录音」→ 允许麦克风 → 说英语 → 点击停止 → 等待 ASR + LLM + TTS

## 变更文件

| 文件 | 变更 |
|------|------|
| `python-engine/services/llm_engine.py` | **新增** — LLM 对话引擎 |
| `python-engine/services/__init__.py` | **更新** — 导出 LLM 模块 |
| `python-engine/fastapi_server.py` | **更新** — 添加 /ws 全双工端点 |
| `frontend/index.html` | **重写** — 对话界面（文字+录音） |
