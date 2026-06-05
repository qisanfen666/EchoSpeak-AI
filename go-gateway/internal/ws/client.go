package ws

import (
	"encoding/json"
	"log"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

const (
	writeWait      = 10 * time.Second
	pongWait       = 60 * time.Second
	pingPeriod     = (pongWait * 9) / 10
	maxMessageSize = 512 * 1024
	sendBufSize    = 256
)

// Client represents a single WebSocket connection
type Client struct {
	hub     *Hub
	conn    *websocket.Conn
	send    chan []byte // JSON text messages
	sendBin chan []byte // binary messages (audio)
	session *Session
	mu      sync.Mutex
	closed  bool
}

func NewClient(hub *Hub, conn *websocket.Conn) *Client {
	return &Client{
		hub:     hub,
		conn:    conn,
		send:    make(chan []byte, sendBufSize),
		sendBin: make(chan []byte, sendBufSize),
	}
}

// SendJSON sends a JSON message (non-blocking)
func (c *Client) SendJSON(msg interface{}) {
	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return
	}
	c.mu.Unlock()

	data, err := json.Marshal(msg)
	if err != nil {
		log.Printf("[Client] Marshal error: %v", err)
		return
	}

	select {
	case c.send <- data:
	default:
		log.Printf("[Client] Send buffer full, dropping message")
	}
}

// SendBinary sends raw binary data (e.g. MP3 audio chunks)
func (c *Client) SendBinary(data []byte) {
	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return
	}
	c.mu.Unlock()

	select {
	case c.sendBin <- data:
	default:
		log.Printf("[Client] Binary buffer full, dropping audio chunk")
	}
}

// Close closes the client connection
func (c *Client) Close() {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.closed {
		c.closed = true
		c.conn.Close()
	}
}

// readPump reads messages from WebSocket
func (c *Client) readPump() {
	defer func() {
		c.hub.Unregister(c)
		c.Close()
	}()

	c.conn.SetReadLimit(maxMessageSize)
	c.conn.SetReadDeadline(time.Now().Add(pongWait))
	c.conn.SetPongHandler(func(string) error {
		c.conn.SetReadDeadline(time.Now().Add(pongWait))
		return nil
	})

	for {
		_, rawMsg, err := c.conn.ReadMessage()
		if err != nil {
			if websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway, websocket.CloseNormalClosure) {
				log.Printf("[Client] Read error: %v", err)
			}
			break
		}

		var msg WSMessage
		if err := json.Unmarshal(rawMsg, &msg); err != nil {
			log.Printf("[Client] Unmarshal error: %v", err)
			continue
		}

		handleMessage(c, &msg)
	}
}

// writePump writes messages to WebSocket (text + binary)
func (c *Client) writePump() {
	ticker := time.NewTicker(pingPeriod)
	defer func() {
		ticker.Stop()
		c.Close()
	}()

	for {
		select {
		case msg, ok := <-c.send:
			c.conn.SetWriteDeadline(time.Now().Add(writeWait))
			if !ok {
				c.conn.WriteMessage(websocket.CloseMessage, []byte{})
				return
			}
			if err := c.conn.WriteMessage(websocket.TextMessage, msg); err != nil {
				return
			}

		case msg, ok := <-c.sendBin:
			c.conn.SetWriteDeadline(time.Now().Add(writeWait))
			if !ok {
				return
			}
			if err := c.conn.WriteMessage(websocket.BinaryMessage, msg); err != nil {
				return
			}

		case <-ticker.C:
			c.conn.SetWriteDeadline(time.Now().Add(writeWait))
			if err := c.conn.WriteMessage(websocket.PingMessage, nil); err != nil {
				return
			}
		}
	}
}
