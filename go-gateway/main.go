package main

import (
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"

	"go-gateway/config"
	"go-gateway/internal/grpc_client"
	"go-gateway/internal/redis_client"
	"go-gateway/internal/ws"
)

func main() {
	cfg := config.Load()

	log.Println("============================================")
	log.Println("  EchoSpeak AI — Go Gateway")
	log.Println("  AI 英语口语陪练系统")
	log.Println("============================================")

	// 1. 初始化 Redis（可选，连接不上不会阻断启动）
	redis_client.Init(cfg.RedisAddr)

	// 2. 初始化 gRPC 客户端（连接到 Python AI 引擎）
	if err := grpc_client.Init(cfg.PythonGRPCAddr); err != nil {
		log.Printf("[Main] WARNING: Cannot connect to Python gRPC at %s: %v", cfg.PythonGRPCAddr, err)
		log.Println("[Main] Python engine is not available — AI features will be unavailable")
	}

	// 3. 启动 WebSocket Hub
	hub := ws.NewHub()
	go hub.Run()

	// 4. 注册路由
	mux := http.NewServeMux()
	mux.HandleFunc("/ws", func(w http.ResponseWriter, r *http.Request) {
		ws.ServeWS(hub, w, r)
	})
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"status":"ok","service":"go-gateway"}`))
	})

	// 5. 托管前端静态文件
	if cfg.FrontendDir != "" {
		fs := http.FileServer(http.Dir(cfg.FrontendDir))
		mux.Handle("/", fs)
		log.Printf("[Main] Serving frontend from: %s", cfg.FrontendDir)
	}

	// 6. 优雅关闭
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		log.Printf("[Main] Listening on %s", cfg.ListenAddr)
		log.Printf("[Main] WebSocket: ws://localhost%s/ws?session_id=xxx", cfg.ListenAddr)
		if err := http.ListenAndServe(cfg.ListenAddr, mux); err != nil && err != http.ErrServerClosed {
			log.Fatalf("[Main] Server error: %v", err)
		}
	}()

	<-sigCh
	log.Println("[Main] Shutting down...")
	grpc_client.Close()
}
