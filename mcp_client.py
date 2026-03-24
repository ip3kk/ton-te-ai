"""
TonPal — MCP client for @ton/mcp HTTP server (Streamable HTTP transport).

Calls tools: send_ton, send_jetton, get_swap_quote, resolve_dns.
Requires: npx @ton/mcp@alpha --http 3001 (or MCP_SERVER_URL)
"""
import asyncio
import json
import logging
from typing import Any, Optional

import aiohttp

from config import MCP_SERVER_URL

log = logging.getLogger("tonpal.mcp")

_request_id = 0
_session_id: Optional[str] = None
_initialized = False
_init_lock = asyncio.Lock()

_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _next_id() -> int:
    global _request_id
    _request_id += 1
    return _request_id


async def _parse_response(resp: aiohttp.ClientResponse) -> dict:
    ct = resp.content_type or ""
    if "text/event-stream" in ct:
        last_data = None
        async for line in resp.content:
            text_line = line.decode("utf-8", errors="replace").strip()
            if text_line.startswith("data:"):
                last_data = text_line[5:].strip()
        if last_data:
            return json.loads(last_data)
        return {}
    return await resp.json()


async def _raw_request(session: aiohttp.ClientSession, payload: dict) -> tuple[dict, Optional[str]]:
    """Send a single JSON-RPC request, handle session headers. Returns (data, error)."""
    global _session_id
    url = f"{MCP_SERVER_URL.rstrip('/')}/mcp"
    hdrs = dict(_HEADERS)
    if _session_id:
        hdrs["Mcp-Session-Id"] = _session_id

    async with session.post(url, json=payload, headers=hdrs, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        sid = resp.headers.get("Mcp-Session-Id")
        if sid:
            _session_id = sid
        if resp.status != 200:
            body = await resp.text()
            return {}, f"MCP server returned {resp.status}: {body[:200]}"
        data = await _parse_response(resp)
        return data, None


async def _ensure_initialized(session: aiohttp.ClientSession) -> Optional[str]:
    """Perform initialize + notifications/initialized handshake if needed."""
    global _initialized
    if _initialized:
        return None

    init_payload = {
        "jsonrpc": "2.0",
        "id": _next_id(),
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "tonpal", "version": "1.0.0"},
        },
    }
    data, err = await _raw_request(session, init_payload)
    if err:
        return f"MCP init failed: {err}"

    if data.get("error"):
        return f"MCP init error: {data['error'].get('message', data['error'])}"

    notif_payload = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }
    hdrs = dict(_HEADERS)
    if _session_id:
        hdrs["Mcp-Session-Id"] = _session_id
    url = f"{MCP_SERVER_URL.rstrip('/')}/mcp"
    try:
        async with session.post(url, json=notif_payload, headers=hdrs, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            pass
    except Exception:
        pass

    _initialized = True
    log.info("MCP session initialized (session_id=%s)", _session_id)
    return None


async def _mcp_call(tool_name: str, arguments: dict) -> tuple[Any, Optional[str]]:
    """Call MCP tool via JSON-RPC. Returns (result, error_message)."""
    global _initialized, _session_id

    try:
        async with aiohttp.ClientSession() as session:
            async with _init_lock:
                err = await _ensure_initialized(session)
            if err:
                return None, err

            payload = {
                "jsonrpc": "2.0",
                "id": _next_id(),
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }
            data, err = await _raw_request(session, payload)
            if err:
                if "not initialized" in err.lower() or "Bad Request" in err:
                    _initialized = False
                    _session_id = None
                    async with _init_lock:
                        err2 = await _ensure_initialized(session)
                    if err2:
                        return None, err2
                    data, err = await _raw_request(session, payload)
                    if err:
                        return None, err
                else:
                    return None, err

    except aiohttp.ClientError as e:
        log.warning("MCP request failed: %s", e)
        return None, f"MCP unavailable: {e}"
    except Exception as e:
        log.warning("MCP error: %s", e)
        return None, str(e)

    rpc_err = data.get("error")
    if rpc_err:
        msg = rpc_err.get("message", "Unknown error")
        return None, msg

    result = data.get("result")
    if result is None:
        return None, "No result from MCP"

    content = result.get("content", [])
    if result.get("isError"):
        text = content[0].get("text", "Unknown error") if content else "Tool error"
        return None, text

    if not content:
        return {}, None
    text = content[0].get("text", "{}")
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        return {"raw": text}, None


async def mcp_get_balance() -> tuple[str, bool]:
    """Hot wallet balance via MCP. Returns (formatted_line, success)."""
    out, err = await _mcp_call("get_balance", {})
    if err:
        return err, False
    if not isinstance(out, dict):
        return str(out), True
    addr = out.get("address") or ""
    bal = out.get("balance") or out.get("balanceNano") or ""
    if addr and bal:
        return f"{addr}\n{bal}", True
    return str(out), True


async def mcp_resolve_dns(domain: str) -> tuple[str, bool]:
    """Resolve TON DNS. Returns (address_or_error, success)."""
    domain = domain.strip().lower()
    if not domain.endswith(".ton"):
        domain = f"{domain}.ton"
    out, err = await _mcp_call("resolve_dns", {"domain": domain})
    if err:
        return err, False
    addr = (out or {}).get("address") or (out or {}).get("result")
    if isinstance(addr, str):
        return addr, True
    if isinstance(out, dict):
        for k in ("address", "result", "wallet"):
            v = out.get(k)
            if isinstance(v, str) and (v.startswith("EQ") or v.startswith("UQ")):
                return v, True
            if isinstance(v, dict) and v.get("address"):
                return v["address"], True
    return "Could not parse address from MCP response", False


async def mcp_get_swap_quote(from_token: str, to_token: str, amount: str) -> tuple[str, bool]:
    """Get swap quote. from_token/to_token: 'TON' or jetton address. Returns (formatted_text, success)."""
    out, err = await _mcp_call("get_swap_quote", {
        "fromToken": from_token,
        "toToken": to_token,
        "amount": amount,
    })
    if err:
        return err, False
    if not isinstance(out, dict):
        return str(out), True
    in_amt = out.get("amountIn") or amount
    out_amt = out.get("amountOut") or out.get("toAmount", "?")
    route = out.get("route") or out.get("dex", "DEX")
    return f"Quote: {in_amt} → ~{out_amt} (via {route})", True


async def mcp_send_ton(to_address: str, amount: str, comment: str = "") -> tuple[str, bool]:
    """Send TON. Returns (result_message, success)."""
    args = {"toAddress": to_address, "amount": amount}
    if comment:
        args["comment"] = comment
    out, err = await _mcp_call("send_ton", args)
    if err:
        return err, False
    tx_hash = (out or {}).get("normalizedHash") or (out or {}).get("hash") or (out or {}).get("txHash")
    if tx_hash:
        return f"Sent {amount} TON. Tx: {tx_hash}", True
    return str(out) if out else "Sent successfully", True


async def mcp_send_jetton(to_address: str, jetton_address: str, amount: str, comment: str = "") -> tuple[str, bool]:
    """Send jetton. Returns (result_message, success)."""
    args = {"toAddress": to_address, "jettonAddress": jetton_address, "amount": amount}
    if comment:
        args["comment"] = comment
    out, err = await _mcp_call("send_jetton", args)
    if err:
        return err, False
    tx_hash = (out or {}).get("normalizedHash") or (out or {}).get("hash")
    if tx_hash:
        return f"Sent {amount} jetton. Tx: {tx_hash}", True
    return str(out) if out else "Sent successfully", True


def is_mcp_available() -> bool:
    """Check if MCP is configured (URL set and non-empty)."""
    return bool(MCP_SERVER_URL and MCP_SERVER_URL.strip())
