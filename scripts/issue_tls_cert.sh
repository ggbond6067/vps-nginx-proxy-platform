#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "失败: .env 不存在，请先 cp .env.example .env"
  exit 1
fi

source "$ENV_FILE"

CERT_CONTAINER="${CERT_AGENT_CONTAINER_NAME:-cert-agent}"
NGINX_CONTAINER="${NGINX_CONTAINER_NAME:-nginx-gateway}"

read -r -p "请输入要申请证书的域名（例如 api.example.com）: " DOMAIN
DOMAIN="$(echo "$DOMAIN" | tr '[:upper:]' '[:lower:]' | xargs)"
if [ -z "$DOMAIN" ]; then
  echo "失败: 域名不能为空"
  exit 1
fi

echo "开始申请证书: $DOMAIN"
docker exec "$CERT_CONTAINER" sh -lc "acme.sh --set-default-ca --server letsencrypt"
docker exec "$CERT_CONTAINER" sh -lc "acme.sh --issue --dns dns_cf -d '$DOMAIN' --keylength 2048"
docker exec "$CERT_CONTAINER" sh -lc "mkdir -p /certs/'$DOMAIN'"
docker exec "$CERT_CONTAINER" sh -lc "acme.sh --install-cert -d '$DOMAIN' --key-file /certs/'$DOMAIN'/privkey.pem --fullchain-file /certs/'$DOMAIN'/fullchain.pem"

echo "证书申请完成，开始重载 Nginx..."
docker exec "$NGINX_CONTAINER" nginx -s reload

echo "完成: $DOMAIN 已申请 HTTPS 证书并重载 Nginx"
