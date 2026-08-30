"""draw-portal internal API 可信来源判定（loopback + 内网 CIDR）。"""
from __future__ import annotations

import ipaddress


def parse_internal_allow_cidrs(raw: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    out: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for part in raw.split(","):
        text = part.strip()
        if not text:
            continue
        out.append(ipaddress.ip_network(text, strict=False))
    return tuple(out)


def is_loopback_host(host: str) -> bool:
    if host in {"127.0.0.1", "::1", "localhost"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_trusted_internal_client(host: str, *, allow_cidrs: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]) -> bool:
    if is_loopback_host(host):
        return True
    if not allow_cidrs:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in allow_cidrs)
