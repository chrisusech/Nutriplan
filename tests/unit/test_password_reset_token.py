from nutriplan.adapters.password_reset_token import issue_token, verify_token


def test_token_roundtrip() -> None:
    secret = "test-secret"
    token = issue_token(email="user@example.com", secret=secret, ttl_seconds=3600)
    assert verify_token(token, secret=secret) == "user@example.com"


def test_token_rejects_tampering() -> None:
    secret = "test-secret"
    token = issue_token(email="user@example.com", secret=secret)
    assert verify_token(token + "x", secret=secret) is None
    assert verify_token(token, secret="other") is None
