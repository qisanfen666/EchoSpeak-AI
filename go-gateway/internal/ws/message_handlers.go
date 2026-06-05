package ws

import "log"

// onAudioChunk processes audio data from the client
// Fast channel: forwards to Python ASR via gRPC
func onAudioChunk(c *Client, data *AudioChunkData, seq int64) {
	if c.session == nil {
		log.Println("[Handler] Audio chunk without session")
		return
	}

	HandleAudioChunk(AudioChunkEvent{
		SessionID: c.session.ID,
		Data:      data.Data,
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

// onEndSession handles session end
func onEndSession(c *Client) {
	if c.session == nil {
		return
	}

	log.Printf("[Handler] Session end requested: session=%s", c.session.ID)

	HandleSessionEnd(c.session.ID, c)
}
