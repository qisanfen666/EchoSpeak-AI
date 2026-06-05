package redis_client

import (
	"context"
	"encoding/json"
	"log"
	"time"

	"github.com/redis/go-redis/v9"
)

var rdb *redis.Client

// Init 初始化 Redis 连接
func Init(addr string) {
	rdb = redis.NewClient(&redis.Options{
		Addr:         addr,
		Password:     "",
		DB:           0,
		DialTimeout:  5 * time.Second,
		ReadTimeout:  3 * time.Second,
		WriteTimeout: 3 * time.Second,
	})

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	if err := rdb.Ping(ctx).Err(); err != nil {
		log.Printf("[Redis] WARNING: Cannot connect to Redis at %s: %v", addr, err)
		log.Printf("[Redis] Continuing without Redis — some features will be unavailable")
		return
	}
	log.Printf("[Redis] Connected: %s", addr)
}

// ============================================
// 会话状态管理
// ============================================

const (
	sessionKeyPrefix    = "session:"
	sessionTTL          = 30 * time.Minute
	asrResultKeyPrefix  = "asr:"
	historyKeyPrefix    = "history:"
)

// SetSessionField 保存会话属性
func SetSessionField(sessionID, field string, value interface{}) error {
	if rdb == nil {
		return nil
	}
	ctx := context.Background()
	key := sessionKeyPrefix + sessionID
	return rdb.HSet(ctx, key, field, value).Err()
}

// GetSessionField 获取会话属性
func GetSessionField(sessionID, field string) (string, error) {
	if rdb == nil {
		return "", redis.Nil
	}
	ctx := context.Background()
	key := sessionKeyPrefix + sessionID
	return rdb.HGet(ctx, key, field).Result()
}

// SetLatestASR 保存最新的 ASR 识别结果
func SetLatestASR(sessionID, text string, isFinal bool) error {
	if rdb == nil {
		return nil
	}
	ctx := context.Background()
	key := asrResultKeyPrefix + sessionID
	data := map[string]interface{}{
		"text":     text,
		"is_final": isFinal,
		"time":     time.Now().Unix(),
	}
	jsonData, _ := json.Marshal(data)
	return rdb.Set(ctx, key, jsonData, sessionTTL).Err()
}

// GetLatestASR 获取最新的 ASR 结果
func GetLatestASR(sessionID string) (text string, isFinal bool) {
	if rdb == nil {
		return "", false
	}
	ctx := context.Background()
	key := asrResultKeyPrefix + sessionID
	result, err := rdb.Get(ctx, key).Result()
	if err != nil {
		return "", false
	}
	var data map[string]interface{}
	if err := json.Unmarshal([]byte(result), &data); err != nil {
		return "", false
	}
	text, _ = data["text"].(string)
	isFinal, _ = data["is_final"].(bool)
	return
}

// AppendHistory 追加对话历史
func AppendHistory(sessionID string, turn map[string]interface{}) error {
	if rdb == nil {
		return nil
	}
	ctx := context.Background()
	key := historyKeyPrefix + sessionID
	jsonData, _ := json.Marshal(turn)
	err := rdb.RPush(ctx, key, jsonData).Err()
	if err != nil {
		return err
	}
	return rdb.Expire(ctx, key, sessionTTL).Err()
}

// GetHistory 获取完整对话历史
func GetHistory(sessionID string) ([]map[string]interface{}, error) {
	if rdb == nil {
		return nil, nil
	}
	ctx := context.Background()
	key := historyKeyPrefix + sessionID
	results, err := rdb.LRange(ctx, key, 0, -1).Result()
	if err != nil {
		return nil, err
	}
	history := make([]map[string]interface{}, 0, len(results))
	for _, r := range results {
		var turn map[string]interface{}
		if err := json.Unmarshal([]byte(r), &turn); err != nil {
			continue
		}
		history = append(history, turn)
	}
	return history, nil
}

// DeleteSession 清理会话数据
func DeleteSession(sessionID string) {
	if rdb == nil {
		return
	}
	ctx := context.Background()
	keys := []string{
		sessionKeyPrefix + sessionID,
		asrResultKeyPrefix + sessionID,
		historyKeyPrefix + sessionID,
	}
	rdb.Del(ctx, keys...)
}
