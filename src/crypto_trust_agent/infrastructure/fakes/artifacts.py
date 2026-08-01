"""Hash-verifying in-memory ArtifactRepository fake (non-production)."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import timedelta
from threading import RLock

from crypto_trust_agent.application.dto.common import ErrorResultDTO
from crypto_trust_agent.application.dto.repositories import (
    ArtifactContentDTO,
    ArtifactDescriptorDTO,
    ArtifactExecutionRequestDTO,
    ArtifactKeyRequestDTO,
    ArtifactListDTO,
    ArtifactManifestDTO,
    ArtifactPutRequestDTO,
    PutManifestRequestDTO,
)
from crypto_trust_agent.domain.primitives import UtcInstant
from crypto_trust_agent.infrastructure.fakes.clock import FakeClock
from crypto_trust_agent.infrastructure.fakes.repositories import _error, _guard_deadlines


@_guard_deadlines(
    "artifact_repository",
    {"put": 5000, "get": 5000, "list_for_execution": 5000, "put_manifest": 5000, "get_manifest": 5000},
)
class FakeArtifactRepository:
    """Single-process fake; not a production object store implementation."""

    non_production = True

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self._lock = RLock()
        self._items: dict[tuple[str, str, str, str, str], tuple[ArtifactDescriptorDTO, str]] = {}
        self._manifests: dict[tuple[str, str], tuple[ArtifactManifestDTO, ArtifactDescriptorDTO, str]] = {}

    def put(self, request: ArtifactPutRequestDTO) -> ArtifactDescriptorDTO | ErrorResultDTO:
        with self._lock:
            if request.artifact_type == "manifest":
                return _error(self._clock, "artifact_repository", request.operation_id, "artifact_conflict")
            content = self._decode(request.content_base64, request.operation_id)
            if isinstance(content, ErrorResultDTO):
                return content
            actual_hash = "sha256:" + hashlib.sha256(content).hexdigest()
            if actual_hash != request.sha256:
                return _error(self._clock, "artifact_repository", request.operation_id, "artifact_hash_mismatch", "integrity")
            if len(content) != request.size_bytes:
                return _error(self._clock, "artifact_repository", request.operation_id, "artifact_size_mismatch", "integrity")
            key = (request.task_id, request.execution_id, request.artifact_type, request.format, request.content_schema_version)
            existing = self._items.get(key)
            if existing is not None:
                if existing[0].sha256 == request.sha256:
                    return existing[0]
                return _error(self._clock, "artifact_repository", request.operation_id, "artifact_conflict")
            artifact_id = f"ART-{len(self._items) + 1:08d}"
            descriptor = ArtifactDescriptorDTO(
                artifact_id=artifact_id,
                task_id=request.task_id,
                execution_id=request.execution_id,
                artifact_type=request.artifact_type,
                format=request.format,
                mime_type=request.mime_type,
                content_schema_version=request.content_schema_version,
                locator=f"urn:cryptotrust:artifact:{artifact_id}",
                sha256=request.sha256,
                size_bytes=request.size_bytes,
                generated_at=request.generated_at,
                stored_at=self._clock.current_utc(),
            )
            self._items[key] = (descriptor, request.content_base64)
            return descriptor

    def get(self, request: ArtifactKeyRequestDTO) -> ArtifactContentDTO | ErrorResultDTO:
        with self._lock:
            key = (request.task_id, request.execution_id, request.artifact_type, request.format, request.content_schema_version)
            item = self._items.get(key)
            if item is None:
                return _error(self._clock, "artifact_repository", request.operation_id, "artifact_not_found", "not_found")
            if len(item[1]) <= 1_398_104:
                delivery = {"kind": "inline", "content_base64": item[1]}
            else:
                expires = self._clock.current_utc().as_datetime() + timedelta(minutes=5)
                delivery = {
                    "kind": "signed_locator",
                    "locator": f"https://fake.invalid/artifacts/{item[0].artifact_id}",
                    "expires_at": UtcInstant(expires.isoformat().replace("+00:00", "Z")),
                }
            return ArtifactContentDTO(item[0], delivery)

    def list_for_execution(self, request: ArtifactExecutionRequestDTO) -> ArtifactListDTO:
        with self._lock:
            descriptors = [item[0] for key, item in self._items.items() if key[0] == request.task_id and key[1] == request.execution_id]
            manifest = self._manifests.get((request.task_id, request.execution_id))
            if manifest is not None:
                descriptors.append(manifest[1])
            return ArtifactListDTO(request.task_id, request.execution_id, tuple(sorted(descriptors, key=lambda item: item.artifact_id)))

    def put_manifest(self, request: PutManifestRequestDTO) -> ArtifactDescriptorDTO | ErrorResultDTO:
        with self._lock:
            content = self._decode(request.content_base64, request.operation_id)
            if isinstance(content, ErrorResultDTO):
                return content
            actual_hash = "sha256:" + hashlib.sha256(content).hexdigest()
            if actual_hash != request.sha256:
                return _error(self._clock, "artifact_repository", request.operation_id, "artifact_hash_mismatch", "integrity")
            if len(content) != request.size_bytes:
                return _error(self._clock, "artifact_repository", request.operation_id, "artifact_size_mismatch", "integrity")
            manifest = request.manifest
            try:
                decoded_manifest = json.loads(content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return _error(self._clock, "artifact_repository", request.operation_id, "manifest_incomplete", "integrity")
            if decoded_manifest != manifest.to_wire():
                return _error(self._clock, "artifact_repository", request.operation_id, "manifest_incomplete", "integrity")
            key = (manifest.task_id, manifest.execution_id)
            existing = self._manifests.get(key)
            if existing is not None:
                if existing[1].sha256 == request.sha256:
                    return existing[1]
                return _error(self._clock, "artifact_repository", request.operation_id, "artifact_conflict")
            available = {(str(item["artifact_type"]), str(item["format"])): item for item in manifest.available}
            required = {("final_report", "json"), ("evidence_list", "json"), ("execution_log", "jsonl")}
            if not required.issubset(available) or ("manifest", "json") in available:
                return _error(self._clock, "artifact_repository", request.operation_id, "manifest_incomplete", "integrity")
            logical_pairs = set(available)
            missing_pairs = {(str(item["artifact_type"]), str(item["format"])) for item in manifest.missing}
            if logical_pairs & missing_pairs or (manifest.publication_outcome == "complete" and manifest.missing) or (manifest.publication_outcome == "partial" and not manifest.missing):
                return _error(self._clock, "artifact_repository", request.operation_id, "manifest_incomplete", "integrity")
            for pair, entry in available.items():
                stored = self._items.get((manifest.task_id, manifest.execution_id, pair[0], pair[1], str(entry["content_schema_version"])))
                if stored is None:
                    return _error(self._clock, "artifact_repository", request.operation_id, "manifest_order_violation", "integrity")
                descriptor = stored[0]
                if any((str(entry["artifact_id"]) != descriptor.artifact_id, str(entry["sha256"]) != descriptor.sha256, int(entry["size_bytes"]) != descriptor.size_bytes)):
                    return _error(self._clock, "artifact_repository", request.operation_id, "manifest_incomplete", "integrity")
                if descriptor.generated_at.as_datetime() > manifest.generated_at.as_datetime():
                    return _error(self._clock, "artifact_repository", request.operation_id, "manifest_order_violation", "integrity")
            artifact_id = f"ART-MANIFEST-{len(self._manifests) + 1:08d}"
            descriptor = ArtifactDescriptorDTO(
                artifact_id=artifact_id,
                task_id=manifest.task_id,
                execution_id=manifest.execution_id,
                artifact_type="manifest",
                format="json",
                mime_type="application/json",
                content_schema_version="1.0.0",
                locator=f"urn:cryptotrust:artifact:{artifact_id}",
                sha256=request.sha256,
                size_bytes=request.size_bytes,
                generated_at=manifest.generated_at,
                stored_at=self._clock.current_utc(),
            )
            self._manifests[key] = (manifest, descriptor, request.content_base64)
            return descriptor

    def get_manifest(self, request: ArtifactExecutionRequestDTO) -> ArtifactManifestDTO | ErrorResultDTO:
        with self._lock:
            item = self._manifests.get((request.task_id, request.execution_id))
            if item is None:
                return _error(self._clock, "artifact_repository", request.operation_id, "artifact_not_found", "not_found")
            return item[0]

    def _decode(self, value: str, operation_id: str) -> bytes | ErrorResultDTO:
        try:
            return base64.b64decode(value, validate=True)
        except (ValueError, TypeError):
            return _error(self._clock, "artifact_repository", operation_id, "artifact_hash_mismatch", "integrity")
