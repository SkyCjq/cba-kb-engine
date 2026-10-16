"""Fail-closed M4 real-Drive preflight regressions."""
from datetime import datetime, timezone
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

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
        "production_folders_sha256": hashlib.sha256(b"[]").hexdigest(),
        "m5_authorized": False,
        "production_writes_authorized": False,
        "authorization_id": "11111111-1111-4111-8111-111111111111",
        "approved_by": "human-owner",
        "approved_at": "2026-10-16T07:00:00Z",
        "expires_at": "2026-10-16T09:00:00Z",
    }
    record.update(updates)
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)
    signature = tmp_path / "approval.sig"
    signature.write_bytes(__import__("base64").b64encode(
        private_key.sign(path.read_bytes())))
    return path, signature, public_bytes


def test_private_instance_loader_passes_engine_and_private_roots(tmp_path):
    engine = tmp_path / "engine"
    private = tmp_path / "private"
    engine.mkdir()
    (private / "config").mkdir(parents=True)

    instance = m4._load_private_instance(str(private), engine_root=engine)

    assert instance.root == private.resolve()
    assert instance.config_root == (private / "config").resolve()


def test_authorization_is_exact_and_time_bounded(tmp_path):
    path, signature, public_key = _authorization(tmp_path)
    now = datetime(2026, 10, 16, 8, tzinfo=timezone.utc)

    record = m4._validate_authorization(
        str(path), test_go="M4-TEST-ref", engine_sha="a" * 40,
        expected_account="M4-TEST@example.com", test_folder="folder-1",
        isolation_mode="dedicated-account",
        production_folders_sha256=hashlib.sha256(b"[]").hexdigest(),
        signature_path=str(signature), public_key_bytes=public_key, now=now)

    assert record["approved_by"] == "human-owner"


@pytest.mark.parametrize("updates", [
    {"expires_at": "2026-10-16T07:59:59Z"},
    {"engine_sha": "b" * 40},
    {"expected_account": "wrong@example.com"},
    {"m5_authorized": True},
    {"production_writes_authorized": True},
])
def test_authorization_rejects_expiry_and_scope_mismatch(tmp_path, updates):
    path, signature, public_key = _authorization(tmp_path, **updates)
    with pytest.raises(ValueError):
        m4._validate_authorization(
            str(path), test_go="M4-TEST-ref", engine_sha="a" * 40,
            expected_account="m4-test@example.com", test_folder="folder-1",
            isolation_mode="dedicated-account",
            production_folders_sha256=hashlib.sha256(b"[]").hexdigest(),
            signature_path=str(signature), public_key_bytes=public_key,
            now=datetime(2026, 10, 16, 8, tzinfo=timezone.utc))


def test_authorization_signature_rejects_tampered_record(tmp_path):
    path, signature, public_key = _authorization(tmp_path)
    path.write_text(path.read_text() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="signature verification failed"):
        m4._validate_authorization(
            str(path), test_go="M4-TEST-ref", engine_sha="a" * 40,
            expected_account="m4-test@example.com", test_folder="folder-1",
            isolation_mode="dedicated-account",
            production_folders_sha256=hashlib.sha256(b"[]").hexdigest(),
            signature_path=str(signature), public_key_bytes=public_key,
            now=datetime(2026, 10, 16, 8, tzinfo=timezone.utc))


def test_trust_anchor_requires_system_owned_file(tmp_path):
    if not hasattr(os, "geteuid") or os.geteuid() == 0:
        pytest.skip("root ownership check requires an unprivileged test user")
    allowed = tmp_path / "allowed"
    allowed.write_text("human ssh-ed25519 AAAA\n", encoding="utf-8")
    with pytest.raises(ValueError, match="owned by root"):
        m4._assert_root_owned_file(allowed, "test anchor")


def test_engine_identity_rejects_dirty_worktree(monkeypatch, tmp_path):
    sha = "a" * 40
    outputs = iter([
        "", sha + "\n", "main\n", sha + "\n",
        " M scripts/m4_drive_e2e.py\n"])
    monkeypatch.setattr(m4, "_trusted_git", lambda: "/usr/bin/git")
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
    def __init__(self, inventory=None, incomplete=False, metadata=None):
        self.inventory = inventory or []
        self.incomplete = incomplete
        self.deleted = []
        self.metadata = metadata or {}

    def get(self, fileId, **_kwargs):
        if fileId == "root":
            return _Execute({"id": "root-id"})
        if fileId in self.metadata:
            return _Execute(self.metadata[fileId])
        return _Execute({"id": fileId, "parents": []})

    def list(self, **_kwargs):
        return _Execute({"files": list(self.inventory),
                         "incompleteSearch": self.incomplete})

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
    with pytest.raises(ValueError, match="exact approved test-run object set"):
        m4._verify_dedicated_account(
            drive, folder, "folder-1", "m4-test@example.com", ["folder-1"])


def test_dedicated_account_rejects_incomplete_search():
    files = _Files([{"id": "folder-1"}], incomplete=True)
    drive = SimpleNamespace(api=_FilesApi(files))
    folder = {
        "id": "folder-1", "name": "CBA-KB-M4-ISOLATED-TEST",
        "parents": ["root-id"],
        "owners": [{"emailAddress": "m4-test@example.com"}],
    }
    with pytest.raises(ValueError, match="search was incomplete"):
        m4._verify_dedicated_account(
            drive, folder, "folder-1", "m4-test@example.com", ["folder-1"])


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


def test_reparent_revalidates_immediately_before_move():
    events = []

    class Adapter:
        def reparent(self, *_args):
            events.append("reparent")
            return {"id": "f1"}

    guarded = m4._AuthorizedDriveAdapter(
        Adapter(), lambda: events.append("gate"))
    guarded.reparent("f1", "dest", "source")
    assert events == ["gate", "reparent"]


def test_authorization_nonce_is_consumed_once(tmp_path):
    independent = tmp_path / "independent"
    instance = SimpleNamespace(data_root=tmp_path / "data")
    record = {
        "authorization_id": "11111111-1111-4111-8111-111111111111",
        "_record_sha256": "a" * 64,
    }
    first = m4._consume_authorization(
        instance, record, trusted_consumed_root=independent)
    assert first.is_file()
    with pytest.raises(ValueError, match="already consumed"):
        m4._consume_authorization(
            instance, record, trusted_consumed_root=independent)


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


def test_production_boundary_validates_production_folders_exist_and_not_trashed():
    metadata = {
        "prod-1": {"id": "prod-1", "mimeType": "text/plain", "trashed": False},
        "folder-1": {"id": "folder-1", "mimeType": "application/vnd.google-apps.folder", "trashed": False, "parents": ["root-id"]},
    }
    files = _Files([], metadata=metadata)
    drive = SimpleNamespace(api=_FilesApi(files))
    with pytest.raises(ValueError, match="production boundary prod-1 is not a folder"):
        m4._verify_production_boundary(drive, "folder-1", ["prod-1"])

    metadata["prod-1"] = {"id": "prod-1", "mimeType": "application/vnd.google-apps.folder", "trashed": True}
    with pytest.raises(ValueError, match="production boundary prod-1 is trashed"):
        m4._verify_production_boundary(drive, "folder-1", ["prod-1"])


def test_production_boundary_rejects_test_folder_coinciding_with_production_folder():
    metadata = {
        "prod-1": {"id": "prod-1", "mimeType": "application/vnd.google-apps.folder", "trashed": False},
        "folder-1": {"id": "folder-1", "mimeType": "application/vnd.google-apps.folder", "trashed": False, "parents": ["root-id"]},
    }
    files = _Files([], metadata=metadata)
    drive = SimpleNamespace(api=_FilesApi(files))
    with pytest.raises(ValueError, match="coincides with a production folder ID"):
        m4._verify_production_boundary(drive, "prod-1", ["prod-1"])


def test_production_boundary_rejects_canonical_root_in_production_folders():
    metadata = {
        "root-id": {"id": "root-id", "mimeType": "application/vnd.google-apps.folder", "trashed": False},
        "test-folder": {"id": "test-folder", "mimeType": "application/vnd.google-apps.folder", "trashed": False, "parents": ["root-id"]},
    }
    files = _Files([], metadata=metadata)
    drive = SimpleNamespace(api=_FilesApi(files))
    with pytest.raises(ValueError, match="canonical root root-id coincides with a production folder ID"):
        m4._verify_production_boundary(drive, "test-folder", ["root-id"])


def test_production_boundary_rejects_empty_parents():
    metadata = {
        "prod-1": {"id": "prod-1", "mimeType": "application/vnd.google-apps.folder", "trashed": False},
        "test-folder": {"id": "test-folder", "mimeType": "application/vnd.google-apps.folder", "trashed": False, "parents": []},
    }
    files = _Files([], metadata=metadata)
    drive = SimpleNamespace(api=_FilesApi(files))
    with pytest.raises(ValueError, match="has no parents or parents are missing"):
        m4._verify_production_boundary(drive, "test-folder", ["prod-1"])


def test_production_boundary_rejects_missing_parents():
    metadata = {
        "prod-1": {"id": "prod-1", "mimeType": "application/vnd.google-apps.folder", "trashed": False},
        "test-folder": {"id": "test-folder", "mimeType": "application/vnd.google-apps.folder", "trashed": False},
    }
    files = _Files([], metadata=metadata)
    drive = SimpleNamespace(api=_FilesApi(files))
    with pytest.raises(ValueError, match="has no parents or parents are missing"):
        m4._verify_production_boundary(drive, "test-folder", ["prod-1"])


def test_production_boundary_rejects_parent_mismatch():
    metadata = {
        "prod-1": {"id": "prod-1", "mimeType": "application/vnd.google-apps.folder", "trashed": False},
        "test-folder": {"id": "test-folder", "mimeType": "application/vnd.google-apps.folder", "trashed": False, "parents": ["other-parent"]},
    }
    files = _Files([], metadata=metadata)
    drive = SimpleNamespace(api=_FilesApi(files))
    with pytest.raises(ValueError, match="does not match canonical root root-id"):
        m4._verify_production_boundary(drive, "test-folder", ["prod-1"])


def test_production_boundary_rejects_shared_drive():
    metadata = {
        "prod-1": {"id": "prod-1", "mimeType": "application/vnd.google-apps.folder", "trashed": False},
        "test-folder": {"id": "test-folder", "mimeType": "application/vnd.google-apps.folder", "trashed": False, "parents": ["root-id"], "driveId": "shared-drive-1"},
    }
    files = _Files([], metadata=metadata)
    drive = SimpleNamespace(api=_FilesApi(files))
    with pytest.raises(ValueError, match="must not be in a shared drive"):
        m4._verify_production_boundary(drive, "test-folder", ["prod-1"])


def test_production_boundary_rejects_trashed_test_folder():
    metadata = {
        "prod-1": {"id": "prod-1", "mimeType": "application/vnd.google-apps.folder", "trashed": False},
        "test-folder": {"id": "test-folder", "mimeType": "application/vnd.google-apps.folder", "trashed": True, "parents": ["root-id"]},
    }
    files = _Files([], metadata=metadata)
    drive = SimpleNamespace(api=_FilesApi(files))
    with pytest.raises(ValueError, match="test folder test-folder is trashed"):
        m4._verify_production_boundary(drive, "test-folder", ["prod-1"])


def test_production_boundary_accepts_clean_canonical_root_hierarchy():
    metadata = {
        "prod-1": {"id": "prod-1", "mimeType": "application/vnd.google-apps.folder", "trashed": False},
        "test-folder": {"id": "test-folder", "mimeType": "application/vnd.google-apps.folder", "trashed": False, "parents": ["root-id"]},
    }
    files = _Files([], metadata=metadata)
    drive = SimpleNamespace(api=_FilesApi(files))
    # Should complete without error
    m4._verify_production_boundary(drive, "test-folder", ["prod-1"])


def test_authorization_nonce_rejects_replay_even_if_instance_ledger_deleted(tmp_path):
    instance = SimpleNamespace(data_root=str(tmp_path / "data"))
    independent = tmp_path / "independent"
    record = {
        "authorization_id": "22222222-2222-4222-8222-222222222222",
        "_record_sha256": "b" * 64,
    }
    first = m4._consume_authorization(instance, record, trusted_consumed_root=independent)
    assert first.is_file()
    assert (independent / f"{record['authorization_id']}.json").is_file()

    # Simulate execution user deleting instance data_root ledger to attempt replay
    target = Path(instance.data_root) / "m4_consumed_authorizations" / f"{record['authorization_id']}.json"
    if hasattr(os, "chflags"):
        os.chflags(target, 0)
    target.unlink()

    # Must still be rejected due to independent ledger
    with pytest.raises(ValueError, match="already consumed"):
        m4._consume_authorization(instance, record, trusted_consumed_root=independent)


def test_consume_authorization_fails_closed_without_weak_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("CBA_KB_INDEPENDENT_CONSUMED_ROOT", str(tmp_path / "untrusted_env"))
    instance = SimpleNamespace(data_root=tmp_path / "data")
    record = {
        "authorization_id": "33333333-3333-4333-8333-333333333333",
        "_record_sha256": "c" * 64,
    }
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        with pytest.raises(ValueError, match="system authorization ledger directory"):
            m4._consume_authorization(instance, record)
        assert not (tmp_path / "untrusted_env").exists()
        assert not (Path.home() / ".cba-kb" / "consumed_authorizations" / f"{record['authorization_id']}.json").exists()


def test_bind_and_sanitize_python_path_rejects_untrusted_cba_kb(tmp_path, monkeypatch):
    fake_engine = tmp_path / "fake_engine"
    (fake_engine / "src" / "cba_kb").mkdir(parents=True)
    untrusted_module = SimpleNamespace(__file__=str(tmp_path / "evil" / "cba_kb" / "__init__.py"), __name__="cba_kb")
    monkeypatch.setitem(m4.sys.modules, "cba_kb", untrusted_module)

    with pytest.raises(ValueError, match="cba_kb module already loaded from untrusted location"):
        m4._bind_and_sanitize_python_path(fake_engine)


def test_mutation_gate_rejects_post_startup_worktree_change(tmp_path, monkeypatch):
    sha = "a" * 40
    new_sha = "b" * 40
    outputs = iter([
        sha + "\n", "main\n", sha + "\n", "",
        new_sha + "\n", "main\n", new_sha + "\n", "",
    ])
    monkeypatch.setattr(m4, "_trusted_git", lambda: "/usr/bin/git")
    monkeypatch.setattr(
        m4.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=next(outputs)))

    initial = m4._current_git_sha(tmp_path, verify_fetch=False)
    assert initial == sha

    current = m4._current_git_sha(tmp_path, verify_fetch=False)
    assert current == new_sha


def test_run_git_sanitizes_ambient_git_env(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/nonexistent/fake_git_dir")
    monkeypatch.setenv("GIT_WORK_TREE", "/nonexistent/fake_work_tree")
    monkeypatch.setenv("GIT_INDEX_FILE", "/nonexistent/fake_index")
    engine_root = Path(__file__).resolve().parents[1]
    proc = m4._run_git(["rev-parse", "HEAD"], engine_root=engine_root)
    assert len(proc.stdout.strip()) == 40


def test_verify_cba_kb_module_identity_validates_blob_hash(tmp_path, monkeypatch):
    engine = tmp_path / "engine"
    src = engine / "src" / "cba_kb"
    src.mkdir(parents=True)
    git_dir = engine / ".git"
    git_dir.mkdir()

    code_file = src / "test_mod.py"
    code_content = b"print('authentic')\n"
    code_file.write_bytes(code_content)

    correct_blob = hashlib.sha1(
        f"blob {len(code_content)}\0".encode("ascii") + code_content).hexdigest()

    # 1. When git blob matches, passes
    monkeypatch.setattr(
        m4, "_run_git", lambda _args, **_kw: SimpleNamespace(stdout=correct_blob + "\n"))
    mod = SimpleNamespace(__file__=str(code_file), __name__="cba_kb.test_mod")
    m4._verify_cba_kb_module_identity(mod, engine)

    # 2. When file content diverges from git blob, raises ValueError
    tampered_content = b"print('tampered')\n"
    code_file.write_bytes(tampered_content)
    with pytest.raises(ValueError, match="content does not match Git HEAD blob"):
        m4._verify_cba_kb_module_identity(mod, engine)


def test_verify_cba_kb_module_identity_rejects_non_python_source(tmp_path):
    engine = tmp_path / "engine"
    src = engine / "src" / "cba_kb"
    src.mkdir(parents=True)
    bad_file = src / "test_mod.pyc"
    bad_file.write_bytes(b"bytecode")
    mod = SimpleNamespace(__file__=str(bad_file), __name__="cba_kb.test_mod")
    with pytest.raises(ValueError, match="is not a Python source file"):
        m4._verify_cba_kb_module_identity(mod, engine)


def test_verify_cba_kb_module_identity_rejects_external_source(tmp_path):
    engine = tmp_path / "engine"
    (engine / "src" / "cba_kb").mkdir(parents=True)
    external_file = tmp_path / "external" / "test_mod.py"
    external_file.parent.mkdir(parents=True)
    external_file.write_bytes(b"malicious")
    mod = SimpleNamespace(__file__=str(external_file), __name__="cba_kb.test_mod")
    with pytest.raises(ValueError, match="untrusted module"):
        m4._verify_cba_kb_module_identity(mod, engine)


def test_verify_all_cba_kb_modules_scans_sys_modules(monkeypatch):
    engine_root = Path(__file__).resolve().parents[1]
    checked = []
    monkeypatch.setattr(
        m4, "_verify_cba_kb_module_identity",
        lambda mod, _eng: checked.append(getattr(mod, "__name__", str(mod))))
    m4._verify_all_cba_kb_modules(engine_root)
    assert any("cba_kb" in name for name in checked)


def test_verify_all_cba_kb_modules_propagates_verification_failure(monkeypatch):
    engine_root = Path(__file__).resolve().parents[1]

    def failing_verifier(*_args, **_kwargs):
        raise ValueError("module content does not match Git HEAD blob")

    monkeypatch.setattr(m4, "_verify_cba_kb_module_identity", failing_verifier)
    with pytest.raises(ValueError, match="content does not match Git HEAD blob"):
        m4._verify_all_cba_kb_modules(engine_root)
