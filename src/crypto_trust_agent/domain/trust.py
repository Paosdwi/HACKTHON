"""Domain objects for evidence contradiction and decomposable confidence flows."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class ContradictionType(str, Enum):
    NUMERIC = "numeric"
    TEMPORAL = "temporal"
    SOURCE = "source"
    NARRATIVE = "narrative"
    SIGNAL = "signal"
    STATUS = "status"


@dataclass(frozen=True, slots=True)
class Contradiction:
    """A contradiction preserves both references; it never removes evidence."""

    contradiction_id: str
    task_id: str
    conflict_type: ContradictionType | str
    left_ref: str
    right_ref: str
    ruleset_version: str
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise ValueError("unsupported contradiction schema_version")
        if not re.fullmatch(r"^CON-[A-Za-z0-9._:-]+$", self.contradiction_id):
            raise ValueError("invalid contradiction_id")
        if not re.fullmatch(r"^TASK-[A-Za-z0-9._:-]+$", self.task_id):
            raise ValueError("invalid contradiction task_id")
        try:
            conflict_type = ContradictionType(self.conflict_type)
        except ValueError as error:
            raise ValueError("invalid contradiction type") from error
        if (
            not isinstance(self.left_ref, str)
            or not isinstance(self.right_ref, str)
            or not self.left_ref
            or not self.right_ref
            or self.left_ref == self.right_ref
        ):
            raise ValueError("contradiction requires distinct bilateral references")
        if not isinstance(self.ruleset_version, str) or not self.ruleset_version:
            raise ValueError("contradiction requires ruleset_version")
        object.__setattr__(self, "conflict_type", conflict_type)


__all__ = ("Contradiction", "ContradictionType")
