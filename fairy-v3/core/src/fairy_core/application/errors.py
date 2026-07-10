from fairy_core.domain.errors import DomainError


class ApprovalRequiredError(DomainError):
    code = "APPROVAL_REQUIRED"
