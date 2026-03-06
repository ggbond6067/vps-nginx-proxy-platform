#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

run_check() {
  bash "$PROJECT_DIR/scripts/check_env.sh"
}

run_up() {
  docker compose -f "$PROJECT_DIR/docker-compose.yml" --env-file "$PROJECT_DIR/.env" up -d --build
  echo "平台已启动。"
  echo "管理面板（本机）: http://127.0.0.1:18080"
}

run_down() {
  docker compose -f "$PROJECT_DIR/docker-compose.yml" --env-file "$PROJECT_DIR/.env" down
  echo "平台已停止。"
}

open_hint() {
  echo "管理面板地址: http://127.0.0.1:18080"
  echo "远程 VPS 建议使用 SSH 隧道访问："
  echo "ssh -L 18080:127.0.0.1:18080 <user>@<vps-ip>"
}

view_routes() {
  if [ -f "$PROJECT_DIR/panel/config/routes.json" ]; then
    cat "$PROJECT_DIR/panel/config/routes.json"
  else
    echo "路由配置文件不存在。"
  fi
}

echo "==============================================="
echo "VPS 反代平台操作菜单（中文）"
echo "==============================================="

while true; do
  cat <<EOF
1. 环境检测
2. 启动/更新反代平台
3. 停止反代平台
4. 创建业务服务模板
5. 手动绑定 Cloudflare DNS
6. 手动申请 HTTPS 证书
7. 查看当前路由配置
8. 查看面板访问提示
0. 退出
EOF
  read -r -p "请选择 [0-8]: " choice
  case "$choice" in
    1)
      run_check
      ;;
    2)
      run_up
      ;;
    3)
      run_down
      ;;
    4)
      bash "$PROJECT_DIR/scripts/create_service_compose.sh"
      ;;
    5)
      read -r -p "请输入域名（例如 api.example.com）: " domain
      python3 "$PROJECT_DIR/scripts/upsert_cf_dns.py" --domain "$domain"
      ;;
    6)
      bash "$PROJECT_DIR/scripts/issue_tls_cert.sh"
      ;;
    7)
      view_routes
      ;;
    8)
      open_hint
      ;;
    0)
      echo "已退出。"
      exit 0
      ;;
    *)
      echo "无效选项，请重试。"
      ;;
  esac
done
