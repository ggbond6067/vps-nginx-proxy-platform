from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests
from flask import Flask, Response, flash, jsonify, redirect, render_template, request, url_for

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
SERVICES_DIR = Path(os.getenv("SERVICES_DIR", "/app/services"))
PROXY_NETWORK_NAME = os.getenv("PROXY_NETWORK_NAME", "proxy_net").strip() or "proxy_net"
NGINX_CERT_ROOT = "/etc/nginx/certs"

PANEL_USER = os.getenv("PANEL_USER", "").strip()
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "").strip()

VPS_PUBLIC_IP = os.getenv("VPS_PUBLIC_IP", "").strip()
VPS_PUBLIC_IPV4 = os.getenv("VPS_PUBLIC_IPV4", "").strip()
VPS_PUBLIC_IPV6 = os.getenv("VPS_PUBLIC_IPV6", "").strip()

CF_API_TOKEN = os.getenv("CF_API_TOKEN", "").strip()
CF_ZONE_ID = os.getenv("CF_ZONE_ID", "").strip()
CF_PROXIED = os.getenv("CF_PROXIED", "true").strip().lower() == "true"
CF_TTL = int(os.getenv("CF_TTL", "1").strip() or "1")

CF_API_BASE = "https://api.cloudflare.com/client/v4"
PUBLIC_IP_CACHE_TTL = 300
ZONE_CACHE_TTL = 300
DOCKER_CLI_CACHE_TTL = 60
PUBLIC_IP_ENDPOINTS = {
    "ipv4": ["https://api.ipify.org", "https://ipv4.icanhazip.com"],
    "ipv6": ["https://api64.ipify.org", "https://ipv6.icanhazip.com"],
}
PLACEHOLDER_IPS = {"1.2.3.4"}
DOMAIN_PATTERN = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9][-A-Za-z0-9]{0,62}\.)+[A-Za-z]{2,63}$")
SERVICE_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
PROJECT_SLUG_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
DNS_TYPES = {"A", "AAAA", "CNAME"}
_PUBLIC_IP_CACHE: Dict[str, Dict[str, Any]] = {}
_ZONE_CACHE: Dict[str, Any] = {"expires_at": 0.0, "items": [], "message": "", "ok": False}
_DOCKER_CLI_CACHE: Dict[str, Any] = {"expires_at": 0.0, "ok": False, "detail": "未检测"}


def _json_error(msg: str) -> Tuple[bool, str]:
    return False, msg


def _json_ok(msg: str) -> Tuple[bool, str]:
    return True, msg


def _api_response(ok: bool, message: str, data: Dict[str, Any] | None = None, status: int = 200) -> Response:
    return jsonify({"ok": ok, "message": message, "data": data or {}}), status


def _request_data() -> Dict[str, Any]:
    if request.is_json:
        raw = request.get_json(silent=True)
        if isinstance(raw, dict):
            return raw
    return {key: value for key, value in request.form.items()}


def _ensure_paths() -> None:
    ROUTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    NGINX_CONF_DIR.mkdir(parents=True, exist_ok=True)
    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    SERVICES_DIR.mkdir(parents=True, exist_ok=True)
    if not ROUTES_FILE.exists():
        ROUTES_FILE.write_text("[]", encoding="utf-8")


def _load_routes() -> List[Dict[str, Any]]:
    _ensure_paths()
    try:
        raw = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
    except Exception:
        pass
    return []


def _save_routes(routes: List[Dict[str, Any]]) -> None:
    routes.sort(key=lambda item: str(item.get("domain", "")))
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


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _normalize_domain(value: Any) -> str:
    return str(value or "").strip().lower().strip(".")


def _normalize_zone_name(value: Any) -> str:
    return _normalize_domain(value)


def _normalize_dns_prefix(value: Any) -> str:
    text = str(value or "").strip().lower().strip(".")
    return "@" if text in {"", "@"} else text


def _normalize_cname_target(value: Any) -> str:
    return _normalize_domain(value)


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _mask_placeholder_ip(value: str) -> str:
    text = value.strip()
    return "" if text in PLACEHOLDER_IPS else text


def _validate_ip_value(value: str, family: str) -> str:
    text = value.strip()
    if not text:
        return ""
    try:
        parsed = ipaddress.ip_address(text)
    except ValueError:
        return ""
    if family == "ipv4" and parsed.version == 4:
        return text
    if family == "ipv6" and parsed.version == 6:
        return text
    return ""


def _manual_public_ip(family: str) -> Tuple[str, str]:
    if family == "ipv4":
        manual_value = _validate_ip_value(_mask_placeholder_ip(VPS_PUBLIC_IPV4), family)
        if manual_value:
            return manual_value, "manual"
        legacy_value = _validate_ip_value(_mask_placeholder_ip(VPS_PUBLIC_IP), family)
        if legacy_value:
            return legacy_value, "legacy"
        return "", ""
    manual_ipv6 = _validate_ip_value(VPS_PUBLIC_IPV6, family)
    return (manual_ipv6, "manual") if manual_ipv6 else ("", "")


def _fetch_public_ip(url: str, family: str) -> str:
    response = requests.get(url, timeout=3)
    response.raise_for_status()
    candidate = response.text.strip().splitlines()[0].strip()
    return _validate_ip_value(candidate, family)


def _public_ip_source_label(source: str) -> str:
    return {
        "detected": "自动探测",
        "manual": "手动配置",
        "legacy": "旧配置回退",
        "unavailable": "未获取",
    }.get(source, "未知")


def _resolve_public_ip(family: str) -> Dict[str, Any]:
    now = time.time()
    cached = _PUBLIC_IP_CACHE.get(family)
    if cached and float(cached.get("expires_at", 0)) > now:
        return dict(cached["value"])

    value = ""
    source = "unavailable"
    for endpoint in PUBLIC_IP_ENDPOINTS.get(family, []):
        try:
            detected = _fetch_public_ip(endpoint, family)
        except Exception:
            detected = ""
        if detected:
            value = detected
            source = "detected"
            break

    if not value:
        manual_value, manual_source = _manual_public_ip(family)
        if manual_value:
            value = manual_value
            source = manual_source

    result = {
        "family": family,
        "value": value,
        "source": source,
        "source_label": _public_ip_source_label(source),
        "ok": bool(value),
    }
    _PUBLIC_IP_CACHE[family] = {"expires_at": now + PUBLIC_IP_CACHE_TTL, "value": result}
    return dict(result)


def _public_ip_targets() -> Dict[str, Dict[str, Any]]:
    return {"ipv4": _resolve_public_ip("ipv4"), "ipv6": _resolve_public_ip("ipv6")}


def _cloudflare_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}


def _extract_cf_error(payload: Dict[str, Any]) -> str:
    errors = payload.get("errors") or []
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            code = first.get("code")
            message = first.get("message")
            if code or message:
                return f"{code or ''} {message or ''}".strip()
        return str(first)
    return payload.get("message") or "未知错误"


def _cloudflare_request(method: str, path: str, *, params: Dict[str, Any] | None = None, payload: Dict[str, Any] | None = None, timeout: int = 15) -> Tuple[bool, Dict[str, Any], str]:
    if not CF_API_TOKEN:
        return False, {}, "未配置 CF_API_TOKEN"
    try:
        response = requests.request(method=method, url=f"{CF_API_BASE}{path}", headers=_cloudflare_headers(), params=params, json=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        return False, {}, f"Cloudflare API 调用异常: {exc}"
    if not data.get("success", False):
        return False, data, _extract_cf_error(data)
    return True, data, "ok"


def _cloudflare_list_zones(force_refresh: bool = False) -> Tuple[bool, List[Dict[str, str]], str]:
    now = time.time()
    if not force_refresh and float(_ZONE_CACHE.get("expires_at", 0)) > now:
        return bool(_ZONE_CACHE.get("ok")), list(_ZONE_CACHE.get("items", [])), str(_ZONE_CACHE.get("message", ""))

    ok, data, message = _cloudflare_request("GET", "/zones", params={"per_page": 100})
    if not ok:
        _ZONE_CACHE.update({"expires_at": now + ZONE_CACHE_TTL, "ok": False, "items": [], "message": message})
        return False, [], message

    items: List[Dict[str, str]] = []
    for item in data.get("result") or []:
        zone_id = str(item.get("id", "")).strip()
        zone_name = _normalize_zone_name(item.get("name", ""))
        if zone_id and zone_name:
            items.append({"id": zone_id, "name": zone_name})
    items.sort(key=lambda item: item["name"])
    _ZONE_CACHE.update({"expires_at": now + ZONE_CACHE_TTL, "ok": True, "items": items, "message": "Cloudflare Zone 列表已加载"})
    return True, items, "Cloudflare Zone 列表已加载"


def _cloudflare_get_zone(zone_id: str) -> Tuple[bool, Dict[str, str], str]:
    zone_id = str(zone_id or "").strip()
    if not zone_id:
        return False, {}, "缺少 Zone ID"
    ok, data, message = _cloudflare_request("GET", f"/zones/{zone_id}")
    if not ok:
        return False, {}, message
    result = data.get("result") or {}
    item = {"id": str(result.get("id", "")).strip(), "name": _normalize_zone_name(result.get("name", ""))}
    if not item["id"] or not item["name"]:
        return False, {}, "Zone 信息不完整"
    return True, item, "Zone 信息已加载"


def _zone_context() -> Dict[str, Any]:
    if not CF_API_TOKEN:
        return {"mode": "manual", "items": [], "default_zone_id": "", "message": "未配置 Cloudflare Token，已降级为完整域名输入模式"}

    ok, items, message = _cloudflare_list_zones()
    if ok and items:
        default_zone_id = CF_ZONE_ID if any(item["id"] == CF_ZONE_ID for item in items) else items[0]["id"]
        return {"mode": "selector", "items": items, "default_zone_id": default_zone_id, "message": ""}

    if CF_ZONE_ID:
        fallback_ok, zone_item, fallback_message = _cloudflare_get_zone(CF_ZONE_ID)
        if fallback_ok:
            return {
                "mode": "selector",
                "items": [zone_item],
                "default_zone_id": zone_item["id"],
                "message": f"{message or fallback_message}，已回退为单 Zone 模式",
            }

    return {"mode": "manual", "items": [], "default_zone_id": "", "message": message or "Cloudflare Zone 列表不可用，已降级为完整域名输入模式"}


def _zone_maps(zone_items: List[Dict[str, str]]) -> Tuple[Dict[str, Dict[str, str]], Dict[str, Dict[str, str]]]:
    return ({item["id"]: item for item in zone_items if item.get("id")}, {item["name"]: item for item in zone_items if item.get("name")})


def _resolve_zone_selection(zone_id: Any, zone_name: Any, zone_items: List[Dict[str, str]]) -> Tuple[str, str]:
    zone_id_text = str(zone_id or "").strip()
    zone_name_text = _normalize_zone_name(zone_name)
    by_id, by_name = _zone_maps(zone_items)
    if zone_id_text and zone_id_text in by_id:
        selected = by_id[zone_id_text]
        return selected["id"], selected["name"]
    if zone_name_text and zone_name_text in by_name:
        selected = by_name[zone_name_text]
        return selected["id"], selected["name"]
    if zone_id_text and zone_name_text:
        return zone_id_text, zone_name_text
    if zone_id_text and zone_id_text == CF_ZONE_ID and not zone_items:
        return zone_id_text, zone_name_text
    return "", zone_name_text


def _compose_domain(dns_prefix: Any, zone_name: Any, full_domain: Any) -> str:
    zone = _normalize_zone_name(zone_name)
    if zone:
        prefix = _normalize_dns_prefix(dns_prefix)
        return zone if prefix in {"", "@"} else f"{prefix}.{zone}".lower()
    return _normalize_domain(full_domain)


def _extract_prefix_from_domain(domain: str, zone_name: str) -> str:
    domain_text = _normalize_domain(domain)
    zone_text = _normalize_zone_name(zone_name)
    if not domain_text or not zone_text:
        return ""
    if domain_text == zone_text:
        return "@"
    suffix = f".{zone_text}"
    return domain_text[: -len(suffix)] if domain_text.endswith(suffix) else ""


def _infer_zone_from_domain(domain: str, zone_items: List[Dict[str, str]]) -> Tuple[str, str, str]:
    domain_text = _normalize_domain(domain)
    best_item: Dict[str, str] | None = None
    best_length = -1
    for item in zone_items:
        zone_name = item["name"]
        if domain_text == zone_name or domain_text.endswith(f".{zone_name}"):
            if len(zone_name) > best_length:
                best_item = item
                best_length = len(zone_name)
    if not best_item:
        return "", "", ""
    prefix = _extract_prefix_from_domain(domain_text, best_item["name"])
    return best_item["id"], best_item["name"], prefix or "@"


def _route_to_view(route: Dict[str, Any], zone_items: List[Dict[str, str]], public_ips: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    domain = _normalize_domain(route.get("domain", ""))
    enable_https = _to_bool(route.get("enable_https"), default=True)
    zone_id = str(route.get("zone_id", "")).strip()
    zone_name = _normalize_zone_name(route.get("zone_name", ""))
    dns_record_type = str(route.get("dns_record_type", "A")).strip().upper() or "A"
    dns_record_type = dns_record_type if dns_record_type in DNS_TYPES else "A"
    dns_value = _normalize_cname_target(route.get("dns_value", ""))
    dns_prefix = str(route.get("dns_prefix", "")).strip()

    if zone_items:
        inferred_zone_id, inferred_zone_name, inferred_prefix = _infer_zone_from_domain(domain, zone_items)
        if not zone_name and inferred_zone_name:
            zone_name = inferred_zone_name
        if not zone_id and inferred_zone_id:
            zone_id = inferred_zone_id
        if not dns_prefix and inferred_prefix:
            dns_prefix = inferred_prefix

    if not dns_prefix and zone_name:
        dns_prefix = _extract_prefix_from_domain(domain, zone_name) or "@"
    if not dns_prefix:
        dns_prefix = "@"

    cert_exists = _cert_ready(domain) if domain else False
    https_active = bool(enable_https and cert_exists)
    target_display = dns_value
    if dns_record_type == "A":
        target_display = public_ips["ipv4"]["value"] or "自动获取 IPv4"
    elif dns_record_type == "AAAA":
        target_display = public_ips["ipv6"]["value"] or "自动获取 IPv6"

    return {
        "domain": domain,
        "service_name": str(route.get("service_name", "")).strip(),
        "service_port": _parse_int(route.get("service_port", 8000), 8000),
        "remark": str(route.get("remark", "")).strip(),
        "enable_https": enable_https,
        "cert_exists": cert_exists,
        "https_active": https_active,
        "zone_id": zone_id,
        "zone_name": zone_name,
        "dns_prefix": dns_prefix,
        "dns_record_type": dns_record_type,
        "dns_value": dns_value,
        "dns_target_display": target_display,
    }


def _render_one_conf(route: Dict[str, Any]) -> str:
    domain = str(route.get("domain", "")).strip()
    service_name = str(route.get("service_name", "")).strip()
    service_port = int(route.get("service_port", 8000))
    enable_https = _to_bool(route.get("enable_https"), default=True)
    tls_available = enable_https and _cert_ready(domain)

    http_proxy_snippet = f'''resolver 127.0.0.11 valid=30s ipv6=off;
    set $upstream "{service_name}:{service_port}";
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";

    proxy_connect_timeout 5s;
    proxy_read_timeout 120s;
    proxy_send_timeout 120s;

    proxy_pass http://$upstream;'''

    if not tls_available:
        return f'''server {{
  listen 80;
  server_name {domain};

  location / {{
    {http_proxy_snippet}
  }}
}}
'''

    fullchain_path = f"{NGINX_CERT_ROOT}/{domain}/fullchain.pem"
    privkey_path = f"{NGINX_CERT_ROOT}/{domain}/privkey.pem"
    return f'''server {{
  listen 80;
  server_name {domain};
  return 301 https://$host$request_uri;
}}

server {{
  listen 443 ssl;
  http2 on;
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
'''


def _rewrite_nginx_confs(routes: List[Dict[str, Any]]) -> None:
    _ensure_paths()
    for conf_file in NGINX_CONF_DIR.glob("route_*.conf"):
        conf_file.unlink(missing_ok=True)
    for route in routes:
        domain = str(route.get("domain", "")).strip()
        if not domain:
            continue
        conf_path = NGINX_CONF_DIR / f"route_{_slug_for_domain(domain)}.conf"
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


def _docker_cli_status(force_refresh: bool = False) -> Dict[str, Any]:
    now = time.time()
    if not force_refresh and float(_DOCKER_CLI_CACHE.get("expires_at", 0)) > now:
        return dict(_DOCKER_CLI_CACHE)
    try:
        result = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True, timeout=10, check=False)
        ok = result.returncode == 0
        detail = (result.stdout or result.stderr or "").strip() or "未检测到 docker compose"
    except Exception as exc:
        ok = False
        detail = f"docker compose 不可用: {exc}"
    _DOCKER_CLI_CACHE.update({"expires_at": now + DOCKER_CLI_CACHE_TTL, "ok": ok, "detail": detail})
    return dict(_DOCKER_CLI_CACHE)


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
        f"acme.sh --install-cert -d {safe_domain} --key-file /certs/{safe_domain}/privkey.pem --fullchain-file /certs/{safe_domain}/fullchain.pem",
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


def _dns_target_value(record_type: str, dns_value: str) -> Tuple[bool, str]:
    record_type = record_type.upper()
    if record_type == "A":
        value = _public_ip_targets()["ipv4"]["value"]
        if value:
            return _json_ok(value)
        return _json_error("未获取到公网 IPv4，无法写入 A 记录")
    if record_type == "AAAA":
        value = _public_ip_targets()["ipv6"]["value"]
        if value:
            return _json_ok(value)
        return _json_error("未获取到公网 IPv6，无法写入 AAAA 记录")
    if record_type == "CNAME":
        normalized = _normalize_cname_target(dns_value)
        if DOMAIN_PATTERN.match(normalized):
            return _json_ok(normalized)
        return _json_error("CNAME 目标域名格式不正确")
    return _json_error("不支持的 DNS 记录类型")


def _cloudflare_upsert_dns_record(domain: str, zone_id: str, record_type: str, dns_value: str) -> Tuple[bool, str]:
    if not CF_API_TOKEN:
        return _json_error("未配置 CF_API_TOKEN，跳过 DNS 绑定")
    zone_id_text = str(zone_id or CF_ZONE_ID or "").strip()
    if not zone_id_text:
        return _json_error("未选择 Cloudflare Zone，跳过 DNS 绑定")

    record_type_text = str(record_type or "A").strip().upper()
    if record_type_text not in DNS_TYPES:
        return _json_error("DNS 记录类型不支持")

    ok, target = _dns_target_value(record_type_text, dns_value)
    if not ok:
        return _json_error(target)

    params = {"type": record_type_text, "name": domain, "page": 1, "per_page": 1}
    payload = {"type": record_type_text, "name": domain, "content": target, "proxied": CF_PROXIED, "ttl": CF_TTL}
    query_ok, query_data, query_message = _cloudflare_request("GET", f"/zones/{zone_id_text}/dns_records", params=params)
    if not query_ok:
        return _json_error(f"查询 DNS 失败: {query_message}")

    records = query_data.get("result") or []
    if records:
        record_id = str(records[0].get("id", "")).strip()
        edit_ok, _, edit_message = _cloudflare_request("PUT", f"/zones/{zone_id_text}/dns_records/{record_id}", payload=payload)
        if not edit_ok:
            return _json_error(f"更新 DNS 失败: {edit_message}")
        return _json_ok(f"DNS 已更新: {domain} -> {target} ({record_type_text})")

    create_ok, _, create_message = _cloudflare_request("POST", f"/zones/{zone_id_text}/dns_records", payload=payload)
    if not create_ok:
        return _json_error(f"创建 DNS 失败: {create_message}")
    return _json_ok(f"DNS 已创建: {domain} -> {target} ({record_type_text})")


def _validate_route(domain: str, service_name: str, service_port: int, dns_record_type: str, dns_value: str) -> Tuple[bool, str]:
    if not DOMAIN_PATTERN.match(domain):
        return _json_error("域名格式不正确")
    if not SERVICE_PATTERN.match(service_name):
        return _json_error("服务名不合法，只允许字母数字和 ._-")
    if service_port <= 0 or service_port > 65535:
        return _json_error("服务端口必须在 1-65535")
    if dns_record_type not in DNS_TYPES:
        return _json_error("DNS 记录类型不支持")
    if dns_record_type == "CNAME":
        normalized_target = _normalize_cname_target(dns_value)
        if not DOMAIN_PATTERN.match(normalized_target):
            return _json_error("CNAME 目标域名格式不正确")
    return _json_ok("参数合法")


def _find_route(domain: str) -> Dict[str, Any] | None:
    domain_text = _normalize_domain(domain)
    for item in _load_routes():
        if _normalize_domain(item.get("domain", "")) == domain_text:
            return item
    return None


def _upsert_route(*, original_domain: str, domain: str, service_name: str, service_port: int, remark: str, enable_https: bool, zone_id: str, zone_name: str, dns_prefix: str, dns_record_type: str, dns_value: str) -> None:
    routes = _load_routes()
    original_domain_text = _normalize_domain(original_domain)
    domain_text = _normalize_domain(domain)
    filtered: List[Dict[str, Any]] = []
    for item in routes:
        item_domain = _normalize_domain(item.get("domain", ""))
        if item_domain == original_domain_text and original_domain_text and original_domain_text != domain_text:
            continue
        if item_domain == domain_text:
            continue
        filtered.append(item)

    filtered.append(
        {
            "domain": domain_text,
            "service_name": service_name,
            "service_port": service_port,
            "remark": remark,
            "enable_https": enable_https,
            "zone_id": zone_id,
            "zone_name": zone_name,
            "dns_prefix": dns_prefix,
            "dns_record_type": dns_record_type,
            "dns_value": dns_value,
        }
    )
    _save_routes(filtered)
    _rewrite_nginx_confs(filtered)


def _set_route_https(domain: str, enable_https: bool) -> Tuple[bool, str]:
    routes = _load_routes()
    updated = False
    domain_text = _normalize_domain(domain)
    for item in routes:
        if _normalize_domain(item.get("domain", "")) == domain_text:
            item["enable_https"] = enable_https
            updated = True
            break
    if not updated:
        return _json_error(f"未找到域名路由: {domain_text}")
    _save_routes(routes)
    _rewrite_nginx_confs(routes)
    return _json_ok(f"已更新 HTTPS 开关: {domain_text} -> {'开启' if enable_https else '关闭'}")


def _delete_route(domain: str) -> bool:
    routes = _load_routes()
    domain_text = _normalize_domain(domain)
    remain = [item for item in routes if _normalize_domain(item.get("domain", "")) != domain_text]
    if len(remain) == len(routes):
        return False
    _save_routes(remain)
    _rewrite_nginx_confs(remain)
    conf_path = NGINX_CONF_DIR / f"route_{_slug_for_domain(domain_text)}.conf"
    conf_path.unlink(missing_ok=True)
    return True


def _container_port_candidates(container: Any) -> List[int]:
    values = set()
    attrs = container.attrs or {}
    config_ports = (attrs.get("Config") or {}).get("ExposedPorts") or {}
    for key in config_ports:
        try:
            values.add(int(str(key).split("/", 1)[0]))
        except Exception:
            continue
    network_ports = (attrs.get("NetworkSettings") or {}).get("Ports") or {}
    for key in network_ports:
        try:
            values.add(int(str(key).split("/", 1)[0]))
        except Exception:
            continue
    return sorted(values)


def _container_network_aliases(container: Any, network_name: str) -> List[str]:
    attrs = container.attrs or {}
    networks = (attrs.get("NetworkSettings") or {}).get("Networks") or {}
    network_info = networks.get(network_name) or {}
    aliases = network_info.get("Aliases") or []
    return [str(alias or "").strip() for alias in aliases if str(alias or "").strip()]


def _recommended_service_name(container: Any) -> str:
    attrs = container.attrs or {}
    labels = (attrs.get("Config") or {}).get("Labels") or {}
    compose_service = str(labels.get("com.docker.compose.service", "")).strip()
    if compose_service:
        return compose_service
    aliases = _container_network_aliases(container, PROXY_NETWORK_NAME)
    for alias in aliases:
        if alias != container.name and SERVICE_PATTERN.match(alias):
            return alias
    if SERVICE_PATTERN.match(container.name):
        return container.name
    return container.name.strip("/") or container.id[:12]


def _discover_proxy_services() -> List[Dict[str, Any]]:
    if docker is None:
        return []
    try:
        client = _docker_client()
        containers = client.containers.list(filters={"status": "running"})
    except Exception:
        return []

    items: List[Dict[str, Any]] = []
    for container in containers:
        attrs = container.attrs or {}
        networks = (attrs.get("NetworkSettings") or {}).get("Networks") or {}
        if PROXY_NETWORK_NAME not in networks:
            continue
        ports = _container_port_candidates(container)
        items.append(
            {
                "id": container.id,
                "service_name": _recommended_service_name(container),
                "container_name": container.name,
                "image": container.image.tags[0] if container.image.tags else container.image.short_id,
                "network": PROXY_NETWORK_NAME,
                "ports": ports,
                "status": container.status,
            }
        )
    items.sort(key=lambda item: (item["service_name"], item["container_name"]))
    return items


def _parse_started_at(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text or text.startswith("0001-01-01"):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _compute_cpu_percent(stats: Dict[str, Any]) -> float:
    cpu_stats = stats.get("cpu_stats") or {}
    precpu_stats = stats.get("precpu_stats") or {}
    current_cpu = ((cpu_stats.get("cpu_usage") or {}).get("total_usage")) or 0
    previous_cpu = ((precpu_stats.get("cpu_usage") or {}).get("total_usage")) or 0
    current_system = cpu_stats.get("system_cpu_usage") or 0
    previous_system = precpu_stats.get("system_cpu_usage") or 0
    cpu_delta = current_cpu - previous_cpu
    system_delta = current_system - previous_system
    online_cpus = cpu_stats.get("online_cpus") or len((cpu_stats.get("cpu_usage") or {}).get("percpu_usage") or []) or 1
    if cpu_delta > 0 and system_delta > 0:
        return round((cpu_delta / system_delta) * online_cpus * 100.0, 2)
    return 0.0


def _sum_network_bytes(stats: Dict[str, Any]) -> Tuple[int, int]:
    rx_total = 0
    tx_total = 0
    for item in (stats.get("networks") or {}).values():
        rx_total += int(item.get("rx_bytes") or 0)
        tx_total += int(item.get("tx_bytes") or 0)
    return rx_total, tx_total


def _monitor_payload() -> Dict[str, Any]:
    if docker is None:
        return {"available": False, "message": "未安装 docker Python SDK", "engine": {}, "summary": {"total": 0, "running": 0, "unhealthy": 0}, "containers": []}
    try:
        client = _docker_client()
        info = client.info()
        version = client.version()
        containers = client.containers.list(all=True)
    except Exception as exc:
        return {"available": False, "message": f"无法访问 Docker: {exc}", "engine": {}, "summary": {"total": 0, "running": 0, "unhealthy": 0}, "containers": []}

    items: List[Dict[str, Any]] = []
    unhealthy = 0
    running = 0
    now = datetime.now(timezone.utc)
    for container in containers:
        attrs = container.attrs or {}
        state = attrs.get("State") or {}
        health = (state.get("Health") or {}).get("Status") or ""
        if health == "unhealthy":
            unhealthy += 1
        if container.status == "running":
            running += 1

        started_at = _parse_started_at(state.get("StartedAt"))
        uptime_seconds = int((now - started_at).total_seconds()) if started_at else None
        cpu_percent = 0.0
        memory_usage = 0
        memory_limit = 0
        rx_bytes = 0
        tx_bytes = 0
        stats_error = ""
        if container.status == "running":
            try:
                stats = container.stats(stream=False)
                cpu_percent = _compute_cpu_percent(stats)
                memory_stats = stats.get("memory_stats") or {}
                cache_value = ((memory_stats.get("stats") or {}).get("cache")) or 0
                memory_usage = max(int(memory_stats.get("usage") or 0) - int(cache_value), 0)
                memory_limit = int(memory_stats.get("limit") or 0)
                rx_bytes, tx_bytes = _sum_network_bytes(stats)
            except Exception as exc:
                stats_error = str(exc)

        items.append(
            {
                "id": container.id,
                "name": container.name,
                "image": container.image.tags[0] if container.image.tags else container.image.short_id,
                "status": container.status,
                "health": health,
                "ports": _container_port_candidates(container),
                "networks": list(((attrs.get("NetworkSettings") or {}).get("Networks") or {}).keys()),
                "started_at": started_at.isoformat() if started_at else "",
                "uptime_seconds": uptime_seconds,
                "cpu_percent": cpu_percent,
                "memory_usage": memory_usage,
                "memory_limit": memory_limit,
                "rx_bytes": rx_bytes,
                "tx_bytes": tx_bytes,
                "stats_error": stats_error,
            }
        )
    items.sort(key=lambda item: item["name"])
    return {
        "available": True,
        "message": "Docker 监控数据已加载",
        "engine": {
            "server_version": version.get("Version") or info.get("ServerVersion") or "",
            "operating_system": info.get("OperatingSystem") or "",
            "kernel_version": info.get("KernelVersion") or "",
            "cpu_count": int(info.get("NCPU") or 0),
            "memory_total": int(info.get("MemTotal") or 0),
            "name": info.get("Name") or "",
        },
        "summary": {"total": len(containers), "running": running, "unhealthy": unhealthy},
        "containers": items,
    }


def _docker_runtime_summary() -> Dict[str, Any]:
    monitor = _monitor_payload()
    return {
        "docker_available": monitor["available"],
        "running_container_count": monitor["summary"]["running"],
        "total_container_count": monitor["summary"]["total"],
        "unhealthy_container_count": monitor["summary"]["unhealthy"],
    }


def _check_env_items(zone_context: Dict[str, Any], public_ips: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    items.append({"name": "路由配置文件", "ok": ROUTES_FILE.exists(), "detail": str(ROUTES_FILE)})
    items.append({"name": "Nginx 配置目录", "ok": NGINX_CONF_DIR.exists(), "detail": str(NGINX_CONF_DIR)})
    items.append({"name": "证书目录", "ok": CERTS_DIR.exists(), "detail": str(CERTS_DIR)})
    items.append({"name": "服务目录", "ok": SERVICES_DIR.exists(), "detail": str(SERVICES_DIR)})
    items.append({"name": "Docker SDK", "ok": docker is not None, "detail": "已安装" if docker is not None else "未安装"})
    docker_cli = _docker_cli_status()
    items.append({"name": "Docker Compose CLI", "ok": docker_cli["ok"], "detail": docker_cli["detail"]})
    items.append({"name": "Cloudflare Token", "ok": bool(CF_API_TOKEN), "detail": "已配置" if CF_API_TOKEN else "未配置"})
    items.append({"name": "Cloudflare Zone 列表", "ok": bool(zone_context.get("items")), "detail": zone_context.get("message") or "可切换"})
    items.append({"name": "证书容器", "ok": bool(CERT_AGENT_CONTAINER_NAME), "detail": CERT_AGENT_CONTAINER_NAME or "未配置"})
    items.append({"name": "反代网络", "ok": bool(PROXY_NETWORK_NAME), "detail": PROXY_NETWORK_NAME})
    items.append({"name": "公网 IPv4", "ok": public_ips["ipv4"]["ok"], "detail": public_ips["ipv4"]["value"] or public_ips["ipv4"]["source_label"]})
    items.append({"name": "公网 IPv6", "ok": public_ips["ipv6"]["ok"], "detail": public_ips["ipv6"]["value"] or public_ips["ipv6"]["source_label"]})
    return items


def _overview_payload() -> Dict[str, Any]:
    zone_context = _zone_context()
    public_ips = _public_ip_targets()
    routes = [_route_to_view(item, zone_context["items"], public_ips) for item in _load_routes()]
    docker_summary = _docker_runtime_summary()
    cert_count = sum(1 for route in routes if route["cert_exists"])
    https_active_count = sum(1 for route in routes if route["https_active"])
    env_items = _check_env_items(zone_context, public_ips)
    return {
        "summary": {
            "route_count": len(routes),
            "cert_count": cert_count,
            "https_active_count": https_active_count,
            "zone_available_count": len(zone_context["items"]),
            "panel_auth_enabled": _basic_auth_enabled(),
            **docker_summary,
        },
        "public_ips": public_ips,
        "env_items": env_items,
    }


def _project_dir(project_slug: str) -> Path:
    slug = str(project_slug or "").strip()
    if not PROJECT_SLUG_PATTERN.match(slug):
        raise ValueError("项目标识不合法，只允许字母数字、下划线和中划线")
    return SERVICES_DIR / slug


def _project_compose_file(project_slug: str) -> Path:
    return _project_dir(project_slug) / "docker-compose.yml"


def _project_meta_file(project_slug: str) -> Path:
    return _project_dir(project_slug) / "panel-meta.json"


def _load_project_meta(project_slug: str) -> Dict[str, Any]:
    meta_path = _project_meta_file(project_slug)
    if not meta_path.exists():
        return {}
    try:
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    return {}


def _save_project_meta(project_slug: str, metadata: Dict[str, Any]) -> None:
    _project_meta_file(project_slug).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def _list_projects() -> List[Dict[str, Any]]:
    _ensure_paths()
    items: List[Dict[str, Any]] = []
    for child in SERVICES_DIR.iterdir():
        if not child.is_dir():
            continue
        compose_file = child / "docker-compose.yml"
        if not compose_file.exists():
            continue
        items.append({
            "project_slug": child.name,
            "compose_file": str(compose_file),
            "updated_at": int(compose_file.stat().st_mtime),
            "meta": _load_project_meta(child.name),
        })
    items.sort(key=lambda item: item["project_slug"])
    return items


def _run_compose_command(command: List[str], cwd: Path | None = None) -> Tuple[bool, str]:
    try:
        result = subprocess.run(command, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=180, check=False)
    except Exception as exc:
        return _json_error(f"命令执行异常: {exc}")
    output = (result.stdout or "").strip()
    error_output = (result.stderr or "").strip()
    detail = "\n".join(part for part in [output, error_output] if part).strip()
    if result.returncode != 0:
        return _json_error(detail or "命令执行失败")
    return _json_ok(detail or "命令执行成功")


def _validate_compose_content(project_slug: str, compose_content: str) -> Tuple[bool, str]:
    project_dir = _project_dir(project_slug)
    project_dir.mkdir(parents=True, exist_ok=True)
    temp_file: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".yml", dir=project_dir, delete=False) as temp:
            temp.write(compose_content)
            temp_file = temp.name
        return _run_compose_command(["docker", "compose", "-f", temp_file, "config"], cwd=project_dir)
    finally:
        if temp_file:
            Path(temp_file).unlink(missing_ok=True)


def _save_project_compose(project_slug: str, compose_content: str, metadata: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    project_dir = _project_dir(project_slug)
    project_dir.mkdir(parents=True, exist_ok=True)
    compose_file = _project_compose_file(project_slug)
    compose_file.write_text(compose_content, encoding="utf-8")
    if metadata:
        _save_project_meta(project_slug, metadata)
    return True, f"Compose 文件已保存: {compose_file}", {"project_slug": project_slug, "compose_file": str(compose_file), "meta": _load_project_meta(project_slug)}


def _route_draft_from_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "service_name": str(metadata.get("primary_service_name", "")).strip(),
        "service_port": _parse_int(metadata.get("internal_port", 8000), 8000),
        "remark": str(metadata.get("project_slug", metadata.get("primary_service_name", ""))).strip(),
    }


def _legacy_flash_from_action(result: Dict[str, Any]) -> None:
    flash(result.get("message", "操作完成"), "success" if result.get("ok") else "error")
    for key in ["nginx", "dns", "cert"]:
        item = result.get(key)
        if isinstance(item, dict) and item.get("message"):
            flash(item["message"], "success" if item.get("ok") else "error")


def _save_route_action(payload: Dict[str, Any]) -> Dict[str, Any]:
    zone_context = _zone_context()
    zone_items = zone_context["items"]
    public_ips = _public_ip_targets()

    original_domain = _normalize_domain(payload.get("original_domain", ""))
    full_domain = _normalize_domain(payload.get("domain") or payload.get("full_domain") or "")
    zone_id, zone_name = _resolve_zone_selection(payload.get("zone_id"), payload.get("zone_name"), zone_items)
    dns_prefix = _normalize_dns_prefix(payload.get("dns_prefix", "@"))
    dns_record_type = str(payload.get("dns_record_type", "A")).strip().upper() or "A"
    dns_value = _normalize_cname_target(payload.get("dns_value", ""))
    service_name = str(payload.get("service_name", "")).strip()
    service_port = _parse_int(payload.get("service_port", 8000), 8000)
    remark = str(payload.get("remark", "")).strip()
    bind_dns = _to_bool(payload.get("bind_dns"), default=False)
    enable_https = _to_bool(payload.get("enable_https"), default=True)

    domain = _compose_domain(dns_prefix, zone_name, full_domain)
    ok, message = _validate_route(domain, service_name, service_port, dns_record_type, dns_value)
    if not ok:
        return {"ok": False, "message": message}

    _upsert_route(
        original_domain=original_domain,
        domain=domain,
        service_name=service_name,
        service_port=service_port,
        remark=remark,
        enable_https=enable_https,
        zone_id=zone_id,
        zone_name=zone_name,
        dns_prefix=dns_prefix,
        dns_record_type=dns_record_type,
        dns_value=dns_value,
    )

    https_ok, https_message = _set_route_https(domain, enable_https)
    if not https_ok:
        return {"ok": False, "message": https_message}

    reload_ok, reload_message = _reload_nginx()
    dns_result = None
    if bind_dns:
        dns_ok, dns_message = _cloudflare_upsert_dns_record(domain, zone_id, dns_record_type, dns_value)
        dns_result = {"ok": dns_ok, "message": dns_message}

    route = _find_route(domain) or {}
    route_view = _route_to_view(route, zone_items, public_ips) if route else {}
    return {"ok": True, "message": f"路由已保存: {domain} -> {service_name}:{service_port}", "route": route_view, "nginx": {"ok": reload_ok, "message": reload_message}, "dns": dns_result}


def _toggle_https_action(domain: str, enable_https: bool) -> Dict[str, Any]:
    ok, message = _set_route_https(domain, enable_https)
    if not ok:
        return {"ok": False, "message": message}
    reload_ok, reload_message = _reload_nginx()
    return {"ok": True, "message": message, "nginx": {"ok": reload_ok, "message": reload_message}}


def _delete_route_action(domain: str) -> Dict[str, Any]:
    deleted = _delete_route(domain)
    if not deleted:
        return {"ok": False, "message": f"未找到域名路由: {_normalize_domain(domain)}"}
    reload_ok, reload_message = _reload_nginx()
    return {"ok": True, "message": f"路由已删除: {_normalize_domain(domain)}", "nginx": {"ok": reload_ok, "message": reload_message}}


def _bind_dns_action(domain: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = payload or {}
    route = _find_route(domain) or {}
    zone_id = str(payload.get("zone_id") or route.get("zone_id") or CF_ZONE_ID or "").strip()
    dns_record_type = str(payload.get("dns_record_type") or route.get("dns_record_type") or "A").strip().upper()
    dns_value = _normalize_cname_target(payload.get("dns_value") or route.get("dns_value") or "")
    ok, message = _cloudflare_upsert_dns_record(_normalize_domain(domain), zone_id, dns_record_type, dns_value)
    return {"ok": ok, "message": message}


def _issue_cert_action(domain: str) -> Dict[str, Any]:
    cert_ok, cert_message = _issue_tls_cert(_normalize_domain(domain))
    if not cert_ok:
        return {"ok": False, "message": cert_message}
    routes = _load_routes()
    _rewrite_nginx_confs(routes)
    reload_ok, reload_message = _reload_nginx()
    return {"ok": True, "message": cert_message, "nginx": {"ok": reload_ok, "message": reload_message}}


def _init_payload() -> Dict[str, Any]:
    zone_context = _zone_context()
    public_ips = _public_ip_targets()
    routes = [_route_to_view(item, zone_context["items"], public_ips) for item in _load_routes()]
    return {
        "overview": _overview_payload(),
        "zones": zone_context,
        "routes": routes,
        "services": _discover_proxy_services(),
        "projects": _list_projects(),
        "defaults": {
            "proxy_network_name": PROXY_NETWORK_NAME,
            "nginx_container_name": NGINX_CONTAINER_NAME,
            "cert_agent_container_name": CERT_AGENT_CONTAINER_NAME,
            "panel_auth_enabled": _basic_auth_enabled(),
        },
    }


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
    return Response("需要认证", 401, {"WWW-Authenticate": 'Basic realm="Proxy Panel", charset="UTF-8"'})


@app.get("/")
def index() -> str:
    return render_template(
        "index.html",
        panel_auth_enabled=_basic_auth_enabled(),
        nginx_container_name=NGINX_CONTAINER_NAME,
        cert_agent_container_name=CERT_AGENT_CONTAINER_NAME,
        proxy_network_name=PROXY_NETWORK_NAME,
    )


@app.get("/api/init")
def api_init() -> Response:
    return _api_response(True, "初始化数据已加载", _init_payload())


@app.get("/api/monitor")
def api_monitor() -> Response:
    return _api_response(True, "监控数据已加载", {"monitor": _monitor_payload()})


@app.get("/api/zones")
def api_zones() -> Response:
    return _api_response(True, "Zone 数据已加载", {"zones": _zone_context()})


@app.get("/api/services")
def api_services() -> Response:
    return _api_response(True, "服务列表已加载", {"services": _discover_proxy_services()})


@app.get("/api/projects")
def api_projects() -> Response:
    return _api_response(True, "项目列表已加载", {"projects": _list_projects()})


@app.get("/api/projects/<project_slug>")
def api_project_detail(project_slug: str) -> Response:
    try:
        compose_file = _project_compose_file(project_slug)
    except ValueError as exc:
        return _api_response(False, str(exc), status=400)
    if not compose_file.exists():
        return _api_response(False, f"项目不存在: {project_slug}", status=404)
    return _api_response(True, "项目详情已加载", {"project": {"project_slug": project_slug, "compose_content": compose_file.read_text(encoding="utf-8"), "meta": _load_project_meta(project_slug)}})


@app.post("/api/projects/validate")
def api_project_validate() -> Response:
    payload = _request_data()
    project_slug = str(payload.get("project_slug", "")).strip()
    compose_content = str(payload.get("compose_content", "")).strip()
    if not compose_content:
        return _api_response(False, "Compose 内容不能为空", status=400)
    try:
        ok, message = _validate_compose_content(project_slug, compose_content)
    except ValueError as exc:
        return _api_response(False, str(exc), status=400)
    return _api_response(ok, message, status=200 if ok else 400)


@app.post("/api/projects/save")
def api_project_save() -> Response:
    payload = _request_data()
    project_slug = str(payload.get("project_slug", "")).strip()
    compose_content = str(payload.get("compose_content", "")).rstrip() + "\n"
    if not compose_content.strip():
        return _api_response(False, "Compose 内容不能为空", status=400)
    metadata = {
        "project_slug": project_slug,
        "primary_service_name": str(payload.get("primary_service_name", "")).strip(),
        "internal_port": _parse_int(payload.get("internal_port", 8000), 8000),
        "image": str(payload.get("image", "")).strip(),
        "container_name": str(payload.get("container_name", "")).strip(),
    }
    try:
        ok, message, data = _save_project_compose(project_slug, compose_content, metadata)
    except ValueError as exc:
        return _api_response(False, str(exc), status=400)
    return _api_response(ok, message, data)


@app.post("/api/projects/deploy")
def api_project_deploy() -> Response:
    payload = _request_data()
    project_slug = str(payload.get("project_slug", "")).strip()
    compose_content = str(payload.get("compose_content", "")).rstrip() + "\n"
    if not compose_content.strip():
        return _api_response(False, "Compose 内容不能为空", status=400)
    metadata = {
        "project_slug": project_slug,
        "primary_service_name": str(payload.get("primary_service_name", "")).strip(),
        "internal_port": _parse_int(payload.get("internal_port", 8000), 8000),
        "image": str(payload.get("image", "")).strip(),
        "container_name": str(payload.get("container_name", "")).strip(),
    }
    try:
        validate_ok, validate_message = _validate_compose_content(project_slug, compose_content)
        if not validate_ok:
            return _api_response(False, validate_message, status=400)
        _, _, save_data = _save_project_compose(project_slug, compose_content, metadata)
        project_dir = _project_dir(project_slug)
        compose_file = _project_compose_file(project_slug)
    except ValueError as exc:
        return _api_response(False, str(exc), status=400)
    deploy_ok, deploy_message = _run_compose_command(["docker", "compose", "-p", project_slug, "-f", str(compose_file), "up", "-d"], cwd=project_dir)
    if not deploy_ok:
        return _api_response(False, deploy_message, {"save": save_data}, status=400)
    return _api_response(True, "项目部署成功", {"save": save_data, "deploy": {"ok": True, "message": deploy_message}, "route_draft": _route_draft_from_metadata(metadata)})


@app.post("/api/routes")
def api_save_route() -> Response:
    result = _save_route_action(_request_data())
    status = 200 if result.get("ok") else 400
    return _api_response(bool(result.get("ok")), str(result.get("message", "")), result, status=status)


@app.post("/api/routes/<path:domain>/https")
def api_route_https(domain: str) -> Response:
    result = _toggle_https_action(domain, _to_bool(_request_data().get("enable_https"), default=False))
    status = 200 if result.get("ok") else 400
    return _api_response(bool(result.get("ok")), str(result.get("message", "")), result, status=status)


@app.delete("/api/routes/<path:domain>")
def api_delete_route(domain: str) -> Response:
    result = _delete_route_action(domain)
    status = 200 if result.get("ok") else 404
    return _api_response(bool(result.get("ok")), str(result.get("message", "")), result, status=status)


@app.post("/api/routes/<path:domain>/dns")
def api_bind_dns(domain: str) -> Response:
    result = _bind_dns_action(domain, _request_data())
    status = 200 if result.get("ok") else 400
    return _api_response(bool(result.get("ok")), str(result.get("message", "")), result, status=status)


@app.post("/api/routes/<path:domain>/cert")
def api_issue_cert(domain: str) -> Response:
    result = _issue_cert_action(domain)
    status = 200 if result.get("ok") else 400
    return _api_response(bool(result.get("ok")), str(result.get("message", "")), result, status=status)


@app.post("/api/nginx/reload")
def api_reload_nginx() -> Response:
    ok, message = _reload_nginx()
    return _api_response(ok, message, {"nginx": {"ok": ok, "message": message}}, status=200 if ok else 400)


@app.post("/route/add")
def add_route() -> Response:
    result = _save_route_action(_request_data())
    _legacy_flash_from_action(result)
    return redirect(url_for("index"))


@app.post("/route/https")
def route_https() -> Response:
    payload = _request_data()
    domain = _normalize_domain(payload.get("domain", ""))
    if not domain:
        flash("缺少域名参数", "error")
        return redirect(url_for("index"))
    result = _toggle_https_action(domain, _to_bool(payload.get("enable_https"), default=False))
    _legacy_flash_from_action(result)
    return redirect(url_for("index"))


@app.post("/route/delete")
def delete_route() -> Response:
    domain = _normalize_domain(request.form.get("domain", ""))
    if not domain:
        flash("缺少要删除的域名", "error")
        return redirect(url_for("index"))
    result = _delete_route_action(domain)
    _legacy_flash_from_action(result)
    return redirect(url_for("index"))


@app.post("/dns/bind")
def bind_dns() -> Response:
    domain = _normalize_domain(request.form.get("domain", ""))
    if not domain:
        flash("缺少域名参数", "error")
        return redirect(url_for("index"))
    result = _bind_dns_action(domain)
    _legacy_flash_from_action(result)
    return redirect(url_for("index"))


@app.post("/cert/issue")
def issue_cert() -> Response:
    domain = _normalize_domain(request.form.get("domain", ""))
    if not domain:
        flash("缺少域名参数", "error")
        return redirect(url_for("index"))
    result = _issue_cert_action(domain)
    _legacy_flash_from_action(result)
    return redirect(url_for("index"))


@app.post("/nginx/reload")
def reload_nginx() -> Response:
    ok, message = _reload_nginx()
    flash(message, "success" if ok else "error")
    return redirect(url_for("index"))


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    return {"ok": True, "service": "proxy-panel"}


if __name__ == "__main__":
    _ensure_paths()
    app.run(host="0.0.0.0", port=18080, debug=False)
