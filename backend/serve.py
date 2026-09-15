"""ChargeFlow 后端启动入口（Windows 兼容）。

为什么不用 `python -m uvicorn app.main:app`：
uvicorn 在 win32 强制 ProactorEventLoop，而 aiomqtt（MQTT 网关/桩通信）依赖
loop.add_reader，仅 SelectorEventLoop 支持。本入口自建 Selector 循环后再交给
uvicorn.Server.serve()，行为与其余平台一致。

用法：python serve.py [--port 8000]
"""

import argparse
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    config = uvicorn.Config(
        "app.main:app", host=args.host, port=args.port, log_level="info"
    )
    server = uvicorn.Server(config)
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
