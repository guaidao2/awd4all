"""自定义插件示例。

把这个文件复制到运行目录的 plugins/ 下，改改就能用，重启程序后在左侧插件列表里出现。
插件文件之间互不影响，写坏了只会让那一个插件不加载，主程序照跑。
"""

from __future__ import annotations

from typing import Any

import urllib.request

from awd4all.plugins.base import Finding, Param, Plugin, find_flags
from awd4all.targets import Target


class DemoProbe(Plugin):
    name = "demo_probe"
    title = "示例：HTTP 探测"
    description = "对目标发一个 GET 请求，响应里出现 flag 就收走。拿它当模板改。"
    default_port = 8080
    params = [
        Param("path", "请求路径", "text", "/flag", required=True, help="以 / 开头"),
        Param("flag_regex", "自定义 flag 正则(可选)", "text", ""),
        Param("timeout", "超时(秒)", "float", 5),
    ]

    def run(self, target: Target, params: dict[str, Any], emit) -> list[Finding] | None:
        port = target.port or self.default_port
        url = f"http://{target.host}:{port}{params.get('path') or '/'}"
        emit("info", f"GET {url}")
        try:
            with urllib.request.urlopen(url, timeout=float(params.get("timeout") or 5)) as response:
                body = response.read(2 * 1024 * 1024).decode("utf-8", "replace")
                status = response.status
        except Exception as exc:
            emit("warn", f"请求失败: {exc}")
            return None

        emit("info", f"HTTP {status}，{len(body)} 字节")
        regexes = [params["flag_regex"]] if params.get("flag_regex") else []
        flags = find_flags(body, regexes)
        if not flags:
            return None
        emit("ok", f"拿到 flag: {flags[0]}")
        return [
            Finding(
                target=target.label,
                plugin=self.name,
                flag=flag,
                evidence=body[:1000],
                extra={"status": status, "url": url},
            )
            for flag in flags
        ]
