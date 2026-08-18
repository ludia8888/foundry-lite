from __future__ import annotations

from scripts.operations.verify_security_time_runtime import verify


def test_security_time_runtime_proves_expiry_rotation_and_revocation() -> None:
    receipt = verify()

    assert receipt["status"] == "passed"
    assert receipt["localJwksRotationGraceAndRetirement"] is True
    assert receipt["expiredAccessTokenRejected"] is True
    assert receipt["revokedSessionTokenRejected"] is True
    assert receipt["executionLeaseExpiryDetected"] is True
    assert receipt["objectCursorExpiryAndRotation"] is True
    assert receipt["operationsCursorExpiryAndRotation"] is True
    assert receipt["externalIssuerNetworkPath"] == "notProven"
