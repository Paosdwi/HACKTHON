"""HTTP/ASGI presentation boundary for CreateTask."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping, Protocol
from urllib.parse import parse_qs

from crypto_trust_agent.application.use_cases.create_task import (
    CreateTaskCommand,
    CreateTaskDependencyError,
    CreateTaskRateLimited,
    CreateTaskUseCase,
    CreateTaskValidationError,
)

_BUSINESS_FIELDS = {"question", "assets", "timeframe", "formal_run"}
_MAX_BODY_BYTES = 65_536
_IDENTITY_FIELDS = {
    "user_id",
    "userid",
    "sub",
    "subject",
    "principal",
    "identity",
    "role",
    "roles",
    "group",
    "groups",
    "cognito:groups",
}


class PrincipalView(Protocol):
    subject: str
    pseudonym: str


class PrincipalAuthenticator(Protocol):
    def authenticate(self, authorization_header: str | None) -> PrincipalView: ...


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: Mapping[str, object]
    headers: tuple[tuple[bytes, bytes], ...] = ()


class TaskHttpHandler:
    def __init__(self, use_case: CreateTaskUseCase, authenticator: PrincipalAuthenticator) -> None:
        self._use_case = use_case
        self._authenticator = authenticator

    def handle(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        query_string: str,
        body_bytes: bytes,
    ) -> HttpResponse:
        if path != "/api/v1/tasks":
            return self._error(404, "not_found", "Resource not found.")
        if method != "POST":
            return self._error(405, "method_not_allowed", "Method not allowed.")
        try:
            body = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._error(422, "invalid_task_request", "Task request is invalid.")
        if not isinstance(body, dict):
            return self._error(422, "invalid_task_request", "Task request is invalid.")

        query = parse_qs(query_string, keep_blank_values=True)
        if self._contains_identity_override(body, query, headers):
            return self._error(422, "identity_override_forbidden", "Client identity override is forbidden.")
        if set(body) != _BUSINESS_FIELDS or query:
            return self._error(422, "invalid_task_request", "Task request is invalid.")

        try:
            principal = self._authenticator.authenticate(headers.get("authorization"))
        except Exception:
            return self._error(
                401,
                "authentication_required",
                "A verified principal is required.",
                ((b"www-authenticate", b"Bearer"),),
            )

        try:
            timeframe = body["timeframe"]
            assets = body["assets"]
            if not isinstance(timeframe, dict) or set(timeframe) != {"start", "end"}:
                raise CreateTaskValidationError("invalid timeframe")
            if not isinstance(assets, list) or any(not isinstance(item, str) for item in assets):
                raise CreateTaskValidationError("invalid assets")
            result = self._use_case.execute(
                CreateTaskCommand(
                    trusted_user_scope=principal.subject,
                    principal_pseudonym=principal.pseudonym,
                    question=body["question"],
                    assets=tuple(assets),
                    timeframe_start=timeframe["start"],
                    timeframe_end=timeframe["end"],
                    formal_run=body["formal_run"],
                )
            )
        except (CreateTaskValidationError, KeyError, TypeError):
            return self._error(422, "invalid_task_request", "Task request is invalid.")
        except CreateTaskRateLimited as error:
            return self._error(
                429,
                "task_create_rate_limited",
                "Task create rate limit exceeded.",
                ((b"retry-after", str(error.retry_after_seconds).encode("ascii")),),
            )
        except CreateTaskDependencyError:
            return self._error(503, "task_service_unavailable", "Task service is unavailable.")

        return HttpResponse(
            status=201 if result.outcome == "created" else 200,
            body={
                "schema_version": "1.0.0",
                "task_id": result.task_id,
                "idempotency_outcome": result.outcome,
                "state": result.state,
                "version": result.version,
                "request_fingerprint": result.request_fingerprint,
                "fingerprint_ruleset_version": result.fingerprint_ruleset_version,
                "idempotency_window_expires_at": result.window_expires_at,
                "rate_limit": {
                    "limit": result.rate_limit.limit,
                    "remaining": result.rate_limit.remaining,
                    "window_seconds": result.rate_limit.window_seconds,
                },
            },
        )

    @staticmethod
    def _contains_identity_override(
        body: Mapping[str, object],
        query: Mapping[str, object],
        headers: Mapping[str, str],
    ) -> bool:
        def identity_name(name: str) -> bool:
            normalized = name.lower().replace("-", "_")
            if normalized in _IDENTITY_FIELDS:
                return True
            if normalized.startswith("x_"):
                custom = normalized[2:]
                return custom in _IDENTITY_FIELDS or any(
                    marker in custom for marker in ("user_id", "principal", "identity", "cognito_group")
                )
            return False

        return any(identity_name(str(key)) for key in body) or any(
            identity_name(str(key)) for key in query
        ) or any(key != "authorization" and identity_name(key) for key in headers)

    @staticmethod
    def _error(
        status: int,
        code: str,
        message: str,
        headers: tuple[tuple[bytes, bytes], ...] = (),
    ) -> HttpResponse:
        return HttpResponse(
            status=status,
            body={"error": {"code": code, "message": message}},
            headers=headers,
        )


class TaskAsgiApp:
    def __init__(self, handler: TaskHttpHandler) -> None:
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

        content_length = headers.get("content-length")
        invalid_length = False
        if content_length is not None:
            try:
                invalid_length = int(content_length) < 0
                body_too_large = int(content_length) > _MAX_BODY_BYTES
            except ValueError:
                invalid_length = True
                body_too_large = False
        else:
            body_too_large = False

        chunks: list[bytes] = []
        body_size = 0
        if not body_too_large and not invalid_length:
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    break
                chunk = message.get("body", b"")
                body_size += len(chunk)
                if body_size > _MAX_BODY_BYTES:
                    body_too_large = True
                    break
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break

        if body_too_large:
            response = TaskHttpHandler._error(413, "request_too_large", "Request body is too large.")
        elif invalid_length:
            response = TaskHttpHandler._error(422, "invalid_task_request", "Task request is invalid.")
        elif duplicate_authorization:
            response = TaskHttpHandler._error(401, "authentication_required", "A verified principal is required.")
        else:
            try:
                query_string = scope.get("query_string", b"").decode("ascii", errors="strict")
            except UnicodeDecodeError:
                response = TaskHttpHandler._error(422, "invalid_task_request", "Task request is invalid.")
            else:
                response = self._handler.handle(
                    method=scope.get("method", ""),
                    path=scope.get("path", ""),
                    headers=headers,
                    query_string=query_string,
                    body_bytes=b"".join(chunks),
                )
        content = json.dumps(response.body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        response_headers = (
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(content)).encode("ascii")),
            *response.headers,
        )
        await send({"type": "http.response.start", "status": response.status, "headers": response_headers})
        await send({"type": "http.response.body", "body": content})


def create_task_asgi_app(
    use_case: CreateTaskUseCase,
    authenticator: PrincipalAuthenticator,
) -> TaskAsgiApp:
    return TaskAsgiApp(TaskHttpHandler(use_case, authenticator))


def create_fastapi_app(use_case: CreateTaskUseCase, authenticator: PrincipalAuthenticator):
    """Create the FastAPI composition when the optional runtime dependency exists."""

    try:
        from fastapi import FastAPI
    except ModuleNotFoundError as error:
        raise RuntimeError("FastAPI is not installed; use the ASGI boundary in this environment") from error
    app = FastAPI()
    app.mount("/", create_task_asgi_app(use_case, authenticator))
    return app


__all__ = (
    "HttpResponse",
    "TaskAsgiApp",
    "TaskHttpHandler",
    "create_fastapi_app",
    "create_task_asgi_app",
)
