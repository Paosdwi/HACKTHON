"""ADR-001 deterministic request fingerprint canonicalization。"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime


FINGERPRINT_RULESET_VERSION = "fingerprint-1.0.0"
SUPPORTED_ASSETS = frozenset({"BTC", "ETH", "SOL", "BNB", "XRP"})
_RFC3339_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


class FingerprintValidationError(ValueError):
    """輸入無法依 fingerprint-1.0.0 無損 canonicalize。"""


@dataclass(frozen=True, slots=True)
class RequestFingerprint:
    normalized_question: str
    assets_requested_order: tuple[str, ...]
    assets_canonical: tuple[str, ...]
    timeframe_start: str
    timeframe_end: str
    canonical_json: bytes
    request_fingerprint: str
    ruleset_version: str = FINGERPRINT_RULESET_VERSION


def _normalize_question(question: str) -> str:
    if not isinstance(question, str):
        raise FingerprintValidationError("question must be a string")
    normalized = unicodedata.normalize("NFKC", question)
    normalized = re.sub(r"\s+", " ", normalized, flags=re.UNICODE).strip()
    if not normalized:
        raise FingerprintValidationError("question must not be empty")
    return normalized


def _normalize_assets(assets: tuple[str, ...] | list[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not isinstance(assets, (tuple, list)) or not assets:
        raise FingerprintValidationError("at least one asset is required")
    requested: list[str] = []
    for raw in assets:
        if not isinstance(raw, str):
            raise FingerprintValidationError("asset must be a string")
        normalized = raw.upper()
        if normalized not in SUPPORTED_ASSETS:
            raise FingerprintValidationError("unsupported asset")
        requested.append(normalized)
    if len(set(requested)) != len(requested):
        raise FingerprintValidationError("duplicate asset after normalization")
    requested_order = tuple(requested)
    return requested_order, tuple(sorted(requested_order))


def _normalize_minute(value: str) -> str:
    if not isinstance(value, str) or not _RFC3339_PATTERN.fullmatch(value):
        raise FingerprintValidationError("timeframe must be timezone-aware RFC 3339")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as error:
        raise FingerprintValidationError("invalid timeframe instant") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FingerprintValidationError("timeframe timezone is required")
    if parsed.second != 0 or parsed.microsecond != 0:
        raise FingerprintValidationError("timeframe must be aligned to a whole minute")
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:00Z")


def create_request_fingerprint(
    *,
    question: str,
    assets: tuple[str, ...] | list[str],
    timeframe_start: str,
    timeframe_end: str,
) -> RequestFingerprint:
    """以 RFC 8785-compatible 的受限 JSON payload 產生 SHA-256 fingerprint。"""

    normalized_question = _normalize_question(question)
    requested_order, canonical_assets = _normalize_assets(assets)
    start = _normalize_minute(timeframe_start)
    end = _normalize_minute(timeframe_end)
    payload = {
        "assets_canonical": list(canonical_assets),
        "question": normalized_question,
        "timeframe": {"end": end, "start": start},
    }
    canonical_json = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(canonical_json).hexdigest()
    return RequestFingerprint(
        normalized_question=normalized_question,
        assets_requested_order=requested_order,
        assets_canonical=canonical_assets,
        timeframe_start=start,
        timeframe_end=end,
        canonical_json=canonical_json,
        request_fingerprint=f"sha256:{digest}",
    )
