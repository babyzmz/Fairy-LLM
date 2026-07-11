from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from fairy_core.information import (
    InformationCapabilityHealth,
    InformationCapabilityStatus,
)


def test_capability_health_rejects_naive_observation_time() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        InformationCapabilityHealth(
            provider="alpha_vantage",
            status=InformationCapabilityStatus.UNAVAILABLE,
            observed_at=datetime(2026, 7, 11),
            error_code="CAPABILITY_NOT_AVAILABLE",
        )


def test_capability_health_rejects_error_for_available_provider() -> None:
    with pytest.raises(ValidationError, match="non-available health"):
        InformationCapabilityHealth(
            provider="alpha_vantage",
            status=InformationCapabilityStatus.AVAILABLE,
            observed_at=datetime.now().astimezone(),
            error_code="SHOULD_NOT_EXIST",
        )
