package config

import (
	"os"
)

// Config 应用配置 — 3天限时赛，保持简单，用环境变量
type Config struct {
	ListenAddr     string // Go 网关监听地址
	PythonGRPCAddr string // Python gRPC 服务地址
	RedisAddr      string // Redis 地址
}

func Load() *Config {
	return &Config{
		ListenAddr:     getEnv("LISTEN_ADDR", ":8080"),
		PythonGRPCAddr: getEnv("PYTHON_GRPC_ADDR", "localhost:50051"),
		RedisAddr:      getEnv("REDIS_ADDR", "localhost:6379"),
	}
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
