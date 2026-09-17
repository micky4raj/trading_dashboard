from core.auth import is_auth_required, verify_password


def test_no_password_configured_means_no_gate():
    assert is_auth_required("") is False


def test_password_configured_means_gate_required():
    assert is_auth_required("secret123") is True


def test_verify_password_passes_when_no_password_configured():
    assert verify_password("anything", "") is True
    assert verify_password("", "") is True


def test_verify_password_correct_match():
    assert verify_password("secret123", "secret123") is True


def test_verify_password_incorrect_match():
    assert verify_password("wrong", "secret123") is False


def test_verify_password_empty_candidate_against_configured_password():
    assert verify_password("", "secret123") is False


def test_verify_password_is_case_sensitive():
    assert verify_password("Secret123", "secret123") is False
