"""Core Domain errors，不含 transport 或 provider concern。"""


class DomainError(Exception):
    """所有可預期 Domain invariant violation 的基底。"""


class InvalidStateTransition(DomainError):
    """狀態機拒絕未定義或 terminal regression transition。"""


class VersionConflict(DomainError):
    """Aggregate expected version 與目前版本不一致。"""


class FormalExecutionNotReady(DomainError):
    """Formal Execution 尚未取得有效 pre-flight 與 Task lock。"""


class AdminRerunNotAllowed(DomainError):
    """Admin technical rerun 不符合次數、授權或 failure allowlist。"""


class LineageViolation(DomainError):
    """Evidence／Analysis reference 缺少 lineage、跨 Task 或引用 quarantine。"""


class AssessmentSequenceConflict(DomainError):
    """EvidenceAssessment sequence 未單調遞增或與既有版本衝突。"""
