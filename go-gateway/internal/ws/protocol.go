package ws

// ============================================
// WebSocket 消息协议 — 前端 ↔ Go 网关
// ============================================

// WSMessage 通用消息信封
type WSMessage struct {
	Type string      `json:"type"`            // 消息类型
	Data interface{} `json:"data,omitempty"`  // 具体载荷
	Seq  int64       `json:"seq,omitempty"`   // 消息序号（用于排序和重传）
}

// ============ 客户端 → 服务端 ============

const (
	MsgAudioChunk  = "audio_chunk"  // 音频数据块
	MsgTextMessage = "text_message" // 文本消息（跳过 ASR，直接 Chat）
	MsgInterrupt   = "interrupt"    // 用户打断（VAD 检测到用户开始说话）
	MsgSceneSelect = "scene_select" // 选择场景
	MsgEndSession  = "end_session"  // 主动结束会话
)

// AudioChunkData 音频块载荷
type AudioChunkData struct {
	DataB64 string `json:"data"`   // base64 encoded PCM audio
	IsEnd   bool   `json:"is_end"`
	ChunkID int    `json:"chunk_id"`
}

// TextMessageData 文本消息载荷
type TextMessageData struct {
	Text string `json:"text"`
}

// SceneSelectData 场景选择
type SceneSelectData struct {
	Scene string `json:"scene"` // interview / ordering / meeting / custom
}

// ============ 服务端 → 客户端 ============

const (
	MsgTranscript    = "transcript"     // ASR 识别结果（实时字幕）
	MsgReplyStart    = "reply_start"    // AI 开始回复
	MsgReplyChunk    = "reply_chunk"    // AI 回复文本片段
	MsgReplyAudio    = "reply_audio"    // TTS 音频块
	MsgReplyEnd      = "reply_end"      // AI 回复结束
	MsgCorrection    = "correction"     // 语法/表达纠错
	MsgScoreUpdate   = "score_update"   // 实时评分更新
	MsgSessionReport = "session_report" // 课后报告
	MsgError         = "error"          // 错误信息
)

// TranscriptData ASR 识别结果
type TranscriptData struct {
	Text    string `json:"text"`
	IsFinal bool   `json:"is_final"`
	IsUser  bool   `json:"is_user"` // true=用户说话, false=AI回复
}

// ReplyChunkData AI 回复文本片段
type ReplyChunkData struct {
	Text    string `json:"text"`
	IsFirst bool   `json:"is_first"`
}

// ReplyAudioData TTS 音频
type ReplyAudioData struct {
	Data    []byte `json:"data"`     // base64 编码
	ChunkID int    `json:"chunk_id"`
	IsFinal bool   `json:"is_final"`
}

// CorrectionData 语法纠错 — matches frontend showCorrection format
type CorrectionData struct {
	OriginalText string      `json:"original_text"`
	CorrectedText string     `json:"corrected_text"`
	Errors       []ErrorItem `json:"errors"`
}

// ErrorItem 单个纠错详情 — matches frontend ErrorDetail
type ErrorItem struct {
	Type          string `json:"type"`           // grammar|tense|preposition|article|vocabulary|word_choice|expression
	Original      string `json:"original"`       // incorrect fragment
	Corrected     string `json:"corrected"`      // suggested correction
	ExplanationCN string `json:"explanation_cn"` // Chinese explanation
}

// ScoreUpdateData 实时评分
type ScoreUpdateData struct {
	Category string  `json:"category"` // pronunciation / grammar / fluency
	Score    float64 `json:"score"`
}

// SessionReportData 课后报告
type SessionReportData struct {
	OverallScore float64      `json:"overall_score"`
	Summary      string       `json:"summary"` // Markdown 格式
	WeakPoints   []WeakPoint  `json:"weak_points"`
	Suggestions  []string     `json:"suggestions"`
}

// WeakPoint 薄弱项
type WeakPoint struct {
	Category  string `json:"category"`
	Detail    string `json:"detail"`
	Frequency int    `json:"frequency"`
}
