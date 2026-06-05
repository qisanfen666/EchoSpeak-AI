package ws

import (
	"log"
	"sync"
	"time"

	"go-gateway/internal/grpc_client"
	"go-gateway/internal/session"
)

// ============================================
// Stream router — audio accumulation + ASR + Chat pipeline
// ============================================

var SessionManager = struct {
	mu       sync.RWMutex
	sessions map[string]*session.Manager
	audioBuf map[string][]byte // accumulated audio per session
}{
	sessions: make(map[string]*session.Manager),
	audioBuf: make(map[string][]byte),
}

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

func RemoveSession(sessionID string) {
	SessionManager.mu.Lock()
	defer SessionManager.mu.Unlock()
	if mgr, ok := SessionManager.sessions[sessionID]; ok {
		mgr.Close()
		delete(SessionManager.sessions, sessionID)
	}
	delete(SessionManager.audioBuf, sessionID)
}

type AudioChunkEvent struct {
	SessionID string
	Data      []byte
	IsEnd     bool
	ChunkID   int
	Seq       int64
	Client    *Client
}

// HandleAudioChunk accumulates audio, triggers ASR+Chat on is_end
func HandleAudioChunk(evt AudioChunkEvent) {
	SessionManager.mu.Lock()
	SessionManager.audioBuf[evt.SessionID] = append(SessionManager.audioBuf[evt.SessionID], evt.Data...)
	bufLen := len(SessionManager.audioBuf[evt.SessionID])
	SessionManager.mu.Unlock()

	log.Printf("[Stream] Audio chunk: session=%s chunk=%d size=%d total=%d is_end=%v",
		evt.SessionID, evt.ChunkID, len(evt.Data), bufLen, evt.IsEnd)

	if evt.IsEnd && bufLen > 0 {
		go HandleUserUtteranceEnd(evt.SessionID, evt.Client)
	}
}

// HandleUserUtteranceEnd: ASR real audio → text → Chat → TTS
func HandleUserUtteranceEnd(sessionID string, client *Client) {
	mgr := GetOrCreateSession(sessionID, client.SessionScene())
	mgr.CancelCurrentTurn()

	turnCtx, _ := mgr.NewTurn()

	// Drain accumulated audio
	SessionManager.mu.Lock()
	audio := SessionManager.audioBuf[sessionID]
	SessionManager.audioBuf[sessionID] = nil
	SessionManager.mu.Unlock()

	log.Printf("[Stream] ASR processing: session=%s audio_bytes=%d", sessionID, len(audio))

	// Notify frontend: ASR is processing
	client.SendJSON(WSMessage{
		Type: MsgTranscript,
		Data: TranscriptData{Text: "...", IsFinal: false, IsUser: true},
	})

	// Call real ASR via gRPC
	userText := ""
	if len(audio) > 0 {
		text, err := grpc_client.StreamASR(turnCtx, sessionID, audio)
		if err != nil {
			log.Printf("[Stream] ASR error: %v", err)
			client.SendJSON(WSMessage{
				Type: MsgError,
				Data: map[string]string{"message": "ASR failed: " + err.Error()},
			})
			return
		}
		userText = text
	} else {
		userText = "Hello"
	}

	log.Printf("[Stream] ASR result: \"%s\"", userText)

	// Send transcript to frontend
	client.SendJSON(WSMessage{
		Type: MsgTranscript,
		Data: TranscriptData{Text: userText, IsFinal: true, IsUser: true},
	})

	// Now call Chat with real text
	startTime := time.Now()
	result := grpc_client.ChatStream(turnCtx, sessionID, mgr.Scene, userText)

	go func() {
		fullReply := ""
		firstChunk := true

		for {
			select {
			case text, ok := <-result.ReplyChunks:
				if !ok {
					goto done
				}
				fullReply += text
				if firstChunk {
					client.SendJSON(WSMessage{
						Type: MsgReplyStart,
						Data: ReplyChunkData{Text: text, IsFirst: true},
					})
					firstChunk = false
				} else {
					client.SendJSON(WSMessage{
						Type: MsgReplyChunk,
						Data: ReplyChunkData{Text: text, IsFirst: false},
					})
				}

			case audio, ok := <-result.AudioChunks:
				if ok && len(audio) > 0 {
					client.SendBinary(audio)
				}

			case correction, ok := <-result.Correction:
				if ok {
					client.SendJSON(WSMessage{
						Type: MsgCorrection,
						Data: CorrectionData{
							Original:  correction.Original,
							Corrected: correction.Corrected,
							ErrorType: correction.ErrorType,
						},
					})
				}

			case <-result.Done:
				elapsed := time.Since(startTime)
				log.Printf("[Stream] Done: session=%s asr=\"%s\" reply=\"%s\" %v",
					sessionID, userText, fullReply, elapsed)

				client.SendJSON(WSMessage{
					Type: MsgReplyEnd,
					Data: map[string]interface{}{
						"interrupted": false,
						"elapsed_ms":  elapsed.Milliseconds(),
					},
				})

				mgr.AddTurn(session.ConversationTurn{
					UserText:      userText,
					AssistantText: fullReply,
				})
				goto done

			case err, ok := <-result.Err:
				if ok {
					log.Printf("[Stream] Chat error: %v", err)
					client.SendJSON(WSMessage{
						Type: MsgError,
						Data: map[string]string{"message": err.Error()},
					})
				}
				goto done
			}
		}
	done:
	}()
}

func HandleInterrupt(sessionID string) {
	mgr := GetOrCreateSession(sessionID, "")
	mgr.CancelCurrentTurn()
	// Clear buffered audio on interrupt
	SessionManager.mu.Lock()
	SessionManager.audioBuf[sessionID] = nil
	SessionManager.mu.Unlock()
	log.Printf("[Stream] Interrupt: session=%s", sessionID)
}

func HandleSessionEnd(sessionID string, client *Client) {
	mgr := GetOrCreateSession(sessionID, client.SessionScene())
	log.Printf("[Stream] Session ending: session=%s turns=%d", sessionID, len(mgr.GetHistory()))
	RemoveSession(sessionID)
}
