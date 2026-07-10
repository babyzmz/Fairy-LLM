class DomainError(Exception):
    """Base class for stable Fairy Core domain failures."""


class InvalidTransitionError(DomainError):
    code = "INVALID_STATE_TRANSITION"


class VersionConflictError(DomainError):
    code = "VERSION_CONFLICT"


class IdempotencyConflictError(DomainError):
    code = "IDEMPOTENCY_CONFLICT"


class ScopeViolationError(DomainError):
    def __init__(self, message: str, *, code: str = "PATH_OUT_OF_SCOPE") -> None:
        super().__init__(message)
        self.code = code
