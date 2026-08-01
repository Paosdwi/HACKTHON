"""Authenticated ASGI/FastAPI boundary for task pre-flight readiness."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Mapping

from crypto_trust_agent.application.use_cases.preflight import (
    PreflightCommand,
    PreflightDependencyError,
    PreflightRateLimited,
    PreflightTaskNotFound,
    PreflightUseCase,
)
from crypto_trust_agent.presentation.api.task_api import HttpResponse, PrincipalAuthenticator, TaskHttpHandler

_PATH = re.compile(r"^/api/v1/tasks/(TASK-[A-Za-z0-9._:-]{1,123})/preflight$")
_MAX_BODY_BYTES = 65_536


class PreflightHttpHandler:
    def __init__(self, use_case: PreflightUseCase, authenticator: PrincipalAuthenticator) -> None:
        self._use_case = use_case
        self._authenticator = authenticator

    def handle(self, *, method: str, path: str, headers: Mapping[str, str], query_string: str, body_bytes: bytes) -> HttpResponse:
        match = _PATH.fullmatch(path)
        if match is None:
            return TaskHttpHandler._error(404, "not_found", "Resource not found.")
        if method != "POST":
            return TaskHttpHandler._error(405, "method_not_allowed", "Method not allowed.")
        try:
            body = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return TaskHttpHandler._error(422, "invalid_preflight_request", "Pre-flight request is invalid.")
        if body != {}:
            if isinstance(body, dict) and TaskHttpHandler._contains_identity_override(body, {}, headers):
                return TaskHttpHandler._error(422, "identity_override_forbidden", "Client identity override is forbidden.")
            return TaskHttpHandler._error(422, "invalid_preflight_request", "Pre-flight request is invalid.")
        if query_string:
            from urllib.parse import parse_qs
            query = parse_qs(query_string, keep_blank_values=True)
            if TaskHttpHandler._contains_identity_override({}, query, headers):
                return TaskHttpHandler._error(422, "identity_override_forbidden", "Client identity override is forbidden.")
            return TaskHttpHandler._error(422, "invalid_preflight_request", "Pre-flight request is invalid.")
        if TaskHttpHandler._contains_identity_override({}, {}, headers):
            return TaskHttpHandler._error(422, "identity_override_forbidden", "Client identity override is forbidden.")
        try:
            principal = self._authenticator.authenticate(headers.get("authorization"))
        except Exception:
            return TaskHttpHandler._error(401, "authentication_required", "A verified principal is required.", ((b"www-authenticate", b"Bearer"),))
        try:
            result = self._use_case.execute(PreflightCommand(principal.subject, match.group(1)))
        except PreflightRateLimited as error:
            return TaskHttpHandler._error(429, "preflight_rate_limited", "Pre-flight rate limit exceeded.", ((b"retry-after", str(error.retry_after_seconds).encode("ascii")),))
        except PreflightTaskNotFound:
            return TaskHttpHandler._error(404, "task_not_found", "Task not found.")
        except PreflightDependencyError:
            return TaskHttpHandler._error(503, "preflight_service_unavailable", "Pre-flight service is unavailable.")
        return HttpResponse(200, result.to_safe_dict())


class PreflightAsgiApp:
    def __init__(self, handler: PreflightHttpHandler) -> None:
        self._handler = handler

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            return
        headers: dict[str, str] = {}
        duplicate_authorization = False
        for raw_name, raw_value in scope.get("headers", ()):
            name = raw_name.decode("latin-1").lower()
            if name == "authorization" and name in headers:
                duplicate_authorization = True
            headers[name] = raw_value.decode("latin-1")
        chunks: list[bytes] = []
        size = 0
        too_large = False
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > _MAX_BODY_BYTES:
                too_large = True
                break
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        if too_large:
            response = TaskHttpHandler._error(413, "request_too_large", "Request body is too large.")
        elif duplicate_authorization:
            response = TaskHttpHandler._error(401, "authentication_required", "A verified principal is required.")
        else:
            try:
                query = scope.get("query_string", b"").decode("ascii", errors="strict")
            except UnicodeDecodeError:
                response = TaskHttpHandler._error(422, "invalid_preflight_request", "Pre-flight request is invalid.")
            else:
                response = await asyncio.to_thread(
                    self._handler.handle,
                    method=scope.get("method", ""),
                    path=scope.get("path", ""),
                    headers=headers,
                    query_string=query,
                    body_bytes=b"".join(chunks),
                )
        content = json.dumps(response.body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        response_headers = ((b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(content)).encode("ascii")), *response.headers)
        await send({"type": "http.response.start", "status": response.status, "headers": response_headers})
        await send({"type": "http.response.body", "body": content})


def create_preflight_asgi_app(use_case: PreflightUseCase, authenticator: PrincipalAuthenticator) -> PreflightAsgiApp:
    return PreflightAsgiApp(PreflightHttpHandler(use_case, authenticator))


def create_preflight_fastapi_app(use_case: PreflightUseCase, authenticator: PrincipalAuthenticator):
    try:
        from fastapi import FastAPI
    except ModuleNotFoundError as error:
        raise RuntimeError("FastAPI is not installed; use the ASGI boundary in this environment") from error
    app = FastAPI()
    app.mount("/", create_preflight_asgi_app(use_case, authenticator))
    return app


__all__ = ("PreflightAsgiApp", "PreflightHttpHandler", "create_preflight_asgi_app", "create_preflight_fastapi_app")
