"""Fail-closed M4 real-Drive preflight regressions."""
from datetime import datetime, timezone
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import m4_drive_e2e as m4
from cba_kb import drive as drive_module


def _authorization(tmp_path, **updates):
    record = {
        "schema": "cba-kb-m4-test-authorization/v1",
        "approved": True,
        "scope": "M4_ISOLATED_TEST_ONLY",
        "test_go": "M4-TEST-ref",
        "engine_sha": "a" * 40,
        "expected_account": "m4-test@example.com",
        "test_folder": "folder-1",
        "isolation_mode": "dedicated-account",
        "m5_authorized": False,
        "production_writes_authorized": False,
        "approved_by": "human-owner",
        "approved_at": "2026-10-16T00:00:00Z",
        "expires_at": "2026-10-17T00:00:00Z",
    }
    record.update(updates)
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_private_instance_loader_passes_engine_and_private_roots(tmp_path):
    engine = tmp_path / "engine"
    private = tmp_path / "private"
    engine.mkdir()
    (private / "config").mkdir(parents=True)

    instance = m4._load_private_instance(str(private), engine_root=engine)

    assert instance.root == private.resolve()
    assert instance.config_root == (private / "config").resolve()


def test_authorization_is_exact_and_time_bounded(tmp_path):
    path = _authorization(tmp_path)
    now = datetime(2026, 10, 16, 8, tzinfo=timezone.utc)

    record = m4._validate_authorization(
        str(path), test_go="M4-TEST-ref", engine_sha="a" * 40,
        expected_account="M4-TEST@example.com", test_folder="folder-1",
        isolation_mode="dedicated-account", now=now)

    assert record["approved_by"] == "human-owner"


@pytest.mark.parametrize("updates", [
    {"expires_at": "2026-10-16T07:59:59Z"},
    {"engine_sha": "b" * 40},
    {"expected_account": "wrong@example.com"},
    {"m5_authorized": True},
    {"production_writes_authorized": True},
])
def test_authorization_rejects_expiry_and_scope_mismatch(tmp_path, updates):
    path = _authorization(tmp_path, **updates)
    with pytest.raises(ValueError):
        m4._validate_authorization(
            str(path), test_go="M4-TEST-ref", engine_sha="a" * 40,
            expected_account="m4-test@example.com", test_folder="folder-1",
            isolation_mode="dedicated-account",
            now=datetime(2026, 10, 16, 8, tzinfo=timezone.utc))


class _Execute:
    def __init__(self, result):
        self.result = result

    def execute(self, **_kwargs):
        return self.result


class _About:
    def __init__(self, email):
        self.email = email

    def get(self, **_kwargs):
        return _Execute({"user": {"emailAddress": self.email}})


class _Api:
    def __init__(self, email):
        self._email = email

    def about(self):
        return _About(self._email)


class _Drive:
    def __init__(self, email):
        self.api = _Api(email)


def test_authenticated_account_mismatch_fails_closed():
    with pytest.raises(ValueError, match="authenticated account mismatch"):
        m4._verify_authenticated_account(
            _Drive("production@example.com"), "m4-test@example.com")


def test_expired_oauth_without_refresh_token_fails_closed(tmp_path, monkeypatch):
    store = tmp_path / ".credentials"
    store.mkdir()
    (store / "token.json").write_text("{}", encoding="utf-8")

    class ExpiredCredentials:
        expired = True
        refresh_token = None
        valid = False

        def has_scopes(self, _scopes):
            return True

    from google.oauth2.credentials import Credentials
    monkeypatch.setattr(
        Credentials, "from_authorized_user_file",
        classmethod(lambda _cls, _path: ExpiredCredentials()))

    with pytest.raises(RuntimeError, match="Drive authorization missing"):
        drive_module.credentials(
            tmp_path, interactive=False, credentials_store=store)


def test_receipt_is_atomic_parseable_and_status_bound(tmp_path):
    path = tmp_path / "evidence" / "receipt.json"
    m4._write_receipt(str(path), {"status": "PASSED", "steps": []})
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "PASSED"


def test_receipt_write_failure_is_not_best_effort(tmp_path, monkeypatch):
    def denied(*_args, **_kwargs):
        raise OSError("disk denied")

    monkeypatch.setattr(m4.os, "replace", denied)
    with pytest.raises(m4._ReceiptWriteFailure, match="disk denied"):
        m4._write_receipt(str(tmp_path / "receipt.json"), {"status": "PASSED"})
