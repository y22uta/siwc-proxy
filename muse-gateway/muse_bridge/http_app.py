"""Authenticated stateless Streamable-HTTP MCP endpoint and optional JSON REST façade."""
from __future__ import annotations
from contextlib import asynccontextmanager
import hmac
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from .config import Config
from .protocol import handle_rpc, VERSION, SUPPORTED
from .service import BridgeService, BridgeError

logger = logging.getLogger(__name__)


def create_app(config: Config | None = None, service: BridgeService | None = None) -> FastAPI:
    config = config or Config.from_env()
    if service is None:
        service = BridgeService(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await service.close()

    app = FastAPI(title="Muse SIWC Bridge", version="1.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.bridge = service

    def authorize(request: Request) -> None:
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in config.allowed_origins:
            raise HTTPException(403, "Origin not in allowlist")
        token = request.headers.get("authorization", "")
        expected = f"Bearer {config.bridge_api_key}"
        if not token.startswith("Bearer ") or not hmac.compare_digest(token, expected):
            raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})

    @app.get("/healthz")
    async def healthz() -> dict:
        # Intentionally does not reveal OAuth, models, account details or limits.
        return {"ok": True, "service": "muse-siwc-bridge"}

    @app.get("/mcp")
    async def mcp_get(request: Request):
        authorize(request)
        return Response(status_code=405, headers={"Allow": "POST"})  # no unsolicited SSE stream

    @app.delete("/mcp")
    async def mcp_delete(request: Request):
        authorize(request)
        return Response(status_code=405, headers={"Allow": "POST"})

    @app.post("/mcp")
    async def mcp_post(request: Request):
        authorize(request)
        protocol = request.headers.get("mcp-protocol-version")
        if protocol and protocol not in SUPPORTED:
            raise HTTPException(400, "Unsupported MCP protocol version")
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            raise HTTPException(415, "Content-Type must be application/json")
        if int(request.headers.get("content-length", "0")) > 256_000:
            raise HTTPException(413, "Request too large")
        body = await request.body()
        if len(body) > 256_000:
            raise HTTPException(413, "Request too large")
        try:
            import json
            message = json.loads(body)
        except ValueError:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400)
        if protocol == "2026-07-28":
            # MCP 2026-07-28 requires request metadata mirrored in headers.
            # Validate these before invoking any tool or consuming a plan request.
            if not isinstance(message, dict) or request.headers.get("mcp-method") != message.get("method"):
                raise HTTPException(400, "Mcp-Method header mismatch")
            params = message.get("params") or {}
            if not isinstance(params, dict):
                raise HTTPException(400, "Invalid params")
            meta = params.get("_meta") or {}
            if not isinstance(meta, dict) or meta.get("io.modelcontextprotocol/protocolVersion") != protocol:
                raise HTTPException(400, "MCP protocol metadata mismatch")
            if message.get("method") == "tools/call":
                if request.headers.get("mcp-name") != params.get("name"):
                    raise HTTPException(400, "Mcp-Name header mismatch")
        answer = await handle_rpc(service, message)
        if answer is None:
            return Response(status_code=202)
        return JSONResponse(answer)

    @app.post("/api/tools/{tool_name}")
    async def rest_tool(tool_name: str, request: Request):
        authorize(request)
        if int(request.headers.get("content-length", "0")) > 256_000:
            raise HTTPException(413, "Request too large")
        body = await request.body()
        if len(body) > 256_000:
            raise HTTPException(413, "Request too large")
        try:
            import json
            args = json.loads(body)
        except ValueError:
            raise HTTPException(400, "Invalid JSON")
        try:
            return await service.dispatch(tool_name, args)
        except BridgeError as exc:
            return JSONResponse({"error": str(exc), "code": exc.code}, status_code=exc.http_status)

    return app


# Uvicorn import target; no insecure fallback when secrets are missing.
def get_app() -> FastAPI:
    return create_app()
