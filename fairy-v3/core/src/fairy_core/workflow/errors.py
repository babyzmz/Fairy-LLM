class WorkflowRevisionError(RuntimeError):
    pass


class WorkflowFenceError(RuntimeError):
    pass


class WorkflowBudgetExceeded(RuntimeError):
    pass


__all__ = ["WorkflowBudgetExceeded", "WorkflowFenceError", "WorkflowRevisionError"]
