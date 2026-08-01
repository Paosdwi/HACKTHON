"""Explicit-live SageMaker runtime boundary for PA72.

No AWS SDK is imported here. Composition must inject an invoker that enforces the
passed timeout, cancellation, one attempt, and zero hidden retries. Environment
construction is fail-closed unless PA72_SAGEMAKER_LIVE_INTEGRATION=1.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from typing import Protocol


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


class ExplicitLiveSageMakerClient:
    """Thin opt-in client; endpoint details and credentials remain in Infrastructure."""

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
            raise ValueError("SageMaker invoker must use one attempt and zero hidden retries")
        self._invoker = invoker
        self._endpoint_name = endpoint_name
        self._region = region

    @classmethod
    def from_environment(
        cls, invoker: SageMakerRuntimeInvoker
    ) -> ExplicitLiveSageMakerClient:
        return cls(
            invoker,
            endpoint_name=os.getenv("PA72_SAGEMAKER_ENDPOINT", ""),
            region=os.getenv("PA72_AWS_REGION", ""),
            enabled=os.getenv("PA72_SAGEMAKER_LIVE_INTEGRATION") == "1",
        )

    def invoke(
        self, *, operation_id: str, body: bytes, timeout_ms: int,
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


__all__ = ("ExplicitLiveSageMakerClient", "SageMakerRuntimeInvoker")
