"""Local status HTTP server for KeyMesh."""

from __future__ import annotations

import asyncio  # asyncio.start_server 提供轻量 HTTP 能力
import json  # json 输出状态
import logging  # logging 记录访问日志
import time  # time 提供当前时间
from typing import Dict

from .app import AppContext

LOGGER = logging.getLogger(__name__)

HTTP_OK = "HTTP/1.1 200 OK\r\n"
HTTP_NOT_FOUND = "HTTP/1.1 404 Not Found\r\n"
HTTP_METHOD_NOT_ALLOWED = "HTTP/1.1 405 Method Not Allowed\r\n"
HTTP_ERROR = "HTTP/1.1 500 Internal Server Error\r\n"

CONTENT_TYPE_JSON = "Content-Type: application/json\r\n"
CONNECTION_CLOSE = "Connection: close\r\n"


async def _write_json_response(writer: asyncio.StreamWriter, status_line: str, payload: Dict) -> None:
    """输出 JSON 响应。"""

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = f"{status_line}{CONTENT_TYPE_JSON}Content-Length: {len(body)}\r\n{CONNECTION_CLOSE}\r\n"
    writer.write(headers.encode("ascii") + body)
    await writer.drain()


async def _handle_request(app_ctx: AppContext, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """处理单次 HTTP 请求。"""

    peername = writer.get_extra_info("peername")
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
    except asyncio.TimeoutError:
        writer.close()
        await writer.wait_closed()
        return
    if not request_line:
        writer.close()
        await writer.wait_closed()
        return
    try:
        method, path, _ = request_line.decode("ascii").strip().split(" ")
    except ValueError:
        await _write_json_response(writer, HTTP_ERROR, {"ok": False, "error": "malformed request"})
        writer.close()
        await writer.wait_closed()
        return
    while True:
        header_line = await reader.readline()
        if not header_line or header_line in {b"\r\n", b"\n"}:
            break
    if method != "GET":
        await _write_json_response(writer, HTTP_METHOD_NOT_ALLOWED, {"ok": False, "error": "GET only"})
        writer.close()
        await writer.wait_closed()
        return
    if path == "/health":
        payload = {"ok": True, "node_id": app_ctx.cfg.node.id, "time": int(time.time())}
        await _write_json_response(writer, HTTP_OK, payload)
    elif path == "/peers":
        states = []
        for peer_id in app_ctx.list_peer_ids():
            state = app_ctx.peer_states.get(peer_id)
            if state is None:
                continue
            snapshot = await state.to_dict()
            states.append(snapshot)
        await _write_json_response(writer, HTTP_OK, {"peers": states})
    elif path == "/shares":
        shares = [
            {
                "name": share.name,
                "path": str(share.path),
                "delete_propagation": share.delete_propagation,
            }
            for share in app_ctx.cfg.shares
        ]
        await _write_json_response(writer, HTTP_OK, {"shares": shares})
    else:
        await _write_json_response(writer, HTTP_NOT_FOUND, {"ok": False, "error": "not found"})
    writer.close()
    await writer.wait_closed()
    LOGGER.debug("status request %s %s from %s", method, path, peername)


async def run_status_http(app_ctx: AppContext, host: str = "127.0.0.1", port: int = 52180) -> None:
    """启动状态页 HTTP 服务。"""

    server = await asyncio.start_server(lambda r, w: asyncio.create_task(_handle_request(app_ctx, r, w)), host=host, port=port)
    sockets = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    LOGGER.info("status HTTP listening on %s", sockets)
    try:
        await server.serve_forever()
    except asyncio.CancelledError:
        LOGGER.info("status HTTP shutdown requested")
        server.close()
        await server.wait_closed()
        raise
