package ws

import (
	"log"
	"sync"

	"go-gateway/internal/session"
)

// ============================================
// 流式路由 — 快通道 + 慢通道的核心调度
// （放在 ws 包内避免循环依赖）
// ============================================

// SessionManager 管理所有活跃会话
var SessionManager = struct {
	mu       sync.RWMutex
	sessions map[string]*session.Manager
}{
	sessions: make(map[string]*session.Manager),
}

// GetOrCreateSession 获取或创建会话管理器
func GetOrCreateSession(sessionID, scene string) *session.Manager {
	SessionManager.mu.Lock()
	defer SessionManager.mu.Unlock()

	if mgr, ok := SessionManager.sessions[sessionID]; ok {
		return mgr
	}
	mgr := session.NewManager(sessionID, scene)
	SessionManager.sessions[sessionID] = mgr
	return mgr
}

// RemoveSession 移除会话
func RemoveSession(sessionID string) {
	SessionManager.mu.Lock()
	defer SessionManager.mu.Unlock()
	if mgr, ok := SessionManager.sessions[sessionID]; ok {
		mgr.Close()
		delete(SessionManager.sessions, sessionID)
	}
}

// AudioChunkEvent 音频数据事件
type AudioChunkEvent struct {
	SessionID string
	Data      []byte
	IsEnd     bool
	ChunkID   int
	Seq       int64
	Client    *Client
}

// HandleAudioChunk 处理音频块（Day 1 实现具体逻辑）
func HandleAudioChunk(evt AudioChunkEvent) {
	mgr := GetOrCreateSession(evt.SessionID, evt.Client.SessionScene())

	go func() {
		log.Printf("[Stream] Audio chunk: session=%s chunk=%d size=%d is_end=%v",
			evt.SessionID, evt.ChunkID, len(evt.Data), evt.IsEnd)
	}()

	if evt.IsEnd {
		go HandleUserUtteranceEnd(evt.SessionID, evt.Client)
	}

	_ = mgr
}

// HandleUserUtteranceEnd 用户一句话说完，启动 LLM 轮次
func HandleUserUtteranceEnd(sessionID string, client *Client) {
	mgr := GetOrCreateSession(sessionID, client.SessionScene())
	turnCtx, _ := mgr.NewTurn()

	log.Printf("[Stream] User utterance end: session=%s", sessionID)

	go func() {
		// TODO Day 1: ASR final → LLM stream → TTS stream
		client.SendJSON(WSMessage{
			Type: MsgReplyStart,
			Data: map[string]string{"session_id": sessionID},
		})
		log.Printf("[Stream] Fast channel processing: session=%s", sessionID)
		_ = turnCtx
	}()
}

// HandleInterrupt 处理打断
func HandleInterrupt(sessionID string) {
	mgr := GetOrCreateSession(sessionID, "")
	mgr.CancelCurrentTurn()
	log.Printf("[Stream] Interrupt handled: session=%s", sessionID)
}

// HandleSessionEnd 会话结束，触发课后报告
func HandleSessionEnd(sessionID string, client *Client) {
	mgr := GetOrCreateSession(sessionID, client.SessionScene())
	log.Printf("[Stream] Session ending: session=%s, turns=%d", sessionID, len(mgr.GetHistory()))
	RemoveSession(sessionID)
}
