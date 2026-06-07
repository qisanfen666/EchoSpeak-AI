package grpc_client

import (
	"context"
	"io"
	"log"
	"sync"

	"go-gateway/proto"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

type AIClient struct {
	addr   string
	conn   *grpc.ClientConn
	client proto.AIServiceClient
	mu     sync.Mutex
}

var defaultClient *AIClient

func Init(addr string) error {
	conn, err := grpc.Dial(addr,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithDefaultCallOptions(
			grpc.MaxCallRecvMsgSize(10*1024*1024),
			grpc.MaxCallSendMsgSize(10*1024*1024),
		),
	)
	if err != nil {
		return err
	}

	defaultClient = &AIClient{
		addr:   addr,
		conn:   conn,
		client: proto.NewAIServiceClient(conn),
	}

	log.Printf("[gRPC] Connected to AI engine at %s", addr)
	return nil
}

func Close() {
	if defaultClient != nil && defaultClient.conn != nil {
		defaultClient.conn.Close()
	}
}

// ChatResult holds the streaming chat response
type ChatResult struct {
	ReplyChunks chan string
	Correction  chan *proto.Correction
	AudioChunks chan []byte // TTS MP3 chunks
	Translation chan string // Chinese translation of the reply
	Done        chan struct{}
	Err         chan error
}

// StreamASR sends audio to Python ASR and returns recognized text + scores
func StreamASR(ctx context.Context, sessionID string, audioData []byte) (string, int32, int32, error) {
	stream, err := defaultClient.client.StreamASR(ctx)
	if err != nil {
		return "", 0, 0, err
	}

	err = stream.Send(&proto.AudioChunk{
		AudioData: audioData,
		SessionId: sessionID,
		IsEnd:     true,
	})
	if err != nil {
		return "", 0, 0, err
	}
	stream.CloseSend()

	resp, err := stream.Recv()
	if err != nil {
		return "", 0, 0, err
	}
	return resp.Text, resp.Pronunciation, resp.Fluency, nil
}

// ChatStream calls Python Chat gRPC (server streaming)
func ChatStream(ctx context.Context, sessionID, scene, userMessage string, history []*proto.ChatMessage) *ChatResult {
	result := &ChatResult{
		ReplyChunks: make(chan string, 50),
		Correction:  make(chan *proto.Correction, 1),
		AudioChunks: make(chan []byte, 20),
		Translation: make(chan string, 1),
		Done:        make(chan struct{}, 1),
		Err:         make(chan error, 1),
	}

	go func() {
		defer func() {
			close(result.ReplyChunks)
			close(result.Correction)
			close(result.AudioChunks)
			close(result.Translation)
			close(result.Done)
			close(result.Err)
		}()

		req := &proto.ChatRequest{
			SessionId:   sessionID,
			Scene:       scene,
			UserMessage: userMessage,
			History:     history,
		}

		stream, err := defaultClient.client.Chat(ctx, req)
		if err != nil {
			result.Err <- err
			return
		}

		for {
			resp, err := stream.Recv()
			if err == io.EOF {
				result.Done <- struct{}{}
				return
			}
			if err != nil {
				result.Err <- err
				return
			}

			switch payload := resp.Payload.(type) {
			case *proto.ChatResponse_Reply:
				result.ReplyChunks <- payload.Reply.Text
			case *proto.ChatResponse_Correction:
				result.Correction <- payload.Correction
			case *proto.ChatResponse_TtsAudio:
				result.AudioChunks <- payload.TtsAudio
			case *proto.ChatResponse_Translation:
				result.Translation <- payload.Translation
			case *proto.ChatResponse_Done:
				result.Done <- struct{}{}
				return
			}
		}
	}()

	return result
}

// Health checks Python engine availability
func Health(ctx context.Context) (bool, string) {
	resp, err := defaultClient.client.Health(ctx, &proto.HealthRequest{})
	if err != nil {
		return false, err.Error()
	}
	return resp.Ok, resp.Message
}
