"""Explicit-live SageMaker boundary for PA72.

Importing this module never imports an AWS SDK. The production factory loads the
SDK only after the explicit live gate and required local configuration pass.
"""
from __future__ import annotations

import importlib
import os
import time
from collections.abc import Callable, Mapping
from typing import Protocol

SAGEMAKER_INVOKE_ENDPOINT_MAX_RESPONSE_BYTES = 6_291_456
_RETRY_POLICY: dict[str, object] = {
    "total_max_attempts": 1,
    "mode": "standard",
}
_CREDENTIAL_CONFIGURATION_ERROR = "Live SageMaker credential configuration is invalid"


class LiveSageMakerError(RuntimeError):
    """Fixed, local failure that contains no provider or configuration details."""


class LiveSageMakerDependencyError(LiveSageMakerError):
    """Raised when the optional production AWS SDK is unavailable."""


class LiveSageMakerCredentialError(LiveSageMakerError):
    """Raised before SDK activity when the credential selection is not explicit."""


class SageMakerClientFactory(Protocol):
    def __call__(
        self, service_name: str, *, region_name: str, config: object
    ) -> object: ...


class SageMakerConfigFactory(Protocol):
    def __call__(
        self,
        *,
        connect_timeout: float,
        read_timeout: float,
        retries: dict[str, object],
    ) -> object: ...


class SageMakerSession(Protocol):
    def client(
        self, service_name: str, *, region_name: str, config: object
    ) -> object: ...


class SageMakerSessionFactory(Protocol):
    def __call__(self, **kwargs: str) -> SageMakerSession: ...


def _credential_selection() -> tuple[str, str | None]:
    mode = os.getenv("PA72_AWS_CREDENTIAL_MODE", "").strip()
    if mode == "profile":
        profile_name = os.getenv("PA72_AWS_PROFILE", "").strip()
        if not profile_name:
            raise LiveSageMakerCredentialError(_CREDENTIAL_CONFIGURATION_ERROR)
        return mode, profile_name
    if mode == "runtime_role":
        return mode, None
    raise LiveSageMakerCredentialError(_CREDENTIAL_CONFIGURATION_ERROR)


class SageMakerRuntimeInvoker(Protocol):
    max_attempts: int
    hidden_retries: int

    def invoke(
        self,
        *,
        endpoint_name: str,
        region: str,
        body: bytes,
        content_type: str,
        accept: str,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bytes: ...

    def probe(
        self,
        *,
        endpoint_name: str,
        region: str,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bool: ...


class Boto3SageMakerInvoker:
    """One-attempt synchronous SageMaker invoker with per-call clients.

    A synchronous SDK request cannot be cancelled server-side here. Cancellation
    and deadlines prevent calls before dispatch and discard results after a call
    or bounded body read returns.
    """

    max_attempts = 1
    hidden_retries = 0

    def __init__(
        self,
        client_factory: SageMakerClientFactory,
        config_factory: SageMakerConfigFactory,
        *,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._config_factory = config_factory
        self._monotonic = monotonic or time.monotonic

    @classmethod
    def production(
        cls,
        *,
        monotonic: Callable[[], float] | None = None,
        session_factory: SageMakerSessionFactory | None = None,
        config_factory: SageMakerConfigFactory | None = None,
    ) -> Boto3SageMakerInvoker:
        """Build clients from one explicitly selected credential source.

        Credential configuration is validated before any SDK import, session
        construction, client construction, or credential-provider resolution.
        Supplying both factories is an offline seam and performs no SDK import.
        """
        mode, profile_name = _credential_selection()
        if (session_factory is None) != (config_factory is None):
            raise ValueError("SageMaker production factories must be provided together")
        if session_factory is None and config_factory is None:
            try:
                boto3_module = importlib.import_module("boto3")
                config_module = importlib.import_module("botocore.config")
                session_factory = boto3_module.Session
                config_factory = config_module.Config
                if not callable(session_factory) or not callable(config_factory):
                    raise TypeError
            except Exception:  # noqa: BLE001 - optional SDK loading must fail closed
                raise LiveSageMakerDependencyError(
                    "Live SageMaker SDK dependency is unavailable"
                ) from None

        assert session_factory is not None
        assert config_factory is not None
        try:
            session = (
                session_factory(profile_name=profile_name)
                if mode == "profile" and profile_name is not None
                else session_factory()
            )
            sdk_client = session.client
            if not callable(sdk_client):
                raise TypeError
        except Exception:  # noqa: BLE001 - redact session and credential failures
            raise LiveSageMakerError("SageMaker session setup failed") from None

        def client_factory(
            service_name: str, *, region_name: str, config: object
        ) -> object:
            return sdk_client(service_name, region_name=region_name, config=config)

        return cls(client_factory, config_factory, monotonic=monotonic)

    def _remaining(
        self, deadline: float, cancelled: Callable[[], bool]
    ) -> float:
        if cancelled():
            raise TimeoutError("SageMaker request was cancelled")
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise TimeoutError("SageMaker request deadline was exceeded")
        return remaining

    def _new_client(
        self,
        service_name: str,
        *,
        region: str,
        deadline: float,
        cancelled: Callable[[], bool],
    ) -> object:
        remaining = self._remaining(deadline, cancelled)
        try:
            config = self._config_factory(
                connect_timeout=remaining,
                read_timeout=remaining,
                retries=dict(_RETRY_POLICY),
            )
            current_remaining = self._remaining(deadline, cancelled)
            if current_remaining < remaining:
                config = self._config_factory(
                    connect_timeout=current_remaining,
                    read_timeout=current_remaining,
                    retries=dict(_RETRY_POLICY),
                )
            self._remaining(deadline, cancelled)
            return self._client_factory(
                service_name, region_name=region, config=config
            )
        except TimeoutError:
            raise
        except Exception:  # noqa: BLE001 - redact unknown SDK factory failures
            raise LiveSageMakerError("SageMaker request setup failed") from None

    @staticmethod
    def _close_body(response: object, body: object | None) -> None:
        candidate = body
        if candidate is None and isinstance(response, Mapping):
            try:
                candidate = response.get("Body")
            except Exception:  # noqa: BLE001 - untrusted SDK response
                candidate = None
        close = getattr(candidate, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - cleanup cannot alter outcome
                return

    def invoke(
        self,
        *,
        endpoint_name: str,
        region: str,
        body: bytes,
        content_type: str,
        accept: str,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bytes:
        deadline = self._monotonic() + (timeout_ms / 1_000)
        self._remaining(deadline, cancelled)
        client = self._new_client(
            "sagemaker-runtime",
            region=region,
            deadline=deadline,
            cancelled=cancelled,
        )
        invoke_endpoint = getattr(client, "invoke_endpoint", None)
        if not callable(invoke_endpoint):
            raise LiveSageMakerError("SageMaker runtime client is invalid")
        try:
            response = invoke_endpoint(
                EndpointName=endpoint_name,
                Body=body,
                ContentType=content_type,
                Accept=accept,
            )
        except Exception:  # noqa: BLE001 - redact unknown runtime failures
            raise LiveSageMakerError("SageMaker request failed") from None

        response_body: object | None = None
        try:
            self._remaining(deadline, cancelled)
            if not isinstance(response, Mapping):
                raise LiveSageMakerError("SageMaker response was invalid")
            response_body = response.get("Body")
            reader = getattr(response_body, "read", None)
            if not callable(reader):
                raise LiveSageMakerError("SageMaker response was invalid")
            self._remaining(deadline, cancelled)
            try:
                raw = reader(SAGEMAKER_INVOKE_ENDPOINT_MAX_RESPONSE_BYTES + 1)
            except Exception:  # noqa: BLE001 - redact untrusted body failures
                raise LiveSageMakerError("SageMaker response read failed") from None
            self._remaining(deadline, cancelled)
            if not isinstance(raw, (bytes, bytearray)):
                raise LiveSageMakerError("SageMaker response was invalid")
            if len(raw) > SAGEMAKER_INVOKE_ENDPOINT_MAX_RESPONSE_BYTES:
                raise LiveSageMakerError(
                    "SageMaker response exceeded the safe size limit"
                )
            return bytes(raw)
        finally:
            self._close_body(response, response_body)

    def probe(
        self,
        *,
        endpoint_name: str,
        region: str,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bool:
        deadline = self._monotonic() + (timeout_ms / 1_000)
        self._remaining(deadline, cancelled)
        client = self._new_client(
            "sagemaker",
            region=region,
            deadline=deadline,
            cancelled=cancelled,
        )
        describe_endpoint = getattr(client, "describe_endpoint", None)
        if not callable(describe_endpoint):
            raise LiveSageMakerError("SageMaker control client is invalid")
        try:
            response = describe_endpoint(EndpointName=endpoint_name)
        except Exception:  # noqa: BLE001 - redact unknown control-plane failures
            raise LiveSageMakerError("SageMaker readiness probe failed") from None
        self._remaining(deadline, cancelled)
        if not isinstance(response, Mapping):
            return False
        status = response.get("EndpointStatus")
        return isinstance(status, str) and status == "InService"


class ExplicitLiveSageMakerClient:
    """Thin opt-in client; endpoint details remain inside Infrastructure."""

    non_production = False
    max_attempts = 1
    hidden_retries = 0

    def __init__(
        self,
        invoker: SageMakerRuntimeInvoker,
        *,
        endpoint_name: str,
        region: str,
        enabled: bool,
    ) -> None:
        if not enabled:
            raise RuntimeError("Live SageMaker inference requires explicit opt-in")
        if not endpoint_name or not region:
            raise ValueError("SageMaker endpoint and region must be configured")
        if invoker.max_attempts != 1 or invoker.hidden_retries != 0:
            raise ValueError(
                "SageMaker invoker must use one attempt and zero hidden retries"
            )
        self._invoker = invoker
        self._endpoint_name = endpoint_name
        self._region = region

    @classmethod
    def from_environment(
        cls,
        invoker: SageMakerRuntimeInvoker | None = None,
        *,
        client_factory: SageMakerClientFactory | None = None,
        config_factory: SageMakerConfigFactory | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> ExplicitLiveSageMakerClient:
        endpoint_name = os.getenv("PA72_SAGEMAKER_ENDPOINT", "")
        region = os.getenv("PA72_AWS_REGION", "")
        enabled = os.getenv("PA72_SAGEMAKER_LIVE_INTEGRATION") == "1"
        if not enabled:
            raise RuntimeError("Live SageMaker inference requires explicit opt-in")
        if not endpoint_name or not region:
            raise ValueError("SageMaker endpoint and region must be configured")
        if invoker is not None and (
            client_factory is not None or config_factory is not None
        ):
            raise ValueError("SageMaker invoker configuration is ambiguous")
        if invoker is None:
            if (client_factory is None) != (config_factory is None):
                raise ValueError("SageMaker factories must be provided together")
            if client_factory is not None and config_factory is not None:
                invoker = Boto3SageMakerInvoker(
                    client_factory,
                    config_factory,
                    monotonic=monotonic,
                )
            else:
                invoker = Boto3SageMakerInvoker.production(monotonic=monotonic)
        return cls(
            invoker,
            endpoint_name=endpoint_name,
            region=region,
            enabled=True,
        )

    def invoke(
        self,
        *,
        operation_id: str,
        body: bytes,
        timeout_ms: int,
        cancelled: Callable[[], bool],
    ) -> bytes:
        del operation_id
        return self._invoker.invoke(
            endpoint_name=self._endpoint_name,
            region=self._region,
            body=body,
            content_type="application/json",
            accept="application/json",
            timeout_ms=timeout_ms,
            cancelled=cancelled,
        )

    def probe(self, *, timeout_ms: int, cancelled: Callable[[], bool]) -> bool:
        return self._invoker.probe(
            endpoint_name=self._endpoint_name,
            region=self._region,
            timeout_ms=timeout_ms,
            cancelled=cancelled,
        )


__all__ = (
    "SAGEMAKER_INVOKE_ENDPOINT_MAX_RESPONSE_BYTES",
    "Boto3SageMakerInvoker",
    "ExplicitLiveSageMakerClient",
    "LiveSageMakerCredentialError",
    "LiveSageMakerDependencyError",
    "LiveSageMakerError",
    "SageMakerClientFactory",
    "SageMakerConfigFactory",
    "SageMakerRuntimeInvoker",
    "SageMakerSessionFactory",
)
