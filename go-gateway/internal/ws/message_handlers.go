package ws

import (
	"encoding/base64"
	"log"
)

// onTextMessage handles typed text — skips ASR, goes directly to Chat
func onTextMessage(c *Client, data *TextMessageData) {
	if c.session == nil {
		log.Println("[Handler] Text message without session")
		return
	}

	text := data.Text
	if text == "" {
		return
	}

	log.Printf("[Handler] Text message: session=%s text=\"%s\"", c.session.ID, text)

	HandleTextInput(c.session.ID, text, c)
}

// onAudioChunk processes audio data from the client
// Fast channel: forwards to Python ASR via gRPC
func onAudioChunk(c *Client, data *AudioChunkData, seq int64) {
	if c.session == nil {
		log.Println("[Handler] Audio chunk without session")
		return
	}

	// Decode base64 PCM data
	pcmData, err := base64.StdEncoding.DecodeString(data.DataB64)
	if err != nil {
		log.Printf("[Handler] Base64 decode error: %v", err)
		return
	}

	HandleAudioChunk(AudioChunkEvent{
		SessionID: c.session.ID,
		Data:      pcmData,
		IsEnd:     data.IsEnd,
		ChunkID:   data.ChunkID,
		Seq:       seq,
		Client:    c,
	})
}

// onInterrupt handles user barge-in
func onInterrupt(c *Client, seq int64) {
	if c.session == nil {
		return
	}

	log.Printf("[Handler] Interrupt: session=%s", c.session.ID)

	HandleInterrupt(c.session.ID)

	// Notify frontend to stop TTS playback
	c.SendJSON(WSMessage{
		Type: MsgReplyEnd,
		Data: map[string]bool{"interrupted": true},
	})
}

// onSceneSelect handles scene switching
func onSceneSelect(c *Client, data *SceneSelectData) {
	if c.session == nil {
		return
	}

	c.session.Scene = data.Scene
	log.Printf("[Handler] Scene changed: session=%s scene=%s", c.session.ID, data.Scene)
}

// onCustomScene handles custom topic scene setup.
// The greeting was already fired in ServeWS based on the URL scene parameter,
// so here we just update the session metadata.
func onCustomScene(c *Client, data *CustomSceneData) {
	if c.session == nil {
		return
	}

	c.session.Scene = "custom"
	log.Printf("[Handler] Custom scene: session=%s topic=\"%s\"", c.session.ID, data.Description)
}

// onEndSession handles session end
func onEndSession(c *Client) {
	if c.session == nil {
		return
	}

	log.Printf("[Handler] Session end requested: session=%s", c.session.ID)

	HandleSessionEnd(c.session.ID, c)
}
