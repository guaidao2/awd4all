"""插件注册表：内置插件 + 运行目录 plugins/ 下的自定义插件。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from .base import Finding, Param, Plugin  # noqa: F401  (给外部插件复用)
from .http_poc import HttpPoc
from .ssh_brute import SshBrute
from .telnet_brute import TelnetBrute

BUILTIN: list[type[Plugin]] = [TelnetBrute, SshBrute, HttpPoc]


def _load_module(path: Path) -> Any | None:
    module_name = f"awd4all_plugin_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # 坏插件不能把整个程序带崩
        return None
    return module


def load_plugins(extra_dirs: list[str] | None = None) -> dict[str, type[Plugin]]:
    plugins: dict[str, type[Plugin]] = {cls.name: cls for cls in BUILTIN}
    directories = [Path.cwd() / "plugins"]
    directories += [Path(item) for item in (extra_dirs or [])]
    for directory in directories:
        if not directory.is_dir():
            continue
        for file in sorted(directory.glob("*.py")):
            if file.name.startswith("_"):
                continue
            module = _load_module(file)
            if module is None:
                continue
            for value in vars(module).values():
                if (
                    isinstance(value, type)
                    and issubclass(value, Plugin)
                    and value is not Plugin
                    and getattr(value, "name", "")
                ):
                    plugins[value.name] = value
    return plugins
