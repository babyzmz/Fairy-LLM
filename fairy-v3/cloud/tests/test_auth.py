from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from fairy_cloud.auth import (
    AuthenticationError,
    OidcTokenVerifier,
    RequestIdentity,
    StaticKeyProvider,
    StaticTokenAuthenticator,
)


@pytest.mark.asyncio
async def test_static_authenticator_binds_token_to_registered_device() -> None:
    identity = RequestIdentity(
        user_id="user-1",
        device_id="device-1",
        scopes=frozenset({"fairy.api"}),
    )
    authenticator = StaticTokenAuthenticator({"test-token": identity})

    assert (
        await authenticator.authenticate(
            authorization="Bearer test-token",
            device_id="device-1",
        )
        == identity
    )
    with pytest.raises(AuthenticationError):
        await authenticator.authenticate(
            authorization="Bearer test-token",
            device_id="device-2",
        )


@pytest.mark.asyncio
async def test_oidc_verifier_checks_issuer_audience_scope_and_device() -> None:
    private_key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "user-1",
            "iss": "https://identity.example.test",
            "aud": "fairy-cloud",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "scope": "openid fairy.api",
            "device_id": "device-1",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    verifier = OidcTokenVerifier(
        issuer="https://identity.example.test",
        audience="fairy-cloud",
        key_provider=StaticKeyProvider({"test-key": private_key.public_key()}),
        required_scopes=frozenset({"fairy.api"}),
    )

    identity = await verifier.authenticate(
        authorization=f"Bearer {token}",
        device_id="device-1",
    )

    assert identity.user_id == "user-1"
    assert identity.device_id == "device-1"
    with pytest.raises(AuthenticationError):
        await verifier.authenticate(
            authorization=f"Bearer {token}",
            device_id="device-2",
        )
