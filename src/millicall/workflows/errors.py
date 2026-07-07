"""Workflow error types (Phase 4b Task 1).

``WorkflowValidationError`` — raised when a workflow *definition* is structurally
invalid (save-time / graph validation). API layer maps this to HTTP 422.

``WorkflowExecutionError`` — raised at *run time* by the state machine (e.g. no
edge matches a handler result, step/time limit exceeded, cycle guard tripped).
"""

from __future__ import annotations


class WorkflowValidationError(Exception):
    """A workflow definition failed structural/graph validation.

    Carries the list of hard-violation messages so callers can surface them.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors) if self.errors else "workflow validation failed")


class WorkflowExecutionError(Exception):
    """The workflow state machine hit an unrecoverable condition at run time."""
