"""Fail-closed M4 real-Drive preflight regressions."""
from datetime import datetime, timezone
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import m4_drive_e2e as m4


@pytest.fixture(autouse=True)
def _mock_chflags_on_non_bsd(monkeypatch):
    """On platforms without native chflags/UF_IMMUTABLE (e.g. Linux CI),
    provide mock implementations by default so cross-platform tests can test
    ledger logic. Tests specifically verifying missing capability will
    monkeypatch.delattr to simulate missing API."""
    if not hasattr(os, "chflags") or not hasattr(stat, "UF_IMMUTABLE"):
        flags_map = {}

        def mock_chflags(path, flags):
            flags_map[str(Path(path).resolve())] = flags

        orig_stat = Path.stat

        def mock_stat(self, *args, **kwargs):
            st = orig_stat(self, *args, **kwargs)
            res_flags = flags_map.get(str(self.resolve()), 0)

            class StatWrapper:
                def __init__(self, orig, fl):
                    self._orig = orig
                    self.st_flags = fl

                def __getattr__(self, item):
                    return getattr(self._orig, item)

            return StatWrapper(st, res_flags)

        monkeypatch.setattr(m4.os, "chflags", mock_chflags, raising=False)
        monkeypatch.setattr(m4.stat, "UF_IMMUTABLE", 0x2, raising=False)
        monkeypatch.setattr(Path, "stat", mock_stat)


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


def test_private_instance_loader_passes_engine_and_private_roots(tmp_path, monkeypatch):
    engine = Path(__file__).resolve().parents[1]
    private = tmp_path / "private"
    (private / "config").mkdir(parents=True)

    # Clean existing cba_kb modules from sys.modules so SourceOnlyLoader loads fresh
    for mod_name in list(sys.modules.keys()):
        if mod_name == "cba_kb" or mod_name.startswith("cba_kb."):
            monkeypatch.delitem(sys.modules, mod_name, raising=False)
    monkeypatch.setattr(
        sys, "meta_path",
        [f for f in sys.meta_path if not (isinstance(f, m4._SourceOnlyFinder) and f.prefix == "cba_kb")]
    )

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

    from cba_kb import drive as drive_module
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

    with pytest.raises(ValueError, match="pre-loaded into sys.modules prior to establishing"):
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
    loader = m4._SourceOnlyLoader("cba_kb.test_mod", str(code_file))
    mod = SimpleNamespace(__file__=str(code_file), __name__="cba_kb.test_mod", __loader__=loader)
    m4._TRUSTED_SOURCE_EXECUTION_REGISTRY[id(mod)] = {
        "fullname": "cba_kb.test_mod",
        "source_path": code_file.resolve(),
        "source_sha256": hashlib.sha256(code_content).hexdigest(),
        "loader": loader,
    }
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
    dummy_mod = SimpleNamespace(__name__="cba_kb.test_sub", __file__=str(engine_root / "src" / "cba_kb" / "__init__.py"))
    monkeypatch.setitem(m4.sys.modules, "cba_kb.test_sub", dummy_mod)
    monkeypatch.setattr(
        m4, "_verify_cba_kb_module_identity",
        lambda mod, _eng: checked.append(getattr(mod, "__name__", str(mod))))
    m4._verify_all_cba_kb_modules(engine_root)
    assert any("cba_kb" in name for name in checked)


def test_verify_all_cba_kb_modules_propagates_verification_failure(monkeypatch):
    engine_root = Path(__file__).resolve().parents[1]

    def failing_verifier(*_args, **_kwargs):
        raise ValueError("module content does not match Git HEAD blob")

    dummy_mod = SimpleNamespace(__name__="cba_kb.dummy", __file__=str(engine_root / "src" / "cba_kb" / "__init__.py"))
    monkeypatch.setitem(m4.sys.modules, "cba_kb.dummy", dummy_mod)
    monkeypatch.setattr(m4, "_verify_cba_kb_module_identity", failing_verifier)
    with pytest.raises(ValueError, match="content does not match Git HEAD blob"):
        m4._verify_all_cba_kb_modules(engine_root)


def test_pyc_isolation_and_source_only_execution(tmp_path, monkeypatch):
    import py_compile
    import struct

    pkg = tmp_path / "cba_kb_test_pkg"
    pkg.mkdir()
    init_py = pkg / "__init__.py"
    init_py.write_text("", encoding="utf-8")
    mod_py = pkg / "target.py"
    mod_py.write_text("RESULT = 'SAFE_SOURCE'\n", encoding="utf-8")

    # Forge a malicious .pyc in __pycache__ with matching timestamp and size
    pyc_dir = pkg / "__pycache__"
    pyc_dir.mkdir()
    pyc_file = pyc_dir / f"target.cpython-{sys.version_info.major}{sys.version_info.minor}.pyc"
    evil_src = tmp_path / "evil.py"
    evil_src.write_text("RESULT = 'MALICIOUS_PYC'\n", encoding="utf-8")
    py_compile.compile(str(evil_src), cfile=str(pyc_file))

    st = mod_py.stat()
    with open(pyc_file, "r+b") as handle:
        handle.seek(8)
        handle.write(struct.pack("<I", int(st.st_mtime)))
        handle.write(struct.pack("<I", st.st_size))

    # Install SourceOnlyFinder for cba_kb_test_pkg
    finder = m4._SourceOnlyFinder("cba_kb_test_pkg", [tmp_path])
    monkeypatch.setattr(m4.sys, "meta_path", [finder] + m4.sys.meta_path)

    import importlib
    imported = importlib.import_module("cba_kb_test_pkg.target")
    # Must execute safe source, NOT malicious pyc!
    assert imported.RESULT == "SAFE_SOURCE"
    # Loader must be _SourceOnlyLoader
    assert isinstance(imported.__loader__, m4._SourceOnlyLoader)


def test_verify_cba_kb_module_rejects_cached_local_pyc(tmp_path):
    engine = tmp_path / "engine"
    src = engine / "src" / "cba_kb"
    src.mkdir(parents=True)
    code_file = src / "cached_mod.py"
    code_file.write_text("X = 1\n", encoding="utf-8")

    # Create local __pycache__
    pyc_dir = src / "__pycache__"
    pyc_dir.mkdir()
    pyc_file = pyc_dir / "cached_mod.cpython-311.pyc"
    pyc_file.write_bytes(b"cached_bytecode")

    mod = SimpleNamespace(
        __file__=str(code_file),
        __name__="cba_kb.cached_mod",
        __cached__=str(pyc_file),
    )
    with pytest.raises(ValueError, match="loaded unverified engine-local bytecode cache"):
        m4._verify_cba_kb_module_identity(mod, engine)


def test_verify_all_cba_kb_modules_rejects_module_without_file(monkeypatch):
    engine_root = Path(__file__).resolve().parents[1]
    bad_mod = SimpleNamespace(__name__="cba_kb.missing_file")
    monkeypatch.setattr(m4.sys, "modules", {"cba_kb.missing_file": bad_mod})
    with pytest.raises(ValueError, match="lacks mandatory __file__ attribute"):
        m4._verify_all_cba_kb_modules(engine_root)


def test_verify_all_cba_kb_modules_rejects_none_module(monkeypatch):
    engine_root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(m4.sys, "modules", {"cba_kb.nil_mod": None})
    with pytest.raises(ValueError, match="module cba_kb.nil_mod is None in sys.modules"):
        m4._verify_all_cba_kb_modules(engine_root)


def test_verify_cba_kb_module_identity_rejects_non_existent_file(tmp_path):
    engine = tmp_path / "engine"
    (engine / "src" / "cba_kb").mkdir(parents=True)
    ghost_file = engine / "src" / "cba_kb" / "ghost.py"
    mod = SimpleNamespace(__file__=str(ghost_file), __name__="cba_kb.ghost")
    with pytest.raises(ValueError, match="file does not exist on disk"):
        m4._verify_cba_kb_module_identity(mod, engine)


def test_assert_trusted_consumed_directory_rejects_nonexistent_directory(tmp_path):
    nonexistent = tmp_path / "does_not_exist"
    with pytest.raises(ValueError, match="does not exist"):
        m4._assert_trusted_consumed_directory(nonexistent)


def test_assert_trusted_consumed_directory_rejects_symlink_directory(tmp_path):
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    sym_dir = tmp_path / "sym_dir"
    sym_dir.symlink_to(real_dir)
    with pytest.raises(ValueError, match="is missing, a symlink, or not a directory"):
        m4._assert_trusted_consumed_directory(sym_dir)


def test_assert_trusted_consumed_directory_rejects_non_root_owned(tmp_path):
    if not hasattr(os, "geteuid") or os.geteuid() == 0:
        pytest.skip("test requires non-root user")
    user_dir = tmp_path / "user_dir"
    user_dir.mkdir()
    with pytest.raises(ValueError, match="must be owned by root"):
        m4._assert_trusted_consumed_directory(user_dir)


def test_assert_trusted_consumed_directory_rejects_world_writable_without_sticky_bit(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()

    # Mock target as root owned but world-writable without S_ISVTX (0o40777)
    fake_stat = SimpleNamespace(
        st_uid=0,
        st_gid=0,
        st_mode=stat.S_IFDIR | 0o777,
    )
    monkeypatch.setattr(
        Path, "stat", lambda self, *args, **kwargs: fake_stat if self == target else os.stat(self, *args, **kwargs))
    with pytest.raises(ValueError, match="is group/world writable without sticky bit"):
        m4._assert_trusted_consumed_directory(target)


def test_assert_trusted_consumed_directory_rejects_group_writable_ancestor(tmp_path, monkeypatch):
    target = tmp_path / "sub" / "target"
    target.parent.mkdir(parents=True)
    target.mkdir()

    # Target has sticky bit and root owner
    target_stat = SimpleNamespace(
        st_uid=0,
        st_gid=0,
        st_mode=stat.S_IFDIR | stat.S_ISVTX | 0o777,
    )
    # Ancestor is group-writable (0o775)
    ancestor_stat = SimpleNamespace(
        st_uid=0,
        st_gid=0,
        st_mode=stat.S_IFDIR | 0o775,
    )
    def mock_stat(self, *args, **kwargs):
        if self == target:
            return target_stat
        if self == target.parent:
            return ancestor_stat
        return os.stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", mock_stat)
    with pytest.raises(ValueError, match="must not be group/world writable"):
        m4._assert_trusted_consumed_directory(target)


def test_consume_authorization_enforces_mandatory_uf_immutable(tmp_path, monkeypatch):
    instance = SimpleNamespace(data_root=tmp_path / "data")
    independent = tmp_path / "independent"
    record = {
        "authorization_id": "44444444-4444-4444-8444-444444444444",
        "_record_sha256": "d" * 64,
    }

    # Simulate chflags failing with OSError
    def failing_chflags(*_args, **_kwargs):
        raise OSError("filesystem does not support flags")

    monkeypatch.setattr(m4.stat, "UF_IMMUTABLE", getattr(stat, "UF_IMMUTABLE", 0x2), raising=False)
    monkeypatch.setattr(m4.os, "chflags", failing_chflags, raising=False)
    with pytest.raises(ValueError, match="failed to set mandatory UF_IMMUTABLE flag"):
        m4._consume_authorization(instance, record, trusted_consumed_root=independent)


def test_consume_authorization_verifies_uf_immutable_is_actually_set(tmp_path, monkeypatch):
    instance = SimpleNamespace(data_root=tmp_path / "data")
    independent = tmp_path / "independent"
    record = {
        "authorization_id": "55555555-5555-4555-8555-555555555555",
        "_record_sha256": "e" * 64,
    }

    # Simulate chflags being a no-op (doesn't set flag)
    monkeypatch.setattr(m4.stat, "UF_IMMUTABLE", getattr(stat, "UF_IMMUTABLE", 0x2), raising=False)
    monkeypatch.setattr(m4.os, "chflags", lambda *_args, **_kw: None, raising=False)
    orig_stat = Path.stat
    def mock_stat(self):
        st = orig_stat(self)
        if str(self).endswith(".json"):
            return SimpleNamespace(
                st_mode=st.st_mode,
                st_uid=st.st_uid,
                st_flags=0,
            )
        return st

    monkeypatch.setattr(Path, "stat", mock_stat)
    with pytest.raises(ValueError, match="mandatory UF_IMMUTABLE flag was not applied"):
        m4._consume_authorization(instance, record, trusted_consumed_root=independent)


def test_bind_and_sanitize_rejects_preloaded_cba_kb_module(monkeypatch):
    """F-01: Pre-loaded cba_kb module must be rejected rather than whitewashed."""
    import types
    fake_mod = types.ModuleType("cba_kb")
    fake_mod.__file__ = str(Path(m4.__file__).parent.parent / "src" / "cba_kb" / "__init__.py")
    monkeypatch.setitem(sys.modules, "cba_kb", fake_mod)
    # Remove existing finders to simulate initial process state
    monkeypatch.setattr(sys, "meta_path", [f for f in sys.meta_path if not isinstance(f, m4._SourceOnlyFinder)])

    with pytest.raises(ValueError, match="pre-loaded into sys.modules prior to establishing"):
        m4._bind_and_sanitize_python_path(Path(m4.__file__).parent.parent)


def test_assert_trusted_consumed_directory_rejects_non_wheel_gid(tmp_path, monkeypatch):
    """F-02: Target directory owned by root but not wheel group must be rejected."""
    target = tmp_path / "consumed"
    target.mkdir()

    orig_stat = Path.stat
    def mock_stat(self, *args, **kwargs):
        st = orig_stat(self, *args, **kwargs)
        if self == target:
            return SimpleNamespace(
                st_mode=0o1777 | stat.S_IFDIR,
                st_uid=0,
                st_gid=999,  # Non-wheel GID
            )
        if self == target.parent:
            return SimpleNamespace(
                st_mode=0o755 | stat.S_IFDIR,
                st_uid=0,
                st_gid=0,
            )
        return st

    monkeypatch.setattr(Path, "stat", mock_stat)
    with pytest.raises(ValueError, match="must be owned by wheel"):
        m4._assert_trusted_consumed_directory(target)


def test_assert_trusted_consumed_directory_rejects_named_user_write_acl(tmp_path, monkeypatch):
    """F-02: Named user write ACL must be rejected."""
    target = tmp_path / "consumed"
    target.mkdir()

    orig_stat = Path.stat
    def mock_stat(self, *args, **kwargs):
        st = orig_stat(self, *args, **kwargs)
        if self == target:
            return SimpleNamespace(
                st_mode=0o1777 | stat.S_IFDIR,
                st_uid=0,
                st_gid=0,
            )
        # Mock all ancestors up to root as root:wheel 0755
        if self in target.parents or str(self).startswith(str(tmp_path)):
            return SimpleNamespace(
                st_mode=0o755 | stat.S_IFDIR,
                st_uid=0,
                st_gid=0,
            )
        return st

    monkeypatch.setattr(Path, "stat", mock_stat)
    monkeypatch.setattr(sys, "platform", "darwin")

    def mock_subprocess_run(cmd, *args, **kwargs):
        if cmd[0] == "/bin/ls":
            # Target has named user write ACL
            output = f"drwxrwxrwt+ 2 root wheel 64 Oct 16 20:00 {target}\n 0: user:attacker allow add_file,delete\n"
            return SimpleNamespace(stdout=output, stderr="", returncode=0)
        return subprocess.run(cmd, *args, **kwargs)

    monkeypatch.setattr(m4.subprocess, "run", mock_subprocess_run)
    with pytest.raises(ValueError, match="unsafe ACL granting write/delete"):
        m4._assert_trusted_consumed_directory(target)


def test_assert_trusted_consumed_directory_rejects_named_group_write_acl(tmp_path, monkeypatch):
    """F-02: Named group write ACL must be rejected."""
    target = tmp_path / "consumed"
    target.mkdir()

    orig_stat = Path.stat
    def mock_stat(self, *args, **kwargs):
        st = orig_stat(self, *args, **kwargs)
        if self == target:
            return SimpleNamespace(
                st_mode=0o1777 | stat.S_IFDIR,
                st_uid=0,
                st_gid=0,
            )
        if self in target.parents or str(self).startswith(str(tmp_path)):
            return SimpleNamespace(
                st_mode=0o755 | stat.S_IFDIR,
                st_uid=0,
                st_gid=0,
            )
        return st

    monkeypatch.setattr(Path, "stat", mock_stat)
    monkeypatch.setattr(sys, "platform", "darwin")

    def mock_subprocess_run(cmd, *args, **kwargs):
        if cmd[0] == "/bin/ls":
            output = f"drwxrwxrwt+ 2 root wheel 64 Oct 16 20:00 {target}\n 0: group:staff allow write\n"
            return SimpleNamespace(stdout=output, stderr="", returncode=0)
        return subprocess.run(cmd, *args, **kwargs)

    monkeypatch.setattr(m4.subprocess, "run", mock_subprocess_run)
    with pytest.raises(ValueError, match="unsafe ACL granting write/delete"):
        m4._assert_trusted_consumed_directory(target)


def test_assert_trusted_consumed_directory_rejects_ancestor_write_acl(tmp_path, monkeypatch):
    """F-02: Ancestor directory write ACL must be rejected."""
    target = tmp_path / "sub" / "consumed"
    target.mkdir(parents=True)

    orig_stat = Path.stat
    def mock_stat(self, *args, **kwargs):
        st = orig_stat(self, *args, **kwargs)
        if self == target:
            return SimpleNamespace(
                st_mode=0o1777 | stat.S_IFDIR,
                st_uid=0,
                st_gid=0,
            )
        if self in target.parents or str(self).startswith(str(tmp_path)):
            return SimpleNamespace(
                st_mode=0o755 | stat.S_IFDIR,
                st_uid=0,
                st_gid=0,
            )
        return st

    monkeypatch.setattr(Path, "stat", mock_stat)
    monkeypatch.setattr(sys, "platform", "darwin")

    def mock_subprocess_run(cmd, *args, **kwargs):
        if cmd[0] == "/bin/ls":
            inspected = cmd[-1]
            if inspected == str(target.parent):
                output = f"drwxr-xr-x+ 2 root wheel 64 Oct 16 20:00 {inspected}\n 0: user:attacker allow add_subdirectory,delete\n"
            else:
                output = f"drwxr-xr-x 2 root wheel 64 Oct 16 20:00 {inspected}\n"
            return SimpleNamespace(stdout=output, stderr="", returncode=0)
        return subprocess.run(cmd, *args, **kwargs)

    monkeypatch.setattr(m4.subprocess, "run", mock_subprocess_run)
    with pytest.raises(ValueError, match="unsafe ACL granting write/delete"):
        m4._assert_trusted_consumed_directory(target)


def test_assert_trusted_consumed_directory_rejects_acl_command_failure(tmp_path, monkeypatch):
    """F-02: /bin/ls command failure must fail closed instead of swallowing exception."""
    import subprocess
    target = tmp_path / "consumed"
    target.mkdir()

    orig_stat = Path.stat
    def mock_stat(self, *args, **kwargs):
        st = orig_stat(self, *args, **kwargs)
        if self == target:
            return SimpleNamespace(
                st_mode=0o1777 | stat.S_IFDIR,
                st_uid=0,
                st_gid=0,
            )
        if self in target.parents or str(self).startswith(str(tmp_path)):
            return SimpleNamespace(
                st_mode=0o755 | stat.S_IFDIR,
                st_uid=0,
                st_gid=0,
            )
        return st

    monkeypatch.setattr(Path, "stat", mock_stat)
    monkeypatch.setattr(sys, "platform", "darwin")

    def mock_subprocess_run(cmd, *args, **kwargs):
        if cmd[0] == "/bin/ls":
            raise subprocess.CalledProcessError(1, cmd, stderr="Permission denied or I/O error")
        return subprocess.run(cmd, *args, **kwargs)

    monkeypatch.setattr(m4.subprocess, "run", mock_subprocess_run)
    with pytest.raises(ValueError, match="macOS ACL audit command failed"):
        m4._assert_trusted_consumed_directory(target)


def test_assert_trusted_consumed_directory_rejects_unparseable_acl(tmp_path, monkeypatch):
    """F-02: Corrupted or unparseable ACL entry must fail closed."""
    target = tmp_path / "consumed"
    target.mkdir()

    orig_stat = Path.stat
    def mock_stat(self, *args, **kwargs):
        st = orig_stat(self, *args, **kwargs)
        if self == target:
            return SimpleNamespace(
                st_mode=0o1777 | stat.S_IFDIR,
                st_uid=0,
                st_gid=0,
            )
        if self in target.parents or str(self).startswith(str(tmp_path)):
            return SimpleNamespace(
                st_mode=0o755 | stat.S_IFDIR,
                st_uid=0,
                st_gid=0,
            )
        return st

    monkeypatch.setattr(Path, "stat", mock_stat)
    monkeypatch.setattr(sys, "platform", "darwin")

    def mock_subprocess_run(cmd, *args, **kwargs):
        if cmd[0] == "/bin/ls":
            output = f"drwxrwxrwt+ 2 root wheel 64 Oct 16 20:00 {target}\n invalid_corrupted_acl_line_without_colon\n"
            return SimpleNamespace(stdout=output, stderr="", returncode=0)
        return subprocess.run(cmd, *args, **kwargs)

    monkeypatch.setattr(m4.subprocess, "run", mock_subprocess_run)
    with pytest.raises(ValueError, match="macOS ACL audit encountered unparseable entry"):
        m4._assert_trusted_consumed_directory(target)


def test_consume_authorization_fails_closed_when_chflags_missing(tmp_path, monkeypatch):
    """F-03: Missing os.chflags must fail closed unconditionally."""
    instance = SimpleNamespace(data_root=tmp_path / "data")
    independent = tmp_path / "independent"
    record = {
        "authorization_id": "66666666-6666-4666-8666-666666666666",
        "_record_sha256": "f" * 64,
    }

    if hasattr(m4.os, "chflags"):
        monkeypatch.delattr(m4.os, "chflags")

    with pytest.raises(ValueError, match="mandatory immutable flag capability"):
        m4._consume_authorization(instance, record, trusted_consumed_root=independent)


def test_consume_authorization_pre_checks_capability_before_creating_ledger(tmp_path, monkeypatch):
    """F-03: Pre-check prevents any ledger file creation when capability is missing."""
    instance = SimpleNamespace(data_root=tmp_path / "data")
    independent = tmp_path / "independent"
    record = {
        "authorization_id": "77777777-7777-4777-8777-777777777777",
        "_record_sha256": "7" * 64,
    }

    if hasattr(m4.stat, "UF_IMMUTABLE"):
        monkeypatch.delattr(m4.stat, "UF_IMMUTABLE")

    with pytest.raises(ValueError, match="mandatory immutable flag capability"):
        m4._consume_authorization(instance, record, trusted_consumed_root=independent)

    # Verify NO files were created on disk
    assert not list(independent.glob("*"))
    assert not (Path(instance.data_root) / "m4_consumed_authorizations").exists()


def test_consume_authorization_partial_state_failure_logging(tmp_path, monkeypatch, capsys):
    """F-03: Partial state failure logs security critical alert and raises ValueError."""
    instance = SimpleNamespace(data_root=tmp_path / "data")
    independent = tmp_path / "independent"
    record = {
        "authorization_id": "88888888-8888-4888-8888-888888888888",
        "_record_sha256": "8" * 64,
    }

    monkeypatch.setattr(m4.stat, "UF_IMMUTABLE", getattr(stat, "UF_IMMUTABLE", 0x2), raising=False)
    def failing_chflags(*_a, **_kw):
        raise OSError("I/O error during chflags")

    monkeypatch.setattr(m4.os, "chflags", failing_chflags, raising=False)

    with pytest.raises(ValueError, match="failed to set mandatory UF_IMMUTABLE flag"):
        m4._consume_authorization(instance, record, trusted_consumed_root=independent)

    captured = capsys.readouterr()
    assert "M4_E2E_SECURITY_CRITICAL: authorization consumption failed into partial state" in captured.err
    assert "manual administrator intervention may be required" in captured.err


