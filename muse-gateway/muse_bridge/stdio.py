"""MCP stdio transport for local agent clients; no exposed HTTP port."""
import asyncio
import json
import sys

from .config import Config
from .protocol import handle_rpc
from .service import BridgeService


async def run() -> None:
    config = Config.from_env(require_bridge_key=False)
    service = BridgeService(config)
    try:
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break
            try:
                message = json.loads(line)
                response = await handle_rpc(service, message)
            except Exception:
                response = {"jsonrpc": "2.0", "id": None,
                            "error": {"code": -32700, "message": "Invalid request"}}
            if response is not None:
                # MCP stdio: each protocol message is a single JSON-encoded line.
                print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)
    finally:
        await service.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
