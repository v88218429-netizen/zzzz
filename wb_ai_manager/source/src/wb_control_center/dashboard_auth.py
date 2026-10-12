from __future__ import annotations

import base64
import binascii
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def valid_basic_authorization(header: str | None, username: str, password: str) -> bool:
    if not header or not username or not password:
        return False
    scheme, _, encoded = header.partition(" ")
    if scheme.casefold() != "basic" or not encoded:
        return False
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    supplied_user, separator, supplied_password = decoded.partition(":")
    return bool(separator) and secrets.compare_digest(supplied_user, username) and secrets.compare_digest(supplied_password, password)


class DashboardBasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, required: bool, username: str, password: str):
        super().__init__(app)
        self.required = required
        self.username = username
        self.password = password

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self.required or request.url.path == "/health":
            return await call_next(request)
        if not self.username or not self.password:
            return JSONResponse({"detail": "dashboard authentication is misconfigured"}, status_code=503)
        if not valid_basic_authorization(request.headers.get("authorization"), self.username, self.password):
            return JSONResponse(
                {"detail": "authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="WB dashboard", charset="UTF-8"'},
            )
        return await call_next(request)
