package ws

import (
	"log"
	"sync"
)

// Hub 管理所有 WebSocket 连接，按 session_id 分组
type Hub struct {
	mu       sync.RWMutex
	sessions map[string]*Session // session_id → Session
	clients  map[*Client]string  // client → session_id (反向索引)
}

// Session 一个会话房间（一个用户一个 session）
// 注意：3天限时赛，暂不考虑多用户进同一房间
type Session struct {
	ID         string
	Clients    map[*Client]bool
	Scene      string // 当前场景
	Difficulty string // easy / medium / hard
	Accent     string // TTS voice name
	Active     bool   // 会话是否活跃
}

// NewHub 创建连接中心
func NewHub() *Hub {
	return &Hub{
		sessions: make(map[string]*Session),
		clients:  make(map[*Client]string),
	}
}

// Register 注册一个新客户端连接
func (h *Hub) Register(c *Client, sessionID string) {
	h.mu.Lock()
	defer h.mu.Unlock()

	// 关联 client → session
	h.clients[c] = sessionID

	// 创建或加入 session
	s, ok := h.sessions[sessionID]
	if !ok {
		s = &Session{
			ID:      sessionID,
			Clients: make(map[*Client]bool),
			Scene:   "ordering", // 默认场景
			Active:  true,
		}
		h.sessions[sessionID] = s
	}
	s.Clients[c] = true
	c.session = s

	log.Printf("[Hub] Client registered to session %s (total sessions: %d)", sessionID, len(h.sessions))
}

// Unregister 注销客户端连接
func (h *Hub) Unregister(c *Client) {
	h.mu.Lock()
	defer h.mu.Unlock()

	sessionID, ok := h.clients[c]
	if !ok {
		return
	}
	delete(h.clients, c)

	s, ok := h.sessions[sessionID]
	if !ok {
		return
	}
	delete(s.Clients, c)

	// 如果 session 没有活跃连接，标记为非活跃（但保留历史数据）
	if len(s.Clients) == 0 {
		s.Active = false
		log.Printf("[Hub] Session %s became inactive (no clients)", sessionID)
	}

	close(c.send)
	log.Printf("[Hub] Client unregistered from session %s", sessionID)
}

// GetSession 获取 session
func (h *Hub) GetSession(sessionID string) *Session {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return h.sessions[sessionID]
}

// Run 启动 Hub（目前主要是占位，后续可扩展定期清理过期 session）
func (h *Hub) Run() {
	log.Println("[Hub] Started")
}
