#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

CERT_CONTAINER_NAME="cert-agent"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  CERT_CONTAINER_NAME="${CERT_AGENT_CONTAINER_NAME:-cert-agent}"
fi

ok() {
  echo "[通过] $1"
}

fail() {
  echo "[失败] $1"
}

echo "==============================================="
echo "环境检测（VPS 反代平台）"
echo "==============================================="

if command -v docker >/dev/null 2>&1; then
  ok "Docker 已安装: $(docker --version)"
else
  fail "Docker 未安装"
fi

if docker compose version >/dev/null 2>&1; then
  ok "Docker Compose 插件可用"
else
  fail "Docker Compose 插件不可用"
fi

if [ -S /var/run/docker.sock ]; then
  ok "Docker Socket 可访问: /var/run/docker.sock"
else
  fail "Docker Socket 不可访问: /var/run/docker.sock"
fi

if [ -f "$ENV_FILE" ]; then
  ok ".env 文件存在: $ENV_FILE"
else
  fail ".env 文件不存在，请先 cp .env.example .env"
fi

if [ -d "$PROJECT_DIR/nginx/conf.d" ]; then
  ok "Nginx 动态配置目录存在"
else
  fail "Nginx 动态配置目录缺失: $PROJECT_DIR/nginx/conf.d"
fi

if [ -d "$PROJECT_DIR/certs" ]; then
  ok "证书目录存在: $PROJECT_DIR/certs"
else
  fail "证书目录缺失: $PROJECT_DIR/certs（首次启动后会自动创建）"
fi

if [ -f "$PROJECT_DIR/panel/config/routes.json" ]; then
  ok "路由配置文件存在"
else
  fail "路由配置文件不存在: $PROJECT_DIR/panel/config/routes.json"
fi

if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "${CERT_CONTAINER_NAME}"; then
  ok "证书容器运行中: ${CERT_CONTAINER_NAME}"
else
  fail "证书容器未运行: ${CERT_CONTAINER_NAME}（启动平台后会自动创建）"
fi

echo "==============================================="
echo "检测完成"
echo "==============================================="
