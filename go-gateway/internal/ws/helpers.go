package ws

// SessionScene 返回客户端的场景（供 stream 包使用）
func (c *Client) SessionScene() string {
	if c.session != nil {
		return c.session.Scene
	}
	return "ordering"
}

// SessionDifficulty 返回客户端的难度
func (c *Client) SessionDifficulty() string {
	if c.session != nil && c.session.Difficulty != "" {
		return c.session.Difficulty
	}
	return "medium"
}
