package ws

// SessionScene 返回客户端的场景（供 stream 包使用）
func (c *Client) SessionScene() string {
	if c.session != nil {
		return c.session.Scene
	}
	return "ordering"
}
