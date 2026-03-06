# Ubuntu 全新部署指南（从 0 开始）

## 一、前置条件

1. 一台新的 Ubuntu VPS（推荐 22.04+）
2. 已有 Cloudflare 域名（可选，但建议）
3. 具备 SSH 登录权限

## 二、上传项目

将目录 `vps-nginx-proxy-platform` 上传到 VPS，例如：

```bash
scp -r ./vps-nginx-proxy-platform user@vps:/opt/
```

登录 VPS 后进入项目目录：

```bash
cd /opt/vps-nginx-proxy-platform
```

## 三、初始化环境

执行一键脚本：

```bash
bash scripts/bootstrap_ubuntu.sh
```

脚本会自动安装 Docker 和 Compose，并启动容器。

## 四、配置环境变量

首次运行后，修改 `.env`：

```bash
cp .env.example .env
```

重点字段：

1. `VPS_PUBLIC_IP`：你的 VPS 公网 IP
2. `CF_API_TOKEN`：Cloudflare API Token（DNS 编辑权限）
3. `CF_ZONE_ID`：主域 Zone ID
4. `PANEL_USER`、`PANEL_PASSWORD`：面板认证账号密码
5. `CERT_AGENT_CONTAINER_NAME`：证书容器名称（默认 `cert-agent`）

修改完成后重启容器：

```bash
docker compose --env-file .env up -d --build
```

## 五、访问管理面板

管理面板默认只监听本机回环：

- `http://127.0.0.1:18080`

远程访问方式（推荐）：

```bash
ssh -L 18080:127.0.0.1:18080 user@vps
```

然后在本地浏览器打开：

- `http://127.0.0.1:18080`

## 六、基础运维命令

1. 查看容器状态：

```bash
docker compose ps
```

2. 查看网关日志：

```bash
docker logs -f nginx-gateway
```

3. 查看面板日志：

```bash
docker logs -f proxy-panel
```

4. 中文菜单操作：

```bash
bash scripts/menu.sh
```
