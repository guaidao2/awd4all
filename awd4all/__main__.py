"""命令行入口：python -m awd4all / awd4all。"""

from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn

from .server import app
from .version import version as __version__


def main() -> None:
    parser = argparse.ArgumentParser(prog="awd4all", description="AWD 批量打点工作台")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认只对本机开放")
    parser.add_argument("--port", type=int, default=8737, help="监听端口")
    parser.add_argument("--no-browser", action="store_true", help="启动后不要自动打开浏览器")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print(f"awd4all v{__version__} 已启动 -> {url}")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
