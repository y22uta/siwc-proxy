"""MCP 2025-06-18 tool declarations and stateless JSON-RPC dispatcher."""
from __future__ import annotations
import json
from typing import Any

from .service import BridgeService, BridgeError

VERSION = "2026-07-28"
SUPPORTED = {"2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26"}


def obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def s(description: str, **additional: Any) -> dict:
    return {"type": "string", "description": description, **additional}


def tool(name: str, description: str, schema: dict) -> dict:
    return {"name": name, "description": description, "inputSchema": schema}


COMMON = {
    "model": s("Exact model ID returned by chatgpt_models; defaults to first eligible model"),
    "session_id": s("Optional persistent conversation session identifier"),
    "request_id": s("Optional idempotency key for retries within a session (1-128 characters)", maxLength=128),
}

TOOLS = [
    tool("chatgpt_ask", "General / research / writing / analysis / code conversation with persistent history when session_id is supplied.",
         obj({"prompt": s("Question or instruction", maxLength=40000),
              "mode": {"type": "string", "enum": ["general", "analysis", "research", "writing", "review", "code"]},
              "instructions": s("Additional constraints", maxLength=4000), **COMMON}, ["prompt"])),
    tool("chatgpt_article", "Draft a structured original blog article. Does not browse; supply sources for substantiated claims.",
         obj({"topic": s("Article topic"), "audience": s("Audience"), "tone": s("Style and voice"),
              "approx_chars": {"type": "integer", "minimum": 500, "maximum": 15000},
              "keywords": s("SEO keywords"), "sources": s("Source materials, URLs, facts, citation notes"),
              "language": s("Article language"), **COMMON}, ["topic"])),
    tool("chatgpt_review", "Review code, content, specification or plan with prioritized actionable findings.",
         obj({"material": s("Material to review"), "criteria": s("Quality criteria, risk focus or standard"), **COMMON}, ["material"])),
    tool("chatgpt_models", "Fetch actual models available to the SIWC authenticated ChatGPT account.",
         obj({"refresh": {"type": "boolean", "description": "Bypass the five-minute catalog cache"}})),
    tool("chatgpt_status", "Get connection/authorization status and local usage counters without revealing secrets.", obj({})),
    tool("chatgpt_session_create", "Create an independent conversation history for follow-up prompts.",
         obj({"label": s("Human-readable purpose"), "mode": {"type": "string", "enum": ["general", "analysis", "research", "writing", "review", "code"]}})),
    tool("chatgpt_sessions", "List stored conversation sessions.", obj({"limit": {"type": "integer", "minimum": 1, "maximum": 100}})),
    tool("chatgpt_history", "Read the stored messages of a conversation session.",
         obj({"session_id": s("Session identifier"), "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, ["session_id"])),
    tool("chatgpt_session_delete", "Permanently delete one session and its stored conversation.",
         obj({"session_id": s("Session identifier")}, ["session_id"])),
]

TOOL_NAMES = {x["name"] for x in TOOLS}


def rpc_result(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def rpc_error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


async def handle_rpc(service: BridgeService, message: dict) -> dict | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return rpc_error(None, -32600, "Invalid JSON-RPC request")
    if "id" not in message:
        # MCP notifications (e.g. notifications/initialized) have no response.
        return None
    msg_id = message["id"]
    method = message.get("method")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return rpc_error(msg_id, -32602, "Params must be an object")
    if method == "initialize":
        client_version = params.get("protocolVersion", VERSION)
        return rpc_result(msg_id, {
            "protocolVersion": client_version if client_version in SUPPORTED else VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "muse-siwc-bridge", "version": "1.0.0"},
            "instructions": "Use chatgpt_models to check eligible models; chatgpt_session_create then chatgpt_ask(session_id=...) to retain context."
        })
    if method == "ping":
        return rpc_result(msg_id, {})
    if method == "tools/list":
        return rpc_result(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in TOOL_NAMES:
            return rpc_error(msg_id, -32602, "Unknown tool")
        try:
            out = await service.dispatch(name, args)
            return rpc_result(msg_id, {
                "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}],
                "structuredContent": out,
                "isError": False,
            })
        except BridgeError as exc:
            return rpc_result(msg_id, {
                "content": [{"type": "text", "text": json.dumps({"code": exc.code, "error": str(exc)}, ensure_ascii=False)}],
                "isError": True,
            })
        except Exception:
            return rpc_result(msg_id, {
                "content": [{"type": "text", "text": "Internal error. Check server logs."}],
                "isError": True,
            })
    return rpc_error(msg_id, -32601, "Method not found")
