package ws

import (
	"encoding/json"
	"log"
	"net/http"

	"github.com/gorilla/websocket"
)

var upgrader = websocket.Upgrader{
	ReadBufferSize:  4096,
	WriteBufferSize: 4096,
	CheckOrigin: func(r *http.Request) bool {
		return true // 3天限时赛，开发阶段允许所有来源
	},
}

// ServeWS 处理 WebSocket 升级请求
// URL: ws://host/ws?session_id=xxx&scene=ordering
func ServeWS(hub *Hub, w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("[WS] Upgrade error: %v", err)
		return
	}

	sessionID := r.URL.Query().Get("session_id")
	if sessionID == "" {
		log.Printf("[WS] Missing session_id, rejecting connection")
		conn.Close()
		return
	}

	client := NewClient(hub, conn)
	hub.Register(client, sessionID)

	// 如果 URL 指定了场景，设置为该场景
	if scene := r.URL.Query().Get("scene"); scene != "" {
		if s := hub.GetSession(sessionID); s != nil {
			s.Scene = scene
		}
	}
	// 读取难度
	if diff := r.URL.Query().Get("difficulty"); diff != "" {
		if s := hub.GetSession(sessionID); s != nil {
			s.Difficulty = diff
		}
	}

	// 启动读写协程
	go client.writePump()
	go client.readPump()

	log.Printf("[WS] New connection: session=%s scene=%s", sessionID,
		hub.GetSession(sessionID).Scene)
}

// handleMessage 分发 WebSocket 消息到具体处理器
func handleMessage(c *Client, msg *WSMessage) {
	// 根据消息类型，重新解析 Data 到具体结构体
	switch msg.Type {

	case MsgAudioChunk:
		// 将通用 Data 转换为 AudioChunkData
		dataBytes, err := json.Marshal(msg.Data)
		if err != nil {
			return
		}
		var data AudioChunkData
		if err := json.Unmarshal(dataBytes, &data); err != nil {
			log.Printf("[Handler] Parse audio_chunk error: %v", err)
			return
		}
		onAudioChunk(c, &data, msg.Seq)

	case MsgTextMessage:
		dataBytes, err := json.Marshal(msg.Data)
		if err != nil {
			return
		}
		var data TextMessageData
		if err := json.Unmarshal(dataBytes, &data); err != nil {
			log.Printf("[Handler] Parse text_message error: %v", err)
			return
		}
		onTextMessage(c, &data)

	case MsgInterrupt:
		onInterrupt(c, msg.Seq)

	case MsgSceneSelect:
		dataBytes, _ := json.Marshal(msg.Data)
		var data SceneSelectData
		if err := json.Unmarshal(dataBytes, &data); err != nil {
			return
		}
		onSceneSelect(c, &data)

	case MsgEndSession:
		onEndSession(c)

	default:
		log.Printf("[Handler] Unknown message type: %s", msg.Type)
	}
}
