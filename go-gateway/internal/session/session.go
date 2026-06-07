package session

import (
	"context"
	"sync"
	"time"
)

// ConversationTurn 一轮对话
type ConversationTurn struct {
	Index         int              `json:"index"`
	UserText      string           `json:"user_text"`
	AssistantText string           `json:"assistant_text"`
	Pronunciation int              `json:"pronunciation"`          // 发音评分 0-100
	Fluency       int              `json:"fluency"`                // 流利度评分 0-100
	ResponseTimeMs int64           `json:"response_time_ms"`       // 该轮回复耗时（毫秒）
	Correction    *TurnCorrection  `json:"correction,omitempty"`
	Timestamp     time.Time        `json:"timestamp"`
}

// TurnCorrection 该轮的纠错信息
type TurnCorrection struct {
	Original  string      `json:"original"`
	Corrected string      `json:"corrected"`
	ErrorType string      `json:"error_type"`
	Errors    []ErrorItem `json:"errors,omitempty"` // 详细错误列表
}

// ErrorItem 单个错误详情
type ErrorItem struct {
	Type      string `json:"type"`      // grammar/tense/preposition/article/vocabulary/word_choice/expression
	Original  string `json:"original"`  // 错误片段
	Corrected string `json:"corrected"` // 纠正后片段
}

// Manager 单个会话的上下文管理器
// 包装 context.Context，支持打断时级联取消
type Manager struct {
	ID         string
	Scene      string
	Difficulty string // easy / medium / hard
	Accent     string // TTS voice name e.g. en-US-JennyNeural
	ctx        context.Context
	cancel   context.CancelFunc
	mu       sync.RWMutex

	// 对话历史
	History []ConversationTurn

	// 当前活跃的轮次（用于打断取消）
	CurrentTurnCtx      context.Context
	CurrentTurnCancel   context.CancelFunc
}

// NewManager 创建会话管理器
func NewManager(sessionID, scene string) *Manager {
	ctx, cancel := context.WithCancel(context.Background())
	return &Manager{
		ID:      sessionID,
		Scene:   scene,
		ctx:     ctx,
		cancel:  cancel,
		History: make([]ConversationTurn, 0, 100),
	}
}

// Context 返回会话的根 context
func (m *Manager) Context() context.Context {
	return m.ctx
}

// NewTurn 开始新的一轮对话
// 返回该轮的 context，打断时 cancel 即可级联取消所有子任务
func (m *Manager) NewTurn() (context.Context, int) {
	// Cancel 上一轮（如果还在进行中）
	if m.CurrentTurnCancel != nil {
		m.CurrentTurnCancel()
	}

	ctx, cancel := context.WithCancel(m.ctx)
	m.CurrentTurnCtx = ctx
	m.CurrentTurnCancel = cancel

	m.mu.Lock()
	turnIndex := len(m.History)
	m.mu.Unlock()

	return ctx, turnIndex
}

// AddTurn 记录一轮完成的对话
func (m *Manager) AddTurn(turn ConversationTurn) {
	m.mu.Lock()
	defer m.mu.Unlock()
	turn.Index = len(m.History)
	m.History = append(m.History, turn)
}

// AddCorrection 给最近一次对话附加纠错
func (m *Manager) AddCorrection(correction TurnCorrection) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if len(m.History) > 0 {
		last := &m.History[len(m.History)-1]
		last.Correction = &correction
	}
}

// GetHistory 获取对话历史
func (m *Manager) GetHistory() []ConversationTurn {
	m.mu.RLock()
	defer m.mu.RUnlock()
	result := make([]ConversationTurn, len(m.History))
	copy(result, m.History)
	return result
}

// CancelCurrentTurn 打断当前轮次
func (m *Manager) CancelCurrentTurn() {
	if m.CurrentTurnCancel != nil {
		m.CurrentTurnCancel()
	}
}

// Close 关闭整个会话
func (m *Manager) Close() {
	m.cancel()
	m.mu.Lock()
	defer m.mu.Unlock()
	m.History = nil
}
