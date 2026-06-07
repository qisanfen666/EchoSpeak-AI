package ws

import (
	"fmt"
	"log"
	"sort"
	"sync"
	"time"

	"go-gateway/internal/grpc_client"
	"go-gateway/internal/session"
	"go-gateway/proto"
)

// ============================================
// Stream router — audio accumulation + ASR + Chat pipeline
// ============================================

var SessionManager = struct {
	mu           sync.RWMutex
	sessions     map[string]*session.Manager
	audioBuf     map[string][]byte    // accumulated audio per session
	sessionStart map[string]time.Time // session start time
}{
	sessions:     make(map[string]*session.Manager),
	audioBuf:     make(map[string][]byte),
	sessionStart: make(map[string]time.Time),
}

func GetOrCreateSession(sessionID, scene string) *session.Manager {
	SessionManager.mu.Lock()
	defer SessionManager.mu.Unlock()

	if mgr, ok := SessionManager.sessions[sessionID]; ok {
		if scene != "" && mgr.Scene != scene {
			mgr.Scene = scene
		}
		return mgr
	}
	mgr := session.NewManager(sessionID, scene)
	SessionManager.sessions[sessionID] = mgr
	SessionManager.sessionStart[sessionID] = time.Now()
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
	delete(SessionManager.sessionStart, sessionID)
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


// buildChatHistory converts session history to proto ChatMessage list
func buildChatHistory(mgr *session.Manager) []*proto.ChatMessage {
	turns := mgr.GetHistory()
	if len(turns) == 0 {
		return nil
	}
	msgs := make([]*proto.ChatMessage, 0, len(turns)*2)
	for _, t := range turns {
		if t.UserText != "" {
			msgs = append(msgs, &proto.ChatMessage{Role: "user", Content: t.UserText})
		}
		if t.AssistantText != "" {
			msgs = append(msgs, &proto.ChatMessage{Role: "assistant", Content: t.AssistantText})
		}
	}
	return msgs
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
	result := grpc_client.ChatStream(turnCtx, sessionID, mgr.Scene, userText, buildChatHistory(mgr))

	go func() {
		fullReply := ""
		firstChunk := true
		var turnCorrection *session.TurnCorrection
		replySent := false

		defer func() {
			if !replySent {
				client.SendJSON(WSMessage{
					Type: MsgReplyEnd,
					Data: map[string]interface{}{
						"interrupted": false,
						"elapsed_ms":  time.Since(startTime).Milliseconds(),
					},
				})
			}
		}()

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
					// Build session.TurnCorrection for report generation
					tc := &session.TurnCorrection{
						Original:  correction.Original,
						Corrected: correction.Corrected,
						ErrorType: correction.ErrorType,
					}

					// Build frontend errors from WordFix highlights
					frontendErrors := make([]ErrorItem, 0, len(correction.Highlights))
					for _, h := range correction.Highlights {
						errType := h.Type
						if errType == "" {
							errType = correction.ErrorType
						}
						if errType == "" {
							errType = "grammar"
						}
						start := int(h.StartIdx)
						end := int(h.EndIdx)
						if end > len(correction.Original) {
							end = len(correction.Original)
						}
						frontendErrors = append(frontendErrors, ErrorItem{
							Type:          errType,
							Original:      correction.Original[start:end],
							Corrected:     h.Suggestion,
							ExplanationCN: h.ExplanationCn,
						})
						tc.Errors = append(tc.Errors, session.ErrorItem{
							Type:      errType,
							Original:  correction.Original[start:end],
							Corrected: h.Suggestion,
						})
					}
					if len(correction.Highlights) == 0 && correction.Corrected != correction.Original {
						frontendErrors = append(frontendErrors, ErrorItem{
							Type:      correction.ErrorType,
							Original:  correction.Original,
							Corrected: correction.Corrected,
						})
						tc.Errors = append(tc.Errors, session.ErrorItem{
							Type:      correction.ErrorType,
							Original:  correction.Original,
							Corrected: correction.Corrected,
						})
					}

					turnCorrection = tc

					client.SendJSON(WSMessage{
						Type: MsgCorrection,
						Data: CorrectionData{
							OriginalText:  correction.Original,
							CorrectedText: correction.Corrected,
							Errors:        frontendErrors,
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

				turn := session.ConversationTurn{
					UserText:       userText,
					AssistantText:  fullReply,
					Pronunciation:  0,
					Fluency:        0,
					ResponseTimeMs: elapsed.Milliseconds(),
				}
				if turnCorrection != nil {
					turn.Correction = turnCorrection
				}
				mgr.AddTurn(turn)
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

// HandleTextInput processes typed text — skips ASR, goes directly to Chat
func HandleTextInput(sessionID string, text string, client *Client) {
	mgr := GetOrCreateSession(sessionID, client.SessionScene())
	mgr.CancelCurrentTurn()

	turnCtx, _ := mgr.NewTurn()

	log.Printf("[Stream] Text input: session=%s text=\"%s\"", sessionID, text)

	// Send transcript to frontend (echo back what user typed)
	client.SendJSON(WSMessage{
		Type: MsgTranscript,
		Data: TranscriptData{Text: text, IsFinal: true, IsUser: true},
	})

	// Call Chat directly (no ASR needed)
	startTime := time.Now()
	result := grpc_client.ChatStream(turnCtx, sessionID, mgr.Scene, text, buildChatHistory(mgr))

	go func() {
		fullReply := ""
		firstChunk := true
		var turnCorrection *session.TurnCorrection
		replySent := false

		defer func() {
			if !replySent {
				client.SendJSON(WSMessage{
					Type: MsgReplyEnd,
					Data: map[string]interface{}{
						"interrupted": false,
						"elapsed_ms":  time.Since(startTime).Milliseconds(),
					},
				})
			}
		}()

		for {
			select {
			case replyText, ok := <-result.ReplyChunks:
				if !ok {
					goto done
				}
				fullReply += replyText
				if firstChunk {
					client.SendJSON(WSMessage{
						Type: MsgReplyStart,
						Data: ReplyChunkData{Text: replyText, IsFirst: true},
					})
					firstChunk = false
				} else {
					client.SendJSON(WSMessage{
						Type: MsgReplyChunk,
						Data: ReplyChunkData{Text: replyText, IsFirst: false},
					})
				}

			case audio, ok := <-result.AudioChunks:
				if ok && len(audio) > 0 {
					client.SendBinary(audio)
				}

			case correction, ok := <-result.Correction:
				if ok {
					tc := &session.TurnCorrection{
						Original:  correction.Original,
						Corrected: correction.Corrected,
						ErrorType: correction.ErrorType,
					}

					frontendErrors := make([]ErrorItem, 0, len(correction.Highlights))
					for _, h := range correction.Highlights {
						errType := h.Type
						if errType == "" {
							errType = correction.ErrorType
						}
						if errType == "" {
							errType = "grammar"
						}
						start := int(h.StartIdx)
						end := int(h.EndIdx)
						if end > len(correction.Original) {
							end = len(correction.Original)
						}
						frontendErrors = append(frontendErrors, ErrorItem{
							Type:          errType,
							Original:      correction.Original[start:end],
							Corrected:     h.Suggestion,
							ExplanationCN: h.ExplanationCn,
						})
						tc.Errors = append(tc.Errors, session.ErrorItem{
							Type:      errType,
							Original:  correction.Original[start:end],
							Corrected: h.Suggestion,
						})
					}
					if len(correction.Highlights) == 0 && correction.Corrected != correction.Original {
						frontendErrors = append(frontendErrors, ErrorItem{
							Type:      correction.ErrorType,
							Original:  correction.Original,
							Corrected: correction.Corrected,
						})
						tc.Errors = append(tc.Errors, session.ErrorItem{
							Type:      correction.ErrorType,
							Original:  correction.Original,
							Corrected: correction.Corrected,
						})
					}

					turnCorrection = tc

					client.SendJSON(WSMessage{
						Type: MsgCorrection,
						Data: CorrectionData{
							OriginalText:  correction.Original,
							CorrectedText: correction.Corrected,
							Errors:        frontendErrors,
						},
					})
				}

			case <-result.Done:
				elapsed := time.Since(startTime)
				log.Printf("[Stream] Done: session=%s text=\"%s\" reply=\"%s\" %v",
					sessionID, text, fullReply, elapsed)

				client.SendJSON(WSMessage{
					Type: MsgReplyEnd,
					Data: map[string]interface{}{
						"interrupted": false,
						"elapsed_ms":  elapsed.Milliseconds(),
					},
				})

				turn := session.ConversationTurn{
					UserText:      text,
					AssistantText: fullReply,
					Pronunciation: 0,
					Fluency:       0,
				}
				if turnCorrection != nil {
					turn.Correction = turnCorrection
				}
				mgr.AddTurn(turn)
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

// ============================================
// Session Report Generation
// ============================================

// Error type → Chinese label mapping
var errorLabelMap = map[string]string{
	"grammar":     "语法错误",
	"tense":       "时态错误",
	"preposition": "介词错误",
	"article":     "冠词遗漏/误用",
	"vocabulary":  "词汇使用",
	"word_choice": "用词选择",
	"expression":  "表达问题",
}

// Suggestion templates for top error types
var suggestionTemplates = map[string]string{
	"article":     "注意冠词（a/an/the）的使用，尤其是定冠词和不定冠词的区分",
	"tense":       "加强时态表达练习，注意过去时和完成时的正确使用",
	"preposition": "多练习介词的搭配，注意 in/on/at 等介词的准确用法",
	"grammar":     "巩固基础语法知识，注意句子结构的完整性",
	"vocabulary":  "扩充词汇量，尝试使用更丰富的表达方式",
	"word_choice": "注意用词准确性，选择更地道的英语表达",
	"expression":  "提高英语表达连贯性，多练习地道口语表达",
}

// calcScoreFromErrors calculates a score (0-100) reducing for each error found.
func calcScoreFromErrors(errorCount int, baseScore int, penalty int) int {
	score := baseScore - errorCount*penalty
	if score < 0 {
		return 0
	}
	return score
}

// generateReport builds a SessionReportData from session history
func generateReport(mgr *session.Manager, sessionID string) SessionReportData {
	history := mgr.GetHistory()

	// Calculate duration
	var durationSec int
	SessionManager.mu.RLock()
	if start, ok := SessionManager.sessionStart[sessionID]; ok {
		durationSec = int(time.Since(start).Seconds())
	}
	SessionManager.mu.RUnlock()

	// Aggregate data from all turns
	totalPron := 0
	totalFlu := 0
	pronCount := 0
	fluCount := 0
	errorCounts := make(map[string]int)

	for _, turn := range history {
		if turn.Pronunciation > 0 {
			totalPron += turn.Pronunciation
			pronCount++
		}
		if turn.Fluency > 0 {
			totalFlu += turn.Fluency
			fluCount++
		}
		if turn.Correction != nil {
			for _, err := range turn.Correction.Errors {
				errorCounts[err.Type]++
			}
			if len(turn.Correction.Errors) == 0 && turn.Correction.ErrorType != "" {
				errorCounts[turn.Correction.ErrorType]++
			}
		}
	}

	// Calculate average pronunciation/fluency
	avgPron := 75
	avgFlu := 75
	if pronCount > 0 {
		avgPron = totalPron / pronCount
	}
	if fluCount > 0 {
		avgFlu = totalFlu / fluCount
	}

	// Calculate Grammar score
	grammarErrors := errorCounts["grammar"] + errorCounts["tense"] +
		errorCounts["preposition"] + errorCounts["article"]
	grammarScore := calcScoreFromErrors(grammarErrors, 100, 10)

	// Calculate Vocabulary score
	vocabErrors := errorCounts["vocabulary"] + errorCounts["word_choice"] + errorCounts["expression"]
	vocabScore := calcScoreFromErrors(vocabErrors, 100, 10)

	// Sort error types by count
	types := make([]string, 0, len(errorCounts))
	for etype := range errorCounts {
		types = append(types, etype)
	}
	sort.Slice(types, func(i, j int) bool {
		return errorCounts[types[i]] > errorCounts[types[j]]
	})

	errorStats := make([]ErrorStat, 0, len(types))
	for _, etype := range types {
		count := errorCounts[etype]
		if count == 0 {
			continue
		}
		label := errorLabelMap[etype]
		if label == "" {
			label = etype
		}
		errorStats = append(errorStats, ErrorStat{
			Type:  etype,
			Label: label,
			Count: count,
		})
	}

	// Generate suggestions from top-3 error types
	suggestions := make([]string, 0, 3)
	for i, etype := range types {
		if i >= 3 {
			break
		}
		if tmpl, ok := suggestionTemplates[etype]; ok {
			suggestions = append(suggestions, tmpl)
		}
	}
	if len(suggestions) == 0 {
		suggestions = append(suggestions, "继续保持练习，尝试更多不同场景的对话")
		suggestions = append(suggestions, "可以挑战更复杂的表达，提高语言丰富度")
	}

	report := SessionReportData{
		Scene:         mgr.Scene,
		DurationSec:   durationSec,
		Turns:         len(history),
		Grammar:       grammarScore,
		Vocabulary:    vocabScore,
		Pronunciation: avgPron,
		Fluency:       avgFlu,
		ErrorStats:    errorStats,
		Suggestions:   suggestions,
	}

	sceneNames := map[string]string{
		"ordering":  "餐厅点餐",
		"interview": "工作面试",
		"meeting":   "商务会议",
		"travel":    "旅行出行",
	}
	if name, ok := sceneNames[report.Scene]; ok {
		report.Scene = fmt.Sprintf("%s (%s)", name, mgr.Scene)
	}

	// Build per-turn trend data for frontend charts
	report.TurnTrends = make([]TurnTrend, 0, len(history))
	for i, turn := range history {
		grammarErr := 0
		vocabErr := 0
		errCount := 0
		if turn.Correction != nil {
			errCount = len(turn.Correction.Errors)
			for _, e := range turn.Correction.Errors {
				switch e.Type {
				case "grammar", "tense", "preposition", "article":
					grammarErr++
				case "vocabulary", "word_choice", "expression":
					vocabErr++
				default:
					grammarErr++
				}
			}
			if errCount == 0 && turn.Correction.ErrorType != "" {
				errCount = 1
				grammarErr = 1
			}
		}
		report.TurnTrends = append(report.TurnTrends, TurnTrend{
			TurnIndex:      i,
			ErrorCount:     errCount,
			ResponseTimeMs: turn.ResponseTimeMs,
			GrammarErrors:  grammarErr,
			VocabErrors:    vocabErr,
		})
	}
	if name, ok := sceneNames[report.Scene]; ok {
		report.Scene = fmt.Sprintf("%s (%s)", name, mgr.Scene)
	}

	// Build per-turn trend data for frontend charts
	report.TurnTrends = make([]TurnTrend, 0, len(history))
	for i, turn := range history {
		grammarErr := 0
		vocabErr := 0
		errCount := 0
		if turn.Correction != nil {
			errCount = len(turn.Correction.Errors)
			for _, e := range turn.Correction.Errors {
				switch e.Type {
				case "grammar", "tense", "preposition", "article":
					grammarErr++
				case "vocabulary", "word_choice", "expression":
					vocabErr++
				default:
					grammarErr++
				}
			}
			if errCount == 0 && turn.Correction.ErrorType != "" {
				errCount = 1
				grammarErr = 1
			}
		}
		report.TurnTrends = append(report.TurnTrends, TurnTrend{
			TurnIndex:      i,
			ErrorCount:     errCount,
			ResponseTimeMs: turn.ResponseTimeMs,
			GrammarErrors:  grammarErr,
			VocabErrors:    vocabErr,
		})
	}

	return report
}

// HandleSessionEnd generates a report and sends it before removing the session
func HandleSessionEnd(sessionID string, client *Client) {
	mgr := GetOrCreateSession(sessionID, client.SessionScene())
	log.Printf("[Stream] Session ending: session=%s turns=%d", sessionID, len(mgr.GetHistory()))

	// Generate and send report before cleanup
	report := generateReport(mgr, sessionID)
	client.SendJSON(WSMessage{
		Type: MsgSessionReport,
		Data: report,
	})
	log.Printf("[Stream] Report sent: session=%s score(g=%d v=%d p=%d f=%d) errors=%d suggestions=%d",
		sessionID, report.Grammar, report.Vocabulary, report.Pronunciation, report.Fluency,
		len(report.ErrorStats), len(report.Suggestions))

	RemoveSession(sessionID)
}
