#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICES_DIR="$PROJECT_DIR/services"
mkdir -p "$SERVICES_DIR"

echo "==============================================="
echo "创建业务服务模板（支持容器内部端口重复）"
echo "==============================================="

read -r -p "请输入服务名（例如 new-api）: " SERVICE_NAME
SERVICE_NAME="$(echo "$SERVICE_NAME" | tr -d '[:space:]')"
if [ -z "$SERVICE_NAME" ]; then
  echo "服务名不能为空"
  exit 1
fi

read -r -p "请输入镜像名（例如 ghcr.io/demo/new-api:latest）: " IMAGE_NAME
IMAGE_NAME="$(echo "$IMAGE_NAME" | tr -d '[:space:]')"
if [ -z "$IMAGE_NAME" ]; then
  echo "镜像名不能为空"
  exit 1
fi

read -r -p "请输入容器内部端口（默认 8000）: " INTERNAL_PORT
INTERNAL_PORT="${INTERNAL_PORT:-8000}"

SERVICE_DIR="$SERVICES_DIR/$SERVICE_NAME"
mkdir -p "$SERVICE_DIR"
COMPOSE_FILE="$SERVICE_DIR/docker-compose.yml"

cat > "$COMPOSE_FILE" <<EOF
services:
  $SERVICE_NAME:
    image: $IMAGE_NAME
    container_name: $SERVICE_NAME
    restart: unless-stopped
    expose:
      - "$INTERNAL_PORT"
    networks:
      - proxy_net

networks:
  proxy_net:
    external: true
    name: proxy_net
EOF

echo "-----------------------------------------------"
echo "模板已生成: $COMPOSE_FILE"
echo "启动命令："
echo "cd $SERVICE_DIR && docker compose up -d"
echo "-----------------------------------------------"
echo "说明："
echo "1) 该服务不会占用 VPS 宿主机端口，因此与其他 8000 端口服务不冲突。"
echo "2) 创建完成后，请到反代面板绑定域名 -> $SERVICE_NAME:$INTERNAL_PORT。"
