"""Application boundary for the local Demo UI.

The Demo workflow composes the existing Core use cases and Formal Run
orchestrator.  It owns only demo-scoped run/ownership state and artifact
queries; publication remains exclusively inside the T64 orchestrator bridge.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import timedelta
from threading import RLock
from types import MappingProxyType
from typing import Callable, Mapping, Protocol, Sequence, runtime_checkable

from crypto_trust_agent.application.dto.common import DeadlineDTO, ErrorResultDTO
from crypto_trust_agent.application.dto.repositories import (
    ArtifactExecutionRequestDTO,
    ArtifactKeyRequestDTO,
    ArtifactListDTO,
    ClockReadRequestDTO,
)
from crypto_trust_agent.application.orchestration.formal_run import (
    FormalRunCommand,
    FormalRunOrchestrator,
    FormalRunValidationError,
    TerminalOutcome,
)
from crypto_trust_agent.application.planning import QuestionType, build_plan
from crypto_trust_agent.application.ports.repositories import ArtifactRepository, Clock
from crypto_trust_agent.application.use_cases.create_task import (
    CreateTaskCommand,
    CreateTaskDependencyError,
    CreateTaskRateLimited,
    CreateTaskUseCase,
    CreateTaskValidationError,
)
from crypto_trust_agent.application.use_cases.preflight import (
    PreflightCommand,
    PreflightDependencyError,
    PreflightRateLimited,
    PreflightTaskNotFound,
    PreflightUseCase,
)
from crypto_trust_agent.application.use_cases.start_formal_execution import (
    StartFormalExecutionAuthorizationError,
    StartFormalExecutionCommand,
    StartFormalExecutionConflict,
    StartFormalExecutionDependencyError,
    StartFormalExecutionTaskNotFound,
    StartFormalExecutionUseCase,
    StartFormalExecutionValidationError,
)
from crypto_trust_agent.domain.fingerprint import (
    FingerprintValidationError,
    create_request_fingerprint,
)

SUPPORTED_ASSETS = ("BTC", "ETH", "SOL", "BNB", "XRP")
_ASSET_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
_TASK_ID = re.compile(r"^TASK-[A-Za-z0-9._:-]{1,123}$")
_EXECUTION_ID = re.compile(r"^EXEC-[A-Za-z0-9._:-]{1,123}$")
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_QUESTION_MAX = 2_000
_MAX_RUNS = 128

_ARTIFACT_TYPES: dict[tuple[str, str], tuple[str, str]] = {
    ("final_report", "json"): ("application/json", "final_report.json"),
    ("evidence_list", "json"): ("application/json", "evidence_list.json"),
    ("execution_log", "jsonl"): ("application/x-ndjson", "execution_log.jsonl"),
    ("manifest", "json"): ("application/json", "manifest.json"),
}

_SAFE_MESSAGES: dict[str, tuple[int, str]] = {
    "validation_error": (422, "輸入資料無效，請檢查後再試一次。"),
    "unsupported_asset": (422, "不支援指定的幣種。"),
    "not_found": (404, "找不到要求的分析執行。"),
    "artifact_not_found": (404, "找不到要求的成果檔案。"),
    "artifact_hash_mismatch": (409, "成果檔案完整性驗證失敗。"),
    "artifact_size_mismatch": (409, "成果檔案大小驗證失敗。"),
    "preflight_not_ready": (409, "執行前檢查未通過，未建立正式分析。"),
    "dependency_unavailable": (503, "必要服務暫時無法使用。"),
    "rate_limited": (429, "要求次數已達上限，請稍後再試。"),
    "internal_error": (500, "系統暫時無法完成要求，請稍後再試。"),
    "unsafe_filename": (400, "要求的檔案名稱無效。"),
}


class DemoUseCaseError(Exception):
    """Safe typed error.  Arbitrary dependency messages never reach Presentation."""

    def __init__(self, code: str) -> None:
        normalized = code if code in _SAFE_MESSAGES else "internal_error"
        self.code = normalized
        self.status_code, self.safe_message = _SAFE_MESSAGES[normalized]
        super().__init__(self.safe_message)


@dataclass(frozen=True, slots=True)
class ArtifactDownloadDTO:
    content_bytes: bytes
    filename: str
    content_type: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class DemoArtifactDocumentDTO:
    """Integrity-checked artifact wire data safe for Presentation rendering."""

    task_id: str
    execution_id: str
    artifact_type: str
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class DemoRunStatusDTO:
    task_id: str
    execution_id: str | None
    state: str
    terminal_outcome: str | None
    publication_outcome: str | None
    partial_reasons: tuple[str, ...]
    degradation_reasons: tuple[str, ...]
    remaining_seconds: int | None
    artifact_count: int


@dataclass(frozen=True, slots=True)
class DemoSubmitRunResultDTO:
    task_id: str
    preflight_id: str
    preflight_ready: bool
    execution_id: str | None
    state: str
    terminal_outcome: str | None
    publication_outcome: str | None
    safe_reason_codes: tuple[str, ...]


@runtime_checkable
class DemoPrincipal(Protocol):
    @property
    def subject(self) -> str: ...

    @property
    def pseudonym(self) -> str: ...

    @property
    def is_admin(self) -> bool: ...


QuestionClassifier = Callable[[str, tuple[str, ...]], QuestionType | str]
IdentifierFactory = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class _OwnedRun:
    owner_subject: str
    preflight_id: str | None
    status: DemoRunStatusDTO


class DemoUseCase:
    """Run the complete demo workflow and expose ownership-checked queries."""

    def __init__(
        self,
        *,
        create_task: CreateTaskUseCase,
        preflight: PreflightUseCase,
        start_execution: StartFormalExecutionUseCase,
        orchestrator: FormalRunOrchestrator,
        artifact_repository: ArtifactRepository,
        clock: Clock,
        identifier_factory: IdentifierFactory,
        question_classifier: QuestionClassifier,
    ) -> None:
        self._create_task = create_task
        self._preflight = preflight
        self._start_execution = start_execution
        self._orchestrator = orchestrator
        self._artifacts = artifact_repository
        self._clock = clock
        self._new_id = identifier_factory
        self._classify = question_classifier
        self._runs: OrderedDict[str, _OwnedRun] = OrderedDict()
        self._runs_lock = RLock()

    def submit_and_run(
        self,
        *,
        principal: DemoPrincipal,
        question: str,
        assets: Sequence[str],
        timeframe_start: str,
        timeframe_end: str,
    ) -> DemoSubmitRunResultDTO:
        """Execute Create→Preflight→Start→Orchestrate→Publish synchronously."""

        self._validate_input(question, assets)
        try:
            fingerprint = create_request_fingerprint(
                question=question,
                assets=tuple(assets),
                timeframe_start=timeframe_start,
                timeframe_end=timeframe_end,
            )
            question_type = QuestionType(
                self._classify(
                    fingerprint.normalized_question,
                    fingerprint.assets_requested_order,
                )
            )
            clock_snapshot = str(self._read_now("OP-DEMO-PLAN-CLOCK-"))
            plan = build_plan(
                question_type=question_type,
                fingerprint=fingerprint,
                clock_snapshot=clock_snapshot,
            )
            created = self._create_task.execute(
                CreateTaskCommand(
                    trusted_user_scope=principal.subject,
                    principal_pseudonym=principal.pseudonym,
                    question=question,
                    assets=tuple(assets),
                    timeframe_start=timeframe_start,
                    timeframe_end=timeframe_end,
                    formal_run=True,
                )
            )
            cached = self._register_task(principal, created.task_id)
            if cached.execution_id is not None or cached.state == "preflight_not_ready":
                return self._submit_result(cached, self._owned_run(principal, created.task_id).preflight_id or "PF-UNKNOWN")

            preflight = self._preflight.execute(
                PreflightCommand(principal.subject, created.task_id)
            )
            if not preflight.ready:
                status = DemoRunStatusDTO(
                    task_id=created.task_id,
                    execution_id=None,
                    state="preflight_not_ready",
                    terminal_outcome=None,
                    publication_outcome=None,
                    partial_reasons=preflight.safe_reason_codes,
                    degradation_reasons=(),
                    remaining_seconds=None,
                    artifact_count=0,
                )
                self._update_run(principal, created.task_id, preflight.record.preflight_id, status)
                return self._submit_result(status, preflight.record.preflight_id)

            started = self._start_execution.execute(
                StartFormalExecutionCommand(
                    trusted_user_scope=principal.subject,
                    principal_pseudonym=principal.pseudonym,
                    is_admin=principal.is_admin,
                    task_id=created.task_id,
                    preflight_id=preflight.record.preflight_id,
                    input_lock_hash=preflight.record.input_lock_hash,
                )
            )
            if started.execution is None:
                raise DemoUseCaseError("internal_error")

            running = DemoRunStatusDTO(
                task_id=created.task_id,
                execution_id=started.execution.execution_id,
                state="running",
                terminal_outcome=None,
                publication_outcome=None,
                partial_reasons=(),
                degradation_reasons=(),
                remaining_seconds=900,
                artifact_count=0,
            )
            self._update_run(principal, created.task_id, preflight.record.preflight_id, running)

            result = self._orchestrator.execute(
                FormalRunCommand(
                    operation_id=self._new_id("OP-DEMO-RUN-"),
                    execution=started.execution,
                    plan=plan,
                    question=fingerprint.normalized_question,
                )
            )
            completed = DemoRunStatusDTO(
                task_id=result.task_id,
                execution_id=result.execution_id,
                state=(
                    "completed"
                    if result.terminal_outcome in {TerminalOutcome.SUCCESS, TerminalOutcome.PARTIAL}
                    else "failed"
                ),
                terminal_outcome=result.terminal_outcome.value,
                publication_outcome=result.publication_outcome,
                partial_reasons=result.partial_reason_codes,
                degradation_reasons=result.degraded_reason_codes,
                remaining_seconds=0,
                artifact_count=len(result.available_descriptors) + (1 if result.manifest_sha256 else 0),
            )
            self._update_run(principal, created.task_id, preflight.record.preflight_id, completed)
            return self._submit_result(completed, preflight.record.preflight_id)
        except DemoUseCaseError:
            raise
        except (CreateTaskValidationError, FingerprintValidationError, StartFormalExecutionValidationError, ValueError):
            raise DemoUseCaseError("validation_error") from None
        except (CreateTaskRateLimited, PreflightRateLimited):
            raise DemoUseCaseError("rate_limited") from None
        except (PreflightTaskNotFound, StartFormalExecutionTaskNotFound):
            raise DemoUseCaseError("not_found") from None
        except StartFormalExecutionConflict:
            raise DemoUseCaseError("preflight_not_ready") from None
        except StartFormalExecutionAuthorizationError:
            raise DemoUseCaseError("not_found") from None
        except (
            CreateTaskDependencyError,
            PreflightDependencyError,
            StartFormalExecutionDependencyError,
            FormalRunValidationError,
        ):
            raise DemoUseCaseError("dependency_unavailable") from None
        except Exception:
            raise DemoUseCaseError("internal_error") from None

    def get_run_status(
        self,
        *,
        principal: DemoPrincipal,
        task_id: str,
        execution_id: str | None,
    ) -> DemoRunStatusDTO:
        self._require_valid_task_id(task_id)
        if execution_id is not None:
            self._require_valid_execution_id(execution_id)
        record = self._owned_run(principal, task_id)
        if execution_id is not None and record.status.execution_id != execution_id:
            raise DemoUseCaseError("not_found")
        return record.status

    def list_artifacts(
        self,
        *,
        principal: DemoPrincipal,
        task_id: str,
        execution_id: str,
    ) -> ArtifactListDTO:
        self._require_owned_execution(principal, task_id, execution_id)
        operation_id, deadline = self._repository_deadline("OP-DEMO-LIST-")
        result = self._artifacts.list_for_execution(
            ArtifactExecutionRequestDTO(operation_id, task_id, execution_id, deadline)
        )
        if isinstance(result, ErrorResultDTO):
            raise DemoUseCaseError("not_found")
        return result

    def get_artifact(
        self,
        *,
        principal: DemoPrincipal,
        task_id: str,
        execution_id: str,
        artifact_type: str,
        artifact_format: str,
    ) -> ArtifactDownloadDTO:
        self._require_owned_execution(principal, task_id, execution_id)
        key = (artifact_type, artifact_format)
        if key not in _ARTIFACT_TYPES:
            raise DemoUseCaseError("artifact_not_found")
        content_type, base_filename = _ARTIFACT_TYPES[key]
        filename = f"{task_id}_{execution_id}_{base_filename}"
        if not _SAFE_FILENAME.fullmatch(filename) or ".." in filename:
            raise DemoUseCaseError("unsafe_filename")

        operation_id, deadline = self._repository_deadline("OP-DEMO-DOWNLOAD-")
        if key == ("manifest", "json"):
            manifest = self._artifacts.get_manifest(
                ArtifactExecutionRequestDTO(operation_id, task_id, execution_id, deadline)
            )
            if isinstance(manifest, ErrorResultDTO):
                raise DemoUseCaseError("artifact_not_found")
            content = json.dumps(
                manifest.to_wire(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            digest = "sha256:" + hashlib.sha256(content).hexdigest()
            return ArtifactDownloadDTO(content, filename, content_type, digest, len(content))

        result = self._artifacts.get(
            ArtifactKeyRequestDTO(
                operation_id,
                task_id,
                execution_id,
                artifact_type,
                artifact_format,
                "1.0.0",
                deadline,
            )
        )
        if isinstance(result, ErrorResultDTO):
            raise DemoUseCaseError("artifact_not_found")
        if result.delivery.get("kind") != "inline":
            raise DemoUseCaseError("artifact_not_found")
        encoded = result.delivery.get("content_base64")
        if not isinstance(encoded, str):
            raise DemoUseCaseError("artifact_not_found")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (TypeError, ValueError):
            raise DemoUseCaseError("artifact_not_found") from None
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        if digest != result.descriptor.sha256:
            raise DemoUseCaseError("artifact_hash_mismatch")
        if len(content) != result.descriptor.size_bytes:
            raise DemoUseCaseError("artifact_size_mismatch")
        return ArtifactDownloadDTO(content, filename, content_type, digest, len(content))

    def get_artifact_document(
        self,
        *,
        principal: DemoPrincipal,
        task_id: str,
        execution_id: str,
        artifact_type: str,
    ) -> DemoArtifactDocumentDTO:
        artifact_format = "jsonl" if artifact_type == "execution_log" else "json"
        download = self.get_artifact(
            principal=principal,
            task_id=task_id,
            execution_id=execution_id,
            artifact_type=artifact_type,
            artifact_format=artifact_format,
        )
        try:
            if artifact_type == "execution_log":
                entries = tuple(
                    json.loads(line)
                    for line in download.content_bytes.decode("utf-8").splitlines()
                    if line.strip()
                )
                if not entries or any(not isinstance(item, dict) for item in entries):
                    raise ValueError("invalid execution log")
                for item in entries:
                    self._verify_document_identity(item, task_id, execution_id)
                payload: Mapping[str, object] = {"entries": entries}
            else:
                parsed = json.loads(download.content_bytes.decode("utf-8"))
                if not isinstance(parsed, dict):
                    raise ValueError("invalid artifact document")
                self._verify_document_identity(parsed, task_id, execution_id)
                payload = parsed
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            raise DemoUseCaseError("artifact_hash_mismatch") from None
        return DemoArtifactDocumentDTO(
            task_id,
            execution_id,
            artifact_type,
            _freeze_json(payload),
        )

    def _register_task(self, principal: DemoPrincipal, task_id: str) -> DemoRunStatusDTO:
        initial = DemoRunStatusDTO(task_id, None, "preflight", None, None, (), (), None, 0)
        with self._runs_lock:
            existing = self._runs.get(task_id)
            if existing is not None:
                if existing.owner_subject != principal.subject:
                    raise DemoUseCaseError("not_found")
                self._runs.move_to_end(task_id)
                return existing.status
            self._runs[task_id] = _OwnedRun(principal.subject, None, initial)
            while len(self._runs) > _MAX_RUNS:
                self._runs.popitem(last=False)
        return initial

    def _update_run(
        self,
        principal: DemoPrincipal,
        task_id: str,
        preflight_id: str,
        status: DemoRunStatusDTO,
    ) -> None:
        with self._runs_lock:
            existing = self._runs.get(task_id)
            if existing is None or existing.owner_subject != principal.subject:
                raise DemoUseCaseError("not_found")
            self._runs[task_id] = replace(
                existing,
                preflight_id=preflight_id,
                status=status,
            )
            self._runs.move_to_end(task_id)

    def _owned_run(self, principal: DemoPrincipal, task_id: str) -> _OwnedRun:
        with self._runs_lock:
            record = self._runs.get(task_id)
            if record is None or record.owner_subject != principal.subject:
                raise DemoUseCaseError("not_found")
            self._runs.move_to_end(task_id)
            return record

    def _require_owned_execution(
        self,
        principal: DemoPrincipal,
        task_id: str,
        execution_id: str,
    ) -> _OwnedRun:
        self._require_valid_task_id(task_id)
        self._require_valid_execution_id(execution_id)
        record = self._owned_run(principal, task_id)
        if record.status.execution_id != execution_id:
            raise DemoUseCaseError("not_found")
        return record

    @staticmethod
    def _submit_result(
        status: DemoRunStatusDTO,
        preflight_id: str,
    ) -> DemoSubmitRunResultDTO:
        return DemoSubmitRunResultDTO(
            task_id=status.task_id,
            preflight_id=preflight_id,
            preflight_ready=status.state != "preflight_not_ready",
            execution_id=status.execution_id,
            state=status.state,
            terminal_outcome=status.terminal_outcome,
            publication_outcome=status.publication_outcome,
            safe_reason_codes=(
                status.partial_reasons
                if status.state == "preflight_not_ready"
                else status.degradation_reasons
            ),
        )

    def _validate_input(self, question: str, assets: Sequence[str]) -> None:
        if not isinstance(question, str) or not question.strip() or len(question) > _QUESTION_MAX:
            raise DemoUseCaseError("validation_error")
        if not isinstance(assets, Sequence) or isinstance(assets, (str, bytes)) or not assets:
            raise DemoUseCaseError("validation_error")
        normalized = tuple(assets)
        if len(normalized) > len(SUPPORTED_ASSETS) or len(set(normalized)) != len(normalized):
            raise DemoUseCaseError("validation_error")
        for asset in normalized:
            if not isinstance(asset, str) or not _ASSET_PATTERN.fullmatch(asset):
                raise DemoUseCaseError("validation_error")
            if asset not in SUPPORTED_ASSETS:
                raise DemoUseCaseError("unsupported_asset")

    @staticmethod
    def _require_valid_task_id(task_id: str) -> None:
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise DemoUseCaseError("validation_error")

    @staticmethod
    def _require_valid_execution_id(execution_id: str) -> None:
        if not isinstance(execution_id, str) or not _EXECUTION_ID.fullmatch(execution_id):
            raise DemoUseCaseError("validation_error")

    @staticmethod
    def _verify_document_identity(
        payload: Mapping[str, object],
        task_id: str,
        execution_id: str,
    ) -> None:
        if payload.get("task_id") != task_id or payload.get("execution_id") != execution_id:
            raise ValueError("artifact identity mismatch")
        if payload.get("schema_version") != "1.0.0":
            raise ValueError("artifact schema mismatch")

    def _read_now(self, operation_prefix: str):
        operation_id = self._new_id(operation_prefix)
        result = self._clock.now_utc(ClockReadRequestDTO(operation_id))
        if isinstance(result, ErrorResultDTO):
            raise DemoUseCaseError("dependency_unavailable")
        return result.utc

    def _repository_deadline(self, operation_prefix: str) -> tuple[str, DeadlineDTO]:
        operation_id = self._new_id(operation_prefix)
        now_result = self._clock.now_utc(ClockReadRequestDTO(self._new_id("OP-DEMO-CLOCK-")))
        if isinstance(now_result, ErrorResultDTO):
            raise DemoUseCaseError("dependency_unavailable")
        now = now_result.utc
        return operation_id, DeadlineDTO(
            "1.0.0",
            operation_id,
            (now.as_datetime() + timedelta(seconds=10)).isoformat().replace("+00:00", "Z"),
            5_000,
            now,
            100,
        )


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if type(value) in {str, int, bool, type(None)}:
        return value
    raise ValueError("artifact contains unsupported value")


__all__ = (
    "ArtifactDownloadDTO",
    "DemoArtifactDocumentDTO",
    "DemoPrincipal",
    "DemoRunStatusDTO",
    "DemoSubmitRunResultDTO",
    "DemoUseCase",
    "DemoUseCaseError",
    "SUPPORTED_ASSETS",
)
