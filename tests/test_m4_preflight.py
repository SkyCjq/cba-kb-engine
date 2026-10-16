"""Fail-closed M4 real-Drive preflight regressions."""
from datetime import datetime, timezone
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import m4_drive_e2e as m4
from cba_kb import drive as drive_module

_REAL_TRUST_CHECK = m4._assert_trusted_allowed_signers


@pytest.fixture(autouse=True)
def _test_signer_trust_anchor(monkeypatch):
    # Functional signature tests use an ephemeral key. Production main() has
    # no injection point and always enforces the real root-owned trust check.
    monkeypatch.setattr(
        m4, "_assert_trusted_allowed_signers", lambda path: path.resolve())


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
        "production_folders_sha256": hashlib.sha256(b"[]").hexdigest(),
        "m5_authorized": False,
        "production_writes_authorized": False,
        "approved_by": "human-owner",
        "approved_at": "2026-10-16T07:00:00Z",
        "expires_at": "2026-10-16T09:00:00Z",
    }
    record.update(updates)
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    key = tmp_path / "human_key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True)
    public_fields = (tmp_path / "human_key.pub").read_text().split()
    allowed = tmp_path / "m4_approval_allowed_signers"
    allowed.write_text(
        f"human-owner {public_fields[0]} {public_fields[1]}\n",
        encoding="utf-8")
    os.chmod(allowed, 0o600)
    subprocess.run(
        ["ssh-keygen", "-Y", "sign", "-f", str(key),
         "-n", "cba-kb-m4-test", str(path)],
        check=True, capture_output=True)
    return path, Path(f"{path}.sig"), allowed


def test_private_instance_loader_passes_engine_and_private_roots(tmp_path):
    engine = tmp_path / "engine"
    private = tmp_path / "private"
    engine.mkdir()
    (private / "config").mkdir(parents=True)

    instance = m4._load_private_instance(str(private), engine_root=engine)

    assert instance.root == private.resolve()
    assert instance.config_root == (private / "config").resolve()


def test_authorization_is_exact_and_time_bounded(tmp_path):
    path, signature, allowed = _authorization(tmp_path)
    now = datetime(2026, 10, 16, 8, tzinfo=timezone.utc)

    record = m4._validate_authorization(
        str(path), test_go="M4-TEST-ref", engine_sha="a" * 40,
        expected_account="M4-TEST@example.com", test_folder="folder-1",
        isolation_mode="dedicated-account",
        production_folders_sha256=hashlib.sha256(b"[]").hexdigest(),
        signature_path=str(signature), allowed_signers_path=allowed, now=now)

    assert record["approved_by"] == "human-owner"


@pytest.mark.parametrize("updates", [
    {"expires_at": "2026-10-16T07:59:59Z"},
    {"engine_sha": "b" * 40},
    {"expected_account": "wrong@example.com"},
    {"m5_authorized": True},
    {"production_writes_authorized": True},
])
def test_authorization_rejects_expiry_and_scope_mismatch(tmp_path, updates):
    path, signature, allowed = _authorization(tmp_path, **updates)
    with pytest.raises(ValueError):
        m4._validate_authorization(
            str(path), test_go="M4-TEST-ref", engine_sha="a" * 40,
            expected_account="m4-test@example.com", test_folder="folder-1",
            isolation_mode="dedicated-account",
            production_folders_sha256=hashlib.sha256(b"[]").hexdigest(),
            signature_path=str(signature), allowed_signers_path=allowed,
            now=datetime(2026, 10, 16, 8, tzinfo=timezone.utc))


def test_authorization_signature_rejects_tampered_record(tmp_path):
    path, signature, allowed = _authorization(tmp_path)
    path.write_text(path.read_text() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="signature verification failed"):
        m4._validate_authorization(
            str(path), test_go="M4-TEST-ref", engine_sha="a" * 40,
            expected_account="m4-test@example.com", test_folder="folder-1",
            isolation_mode="dedicated-account",
            production_folders_sha256=hashlib.sha256(b"[]").hexdigest(),
            signature_path=str(signature), allowed_signers_path=allowed,
            now=datetime(2026, 10, 16, 8, tzinfo=timezone.utc))


def test_allowed_signers_requires_system_owned_trust_anchor(tmp_path):
    if not hasattr(os, "geteuid") or os.geteuid() == 0:
        pytest.skip("root ownership check requires an unprivileged test user")
    allowed = tmp_path / "allowed"
    allowed.write_text("human ssh-ed25519 AAAA\n", encoding="utf-8")
    with pytest.raises(ValueError, match="owned by root"):
        _REAL_TRUST_CHECK(allowed)


def test_engine_identity_rejects_dirty_worktree(monkeypatch, tmp_path):
    sha = "a" * 40
    outputs = iter([sha + "\n", "main\n", sha + "\n", " M scripts/m4_drive_e2e.py\n"])
    monkeypatch.setattr(
        m4.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=next(outputs)))
    with pytest.raises(ValueError, match="clean main"):
        m4._current_git_sha(tmp_path)


class _Execute:
    def __init__(self, result):
        self.result = result

    def execute(self, **_kwargs):
        return self.result() if callable(self.result) else self.result


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


class _Files:
    def __init__(self, inventory=None):
        self.inventory = inventory or []
        self.deleted = []

    def get(self, fileId, **_kwargs):
        assert fileId == "root"
        return _Execute({"id": "root-id"})

    def list(self, **_kwargs):
        return _Execute({"files": list(self.inventory)})

    def delete(self, fileId, **_kwargs):
        return _Execute(lambda: self.deleted.append(fileId) or {})


class _FilesApi:
    def __init__(self, files):
        self._files = files

    def files(self):
        return self._files


def test_dedicated_account_requires_root_and_empty_account_inventory():
    files = _Files([{"id": "folder-1"}, {"id": "production-file"}])
    drive = SimpleNamespace(api=_FilesApi(files))
    folder = {
        "id": "folder-1", "name": "CBA-KB-M4-ISOLATED-TEST",
        "parents": ["root-id"],
        "owners": [{"emailAddress": "m4-test@example.com"}],
    }
    with pytest.raises(ValueError, match="no other accessible"):
        m4._verify_dedicated_account(
            drive, folder, "folder-1", "m4-test@example.com")


def test_cleanup_deletes_child_before_parent_and_reads_back_marker():
    files = _Files([])
    drive = SimpleNamespace(api=_FilesApi(files))
    residuals = m4._cleanup_created(
        drive, ["source-file", "destination-folder"], "marker")
    assert files.deleted == ["source-file", "destination-folder"]
    assert residuals == []


def test_expired_authorization_prevents_mutation():
    events = []

    def expired():
        events.append("authorize")
        raise ValueError("authorization is not currently valid")

    def mutate():
        events.append("mutate")

    with pytest.raises(ValueError, match="not currently valid"):
        m4._authorized_mutation(expired, mutate)
    assert events == ["authorize"]


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
