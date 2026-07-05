"""왜움직여? MCP 웹 테스트 클라이언트 서버.

정적 테스트 페이지를 서빙하고, 브라우저의 CORS 제약을 피하기 위해
원격 MCP 엔드포인트로의 요청을 프록시한다.

사용법:
    uv run python webclient/serve.py
    # 브라우저에서 http://localhost:8765 접속
    # 다른 엔드포인트 테스트: MCP_URL=http://localhost:8000/mcp uv run python webclient/serve.py
"""

import os
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

MCP_URL = os.environ.get("MCP_URL", "https://why-moved.playmcp-endpoint.kakaocloud.io/mcp")
PORT = int(os.environ.get("WEBCLIENT_PORT", "8765"))
INDEX = Path(__file__).parent / "index.html"


async def index(_request: Request) -> FileResponse:
    return FileResponse(INDEX)


async def config(_request: Request) -> JSONResponse:
    return JSONResponse({"mcp_url": MCP_URL})


async def proxy(request: Request) -> Response:
    """브라우저 → 원격 MCP 프록시. mcp-session-id 헤더를 양방향 전달한다."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    session_id = request.headers.get("mcp-session-id")
    if session_id:
        headers["mcp-session-id"] = session_id

    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(MCP_URL, content=body, headers=headers)
    except httpx.HTTPError as exc:
        return JSONResponse({"proxy_error": str(exc)}, status_code=502)

    out_headers = {}
    if "mcp-session-id" in resp.headers:
        out_headers["mcp-session-id"] = resp.headers["mcp-session-id"]
    return Response(
        resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
        headers=out_headers,
    )


app = Starlette(routes=[
    Route("/", index),
    Route("/config", config),
    Route("/proxy/mcp", proxy, methods=["POST"]),
])

if __name__ == "__main__":
    print(f"왜움직여? 웹 테스트 클라이언트: http://localhost:{PORT}  (MCP: {MCP_URL})")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
