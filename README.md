# VPS Nginx 反代平台（Docker + Cloudflare）

一个面向全新 Ubuntu VPS 的反代部署项目，目标是：

- 支持部署多个 Docker 业务容器，即使内部端口都为 `8000` 也不冲突
- 支持将 Cloudflare 子域名绑定到对应容器服务
- 支持基于 Cloudflare DNS API 自动申请 HTTPS 证书（Let's Encrypt）
- 提供中文 Web 管理页面和中文脚本菜单，降低操作复杂度

## 快速开始

1. 进入项目目录：

```bash
cd vps-nginx-proxy-platform
```

2. 复制环境变量模板并修改：

```bash
cp .env.example .env
```

3. 首次部署（全新 Ubuntu 推荐）：

```bash
bash scripts/bootstrap_ubuntu.sh
```

4. 启动平台：

```bash
docker compose up -d --build
```

5. 访问管理面板（默认只监听本机回环）：

- `http://127.0.0.1:18080`
- 远程访问建议通过 SSH 隧道：`ssh -L 18080:127.0.0.1:18080 user@vps`

## 项目结构

```text
vps-nginx-proxy-platform/
  docker-compose.yml
  .env.example
  .env
  nginx/
    nginx.conf
    conf.d/
  certs/
  acme/
  panel/
    app.py
    requirements.txt
    Dockerfile
    templates/index.html
    static/style.css
    config/routes.json
  scripts/
    bootstrap_ubuntu.sh
    check_env.sh
    menu.sh
    create_service_compose.sh
    upsert_cf_dns.py
    issue_tls_cert.sh
  docs/
    01-架构与能力.md
    02-Ubuntu全新部署.md
    03-服务接入与域名绑定.md
    04-HTTPS证书与Cloudflare配置.md
```

## 核心能力

- **反代网关**：`nginx-gateway`（对外暴露 `80/443`）
- **管理面板**：`proxy-panel`（中文 UI，支持路由增删、检测、DNS 绑定）
- **证书代理**：`cert-agent`（acme.sh，基于 Cloudflare DNS 自动申请证书）
- **动态路由**：基于 `panel/config/routes.json` 自动生成 `nginx/conf.d/*.conf`
- **自动重载**：路由变更后自动执行 Nginx reload
- **SSO/Token 场景适配**：你可以继续在本地跑注册流程，VPS 只做反代与接口转发

## 文档入口

- 架构说明：[docs/01-架构与能力.md](docs/01-架构与能力.md)
- Ubuntu 从零部署：[docs/02-Ubuntu全新部署.md](docs/02-Ubuntu全新部署.md)
- 服务接入与域名绑定：[docs/03-服务接入与域名绑定.md](docs/03-服务接入与域名绑定.md)
- HTTPS 证书与 Cloudflare：[docs/04-HTTPS证书与Cloudflare配置.md](docs/04-HTTPS证书与Cloudflare配置.md)
