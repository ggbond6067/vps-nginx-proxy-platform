# HTTPS 证书与 Cloudflare 配置

## 一、先澄清术语

1. `SSH 证书`  
用于服务器登录认证（`ssh user@host`），不是浏览器 HTTPS 访问证书。

2. `TLS/SSL 证书`  
用于浏览器访问域名时的 HTTPS 加密，这才是反代平台需要自动申请的证书。

本项目自动申请的是第 2 类（HTTPS 证书）。

## 二、推荐流程（成熟方案）

1. 在 Cloudflare 解析域名到 VPS（A 记录，建议开启代理）
2. 在面板创建路由：`域名 -> 服务名:端口`
3. 在面板点击“申请证书”（acme.sh + Cloudflare DNS API）
4. 面板自动重载 Nginx，`443` 生效
5. 在 Cloudflare 设置 `SSL/TLS` 模式为 `Full (strict)`

## 三、Cloudflare 侧需要什么配置

1. API Token  
需要至少包含该 Zone 的 DNS 编辑权限（`Zone.DNS:Edit`）。

2. Zone ID  
主域的 Zone ID（不是账号 ID）。

3. SSL/TLS 模式  
请设置为 `Full (strict)`，不要用 `Flexible`。

## 四、项目内相关配置

在 `.env` 中确保这些值正确：

1. `CF_API_TOKEN`
2. `CF_ZONE_ID`
3. `CERT_AGENT_CONTAINER_NAME`（默认 `cert-agent`）

## 五、命令行申请证书（备选）

除了页面按钮，还可以用中文脚本：

```bash
bash scripts/issue_tls_cert.sh
```

## 六、常见问题

1. 证书申请失败（DNS 验证失败）
- 检查 Token 权限
- 检查 Zone ID
- 检查域名是否确实托管在该 Zone

2. 证书文件已生成但 HTTPS 不生效
- 在面板点击“手动重载 Nginx”
- 检查 `certs/<域名>/fullchain.pem` 与 `privkey.pem` 是否存在

3. Cloudflare 正常但访问仍有证书报错
- 确认 Cloudflare SSL 模式不是 `Flexible`
- 确认源站域名路由与证书域名一致
