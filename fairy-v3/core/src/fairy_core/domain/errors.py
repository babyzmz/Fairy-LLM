class DomainError(Exception):
    """Base class for stable Fairy Core domain failures."""


class InvalidTransitionError(DomainError):
    code = "INVALID_STATE_TRANSITION"


class VersionConflictError(DomainError):
    code = "VERSION_CONFLICT"


class IdempotencyConflictError(DomainError):
    code = "IDEMPOTENCY_CONFLICT"


class WorkerFenceError(DomainError):
    code = "WORKER_INTERRUPTED"


class CapabilityUnavailableError(DomainError):
    code = "CAPABILITY_NOT_AVAILABLE"


class CommandRejectedError(DomainError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class ScopeViolationError(DomainError):
    def __init__(self, message: str, *, code: str = "PATH_OUT_OF_SCOPE") -> None:
        super().__init__(message)
        self.code = code


class PreviewScopeViolationError(DomainError):
    code = "SCOPE_MISMATCH"


class MemoryScopeViolationError(DomainError):
    code = "MEMORY_SCOPE_VIOLATION"


class MemoryConflictError(DomainError):
    code = "MEMORY_CONFLICT"


class MemoryInjectionBlockedError(DomainError):
    code = "MEMORY_INJECTION_BLOCKED"


class MemorySecretBlockedError(DomainError):
    code = "MEMORY_SECRET_BLOCKED"


class MemoryProjectionStaleError(DomainError):
    code = "MEMORY_PROJECTION_STALE"


class MemorySnapshotTooLargeError(DomainError):
    code = "MEMORY_SNAPSHOT_TOO_LARGE"


class MemoryForgottenError(DomainError):
    code = "MEMORY_FORGOTTEN"
