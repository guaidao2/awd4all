"""插件基类与公共工具。

想加一个新打法，只需要在 awd4all/plugins/ 下（或者运行目录的 plugins/ 下）
新建一个 .py 文件，写一个继承 Plugin 的类，声明参数和 run() 就完事，不用动主程序。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..targets import Target

# 常见 flag 形态，插件里可以追加自己的正则
DEFAULT_FLAG_REGEXES = [
    r"[A-Za-z0-9_]{2,32}\{[^{}\s]{4,200}\}",
]

# emit(level, message)，level 取 info / ok / warn / error / debug
Emit = Callable[[str, str], None]


@dataclass
class Param:
    key: str
    label: str
    type: str = "text"  # text | textarea | password | int | float | bool | file | select
    default: Any = ""
    required: bool = False
    help: str = ""
    options: list[str] = field(default_factory=list)
    rows: int = 5

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "default": self.default,
            "required": self.required,
            "help": self.help,
        }
        if self.options:
            data["options"] = self.options
        if self.type == "textarea":
            data["rows"] = self.rows
        return data


@dataclass
class Finding:
    target: str
    plugin: str
    flag: str = ""
    evidence: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "plugin": self.plugin,
            "flag": self.flag,
            "evidence": self.evidence,
            "extra": self.extra,
        }


class Plugin:
    """所有打法插件的基类。"""

    name: str = ""
    title: str = ""
    description: str = ""
    default_port: int | None = None
    params: list[Param] = []

    def run(self, target: Target, params: dict[str, Any], emit: Emit) -> list[Finding] | None:
        raise NotImplementedError

    @classmethod
    def meta(cls) -> dict[str, Any]:
        return {
            "name": cls.name,
            "title": cls.title,
            "description": cls.description,
            "default_port": cls.default_port,
            "params": [param.to_dict() for param in cls.params],
        }


_PREFIX_PATTERN = re.compile(r"^([A-Za-z0-9_\-]+)\{?(?:\.\*\}?)?$")


def normalize_flag_pattern(value: str) -> str | None:
    """把用户填的 flag 标记/正则统一成可用的正则。

    允许只填前缀，比如 ``flag{``、``flag``、``CTF{``，也允许 ``flag{.*}`` 这种
    半正则写法，这些都会被补成 ``前缀\\{[^{}\\s]{1,300}\\}``。
    真正写了正则（带 ``\\``、``[``、``(`` 等）的原样返回。
    """
    value = (value or "").strip()
    if not value:
        return None
    matched = _PREFIX_PATTERN.match(value)
    if matched:
        return re.escape(matched.group(1)) + r"\{[^{}\s]{1,300}\}"
    return value


def find_flags(text: str, extra_regexes: list[str] | None = None) -> list[str]:
    """从文本里抠出 flag，按出现顺序去重。"""
    if not text:
        return []
    regexes = list(DEFAULT_FLAG_REGEXES)
    for raw in extra_regexes or []:
        normalized = normalize_flag_pattern(raw)
        if normalized:
            regexes.append(normalized)
    seen: set[str] = set()
    found: list[str] = []
    for pattern in regexes:
        try:
            matches = re.findall(pattern, text)
        except re.error:
            continue
        for match in matches:
            if match not in seen:
                seen.add(match)
                found.append(match)
    return found


def read_text_param(path_value: str, what: str) -> str:
    """读取用户指定的落盘文件，路径可以是相对当前目录的。"""
    if not path_value:
        raise ValueError(f"没有指定{what}文件")
    path = Path(path_value).expanduser()
    if not path.is_file():
        raise ValueError(f"{what}文件不存在: {path_value}")
    return path.read_text(encoding="utf-8", errors="ignore")


def parse_credentials(text: str) -> list[tuple[str, str]]:
    """解析账号字典，每行 user:pass 或 user pass，忽略空行和 # 注释。"""
    creds: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            user, _, password = line.partition(":")
        else:
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            user, password = parts
        pair = (user.strip(), password.strip())
        if pair and pair not in seen:
            seen.add(pair)
            creds.append(pair)
    return creds
