#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==============================================="
echo "Ubuntu VPS 一键初始化脚本"
echo "==============================================="
echo "本脚本会执行："
echo "1) 安装 Docker 与 Compose 插件"
echo "2) 初始化项目 .env"
echo "3) 启动反代平台容器"
echo "==============================================="

if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    echo "检测到非 root 用户，将通过 sudo 执行安装步骤。"
    SUDO="sudo"
  else
    echo "失败：当前不是 root 且系统没有 sudo。"
    exit 1
  fi
else
  SUDO=""
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "[步骤] 安装 Docker..."
  $SUDO apt-get update -y
  $SUDO apt-get install -y ca-certificates curl gnupg
  curl -fsSL https://get.docker.com | $SUDO sh
else
  echo "[步骤] Docker 已安装，跳过安装。"
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "[步骤] 安装 Docker Compose 插件..."
  $SUDO apt-get update -y
  $SUDO apt-get install -y docker-compose-plugin
else
  echo "[步骤] Docker Compose 插件已可用，跳过安装。"
fi

CURRENT_USER="${SUDO_USER:-$(whoami)}"
if [ "$CURRENT_USER" = "root" ]; then
  echo "[步骤] 当前用户为 root，无需加入 docker 组。"
elif id -nG "$CURRENT_USER" | grep -qw docker; then
  echo "[步骤] 用户 $CURRENT_USER 已在 docker 组，跳过。"
else
  echo "[步骤] 将用户 $CURRENT_USER 加入 docker 组..."
  $SUDO usermod -aG docker "$CURRENT_USER"
  echo "提示：请重新登录一次 SSH 会话，使 docker 组生效。"
fi

if [ ! -f "$PROJECT_DIR/.env" ]; then
  cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
  echo "[步骤] 已生成 .env，请先按实际信息修改后再继续生产使用。"
else
  echo "[步骤] .env 已存在，跳过生成。"
fi

mkdir -p "$PROJECT_DIR/certs" "$PROJECT_DIR/acme"
echo "[步骤] 已初始化证书目录与 acme 数据目录。"

echo "[步骤] 启动容器..."
docker compose -f "$PROJECT_DIR/docker-compose.yml" --env-file "$PROJECT_DIR/.env" up -d --build

echo "==============================================="
echo "初始化完成。"
echo "管理面板默认地址: http://127.0.0.1:18080"
echo "建议通过 SSH 隧道访问面板。"
echo "==============================================="
