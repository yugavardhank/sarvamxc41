"""Same-origin gateway for a Vercel dashboard backed by a persistent server."""
import ipaddress
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length",
}


def configuration_error(backend_url: str, deployed: bool) -> str | None:
    if not backend_url:
        return "Set API_URL (or VITE_API_URL) to your backend's public HTTPS URL, then redeploy."
    try:
        parsed = urlsplit(backend_url)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return "API_URL is not a valid backend URL."
    if parsed.scheme not in ("http", "https") or not hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return "API_URL must be an HTTP(S) backend URL without credentials, query parameters, or fragments."
    local = hostname.lower() == "localhost" or hostname.lower().endswith(".localhost")
    try:
        address = ipaddress.ip_address(hostname)
        local = local or address.is_loopback or address.is_unspecified or address.is_private or address.is_link_local
    except ValueError:
        pass
    if deployed and (local or parsed.scheme != "https"):
        return "Vercel cannot reach a local HTTP/private address. Set API_URL to your backend's public HTTPS tunnel URL, then redeploy."
    return None


def create_gateway_app(backend_url: str, *, deployed: bool = False) -> FastAPI:
    app = FastAPI(title="Tokri gateway", docs_url=None, redoc_url=None, openapi_url=None)
    backend_url = backend_url.strip().rstrip("/")
    error = configuration_error(backend_url, deployed)

    @app.get("/deployment-health")
    def deployment_health():
        return JSONResponse(
            {"status": "configuration_error" if error else "ok", "mode": "gateway", "detail": error},
            status_code=503 if error else 200,
            headers={"Cache-Control": "no-store"},
        )

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def forward(request: Request, path: str):
        if error:
            return JSONResponse({"detail": error}, status_code=503, headers={"Cache-Control": "no-store"})
        if urlsplit(backend_url).netloc.lower() == request.url.netloc.lower():
            return JSONResponse({"detail": "API_URL points to this gateway. Use the separate backend URL to avoid a request loop."}, status_code=503)
        raw_path = request.scope.get("raw_path", request.url.path.encode()).decode("ascii")
        url = backend_url + "/" + raw_path.lstrip("/")
        if request.url.query:
            url += "?" + request.url.query
        headers = {key: value for key, value in request.headers.items() if key.lower() not in HOP_HEADERS}
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
                upstream = await client.request(request.method, url, headers=headers, content=await request.body())
        except httpx.TimeoutException:
            return JSONResponse({"detail": "The backend timed out. Check that the local server and its HTTPS tunnel are running."}, status_code=504)
        except httpx.RequestError:
            return JSONResponse({"detail": "Cannot reach the backend. Check API_URL and keep the local server and HTTPS tunnel running."}, status_code=502)
        response = Response(content=upstream.content, status_code=upstream.status_code)
        # httpx decompresses response bodies; do not forward the old encoding/length.
        response.raw_headers = [
            (key, value) for key, value in upstream.headers.raw
            if key.decode().lower() not in HOP_HEADERS | {"content-encoding", "cache-control"}
        ]
        response.headers["Cache-Control"] = "no-store"
        location = response.headers.get("location", "")
        if location.startswith(backend_url + "/"):
            response.headers["location"] = location[len(backend_url):]
        return response

    return app
