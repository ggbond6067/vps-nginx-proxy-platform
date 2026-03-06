#!/usr/bin/env python3
"""Cloudflare DNS 绑定脚本（中文交互）。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Dict

import requests


def load_env_file(env_path: Path) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not env_path.exists():
        return result
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="更新或创建 Cloudflare DNS A 记录")
    parser.add_argument("--domain", required=True, help="要绑定的域名，例如 api.example.com")
    parser.add_argument("--ip", default="", help="公网 IP（为空时从 .env 的 VPS_PUBLIC_IP 读取）")
    parser.add_argument("--zone-id", default="", help="Cloudflare Zone ID（为空时从 .env 读取）")
    parser.add_argument("--token", default="", help="Cloudflare API Token（为空时从 .env 读取）")
    parser.add_argument("--proxied", default="", help="是否代理 true/false（为空时从 .env 读取）")
    parser.add_argument("--ttl", default="", help="TTL，1 表示自动（为空时从 .env 读取）")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    env = load_env_file(project_root / ".env")

    domain = args.domain.strip().lower()
    ip = (args.ip or env.get("VPS_PUBLIC_IP", "")).strip()
    zone_id = (args.zone_id or env.get("CF_ZONE_ID", "")).strip()
    token = (args.token or env.get("CF_API_TOKEN", "")).strip()
    proxied_raw = (args.proxied or env.get("CF_PROXIED", "true")).strip().lower()
    ttl_raw = (args.ttl or env.get("CF_TTL", "1")).strip()

    proxied = proxied_raw == "true"
    ttl = int(ttl_raw or "1")

    if not token:
        print("失败: 缺少 Cloudflare Token")
        return 2
    if not zone_id:
        print("失败: 缺少 Zone ID")
        return 2
    if not ip:
        print("失败: 缺少 VPS 公网 IP")
        return 2

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    base_url = "https://api.cloudflare.com/client/v4"
    params = {"type": "A", "name": domain, "page": 1, "per_page": 1}
    payload = {"type": "A", "name": domain, "content": ip, "ttl": ttl, "proxied": proxied}

    try:
        query_resp = requests.get(
            f"{base_url}/zones/{zone_id}/dns_records",
            headers=headers,
            params=params,
            timeout=15,
        )
        query_data = query_resp.json()
        if not query_data.get("success", False):
            print(f"失败: 查询 DNS 记录失败: {query_data.get('errors')}")
            return 1
        records = query_data.get("result") or []

        if records:
            record_id = records[0]["id"]
            update_resp = requests.put(
                f"{base_url}/zones/{zone_id}/dns_records/{record_id}",
                headers=headers,
                json=payload,
                timeout=15,
            )
            update_data = update_resp.json()
            if not update_data.get("success", False):
                print(f"失败: 更新 DNS 失败: {update_data.get('errors')}")
                return 1
            print(f"成功: 已更新 DNS -> {domain} = {ip}")
            return 0

        create_resp = requests.post(
            f"{base_url}/zones/{zone_id}/dns_records",
            headers=headers,
            json=payload,
            timeout=15,
        )
        create_data = create_resp.json()
        if not create_data.get("success", False):
            print(f"失败: 创建 DNS 失败: {create_data.get('errors')}")
            return 1
        print(f"成功: 已创建 DNS -> {domain} = {ip}")
        return 0
    except Exception as exc:
        print(f"失败: Cloudflare API 调用异常: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
