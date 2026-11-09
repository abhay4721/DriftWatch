from driftwatch.auth import hash_password, verify_password


def test_password_hash_uses_unique_salt_and_verifies() -> None:
    password = "sample-password-with-enough-length"
    first = hash_password(password)
    second = hash_password(password)

    assert first.startswith("scrypt$")
    assert first != second
    assert verify_password(password, first)
    assert not verify_password("incorrect-password", first)


def test_password_verification_rejects_malformed_hashes() -> None:
    assert not verify_password("anything", "not-a-scrypt-hash")
    assert not verify_password("anything", "scrypt$1$1$1$invalid$invalid")
