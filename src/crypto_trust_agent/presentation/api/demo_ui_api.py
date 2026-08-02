"""HTTP/ASGI boundary and uvicorn entry point for the local Demo UI."""

from __future__ import annotations

import asyncio
import json
import re
from http.cookies import SimpleCookie
from typing import Mapping, Protocol
from urllib.parse import parse_qs, urlencode

from crypto_trust_agent.application.use_cases.demo_ui import (
    DemoPrincipal,
    DemoUseCase,
    DemoUseCaseError,
)
from crypto_trust_agent.presentation.demo_ui.app import DemoApp

_MAX_BODY = 65_536
_MAX_QUERY = 4_096
_ROUTE = re.compile(r"^/demo/(?P<action>submit|status|report|evidence|log|manifest|download)$")
_IDENTITY_HEADERS = frozenset({"x-user-id", "x-cognito-groups", "x-principal", "x-sub"})
_IDENTITY_FIELDS = frozenset({
    "sub",
    "subject",
    "user_id",
    "trusted_user_scope",
    "principal_pseudonym",
    "is_admin",
    "cognito:groups",
})
_DEFAULT_COOKIE_NAME = "crypto_trust_demo_session"
_ASSET_ALIASES = {
    "BTC": ("BTC", "BITCOIN", "比特幣", "比特币"),
    "ETH": ("ETH", "ETHEREUM", "以太坊"),
    "SOL": ("SOL", "SOLANA", "索拉納", "索拉纳"),
    "BNB": ("BNB", "BINANCE COIN", "幣安幣", "币安币"),
    "XRP": ("XRP", "RIPPLE", "瑞波幣", "瑞波币"),
}


def _explicit_question_assets(question: str) -> tuple[str, ...]:
    """Return explicitly named supported assets in first-mention order."""

    mentions: list[tuple[int, str]] = []
    for asset, aliases in _ASSET_ALIASES.items():
        positions: list[int] = []
        for alias in aliases:
            if alias.isascii():
                match = re.search(
                    rf"(?<![A-Z0-9]){re.escape(alias)}(?![A-Z0-9])",
                    question,
                    flags=re.IGNORECASE,
                )
                if match is not None:
                    positions.append(match.start())
            else:
                position = question.find(alias)
                if position >= 0:
                    positions.append(position)
        if positions:
            mentions.append((min(positions), asset))
    return tuple(asset for _, asset in sorted(mentions))


class DemoAuthenticator(Protocol):
    def authenticate(self, authorization_header: str | None) -> DemoPrincipal: ...


class DemoUiHttpHandler:
    """Synchronous HTTP mapper.  Business decisions remain in DemoUseCase."""

    def __init__(
        self,
        app: DemoApp,
        use_case: DemoUseCase,
        authenticator: DemoAuthenticator,
        *,
        browser_token: str | None = None,
        cookie_name: str = _DEFAULT_COOKIE_NAME,
    ) -> None:
        self._app = app
        self._use_case = use_case
        self._authenticator = authenticator
        self._browser_token = browser_token
        self._cookie_name = cookie_name

    def handle(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body_bytes: bytes,
        query_string: bytes = b"",
    ) -> dict[str, object]:
        if method == "GET" and path == "/":
            page = self._app.render_home()
            extra_headers: tuple[tuple[bytes, bytes], ...] = ()
            if self._browser_token is not None:
                cookie = (
                    f"{self._cookie_name}={self._browser_token}; Path=/; "
                    "HttpOnly; SameSite=Strict"
                )
                extra_headers = ((b"set-cookie", cookie.encode("ascii")),)
            return self._html(page.status_code, page.html_body, extra_headers)

        match = _ROUTE.fullmatch(path)
        if match is None:
            return self._error_response(DemoUseCaseError("not_found"))
        action = match.group("action")

        try:
            query = self._parse_query(query_string)
            self._reject_identity_override(headers, query)
            try:
                principal = self._authenticator.authenticate(
                    self._authorization_value(headers)
                )
            except Exception:
                return self.unauthorized_response()
            if method == "POST" and action == "submit":
                return self._handle_submit(principal, headers, body_bytes)
            if method == "GET" and action == "status":
                return self._handle_status(principal, query)
            if method == "GET" and action in {"report", "evidence", "log", "manifest"}:
                return self._handle_render(principal, query, action)
            if method == "GET" and action == "download":
                return self._handle_download(principal, query)
            return self._error_response(DemoUseCaseError("not_found"))
        except DemoUseCaseError as error:
            return self._error_response(error)
        except Exception:
            return self._error_response(DemoUseCaseError("internal_error"))

    def _handle_submit(
        self,
        principal: DemoPrincipal,
        headers: Mapping[str, str],
        body_bytes: bytes,
    ) -> dict[str, object]:
        content_type = headers.get("content-type", "application/json").split(";", 1)[0].strip().lower()
        body = self._parse_payload(body_bytes, content_type)
        self._reject_payload_identity(body)
        assets_value = body.get("assets", ())
        if isinstance(assets_value, str):
            assets = (assets_value,)
        elif isinstance(assets_value, (list, tuple)):
            assets = tuple(str(item) for item in assets_value)
        else:
            raise DemoUseCaseError("validation_error")
        question = self._scalar(body, "question", "")
        explicitly_named = _explicit_question_assets(question)
        if explicitly_named:
            assets = explicitly_named
        result = self._use_case.submit_and_run(
            principal=principal,
            question=question,
            assets=assets,
            timeframe_start=self._scalar(body, "timeframe_start", "2026-07-18T00:00:00Z"),
            timeframe_end=self._scalar(body, "timeframe_end", "2026-08-01T00:00:00Z"),
        )
        status_query: dict[str, str] = {"task_id": result.task_id}
        if result.execution_id is not None:
            status_query["execution_id"] = result.execution_id
        location = f"/demo/status?{urlencode(status_query)}"
        if content_type == "application/x-www-form-urlencoded":
            return {
                "status": 303,
                "content_type": "text/plain",
                "body": "See Other",
                "extra_headers": ((b"location", location.encode("ascii")),),
            }
        payload = {
            "schema_version": "1.0.0",
            "task_id": result.task_id,
            "preflight_id": result.preflight_id,
            "preflight_ready": result.preflight_ready,
            "execution_id": result.execution_id,
            "state": result.state,
            "terminal_outcome": result.terminal_outcome,
            "publication_outcome": result.publication_outcome,
            "safe_reason_codes": list(result.safe_reason_codes),
            "status_url": location,
        }
        return {
            "status": 201 if result.preflight_ready else 409,
            "content_type": "application/json",
            "body": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        }

    def _handle_status(
        self,
        principal: DemoPrincipal,
        query: Mapping[str, list[str]],
    ) -> dict[str, object]:
        task_id = self._query_scalar(query, "task_id")
        execution_id = self._query_scalar(query, "execution_id", required=False)
        status = self._use_case.get_run_status(
            principal=principal,
            task_id=task_id,
            execution_id=execution_id,
        )
        page = self._app.render_status(status)
        return self._html(page.status_code, page.html_body)

    def _handle_render(
        self,
        principal: DemoPrincipal,
        query: Mapping[str, list[str]],
        action: str,
    ) -> dict[str, object]:
        task_id = self._query_scalar(query, "task_id")
        execution_id = self._query_scalar(query, "execution_id")
        artifact_type = {
            "report": "final_report",
            "evidence": "evidence_list",
            "log": "execution_log",
            "manifest": "manifest",
        }[action]
        document = self._use_case.get_artifact_document(
            principal=principal,
            task_id=task_id,
            execution_id=execution_id,
            artifact_type=artifact_type,
        )
        page = self._app.render_artifact(document)
        return self._html(page.status_code, page.html_body)

    def _handle_download(
        self,
        principal: DemoPrincipal,
        query: Mapping[str, list[str]],
    ) -> dict[str, object]:
        download = self._app.download_artifact(
            principal=principal,
            task_id=self._query_scalar(query, "task_id"),
            execution_id=self._query_scalar(query, "execution_id"),
            artifact_type=self._query_scalar(query, "artifact_type"),
            artifact_format=self._query_scalar(query, "format"),
        )
        return {
            "status": 200,
            "content_type": download.content_type,
            "body": download.content_bytes,
            "extra_headers": (
                (b"content-disposition", f'attachment; filename="{download.filename}"'.encode("ascii")),
            ),
        }

    def unauthorized_response(self) -> dict[str, object]:
        return {
            "status": 401,
            "content_type": "text/html",
            "body": (
                '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>需要驗證身分</title></head>'
                '<body><h1>需要驗證身分</h1><p>必須提供已驗證的使用者身分。</p></body></html>'
            ),
            "extra_headers": ((b"www-authenticate", b"Bearer"),),
        }

    def _authorization_value(self, headers: Mapping[str, str]) -> str | None:
        authorization = headers.get("authorization")
        if authorization is not None:
            return authorization
        raw_cookie = headers.get("cookie")
        if not raw_cookie:
            return None
        try:
            cookie = SimpleCookie()
            cookie.load(raw_cookie)
            morsel = cookie.get(self._cookie_name)
            token = None if morsel is None else morsel.value
        except Exception:
            return None
        if not isinstance(token, str) or not token or len(token) > 256 or token != token.strip():
            return None
        return f"Bearer {token}"

    @staticmethod
    def _parse_query(query_string: bytes) -> dict[str, list[str]]:
        if len(query_string) > _MAX_QUERY:
            raise DemoUseCaseError("validation_error")
        try:
            text = query_string.decode("ascii")
            parsed = parse_qs(text, keep_blank_values=True, max_num_fields=16)
        except (UnicodeDecodeError, ValueError):
            raise DemoUseCaseError("validation_error") from None
        return {str(key): list(values) for key, values in parsed.items()}

    @staticmethod
    def _parse_payload(body_bytes: bytes, content_type: str) -> dict[str, object]:
        try:
            if content_type == "application/json":
                value = json.loads(body_bytes.decode("utf-8"))
                if not isinstance(value, dict):
                    raise DemoUseCaseError("validation_error")
                return value
            if content_type == "application/x-www-form-urlencoded":
                parsed = parse_qs(
                    body_bytes.decode("utf-8"),
                    keep_blank_values=True,
                    max_num_fields=16,
                )
                return {str(key): list(values) for key, values in parsed.items()}
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise DemoUseCaseError("validation_error") from None
        raise DemoUseCaseError("validation_error")

    @staticmethod
    def _scalar(payload: Mapping[str, object], name: str, default: str) -> str:
        value = payload.get(name, default)
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], str):
            return value[0]
        raise DemoUseCaseError("validation_error")

    @staticmethod
    def _query_scalar(
        query: Mapping[str, list[str]],
        name: str,
        *,
        required: bool = True,
    ) -> str | None:
        values = query.get(name)
        if values is None and not required:
            return None
        if values is None or len(values) != 1 or not values[0]:
            raise DemoUseCaseError("validation_error")
        return values[0]

    @staticmethod
    def _reject_identity_override(
        headers: Mapping[str, str],
        query: Mapping[str, list[str]],
    ) -> None:
        if _IDENTITY_HEADERS & {key.casefold() for key in headers}:
            raise DemoUseCaseError("validation_error")
        if _IDENTITY_FIELDS & {key.casefold() for key in query}:
            raise DemoUseCaseError("validation_error")

    @staticmethod
    def _reject_payload_identity(payload: Mapping[str, object]) -> None:
        if _IDENTITY_FIELDS & {str(key).casefold() for key in payload}:
            raise DemoUseCaseError("validation_error")

    def _error_response(self, error: DemoUseCaseError) -> dict[str, object]:
        page = self._app.render_error(error)
        return self._html(page.status_code, page.html_body)

    @staticmethod
    def _html(
        status: int,
        body: str,
        extra_headers: tuple[tuple[bytes, bytes], ...] = (),
    ) -> dict[str, object]:
        return {
            "status": status,
            "content_type": "text/html",
            "body": body,
            "extra_headers": extra_headers,
        }


class DemoUiAsgiApp:
    """Small ASGI wrapper used directly by E2E and mounted by FastAPI."""

    def __init__(self, handler: DemoUiHttpHandler) -> None:
        self._handler = handler

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            return
        header_values: dict[str, list[str]] = {}
        for raw_name, raw_value in scope.get("headers", ()):
            try:
                name = raw_name.decode("latin-1").lower()
                value = raw_value.decode("latin-1")
            except UnicodeDecodeError:
                continue
            header_values.setdefault(name, []).append(value)
        if len(header_values.get("authorization", ())) > 1 or len(header_values.get("cookie", ())) > 1:
            response = self._handler.unauthorized_response()
            await self._send(send, response)
            return
        headers = {name: values[0] for name, values in header_values.items()}

        chunks: list[bytes] = []
        size = 0
        too_large = False
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > _MAX_BODY:
                too_large = True
                break
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        if too_large:
            response = self._handler._error_response(DemoUseCaseError("validation_error"))
        else:
            response = await asyncio.to_thread(
                self._handler.handle,
                method=str(scope.get("method", "")),
                path=str(scope.get("path", "")),
                headers=headers,
                body_bytes=b"".join(chunks),
                query_string=scope.get("query_string", b""),
            )
        await self._send(send, response)

    @staticmethod
    async def _send(send, response: Mapping[str, object]) -> None:
        body = response["body"]
        if isinstance(body, str):
            encoded = body.encode("utf-8")
        else:
            encoded = bytes(body)
        content_type = str(response["content_type"])
        if content_type.startswith("text/") and "charset=" not in content_type:
            content_type += "; charset=utf-8"
        security_headers = (
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
            (
                b"content-security-policy",
                b"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            ),
        )
        await send({
            "type": "http.response.start",
            "status": int(response["status"]),
            "headers": [
                (b"content-type", content_type.encode("ascii")),
                (b"content-length", str(len(encoded)).encode("ascii")),
                *security_headers,
                *response.get("extra_headers", ()),
            ],
        })
        await send({"type": "http.response.body", "body": encoded})


def create_demo_fastapi_app(asgi_app: DemoUiAsgiApp):
    """Mount the Demo ASGI boundary in a FastAPI application."""
    from fastapi import FastAPI

    fastapi_app = FastAPI(
        title="CryptoTrust Agent 本機示範",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    fastapi_app.mount("/", asgi_app)
    return fastapi_app


def create_local_demo_fastapi_app(
    *,
    dataset_status: str = "healthy",
    dataset_reason_code: str | None = None,
):
    """Build an isolated FastAPI app and expose its composition for local tests."""
    from crypto_trust_agent.presentation.api.demo_ui_composition import (
        DEMO_COOKIE_NAME,
        DEMO_USER_TOKEN,
        build_local_demo_composition,
    )

    composition = build_local_demo_composition(
        dataset_status=dataset_status,
        dataset_reason_code=dataset_reason_code,
    )
    handler = DemoUiHttpHandler(
        DemoApp(composition.use_case),
        composition.use_case,
        composition.authenticator,
        browser_token=DEMO_USER_TOKEN,
        cookie_name=DEMO_COOKIE_NAME,
    )
    fastapi_app = create_demo_fastapi_app(DemoUiAsgiApp(handler))
    fastapi_app.state.demo_composition = composition
    return fastapi_app


app = create_local_demo_fastapi_app()


__all__ = (
    "DemoAuthenticator",
    "DemoUiAsgiApp",
    "DemoUiHttpHandler",
    "app",
    "create_demo_fastapi_app",
    "create_local_demo_fastapi_app",
)
