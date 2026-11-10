"""目标范围解析。

把用户随手写的各种 IP 表达展开成 (host, port) 列表。支持的写法：

    10.0.0.1                     单个
    10.0.0.0/24                  CIDR
    10.0.0.1-10.0.0.50           完整区间
    10.0.0.1-50                  末段区间
    10.0.0.*                     通配（末段 1-254，中间段 0-255）
    10.0.0.1:22                  带端口
    10.0.0.0/24:8080             CIDR 带端口
    [::1]:22                     IPv6 带端口
    @targets.txt                 从文件读（文件内可混用以上任何写法）
    host-a host-b, host-c        列表（空白、逗号、分号、换行都行）
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path

SEPARATORS = re.compile(r"[\s,;]+")
MAX_TARGETS = 200_000


class TargetSpecError(ValueError):
    """用户输入的目标范围无法解析。"""


@dataclass(frozen=True)
class Target:
    host: str
    port: int | None = None
    team: str = ""

    @property
    def label(self) -> str:
        if self.port is None:
            return self.host
        if ":" in self.host:  # IPv6
            return f"[{self.host}]:{self.port}"
        return f"{self.host}:{self.port}"


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def _looks_like_ipv6(text: str) -> bool:
    if "::" in text:
        return True
    return text.count(":") >= 3


def _split_port(token: str) -> tuple[str, int | None]:
    if token.startswith("["):
        end = token.find("]")
        if end > 0:
            host = token[1:end]
            rest = token[end + 1:]
            if rest.startswith(":") and rest[1:].isdigit():
                return host, int(rest[1:])
            return host, None
        return token, None
    if token.count(":") == 1 and not _looks_like_ipv6(token):
        host, maybe_port = token.split(":")
        if maybe_port.isdigit():
            return host, int(maybe_port)
    return token, None


def _expand_wildcard(expr: str) -> list[str]:
    parts = expr.split(".")
    if len(parts) != 4 or "*" not in parts:
        raise TargetSpecError(f"无法解析的通配写法: {expr}")
    spans = []
    for part in parts:
        if part == "*":
            spans.append(range(1, 255))
        else:
            if not part.isdigit() or not 0 <= int(part) <= 255:
                raise TargetSpecError(f"无法解析的通配写法: {expr}")
            spans.append(range(int(part), int(part) + 1))
    return [f"{a}.{b}.{c}.{d}" for a in spans[0] for b in spans[1]
            for c in spans[2] for d in spans[3]]


def _expand_range(expr: str) -> list[str]:
    low, _, high = expr.partition("-")
    low, high = low.strip(), high.strip()
    if not low or not high:
        raise TargetSpecError(f"无法解析的区间: {expr}")
    if _is_ip(low) and _is_ip(high):
        start = int(ipaddress.ip_address(low))
        stop = int(ipaddress.ip_address(high))
        cls = ipaddress.ip_address(low).__class__
        if stop < start:
            start, stop = stop, start
        return [str(cls(i)) for i in range(start, stop + 1)]
    if _is_ip(low) and high.isdigit() and "." in low:
        head, tail = low.rsplit(".", 1)
        if not tail.isdigit():
            raise TargetSpecError(f"无法解析的区间: {expr}")
        start, stop = int(tail), int(high)
        if stop < start:
            start, stop = stop, start
        if stop > 255:
            raise TargetSpecError(f"末段区间超出 255: {expr}")
        return [f"{head}.{i}" for i in range(start, stop + 1)]
    raise TargetSpecError(f"无法解析的区间: {expr}")


def _expand_host(host: str) -> list[str]:
    if "/" in host:
        try:
            network = ipaddress.ip_network(host, strict=False)
        except ValueError as exc:
            raise TargetSpecError(f"无法解析的网段: {host}") from exc
        return [str(ip) for ip in network.hosts()] or [str(network.network_address)]
    if "*" in host:
        return _expand_wildcard(host)
    if "-" in host and host.count("-") == 1:
        return _expand_range(host)
    return [host]


def parse_targets(
    spec: str,
    default_port: int | None = None,
    max_targets: int = MAX_TARGETS,
) -> list[Target]:
    """解析一段目标描述，按输入顺序去重后返回。"""
    text = (spec or "").strip()
    if not text:
        return []

    # 支持 # 注释，@文件里写说明比较方便
    text = "\n".join(line.split("#", 1)[0] for line in text.splitlines()).strip()
    if not text:
        return []

    targets: list[Target] = []
    seen: set[tuple[str, int | None]] = set()

    def add(host: str, port: int | None) -> None:
        key = (host, port)
        if key in seen:
            return
        seen.add(key)
        targets.append(Target(host, port))
        if len(targets) > max_targets:
            raise TargetSpecError(
                f"展开后目标超过 {max_targets} 个，请缩小范围"
            )

    for token in SEPARATORS.split(text):
        token = token.strip()
        if not token:
            continue
        if token.startswith("@"):
            path = Path(token[1:])
            if not path.is_file():
                raise TargetSpecError(f"目标文件不存在: {path}")
            nested = parse_targets(
                path.read_text(encoding="utf-8", errors="ignore"),
                default_port,
                max_targets,
            )
            for target in nested:
                add(target.host, target.port)
            continue
        host, port = _split_port(token)
        port = port if port is not None else default_port
        for expanded in _expand_host(host):
            add(expanded, port)
    return targets
