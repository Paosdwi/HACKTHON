"""Explicit-live Nova 2 Lite client boundary for PA71.

No AWS SDK type crosses this module. Composition supplies an invoker only when
PA71_LIVE_INTEGRATION=1; importing or constructing the default adapter is offline.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import os
from typing import Protocol


class NovaRuntimeInvoker(Protocol):
    def invoke(self, *, model_id: str, region: str, payload: Mapping[str, object],
               timeout_ms: int, cancelled: Callable[[], bool]) -> Mapping[str, object]: ...

    def probe(self, *, model_id: str, region: str, timeout_ms: int,
              cancelled: Callable[[], bool]) -> bool: ...


class ExplicitLiveNovaClient:
    """Thin opt-in boundary; credentials and auth headers remain inside invoker."""

    non_production = False

    def __init__(self, invoker: NovaRuntimeInvoker, *, model_id: str, region: str, enabled: bool) -> None:
        if not enabled:
            raise RuntimeError("Live Nova extraction requires explicit opt-in")
        if not model_id or not region:
            raise ValueError("Live Nova model and region must be configured")
        self._invoker = invoker
        self._model_id = model_id
        self._region = region

    @classmethod
    def from_environment(cls, invoker: NovaRuntimeInvoker) -> "ExplicitLiveNovaClient":
        return cls(
            invoker, model_id=os.getenv("PA71_NOVA_MODEL_ID", ""),
            region=os.getenv("PA71_AWS_REGION", ""),
            enabled=os.getenv("PA71_LIVE_INTEGRATION") == "1",
        )

    def invoke(self, *, operation_id: str, payload: Mapping[str, object], timeout_ms: int,
               cancelled: Callable[[], bool]) -> Mapping[str, object]:
        del operation_id
        return self._invoker.invoke(
            model_id=self._model_id, region=self._region, payload=payload,
            timeout_ms=timeout_ms, cancelled=cancelled,
        )

    def probe(self, *, timeout_ms: int, cancelled: Callable[[], bool]) -> bool:
        return self._invoker.probe(
            model_id=self._model_id, region=self._region,
            timeout_ms=timeout_ms, cancelled=cancelled,
        )


__all__ = ("ExplicitLiveNovaClient", "NovaRuntimeInvoker")
