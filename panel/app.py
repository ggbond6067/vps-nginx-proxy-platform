from __future__ import annotations

import base64
import json
import os
import re
import shlex
from pathlib import Path
from typing import Dict, List, Tuple

import requests
from flask import Flask, Response, flash, redirect, render_template, request, url_for

try:
    import docker
except Exception:  # pragma: no cover
    docker = None


app = Flask(__name__)
app.secret_key = os.getenv("PANEL_SECRET_KEY", "proxy-panel-secret")

ROUTES_FILE = Path(os.getenv("ROUTES_FILE", "/app/config/routes.json"))
NGINX_CONF_DIR = Path(os.getenv("NGINX_CONF_DIR", "/app/nginx/conf.d"))
NGINX_CONTAINER_NAME = os.getenv("NGINX_CONTAINER_NAME", "nginx-gateway")
CERT_AGENT_CONTAINER_NAME = os.getenv("CERT_AGENT_CONTAINER_NAME", "cert-agent")
CERTS_DIR = Path(os.getenv("CERTS_DIR", "/app/certs"))
NGINX_CERT_ROOT = "/etc/nginx/certs"

PANEL_USER = os.getenv("PANEL_USER", "").strip()
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "").strip()

VPS_PUBLIC_IP = os.getenv("VPS_PUBLIC_IP", "").strip()
CF_API_TOKEN = os.getenv("CF_API_TOKEN", "").strip()
CF_ZONE_ID = os.getenv("CF_ZONE_ID", "").strip()
CF_PROXIED = os.getenv("CF_PROXIED", "true").strip().lower() == "true"
CF_TTL = int(os.getenv("CF_TTL", "1").strip() or "1")

DOMAIN_PATTERN = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9][-A-Za-z0-9]{0,62}\.)+[A-Za-z]{2,63}$")
SERVICE_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")


def _json_error(msg: str) -> Tuple[bool, str]:
    return False, msg


def _json_ok(msg: str) -> Tuple[bool, str]:
    return True, msg


def _ensure_paths() -> None:
    ROUTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    NGINX_CONF_DIR.mkdir(parents=True, exist_ok=True)
    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    if not ROUTES_FILE.exists():
        ROUTES_FILE.write_text("[]", encoding="utf-8")


def _load_routes() -> List[Dict[str, object]]:
    _ensure_paths()
    try:
        raw = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            result: List[Dict[str, object]] = []
            for item in raw:
                if isinstance(item, dict):
                    result.append(item)
            return result
    except Exception:
        pass
    return []


def _save_routes(routes: List[Dict[str, object]]) -> None:
    ROUTES_FILE.write_text(json.dumps(routes, ensure_ascii=False, indent=2), encoding="utf-8")


def _slug_for_domain(domain: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9.-]", "_", domain).strip("._")
    return slug or "route"


def _cert_paths_for_domain(domain: str) -> Tuple[Path, Path]:
    cert_dir = CERTS_DIR / domain
    return cert_dir / "fullchain.pem", cert_dir / "privkey.pem"


def _cert_ready(domain: str) -> bool:
    fullchain_path, privkey_path = _cert_paths_for_domain(domain)
    return fullchain_path.exists() and privkey_path.exists()


def _to_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on", "y"}


def _render_one_conf(route: Dict[str, object]) -> str:
    domain = str(route.get("domain", "")).strip()
    service_name = str(route.get("service_name", "")).strip()
    service_port = int(route.get("service_port", 8000))
    enable_https = _to_bool(route.get("enable_https"), default=True)
    tls_available = enable_https and _cert_ready(domain)

    http_proxy_snippet = f"""proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";

    proxy_connect_timeout 5s;
    proxy_read_timeout 120s;
    proxy_send_timeout 120s;

    proxy_pass http://{service_name}:{service_port};"""

    if not tls_available:
        return f"""server {{
  listen 80;
  server_name {domain};

  location / {{
    {http_proxy_snippet}
  }}
}}
"""

    fullchain_path = f"{NGINX_CERT_ROOT}/{domain}/fullchain.pem"
    privkey_path = f"{NGINX_CERT_ROOT}/{domain}/privkey.pem"
    return f"""server {{
  listen 80;
  server_name {domain};
  return 301 https://$host$request_uri;
}}

server {{
  listen 443 ssl http2;
  server_name {domain};

  ssl_certificate {fullchain_path};
  ssl_certificate_key {privkey_path};
  ssl_protocols TLSv1.2 TLSv1.3;
  ssl_prefer_server_ciphers off;
  ssl_session_timeout 10m;

  location / {{
    {http_proxy_snippet}
  }}
}}
"""


def _rewrite_nginx_confs(routes: List[Dict[str, object]]) -> None:
    _ensure_paths()
    for conf_file in NGINX_CONF_DIR.glob("route_*.conf"):
        conf_file.unlink(missing_ok=True)

    for route in routes:
        domain = str(route.get("domain", "")).strip()
        if not domain:
            continue
        conf_name = f"route_{_slug_for_domain(domain)}.conf"
        conf_path = NGINX_CONF_DIR / conf_name
        conf_path.write_text(_render_one_conf(route), encoding="utf-8")


def _reload_nginx() -> Tuple[bool, str]:
    if docker is None:
        return _json_error("未安装 docker Python SDK，无法自动重载 Nginx")
    try:
        client = docker.from_env()
        container = client.containers.get(NGINX_CONTAINER_NAME)
        result = container.exec_run("nginx -s reload")
        if result.exit_code == 0:
            return _json_ok("Nginx 重载成功")
        detail = (result.output or b"").decode("utf-8", errors="ignore").strip()
        return _json_error(f"Nginx 重载失败: {detail or '未知错误'}")
    except Exception as exc:
        return _json_error(f"Nginx 重载异常: {exc}")


def _docker_client():
    if docker is None:
        raise RuntimeError("未安装 docker Python SDK")
    return docker.from_env()


def _run_in_container(container_name: str, command: str) -> Tuple[bool, str]:
    try:
        client = _docker_client()
        container = client.containers.get(container_name)
        result = container.exec_run(["sh", "-lc", command])
        output = (result.output or b"").decode("utf-8", errors="ignore").strip()
        if result.exit_code != 0:
            return _json_error(output or f"命令执行失败: {command}")
        return _json_ok(output)
    except Exception as exc:
        return _json_error(f"容器命令执行异常: {exc}")


def _issue_tls_cert(domain: str) -> Tuple[bool, str]:
    if not DOMAIN_PATTERN.match(domain):
        return _json_error("域名格式不正确，无法申请证书")
    if not CF_API_TOKEN:
        return _json_error("未配置 CF_API_TOKEN，无法申请证书")

    safe_domain = shlex.quote(domain)
    commands = [
        "acme.sh --set-default-ca --server letsencrypt",
        f"acme.sh --issue --dns dns_cf -d {safe_domain} --keylength 2048",
        f"mkdir -p /certs/{safe_domain}",
        (
            f"acme.sh --install-cert -d {safe_domain} "
            f"--key-file /certs/{safe_domain}/privkey.pem "
            f"--fullchain-file /certs/{safe_domain}/fullchain.pem"
        ),
    ]

    outputs: List[str] = []
    for command in commands:
        ok, output = _run_in_container(CERT_AGENT_CONTAINER_NAME, command)
        if output:
            outputs.append(output)
        if not ok:
            return _json_error(f"证书申请失败: {output}")

    if _cert_ready(domain):
        return _json_ok(f"证书申请成功: {domain}")
    detail = outputs[-1] if outputs else "未生成证书文件"
    return _json_error(f"证书申请结束但未发现证书文件: {detail}")


def _cloudflare_upsert_a_record(domain: str) -> Tuple[bool, str]:
    if not CF_API_TOKEN:
        return _json_error("未配置 CF_API_TOKEN，跳过 DNS 绑定")
    if not CF_ZONE_ID:
        return _json_error("未配置 CF_ZONE_ID，跳过 DNS 绑定")
    if not VPS_PUBLIC_IP:
        return _json_error("未配置 VPS_PUBLIC_IP，跳过 DNS 绑定")

    base_url = "https://api.cloudflare.com/client/v4"
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json",
    }
    params = {
        "type": "A",
        "name": domain,
        "page": 1,
        "per_page": 1,
    }
    payload = {
        "type": "A",
        "name": domain,
        "content": VPS_PUBLIC_IP,
        "proxied": CF_PROXIED,
        "ttl": CF_TTL,
    }

    try:
        query_resp = requests.get(
            f"{base_url}/zones/{CF_ZONE_ID}/dns_records",
            headers=headers,
            params=params,
            timeout=15,
        )
        query_data = query_resp.json()
        if not query_data.get("success", False):
            return _json_error(f"查询 DNS 失败: {query_data.get('errors')}")

        records = query_data.get("result") or []
        if records:
            record_id = records[0].get("id")
            edit_resp = requests.put(
                f"{base_url}/zones/{CF_ZONE_ID}/dns_records/{record_id}",
                headers=headers,
                json=payload,
                timeout=15,
            )
            edit_data = edit_resp.json()
            if not edit_data.get("success", False):
                return _json_error(f"更新 DNS 失败: {edit_data.get('errors')}")
            return _json_ok(f"DNS 已更新: {domain} -> {VPS_PUBLIC_IP}")

        create_resp = requests.post(
            f"{base_url}/zones/{CF_ZONE_ID}/dns_records",
            headers=headers,
            json=payload,
            timeout=15,
        )
        create_data = create_resp.json()
        if not create_data.get("success", False):
            return _json_error(f"创建 DNS 失败: {create_data.get('errors')}")
        return _json_ok(f"DNS 已创建: {domain} -> {VPS_PUBLIC_IP}")
    except Exception as exc:
        return _json_error(f"Cloudflare API 调用异常: {exc}")


def _validate_route(domain: str, service_name: str, service_port: int) -> Tuple[bool, str]:
    if not DOMAIN_PATTERN.match(domain):
        return _json_error("域名格式不正确")
    if not SERVICE_PATTERN.match(service_name):
        return _json_error("服务名不合法，只允许字母数字和 ._-")
    if service_port <= 0 or service_port > 65535:
        return _json_error("服务端口必须在 1-65535")
    return _json_ok("参数合法")


def _upsert_route(domain: str, service_name: str, service_port: int, remark: str) -> None:
    routes = _load_routes()
    updated = False
    for item in routes:
        if str(item.get("domain", "")).strip() == domain:
            item["service_name"] = service_name
            item["service_port"] = service_port
            item["remark"] = remark
            if "enable_https" not in item:
                item["enable_https"] = True
            updated = True
            break
    if not updated:
        routes.append(
            {
                "domain": domain,
                "service_name": service_name,
                "service_port": service_port,
                "remark": remark,
                "enable_https": True,
            }
        )
    routes.sort(key=lambda x: str(x.get("domain", "")))
    _save_routes(routes)
    _rewrite_nginx_confs(routes)


def _delete_route(domain: str) -> bool:
    routes = _load_routes()
    new_routes = [item for item in routes if str(item.get("domain", "")).strip() != domain]
    if len(new_routes) == len(routes):
        return False
    _save_routes(new_routes)
    _rewrite_nginx_confs(new_routes)
    return True


def _set_route_https(domain: str, enable_https: bool) -> Tuple[bool, str]:
    routes = _load_routes()
    updated = False
    for item in routes:
        if str(item.get("domain", "")).strip() == domain:
            item["enable_https"] = enable_https
            updated = True
            break
    if not updated:
        return _json_error(f"未找到域名路由: {domain}")
    _save_routes(routes)
    _rewrite_nginx_confs(routes)
    return _json_ok(f"已更新 HTTPS 开关: {domain} -> {'开启' if enable_https else '关闭'}")


def _check_env_items() -> List[Dict[str, object]]:
    items: List[Dict[str, object]] = []
    items.append(
        {
            "name": "路由配置文件",
            "ok": ROUTES_FILE.exists(),
            "detail": str(ROUTES_FILE),
        }
    )
    items.append(
        {
            "name": "Nginx 配置目录",
            "ok": NGINX_CONF_DIR.exists(),
            "detail": str(NGINX_CONF_DIR),
        }
    )
    items.append(
        {
            "name": "证书目录",
            "ok": CERTS_DIR.exists(),
            "detail": str(CERTS_DIR),
        }
    )
    items.append(
        {
            "name": "Docker SDK",
            "ok": docker is not None,
            "detail": "已安装" if docker is not None else "未安装",
        }
    )
    items.append(
        {
            "name": "Cloudflare Token",
            "ok": bool(CF_API_TOKEN),
            "detail": "已配置" if CF_API_TOKEN else "未配置",
        }
    )
    items.append(
        {
            "name": "Cloudflare Zone",
            "ok": bool(CF_ZONE_ID),
            "detail": CF_ZONE_ID or "未配置",
        }
    )
    items.append(
        {
            "name": "证书容器",
            "ok": bool(CERT_AGENT_CONTAINER_NAME),
            "detail": CERT_AGENT_CONTAINER_NAME or "未配置",
        }
    )
    cert_container_running = False
    cert_container_detail = "未运行"
    if docker is not None:
        try:
            client = docker.from_env()
            cert_container = client.containers.get(CERT_AGENT_CONTAINER_NAME)
            cert_container_running = cert_container.status == "running"
            cert_container_detail = f"状态: {cert_container.status}"
        except Exception:
            cert_container_running = False
            cert_container_detail = "未找到容器或无法访问 Docker"
    items.append(
        {
            "name": "证书容器运行状态",
            "ok": cert_container_running,
            "detail": cert_container_detail,
        }
    )
    items.append(
        {
            "name": "VPS 公网 IP",
            "ok": bool(VPS_PUBLIC_IP),
            "detail": VPS_PUBLIC_IP or "未配置",
        }
    )
    return items


def _basic_auth_enabled() -> bool:
    return bool(PANEL_USER and PANEL_PASSWORD)


def _check_basic_auth() -> bool:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return False
    encoded = auth_header.replace("Basic ", "", 1).strip()
    try:
        raw = base64.b64decode(encoded).decode("utf-8")
    except Exception:
        return False
    if ":" not in raw:
        return False
    username, password = raw.split(":", 1)
    return username == PANEL_USER and password == PANEL_PASSWORD


@app.before_request
def _auth_guard() -> Response | None:
    if not _basic_auth_enabled():
        return None
    if _check_basic_auth():
        return None
    return Response(
        "需要认证",
        401,
        {"WWW-Authenticate": 'Basic realm="Proxy Panel", charset="UTF-8"'},
    )


@app.get("/")
def index() -> str:
    raw_routes = _load_routes()
    routes: List[Dict[str, object]] = []
    for item in raw_routes:
        domain = str(item.get("domain", "")).strip()
        enable_https = _to_bool(item.get("enable_https"), default=True)
        cert_exists = _cert_ready(domain) if domain else False
        view_item = dict(item)
        view_item["enable_https"] = enable_https
        view_item["cert_exists"] = cert_exists
        view_item["https_active"] = bool(enable_https and cert_exists)
        routes.append(view_item)
    env_items = _check_env_items()
    return render_template(
        "index.html",
        routes=routes,
        env_items=env_items,
        nginx_container_name=NGINX_CONTAINER_NAME,
        cert_agent_container_name=CERT_AGENT_CONTAINER_NAME,
        panel_auth_enabled=_basic_auth_enabled(),
    )


@app.post("/route/add")
def add_route() -> Response:
    domain = request.form.get("domain", "").strip().lower()
    service_name = request.form.get("service_name", "").strip()
    port_text = request.form.get("service_port", "8000").strip()
    remark = request.form.get("remark", "").strip()
    bind_dns = request.form.get("bind_dns", "").strip() == "on"
    enable_https = request.form.get("enable_https", "").strip() == "on"

    try:
        service_port = int(port_text)
    except ValueError:
        flash("服务端口必须是整数", "error")
        return redirect(url_for("index"))

    ok, msg = _validate_route(domain, service_name, service_port)
    if not ok:
        flash(msg, "error")
        return redirect(url_for("index"))

    _upsert_route(domain, service_name, service_port, remark)
    https_ok, https_msg = _set_route_https(domain, enable_https)
    if not https_ok:
        flash(https_msg, "error")
        return redirect(url_for("index"))
    flash(f"路由已保存: {domain} -> {service_name}:{service_port}", "success")
    flash(f"HTTPS 开关: {'开启' if enable_https else '关闭'}", "success")

    reload_ok, reload_msg = _reload_nginx()
    flash(reload_msg, "success" if reload_ok else "error")

    if bind_dns:
        dns_ok, dns_msg = _cloudflare_upsert_a_record(domain)
        flash(dns_msg, "success" if dns_ok else "error")
    return redirect(url_for("index"))


@app.post("/route/https")
def route_https() -> Response:
    domain = request.form.get("domain", "").strip().lower()
    enable_https = request.form.get("enable_https", "").strip() == "on"
    if not domain:
        flash("缺少域名参数", "error")
        return redirect(url_for("index"))
    ok, msg = _set_route_https(domain, enable_https)
    flash(msg, "success" if ok else "error")
    if ok:
        reload_ok, reload_msg = _reload_nginx()
        flash(reload_msg, "success" if reload_ok else "error")
    return redirect(url_for("index"))


@app.post("/route/delete")
def delete_route() -> Response:
    domain = request.form.get("domain", "").strip().lower()
    if not domain:
        flash("缺少要删除的域名", "error")
        return redirect(url_for("index"))

    deleted = _delete_route(domain)
    if not deleted:
        flash(f"未找到域名路由: {domain}", "error")
        return redirect(url_for("index"))

    flash(f"路由已删除: {domain}", "success")
    reload_ok, reload_msg = _reload_nginx()
    flash(reload_msg, "success" if reload_ok else "error")
    return redirect(url_for("index"))


@app.post("/dns/bind")
def bind_dns() -> Response:
    domain = request.form.get("domain", "").strip().lower()
    if not domain:
        flash("缺少域名参数", "error")
        return redirect(url_for("index"))
    dns_ok, dns_msg = _cloudflare_upsert_a_record(domain)
    flash(dns_msg, "success" if dns_ok else "error")
    return redirect(url_for("index"))


@app.post("/cert/issue")
def issue_cert() -> Response:
    domain = request.form.get("domain", "").strip().lower()
    if not domain:
        flash("缺少域名参数", "error")
        return redirect(url_for("index"))

    cert_ok, cert_msg = _issue_tls_cert(domain)
    flash(cert_msg, "success" if cert_ok else "error")
    if not cert_ok:
        return redirect(url_for("index"))

    routes = _load_routes()
    _rewrite_nginx_confs(routes)
    reload_ok, reload_msg = _reload_nginx()
    flash(reload_msg, "success" if reload_ok else "error")
    return redirect(url_for("index"))


@app.post("/nginx/reload")
def reload_nginx() -> Response:
    ok, msg = _reload_nginx()
    flash(msg, "success" if ok else "error")
    return redirect(url_for("index"))


@app.get("/healthz")
def healthz() -> Dict[str, object]:
    return {"ok": True, "service": "proxy-panel"}


if __name__ == "__main__":
    _ensure_paths()
    app.run(host="0.0.0.0", port=18080, debug=False)
