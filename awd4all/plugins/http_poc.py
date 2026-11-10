"""HTTP 原始数据包 / curl 命令重放。

把 Burp 里的 raw 请求整段粘进来，或者直接粘一条 curl 命令，工具自己识别格式，
把 Host 换成目标后批量重放，从响应里抠 flag。占位符在发包前替换：

    {{ip}} / {{host}}   目标 IP
    {{port}}            目标端口
    {{team}}            目标标签

raw 示例：

    POST /api/login HTTP/1.1
    Host: 127.0.0.1
    Content-Type: application/json

    {"user":"admin"}

curl 示例：

    curl -X POST 'http://127.0.0.1:8080/api/login' \\
      -H 'Content-Type: application/json' \\
      -d '{"user":"admin"}'
"""

from __future__ import annotations

import base64
import re
import shlex
import socket
import ssl
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ..targets import Target
from .base import Emit, Finding, Param, Plugin, find_flags, read_text_param

DEFAULT_PORT = 80

# curl 里需要吞掉一个参数的选项
_CURL_VALUE_OPTIONS = {
    "-X", "--request",
    "-H", "--header",
    "-d", "--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode",
    "-b", "--cookie",
    "-u", "--user",
    "-A", "--user-agent",
    "-e", "--referer",
    "--url",
    "--max-time", "--connect-timeout",
    "-o", "--output",
    "-c", "--cookie-jar",
    "-x", "--proxy",
    "--resolve", "--interface",
    "-T", "--upload-file",
    "--cert", "--key", "--cacert",
    "-F", "--form",
}

# curl 里不带参数的开关
_CURL_FLAG_OPTIONS = {
    "-k", "--insecure",
    "-s", "--silent", "-S", "--show-error",
    "-v", "--verbose",
    "-i", "--include",
    "-L", "--location",
    "--compressed",
    "-N", "--no-buffer",
    "--http1.1", "--http2", "--http2-prior-knowledge",
    "-G", "--get",
}


@dataclass
class ParsedRequest:
    method: str = "GET"
    path: str = "/"
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: str = ""
    scheme: str | None = None
    port: int | None = None
    source: str = "raw"


# ---------------------------------------------------------------- 解析


def render_placeholders(text: str, target: Target) -> str:
    replacements = {
        "{{ip}}": target.host,
        "{{host}}": target.host,
        "{{port}}": str(target.port or ""),
        "{{team}}": target.team or "",
    }
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


def _split_url(url: str) -> tuple[str, str, str, int | None]:
    """返回 (scheme, host, path_with_query, port)。"""
    if "://" not in url:
        url = "http://" + url
    parts = urlsplit(url)
    scheme = (parts.scheme or "http").lower()
    host = parts.hostname or ""
    if not host:
        raise ValueError(f"URL 里没有主机: {url}")
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return scheme, host, path, parts.port


def _host_header(host: str, port: int | None, scheme: str) -> str:
    default_port = 443 if scheme == "https" else 80
    if port and port != default_port:
        return f"{host}:{port}"
    return host


def parse_curl(text: str) -> ParsedRequest:
    normalized = re.sub(r"\\\r?\n", " ", text)   # bash 续行
    normalized = re.sub(r"\^\r?\n", " ", normalized)  # cmd 续行
    try:
        tokens = shlex.split(normalized, posix=True)
    except ValueError as exc:
        raise ValueError(f"curl 命令解析失败: {exc}") from exc

    method: str | None = None
    headers: list[tuple[str, str]] = []
    body_parts: list[str] = []
    cookies: list[str] = []
    basic: str | None = None
    get_mode = False
    url: str | None = None

    index = 1
    while index < len(tokens):
        token = tokens[index]

        if token.startswith("--") and "=" in token:
            key, _, value = token.partition("=")
            if key in ("--data", "--data-raw", "--data-binary", "--data-urlencode"):
                body_parts.append(value)
            elif key == "--header":
                headers.append(_split_header(value))
            elif key == "--request":
                method = value
            elif key == "--url":
                url = value
            index += 1
            continue

        if token in _CURL_VALUE_OPTIONS:
            if index + 1 >= len(tokens):
                raise ValueError(f"{token} 后面缺参数")
            value = tokens[index + 1]
            if token in ("-X", "--request"):
                method = value
            elif token in ("-H", "--header"):
                headers.append(_split_header(value))
            elif token in ("-d", "--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode"):
                body_parts.append(value)
            elif token in ("-b", "--cookie"):
                cookies.append(value)
            elif token in ("-u", "--user"):
                basic = value
            elif token in ("-A", "--user-agent"):
                headers.append(("User-Agent", value))
            elif token in ("-e", "--referer"):
                headers.append(("Referer", value))
            elif token in ("--url",):
                url = value
            elif token in ("-F", "--form"):
                raise ValueError("暂不支持 -F 表单，改成 -d 或直接粘原始数据包")
            index += 2
            continue

        if token in ("-G", "--get"):
            get_mode = True
            index += 1
            continue

        if token in _CURL_FLAG_OPTIONS:
            index += 1
            continue

        if token.startswith("-"):
            # 不认识的选项：有参数就一起跳过，没有就只跳自己
            if index + 1 < len(tokens) and not tokens[index + 1].startswith("-"):
                index += 2
            else:
                index += 1
            continue

        url = url or token
        index += 1

    if not url:
        raise ValueError("curl 命令里没找到 URL")

    scheme, host, path, port = _split_url(url)
    body = "&".join(body_parts)
    if get_mode and body:
        path = f"{path}{'&' if '?' in path else '?'}{body}"
        body = ""

    if cookies:
        headers.append(("Cookie", "; ".join(cookies)))
    if basic:
        encoded = base64.b64encode(basic.encode()).decode()
        headers.append(("Authorization", f"Basic {encoded}"))
    if not any(name.lower() == "host" for name, _ in headers):
        headers.append(("Host", _host_header(host, port, scheme)))

    return ParsedRequest(
        method=(method or ("POST" if body else "GET")).upper(),
        path=path,
        headers=headers,
        body=body,
        scheme=scheme,
        port=port,
        source="curl",
    )


def _split_header(value: str) -> tuple[str, str]:
    name, sep, content = value.partition(":")
    if not sep:
        raise ValueError(f"请求头格式不对: {value}")
    return name.strip(), content.strip()


def parse_raw(text: str) -> ParsedRequest:
    if "\r\n\r\n" in text:
        head, _, body = text.partition("\r\n\r\n")
    elif "\n\n" in text:
        head, _, body = text.partition("\n\n")
    else:
        head, body = text, ""
    lines = head.splitlines()
    if not lines:
        raise ValueError("数据包是空的")
    parts = lines[0].split(" ", 2)
    if len(parts) < 2:
        raise ValueError("请求行解析失败，需要形如 GET /path HTTP/1.1")
    method, path = parts[0].upper(), parts[1]
    if not re.match(r"^[A-Z]{3,10}$", method) or not (
        path.startswith("/") or path == "*" or re.match(r"(?i)^https?://", path)
    ):
        raise ValueError(f"请求行不像 HTTP 请求: {lines[0]}")

    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        headers.append(_split_header(line))
    port: int | None = None
    for name, value in headers:
        if name.lower() == "host":
            _, _, maybe_port = value.rpartition(":")
            if maybe_port.isdigit():
                port = int(maybe_port)
    return ParsedRequest(
        method=method, path=path, headers=headers, body=body, port=port, source="raw"
    )


def parse_url(url: str) -> ParsedRequest:
    scheme, host, path, port = _split_url(url)
    return ParsedRequest(
        method="GET",
        path=path,
        headers=[("Host", _host_header(host, port, scheme))],
        scheme=scheme,
        port=port,
        source="url",
    )


def parse_request(text: str) -> ParsedRequest:
    stripped = text.strip()
    if not stripped:
        raise ValueError("请求内容为空")
    if re.match(r"(?i)^curl(?:\.exe)?(?:\s|$)", stripped):
        return parse_curl(stripped)
    if re.match(r"(?i)^https?://", stripped):
        return parse_url(stripped)
    return parse_raw(stripped)


# ---------------------------------------------------------------- 构造


def build_request(
    parsed: ParsedRequest,
    target: Target,
    scheme: str = "auto",
    replace_host: bool = True,
    port: int | None = None,
) -> tuple[bytes, bool]:
    """返回 (要发送的字节, 是否用 TLS)。

    port 是真正要连接的端口（目标写了端口就是目标的，没写就用请求自带的），
    只影响 Host 头里带不带端口号。
    """
    final_scheme = scheme if scheme in ("http", "https") else (parsed.scheme or "http")
    use_tls = final_scheme == "https"

    headers = list(parsed.headers)
    effective_port = target.port if target.port is not None else port
    default_port = 443 if use_tls else 80
    host_value = target.host
    if effective_port and effective_port != default_port:
        host_value = f"{target.host}:{effective_port}"
    replaced = False
    for index, (name, _) in enumerate(headers):
        if name.lower() == "host":
            if replace_host:
                headers[index] = (name, host_value)
            replaced = True
    if not replaced:
        headers.append(("Host", host_value))

    lines = [f"{parsed.method} {parsed.path} HTTP/1.1"]
    for name, value in headers:
        if name.lower() == "content-length":
            continue
        lines.append(f"{name}: {value}")
    payload = parsed.body.encode("utf-8", "replace")
    lines.append(f"Content-Length: {len(payload)}")
    lines.append("Connection: close")
    head = "\r\n".join(lines) + "\r\n\r\n"
    return head.encode("utf-8", "replace") + payload, use_tls


# ---------------------------------------------------------------- 发送


def _recv_all(sock: socket.socket, timeout: float, limit: int = 4 * 1024 * 1024) -> bytes:
    chunks: list[bytes] = []
    total = 0
    sock.settimeout(timeout)
    while total < limit:
        try:
            chunk = sock.recv(65536)
        except (socket.timeout, TimeoutError):
            break
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


class HttpPoc(Plugin):
    name = "http_poc"
    title = "HTTP 数据包 / curl 重放"
    description = "粘贴 raw 请求或 curl 命令，批量重放，正则抠 flag。"
    default_port = DEFAULT_PORT
    params = [
        Param("request", "请求内容（raw 或 curl）", "textarea", "",
              help="整段粘进来即可，两种格式自动识别，支持 {{ip}} / {{port}} 占位符", rows=8),
        Param("request_file", "或从文件读（可选）", "file", "",
              help="路径相对当前目录，填了文件就忽略上面的粘贴内容"),
        Param("scheme", "协议", "select", "auto", options=["auto", "http", "https"],
              help="auto 时按 URL 或端口判断"),
        Param("replace_host", "自动把 Host 换成目标", "bool", True,
              help="关掉就保留原始 Host，适合需要指定虚拟主机的场景"),
        Param("timeout", "读响应超时(秒)", "float", 10),
        Param("flag_regex", "flag 标记或正则(可选)", "text", "",
              help="填 flag{ 或 flag 就行，会自动补全；要精确控制再写完整正则"),
    ]

    def run(self, target: Target, params: dict[str, Any], emit: Emit) -> list[Finding] | None:
        text = (params.get("request") or "").strip()
        if not text and params.get("request_file"):
            text = read_text_param(params["request_file"], "数据包").strip()
        if not text:
            emit("error", "没有请求内容，粘贴 raw 或 curl 都行")
            return None

        text = render_placeholders(text, target)
        try:
            parsed = parse_request(text)
            scheme = params.get("scheme") or "auto"
            final_scheme = scheme if scheme in ("http", "https") else (parsed.scheme or "http")
            # 目标写了端口就用目标的；没写就用请求里自带的；都没有才回默认
            port = target.port or parsed.port or (
                443 if final_scheme == "https" else self.default_port
            )
            payload, use_tls = build_request(
                parsed,
                target,
                scheme=scheme,
                replace_host=bool(params.get("replace_host", True)),
                port=port,
            )
        except ValueError as exc:
            emit("error", f"解析失败: {exc}")
            return None

        timeout = float(params.get("timeout") or 10)
        extra_regex = [params["flag_regex"]] if params.get("flag_regex") else []

        emit("info", f"{parsed.source} 请求: {parsed.method} {parsed.path} -> {target.host}:{port}")

        sock = socket.create_connection((target.host, port), timeout=timeout)
        try:
            if use_tls:
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                sock = context.wrap_socket(sock, server_hostname=target.host)
            sock.sendall(payload)
            response = _recv_all(sock, timeout)
        finally:
            try:
                sock.close()
            except OSError:
                pass

        text = response.decode("utf-8", "replace")
        status = text.split("\r\n", 1)[0].strip() or "无响应"
        emit("info", f"-> {status} ({len(response)} 字节)")

        flags = find_flags(text, extra_regex)
        if not flags:
            return None
        emit("ok", f"拿到 {len(flags)} 个 flag: {flags[0]}")
        return [
            Finding(
                target=target.label,
                plugin=self.name,
                flag=flag,
                evidence=text[:2000],
                extra={
                    "status": status,
                    "method": parsed.method,
                    "path": parsed.path,
                    "source": parsed.source,
                },
            )
            for flag in flags
        ]
