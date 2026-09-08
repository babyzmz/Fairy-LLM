from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError

from fairy_core.application.service import CoreResponseValidationError, CoreService
from fairy_core.contracts.methods import CORE_METHODS
from fairy_core.domain.errors import DomainError
from fairy_core.workspace.worker_transport import WorkerRpcError

logger = logging.getLogger(__name__)


class JsonRpcDispatcher:
    """JSON-RPC 2.0 envelope adapter around CoreService."""

    def __init__(self, service: CoreService) -> None:
        self._service = service
        self.ledger_signal = getattr(service, "ledger_signal", None)

    def close(self) -> None:
        close = getattr(self._service, "close", None)
        if callable(close):
            close()

    @classmethod
    def method_names(cls) -> frozenset[str]:
        return frozenset(CORE_METHODS)

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("id")
        method_name = str(request.get("method") or "")
        if method_name not in CORE_METHODS:
            return self._error(
                request_id,
                code=-32601,
                message="Method not found",
                data={"method": method_name},
            )
        params = request.get("params", {})
        if not isinstance(params, dict):
            return self._error(
                request_id,
                code=-32602,
                message="Invalid params",
                data={"error_code": "INVALID_PARAMS"},
            )
        try:
            result = self._service.invoke(method_name, params)
        except ValidationError as exc:
            return self._error(
                request_id,
                code=-32602,
                message="Invalid params",
                data={"error_code": "INVALID_PARAMS", "details": exc.errors(include_url=False)},
            )
        except KeyError as exc:
            return self._error(
                request_id,
                code=-32004,
                message="Resource not found",
                data={"error_code": "NOT_FOUND", "details": str(exc)},
            )
        except DomainError as exc:
            return self._error(
                request_id,
                code=-32000,
                message=str(exc),
                data={"error_code": getattr(exc, "code", "DOMAIN_ERROR")},
            )
        except WorkerRpcError as exc:
            return self._error(
                request_id,
                code=-32050,
                message=str(exc),
                data={"error_code": exc.error_code},
            )
        except ValueError as exc:
            return self._error(
                request_id,
                code=-32602,
                message="Invalid params",
                data={"error_code": "INVALID_PARAMS", "details": str(exc)},
            )
        except CoreResponseValidationError as exc:
            return self._error(
                request_id,
                code=-32603,
                message="Internal error",
                data={"error_code": "CORE_ERROR", "method": exc.method},
            )
        except Exception:
            logger.exception("Unhandled Fairy Core error while dispatching %s", method_name)
            return self._error(
                request_id,
                code=-32603,
                message="Internal error",
                data={"error_code": "CORE_ERROR", "method": method_name},
            )
        return {"jsonrpc": "2.0", "id": request_id, "result": _json_value(result)}

    @staticmethod
    def _error(
        request_id: object,
        *,
        code: int,
        message: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message, "data": data},
        }


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if isinstance(value, (UUID, Path)):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value
