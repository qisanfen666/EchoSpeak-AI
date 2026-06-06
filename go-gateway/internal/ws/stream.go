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
	mu          sync.RWMutex
	sessions    map[string]*session.Manager
	audioBuf    map[string][]byte // accumulated audio per session
	sessionStart map[string]time.Time // session start time
}{
	sessions:    make(map[string]*session.Manager),
	audioBuf:    make(map[string][]byte),
	sessionStart: make(map[string]time.Time),
}

func GetOrCreateSession(sessionID, scene string) *session.Manager {
	SessionManager.mu.Lock()
	defer SessionManager.mu.Unlock()

	if mgr, ok := SessionManager.sessions[sessionID]; ok {
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
		var turnCorrection *session.TurnCorrection

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
					tc := &session.TurnCorrection{
						Original:  correction.Original,
						Corrected: correction.Corrected,
						ErrorType: correction.ErrorType,
					}
					for _, h := range correction.Highlights {
						tc.Errors = append(tc.Errors, session.ErrorItem{
							Type:      correction.ErrorType,
							Original:  h.Suggestion,
							Corrected: h.Suggestion,
						})
					}
					if len(correction.Highlights) == 0 && correction.ErrorType != "" {
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
							Original:   correction.Original,
							Corrected:  correction.Corrected,
							ErrorType:  correction.ErrorType,
							Highlights: convertWordFixes(correction.Highlights),
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
					UserText:      userText,
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

func HandleInterrupt(sessionID string) {
	mgr := GetOrCreateSession(sessionID, "")
	mgr.CancelCurrentTurn()
	// Clear buffered audio on interrupt
	SessionManager.mu.Lock()
	SessionManager.audioBuf[sessionID] = nil
	SessionManager.mu.Unlock()
	log.Printf("[Stream] Interrupt: session=%s", sessionID)
}

// convertWordFixes converts proto WordFix to our WordFix type
func convertWordFixes(pbFixes []*proto.WordFix) []WordFix {
	if len(pbFixes) == 0 {
		return nil
	}
	out := make([]WordFix, len(pbFixes))
	for i, f := range pbFixes {
		out[i] = WordFix{
			StartIdx:   int(f.StartIdx),
			EndIdx:     int(f.EndIdx),
			Suggestion: f.Suggestion,
		}
	}
	return out
}

// ============================================
// Session Report Generation
// ============================================

// Error type → Chinese label mapping
var errorLabelMap = map[string]string{
	"grammar":      "语法错误",
	"tense":        "时态错误",
	"preposition":  "介词错误",
	"article":      "冠词遗漏/误用",
	"vocabulary":   "词汇使用",
	"word_choice":  "用词选择",
	"expression":   "表达问题",
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
// baseScore: starting score, penalty: points deducted per error
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
	errorCounts := make(map[string]int) // error type → count

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
			// Fallback: if no detailed errors, count the ErrorType
			if len(turn.Correction.Errors) == 0 && turn.Correction.ErrorType != "" {
				errorCounts[turn.Correction.ErrorType]++
			}
		}
	}

	// Calculate average pronunciation/fluency
	avgPron := 75 // default
	avgFlu := 75
	if pronCount > 0 {
		avgPron = totalPron / pronCount
	}
	if fluCount > 0 {
		avgFlu = totalFlu / fluCount
	}

	// Calculate Grammar score (grammar + tense + preposition + article errors)
	grammarErrors := errorCounts["grammar"] + errorCounts["tense"] +
		errorCounts["preposition"] + errorCounts["article"]
	grammarScore := calcScoreFromErrors(grammarErrors, 100, 10)

	// Calculate Vocabulary score (vocabulary + word_choice + expression errors)
	vocabErrors := errorCounts["vocabulary"] + errorCounts["word_choice"] + errorCounts["expression"]
	vocabScore := calcScoreFromErrors(vocabErrors, 100, 10)

	// Build error stats, sorted by count descending
	type kv struct{ k, v int }
	var sorted []kv
	for etype, count := range errorCounts {
		sorted = append(sorted, kv{v: count})
		// store type in a parallel way — we'll rebuild
	}
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
	// If no errors found, give general encouragement
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
