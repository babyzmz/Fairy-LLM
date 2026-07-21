from fairy_core.application.service import (
    CoreMethodNotFoundError,
    CoreResponseValidationError,
)
from fairy_core.contracts.models import ErrorCode
from fairy_core.domain.errors import DomainError
from fairy_core.workspace.worker_transport import WorkerRpcError
from fastapi import HTTPException
from pydantic import ValidationError

PUBLIC_ERROR_STATUS = {
    ErrorCode.PATH_OUT_OF_SCOPE.value: 403,
    ErrorCode.PATH_IDENTITY_CHANGED.value: 409,
    ErrorCode.SCOPE_MISMATCH.value: 409,
    ErrorCode.APPROVAL_REQUIRED.value: 409,
    ErrorCode.SANDBOX_UNAVAILABLE.value: 503,
    ErrorCode.VERSION_CONFLICT.value: 409,
    ErrorCode.IDEMPOTENCY_CONFLICT.value: 409,
    ErrorCode.SECRET_EGRESS_BLOCKED.value: 403,
    ErrorCode.CAPABILITY_NOT_AVAILABLE.value: 503,
    ErrorCode.WORKER_INTERRUPTED.value: 503,
    ErrorCode.PROJECT_BUSY.value: 409,
    ErrorCode.MEMORY_SCOPE_VIOLATION.value: 409,
    ErrorCode.MEMORY_CONFLICT.value: 409,
    ErrorCode.MEMORY_INJECTION_BLOCKED.value: 403,
    ErrorCode.MEMORY_SECRET_BLOCKED.value: 403,
    ErrorCode.MEMORY_PROJECTION_STALE.value: 503,
    ErrorCode.MEMORY_SNAPSHOT_TOO_LARGE.value: 413,
    ErrorCode.MEMORY_FORGOTTEN.value: 410,
    ErrorCode.DOCUMENT_PROJECTION_STALE.value: 503,
    ErrorCode.DOCUMENT_INTEGRITY_FAILED.value: 409,
    ErrorCode.MCP_CAPABILITY_MISSING.value: 409,
    ErrorCode.MCP_CREDENTIAL_UNAVAILABLE.value: 503,
    ErrorCode.MCP_DESTINATION_BLOCKED.value: 403,
    ErrorCode.MCP_OUTPUT_INVALID.value: 502,
    ErrorCode.MCP_OUTPUT_UNSUPPORTED.value: 502,
    ErrorCode.MCP_PROTOCOL_MISMATCH.value: 409,
    ErrorCode.MCP_RESULT_UNCERTAIN.value: 409,
    ErrorCode.MCP_SCHEMA_CHANGED.value: 409,
    ErrorCode.MCP_SCHEMA_INVALID.value: 409,
    ErrorCode.MCP_TOOL_ERROR.value: 502,
    ErrorCode.MCP_TRANSPORT_INTERRUPTED.value: 503,
    ErrorCode.MCP_TRANSPORT_NOT_ALLOWED.value: 403,
    ErrorCode.MCP_UNAVAILABLE.value: 503,
    ErrorCode.FORMAT_UNSUPPORTED.value: 415,
    ErrorCode.PACK_REQUIRED.value: 409,
    ErrorCode.PACK_UNTRUSTED.value: 403,
    ErrorCode.FILE_TOO_LARGE.value: 413,
    ErrorCode.FILE_ENCRYPTED.value: 409,
    ErrorCode.ACTIVE_CONTENT_BLOCKED.value: 403,
    ErrorCode.DEPENDENCY_MISSING.value: 409,
    ErrorCode.CONVERSION_TIMEOUT.value: 504,
    ErrorCode.DERIVATIVE_INVALID.value: 502,
    ErrorCode.FIDELITY_DEGRADED.value: 409,
    ErrorCode.CACHE_QUOTA_EXCEEDED.value: 507,
    ErrorCode.EDIT_NOT_EXPORTABLE.value: 409,
    "INVALID_STATE_TRANSITION": 409,
}


def core_http_exception(error: Exception) -> HTTPException:
    if isinstance(error, ValidationError):
        return HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_PARAMS",
                "message": "Invalid params",
                "details": error.errors(include_url=False),
            },
        )
    if isinstance(error, KeyError):
        return HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": str(error)},
        )
    if isinstance(error, DomainError):
        error_code = str(getattr(error, "code", "DOMAIN_ERROR"))
        status_code = PUBLIC_ERROR_STATUS.get(error_code, 400)
        return HTTPException(
            status_code=status_code,
            detail={"code": error_code, "message": str(error)},
        )
    if isinstance(error, WorkerRpcError):
        return HTTPException(
            status_code=503 if error.error_code == "WORKER_INTERRUPTED" else 400,
            detail={"code": error.error_code, "message": str(error)},
        )
    if isinstance(error, CoreMethodNotFoundError):
        return HTTPException(
            status_code=404,
            detail={"code": "METHOD_NOT_FOUND", "message": str(error)},
        )
    if isinstance(error, ValueError):
        return HTTPException(
            status_code=422,
            detail={"code": "INVALID_PARAMS", "message": str(error)},
        )
    if isinstance(error, CoreResponseValidationError):
        return HTTPException(
            status_code=500,
            detail={"code": "CORE_ERROR", "message": str(error)},
        )
    return HTTPException(
        status_code=500,
        detail={"code": "CORE_ERROR", "message": "Core request failed"},
    )


__all__ = ["PUBLIC_ERROR_STATUS", "core_http_exception"]
