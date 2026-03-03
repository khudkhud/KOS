"""Unified runtime error codes and dispatch exception semantics."""

from __future__ import annotations

from dataclasses import dataclass


ERR_ADMISSION_REJECTED = "ADMISSION_REJECTED"
ERR_HPU_QUEUED = "HPU_QUEUED"
ERR_HPU_TIMEOUT = "HPU_TIMEOUT"
ERR_SCHED_QUOTA = "SCHED_QUOTA_EXCEEDED"
ERR_EXEC_FAIL = "EXEC_FAIL"


@dataclass
class ActionDispatchError(RuntimeError):
    code: str
    detail: str
    retryable: bool = False
    suggest_replan: bool = False

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"
