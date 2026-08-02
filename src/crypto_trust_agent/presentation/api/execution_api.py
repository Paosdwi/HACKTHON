"""Authenticated HTTP/ASGI boundary for formal execution start and escalation."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Mapping, Protocol
from urllib.parse import parse_qs

from crypto_trust_agent.application.use_cases.start_formal_execution import (
    StartFormalExecutionAuthorizationError,
    StartFormalExecutionCommand,
    StartFormalExecutionConflict,
    StartFormalExecutionDependencyError,
    StartFormalExecutionTaskNotFound,
    StartFormalExecutionUseCase,
    StartFormalExecutionValidationError,
)
from crypto_trust_agent.presentation.api.task_api import HttpResponse, TaskHttpHandler

_PATH = re.compile(r"^/api/v1/tasks/(TASK-[A-Za-z0-9._:-]+)/executions$")
_MAX_BODY_BYTES = 65_536
_COMMON_FIELDS = {"operation_id", "execution_id", "preflight_id", "input_lock_hash"}
_RERUN_FIELDS = {"original_execution_id", "technical_failure_code"}
_FORBIDDEN_IDENTITY_FIELDS = {
    "trusted_user_scope",
    "user_scope",
    "principal_pseudonym",
    "is_admin",
    "admin",
    "admin_authorization",
    "authorization_id",
}


class ExecutionPrincipalView(Protocol):
    subject: str
    pseudonym: str
    is_admin: bool


class ExecutionPrincipalAuthenticator(Protocol):
    def authenticate(self, authorization_header: str | None) -> ExecutionPrincipalView: ...


class ExecutionHttpHandler:
    def __init__(self, use_case: StartFormalExecutionUseCase, authenticator: ExecutionPrincipalAuthenticator) -> None:
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
            return self._invalid()
        if not isinstance(body, dict):
            return self._invalid()
        try:
            query = parse_qs(query_string, keep_blank_values=True)
        except ValueError:
            return self._invalid()
        if self._contains_identity_override(body, query, headers):
            return TaskHttpHandler._error(422, "identity_override_forbidden", "Client identity override is forbidden.")
        fields = set(body)
        if query or (fields != _COMMON_FIELDS and fields != _COMMON_FIELDS | _RERUN_FIELDS):
            return self._invalid()
        if any(not isinstance(body.get(name), str) for name in fields):
            return self._invalid()
        try:
            principal = self._authenticator.authenticate(headers.get("authorization"))
        except Exception:
            return TaskHttpHandler._error(
                401,
                "authentication_required",
                "A verified principal is required.",
                ((b"www-authenticate", b"Bearer"),),
            )
        try:
            result = self._use_case.execute(
                StartFormalExecutionCommand(
                    trusted_user_scope=principal.subject,
                    principal_pseudonym=principal.pseudonym,
                    is_admin=principal.is_admin,
                    task_id=match.group(1),
                    preflight_id=body["preflight_id"],
                    input_lock_hash=body["input_lock_hash"],
                    operation_id=body["operation_id"],
                    execution_id=body["execution_id"],
                    original_execution_id=body.get("original_execution_id"),
                    technical_failure_code=body.get("technical_failure_code"),
                )
            )
        except (StartFormalExecutionValidationError, KeyError, TypeError):
            return self._invalid()
        except StartFormalExecutionAuthorizationError:
            return TaskHttpHandler._error(403, "admin_authorization_required", "Verified administrator authorization is required.")
        except StartFormalExecutionTaskNotFound:
            return TaskHttpHandler._error(404, "task_not_found", "Task not found.")
        except StartFormalExecutionConflict as error:
            public_code = error.code if error.code in {
                "preflight_not_passed",
                "preflight_consumed",
                "preflight_expired",
                "preflight_stale",
                "formal_quota_exhausted",
                "invalid_technical_failure_code",
            } else "execution_start_conflict"
            return TaskHttpHandler._error(409, public_code, "Formal execution cannot be started.")
        except StartFormalExecutionDependencyError:
            return TaskHttpHandler._error(503, "execution_service_unavailable", "Formal execution service is unavailable.")
        return HttpResponse(202 if result.manual_case is not None else 201, result.to_safe_dict())

    @staticmethod
    def _contains_identity_override(body: Mapping[str, object], query: Mapping[str, object], headers: Mapping[str, str]) -> bool:
        normalized_body = {str(key).lower().replace("-", "_") for key in body}
        normalized_query = {str(key).lower().replace("-", "_") for key in query}
        if normalized_body & _FORBIDDEN_IDENTITY_FIELDS or normalized_query & _FORBIDDEN_IDENTITY_FIELDS:
            return True
        return TaskHttpHandler._contains_identity_override(body, query, headers)

    @staticmethod
    def _invalid() -> HttpResponse:
        return TaskHttpHandler._error(422, "invalid_execution_request", "Formal execution request is invalid.")


class ExecutionAsgiApp:
    def __init__(self, handler: ExecutionHttpHandler) -> None:
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
                response = self._handler._invalid()
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
        response_headers = (
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(content)).encode("ascii")),
            *response.headers,
        )
        await send({"type": "http.response.start", "status": response.status, "headers": response_headers})
        await send({"type": "http.response.body", "body": content})


def create_execution_asgi_app(use_case: StartFormalExecutionUseCase, authenticator: ExecutionPrincipalAuthenticator) -> ExecutionAsgiApp:
    return ExecutionAsgiApp(ExecutionHttpHandler(use_case, authenticator))


def create_execution_fastapi_app(use_case: StartFormalExecutionUseCase, authenticator: ExecutionPrincipalAuthenticator):
    try:
        from fastapi import FastAPI
    except ModuleNotFoundError as error:
        raise RuntimeError("FastAPI is not installed; use the ASGI boundary in this environment") from error
    app = FastAPI()
    app.mount("/", create_execution_asgi_app(use_case, authenticator))
    return app


__all__ = (
    "ExecutionAsgiApp",
    "ExecutionHttpHandler",
    "create_execution_asgi_app",
    "create_execution_fastapi_app",
)
