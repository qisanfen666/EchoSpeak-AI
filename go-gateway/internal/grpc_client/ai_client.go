package grpc_client

import (
	"context"
	"log"
	"sync"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

// AIClient 封装对 Python AI 引擎的 gRPC 调用
// 注意：3天限时赛，先实现核心功能，不做连接池/重试/负载均衡
type AIClient struct {
	addr   string
	conn   *grpc.ClientConn
	mu     sync.Mutex
}

var defaultClient *AIClient

// Init 初始化 gRPC 客户端
func Init(addr string) error {
	conn, err := grpc.Dial(addr,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithDefaultCallOptions(grpc.MaxCallRecvMsgSize(10*1024*1024)), // 10MB for audio
		grpc.WithDefaultCallOptions(grpc.MaxCallSendMsgSize(10*1024*1024)),
	)
	if err != nil {
		return err
	}

	defaultClient = &AIClient{
		addr: addr,
		conn: conn,
	}

	log.Printf("[gRPC] Connected to Python engine at %s", addr)
	return nil
}

// GetConn 获取连接（后续 proto 生成后使用）
func GetConn() *grpc.ClientConn {
	if defaultClient == nil {
		log.Fatal("[gRPC] Not initialized")
	}
	return defaultClient.conn
}

// Close 关闭连接
func Close() {
	if defaultClient != nil && defaultClient.conn != nil {
		defaultClient.conn.Close()
	}
}

// ============================================
// 以下为占位接口，Day 1 生成 proto 后替换为实际调用
// ============================================

// StreamASR 流式 ASR（占位）
func StreamASR(ctx context.Context, sessionID string, audioChunks <-chan []byte) (<-chan string, error) {
	// TODO Day 1: 实现 client streaming gRPC 调用
	resultCh := make(chan string, 10)
	go func() {
		defer close(resultCh)
		for chunk := range audioChunks {
			select {
			case <-ctx.Done():
				return
			default:
				_ = chunk // 实际发送到 gRPC stream
				log.Printf("[gRPC:ASR] Sending chunk: session=%s size=%d", sessionID, len(chunk))
			}
		}
	}()
	return resultCh, nil
}

// ChatStream 流式 LLM 对话（占位）
func ChatStream(ctx context.Context, sessionID string, userMessage string, history []map[string]string) (<-chan string, <-chan interface{}, error) {
	// TODO Day 1: 实现 bidirectional streaming gRPC 调用
	replyCh := make(chan string, 20)
	correctionCh := make(chan interface{}, 1)

	go func() {
		defer close(replyCh)
		defer close(correctionCh)
		// 实际 gRPC stream 调用
		log.Printf("[gRPC:LLM] Chat: session=%s msg=%s", sessionID, userMessage)
	}()

	return replyCh, correctionCh, nil
}

// SynthesizeStream 流式 TTS（占位）
func SynthesizeStream(ctx context.Context, sessionID string, textChunks <-chan string) (<-chan []byte, error) {
	// TODO Day 1: 实现 server streaming gRPC 调用
	audioCh := make(chan []byte, 20)
	go func() {
		defer close(audioCh)
		for text := range textChunks {
			select {
			case <-ctx.Done():
				return
			default:
				_ = text
				log.Printf("[gRPC:TTS] Synthesizing chunk: session=%s", sessionID)
			}
		}
	}()
	return audioCh, nil
}

// EvaluatePronunciation 发音评测（占位，Day 2 异步使用）
func EvaluatePronunciation(ctx context.Context, sessionID string, audio []byte, referenceText string) (map[string]interface{}, error) {
	// TODO Day 2: 实现 unary gRPC 调用
	log.Printf("[gRPC:Eval] Evaluating: session=%s", sessionID)
	return map[string]interface{}{
		"overall_score": 0,
		"accuracy":      0,
		"fluency":       0,
	}, nil
}

// GenerateReport 课后报告（占位，Day 2）
func GenerateReport(ctx context.Context, sessionID string, history []map[string]string) (map[string]interface{}, error) {
	// TODO Day 2: 实现 unary gRPC 调用
	log.Printf("[gRPC:Report] Generating report: session=%s", sessionID)
	return map[string]interface{}{
		"overall_score": 0,
		"summary":       "# 课后报告\n\n会话总结（Demo）",
	}, nil
}
