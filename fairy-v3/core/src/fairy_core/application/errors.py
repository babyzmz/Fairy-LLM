from fairy_core.commanding.bus import CommandDispatchResult
from fairy_core.domain.errors import CommandRejectedError, DomainError


class ApprovalRequiredError(DomainError):
    code = "APPROVAL_REQUIRED"


def command_rejected(
    dispatch: CommandDispatchResult,
    fallback: str,
) -> CommandRejectedError:
    return CommandRejectedError(
        dispatch.reason or fallback,
        code=dispatch.error_code or "CAPABILITY_NOT_AVAILABLE",
    )
