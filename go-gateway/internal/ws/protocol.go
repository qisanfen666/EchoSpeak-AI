package ws

// ============================================
// WebSocket 消息协议 — 前端 ↔ Go 网关
// ============================================

// WSMessage 通用消息信封
type WSMessage struct {
	Type string      `json:"type"`           // 消息类型
	Data interface{} `json:"data,omitempty"` // 具体载荷
	Seq  int64       `json:"seq,omitempty"`  // 消息序号（用于排序和重传）
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
	DataB64 string `json:"data"` // base64 encoded PCM audio
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
	Text          string `json:"text"`
	IsFinal       bool   `json:"is_final"`
	IsUser        bool   `json:"is_user"`                 // true=用户说话, false=AI回复
	Pronunciation int    `json:"pronunciation,omitempty"` // 发音评分 0-100
	Fluency       int    `json:"fluency,omitempty"`       // 流利度评分 0-100
}

// ReplyChunkData AI 回复文本片段
type ReplyChunkData struct {
	Text    string `json:"text"`
	IsFirst bool   `json:"is_first"`
}

// ReplyAudioData TTS 音频
type ReplyAudioData struct {
	Data    []byte `json:"data"` // base64 编码
	ChunkID int    `json:"chunk_id"`
	IsFinal bool   `json:"is_final"`
}

// CorrectionData 语法纠错 — matches frontend showCorrection format
type CorrectionData struct {
	OriginalText  string      `json:"original_text"`
	CorrectedText string      `json:"corrected_text"`
	Errors        []ErrorItem `json:"errors"`
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
	Scene         string      `json:"scene"`                 // 场景名称
	DurationSec   int         `json:"duration_sec"`          // 练习时长（秒）
	Turns         int         `json:"turns"`                 // 对话轮数
	Grammar       int         `json:"grammar"`               // 语法评分 0-100
	Vocabulary    int         `json:"vocabulary"`            // 词汇评分 0-100
	Pronunciation int         `json:"pronunciation"`         // 发音评分 0-100
	Fluency       int         `json:"fluency"`               // 流利度评分 0-100
	ErrorStats    []ErrorStat `json:"error_stats"`           // 高频错误统计
	Suggestions   []string    `json:"suggestions"`           // 学习建议
	TurnTrends    []TurnTrend `json:"turn_trends,omitempty"` // 逐轮趋势数据
}

// TurnTrend 单轮趋势数据（用于前端绘制折线图）
type TurnTrend struct {
	TurnIndex      int   `json:"turn_index"`       // 轮次序号（从 0 开始）
	ErrorCount     int   `json:"error_count"`      // 该轮错误总数
	ResponseTimeMs int64 `json:"response_time_ms"` // 该轮回复耗时（毫秒）
	GrammarErrors  int   `json:"grammar_errors"`   // 语法类错误数
	VocabErrors    int   `json:"vocab_errors"`     // 词汇类错误数
}

// ErrorStat 错误统计
type ErrorStat struct {
	Type  string `json:"type"`  // 错误类型标识
	Label string `json:"label"` // 中文标签，如 "冠词遗漏"
	Count int    `json:"count"` // 出现次数
}
