"""M4 E2E Attacker Tampering Exercises (Scenarios A, B, C, D).

This test suite executes concrete attacker tampering scenarios against
m4_drive_e2e.py guards, verifying fail-closed enforcement and 0 unauthorized mutations.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import m4_drive_e2e as m4
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@pytest.fixture(autouse=True)
def _mock_chflags_on_non_bsd(monkeypatch):
    """On platforms without native chflags/UF_IMMUTABLE (e.g. Linux CI),
    provide mock implementations by default so cross-platform tests can test
    ledger logic. Tests specifically verifying missing capability will
    monkeypatch.delattr to simulate missing API."""
    if not hasattr(os, "chflags") or not hasattr(stat, "UF_IMMUTABLE"):
        flags_map = {}

        def mock_chflags(path, flags):
            flags_map[os.path.abspath(str(path))] = flags

        orig_stat = Path.stat

        def mock_stat(self, *args, **kwargs):
            st = orig_stat(self, *args, **kwargs)
            key = os.path.abspath(str(self))
            if key in flags_map:
                res_flags = flags_map[key]

                class StatWrapper:
                    def __init__(self, orig, fl):
                        self._orig = orig
                        self.st_flags = fl

                    def __getattr__(self, item):
                        return getattr(self._orig, item)

                return StatWrapper(st, res_flags)
            return st

        monkeypatch.setattr(m4.os, "chflags", mock_chflags, raising=False)
        monkeypatch.setattr(m4.stat, "UF_IMMUTABLE", 0x2, raising=False)
        monkeypatch.setattr(Path, "stat", mock_stat)


def _make_signed_auth(tmp_path: Path, signing_key: Ed25519PrivateKey, **updates) -> Path:
    now = datetime.now(timezone.utc)
    record = {
        "schema": "cba-kb-m4-test-authorization/v1",
        "approved": True,
        "scope": "M4_ISOLATED_TEST_ONLY",
        "test_go": "M4-TEST-ATTACK",
        "engine_sha": "0" * 40,
        "expected_account": "attacker@example.com",
        "test_folder": "attack_folder_id",
        "isolation_mode": "production-boundary",
        "production_folders_sha256": hashlib.sha256(json.dumps(["prod_id"]).encode()).hexdigest(),
        "m5_authorized": False,
        "production_writes_authorized": False,
        "authorization_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "approved_by": "human-owner",
        "approved_at": (now - timedelta(minutes=5)).isoformat(),
        "expires_at": (now + timedelta(minutes=30)).isoformat(),
    }
    record.update(updates)
    raw = json.dumps(record, sort_keys=True).encode("utf-8")
    auth_file = tmp_path / "auth.json"
    auth_file.write_bytes(raw)
    sig_file = tmp_path / "auth.json.sig"
    sig = signing_key.sign(raw)
    import base64
    sig_file.write_bytes(base64.b64encode(sig))
    return auth_file


class FakeDriveAPI:
    """Mock Google Drive API tracking all mutation calls."""

    def __init__(self):
        self.files_db: Dict[str, Dict[str, Any]] = {}
        self.mutation_log: List[Dict[str, Any]] = []

    def files(self):
        return self

    def get(self, fileId: str, **kwargs):
        class Request:
            def __init__(req_self):
                req_self.fileId = fileId
            def execute(req_self, **_kw):
                if req_self.fileId not in self.files_db:
                    raise RuntimeError(f"File not found: {req_self.fileId}")
                return dict(self.files_db[req_self.fileId])
        return Request()

    def create(self, body: Dict[str, Any], **kwargs):
        class Request:
            def execute(req_self, **_kw):
                file_id = f"file_{len(self.files_db) + 1}"
                entry = dict(body)
                entry["id"] = file_id
                self.files_db[file_id] = entry
                self.mutation_log.append({"action": "create", "id": file_id, "body": body})
                return entry
        return Request()

    def update(self, fileId: str, addParents: str = "", removeParents: str = "", **kwargs):
        class Request:
            def execute(req_self, **_kw):
                if fileId not in self.files_db:
                    raise RuntimeError(f"File not found: {fileId}")
                entry = self.files_db[fileId]
                entry["parents"] = [addParents]
                self.mutation_log.append({
                    "action": "reparent",
                    "id": fileId,
                    "addParents": addParents,
                    "removeParents": removeParents,
                })
                return entry
        return Request()

    def delete(self, fileId: str, **kwargs):
        class Request:
            def execute(req_self, **_kw):
                self.mutation_log.append({"action": "delete", "id": fileId})
                self.files_db.pop(fileId, None)
                return {}
        return Request()


# ==============================================================================
# Attack Exercise A: Malicious Pre-loaded Module
# ==============================================================================

def test_attack_a_preloaded_module_tampering(tmp_path, monkeypatch):
    """Attack A: Attacker pre-loads a forged cba_kb module into sys.modules
    and attempts to whitewash loader/cached attributes to bypass verification."""
    import types
    repo_root = Path(__file__).resolve().parents[1]

    # Pre-condition: attacker inserts malicious module into sys.modules
    evil_mod = types.ModuleType("cba_kb")
    evil_mod.__file__ = str(repo_root / "src" / "cba_kb" / "__init__.py")
    evil_mod.__loader__ = m4._SourceOnlyLoader("cba_kb", str(repo_root / "src" / "cba_kb" / "__init__.py"))
    evil_mod.__cached__ = None
    evil_mod.ATTACK_PAYLOAD = "EXECUTED_MALICIOUS_BYTECODE"

    monkeypatch.setitem(sys.modules, "cba_kb", evil_mod)
    # Ensure finder is not installed yet
    monkeypatch.setattr(sys, "meta_path", [f for f in sys.meta_path if not isinstance(f, m4._SourceOnlyFinder)])

    # Attack action: launch _bind_and_sanitize_python_path()
    with pytest.raises(ValueError, match="pre-loaded into sys.modules prior to establishing trusted source-only import boundary"):
        m4._bind_and_sanitize_python_path(repo_root)

    # Verification: rejection occurred before any Drive API initialization or execution.


# ==============================================================================
# Attack Exercise B: ACL & Ledger Replay Attack
# ==============================================================================

def test_attack_b_acl_and_replay_attack(tmp_path, monkeypatch):
    """Attack B: Attacker attempts to compromise authorization ledger using ACL grants
    and attempts replay by deleting the instance ledger."""
    target = tmp_path / "consumed"
    target.mkdir()

    # 1. Attacker attempts to grant named user write capability via ACL
    orig_stat = Path.stat
    def mock_stat(self, *args, **kwargs):
        st = orig_stat(self, *args, **kwargs)
        if self == target:
            return SimpleNamespace(st_mode=0o1777 | stat.S_IFDIR, st_uid=0, st_gid=0)
        if self in target.parents or str(self).startswith(str(tmp_path)):
            return SimpleNamespace(st_mode=0o755 | stat.S_IFDIR, st_uid=0, st_gid=0)
        return st

    monkeypatch.setattr(Path, "stat", mock_stat)
    monkeypatch.setattr(sys, "platform", "darwin")

    # Simulate /bin/ls returning named attacker write permission
    def mock_ls_attacker_write(cmd, *args, **kwargs):
        if cmd[0] == "/bin/ls":
            out = f"drwxrwxrwt+ 2 root wheel 64 Oct 16 20:00 {target}\n 0: user:evil_user allow add_file,delete,write\n"
            return SimpleNamespace(stdout=out, stderr="", returncode=0)
        return subprocess.run(cmd, *args, **kwargs)

    monkeypatch.setattr(m4.subprocess, "run", mock_ls_attacker_write)
    with pytest.raises(ValueError, match="unsafe ACL granting write/delete"):
        m4._assert_trusted_consumed_directory(target)


def test_attack_b2_replay_blocked_by_independent_ledger(tmp_path):
    """Attack B2: Attacker attempts to replay authorization after wiping instance ledger."""
    instance = SimpleNamespace(data_root=tmp_path / "data")
    independent = tmp_path / "independent"
    record = {
        "authorization_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "_record_sha256": "b" * 64,
    }

    # Consume once
    m4._consume_authorization(instance, record, trusted_consumed_root=independent)

    # Attacker wipes instance ledger to attempt replay
    instance_ledger = Path(instance.data_root) / "m4_consumed_authorizations"
    for f in instance_ledger.glob("*"):
        if hasattr(os, "chflags"):
            os.chflags(f, 0)
        f.unlink()

    # Replay attempt MUST be blocked by independent system ledger
    with pytest.raises(ValueError, match="TEST authorization was already consumed"):
        m4._consume_authorization(instance, record, trusted_consumed_root=independent)


def test_attack_b3_same_uid_can_clear_both_user_owned_ledgers_and_replay(tmp_path):
    """F-03 R5 characterization: UF_IMMUTABLE is not same-UID anti-rollback.

    This passing test records the Human-accepted
    LOCAL_SAME_UID_LEDGER_ROLLBACK model exclusion; it is not a claimed
    defense. The owner can clear the user flag, delete both records, and make
    O_EXCL accept the authorization again.
    """
    instance = SimpleNamespace(data_root=tmp_path / "data")
    independent = tmp_path / "independent"
    record = {
        "authorization_id": "b3b3b3b3-b3b3-4b3b-8b3b-b3b3b3b3b3b3",
        "_record_sha256": "3" * 64,
    }

    first = m4._consume_authorization(
        instance, record, trusted_consumed_root=independent)
    ledger_files = [
        first,
        independent / f"{record['authorization_id']}.json",
    ]
    assert all(path.exists() for path in ledger_files)

    for path in ledger_files:
        os.chflags(path, 0)
        path.unlink()

    replayed = m4._consume_authorization(
        instance, record, trusted_consumed_root=independent)
    assert replayed.exists()


# ==============================================================================
# Attack Exercise C: Immutable Flag Bypass
# ==============================================================================

def test_attack_c_immutable_bypass(tmp_path, monkeypatch):
    """Attack C: Attacker attempts to execute on platform without UF_IMMUTABLE capability
    or bypass immutable flag enforcement."""
    instance = SimpleNamespace(data_root=tmp_path / "data")
    independent = tmp_path / "independent"
    record = {
        "authorization_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        "_record_sha256": "c" * 64,
    }

    # 1. Missing chflags capability
    monkeypatch.delattr(m4.os, "chflags", raising=False)
    with pytest.raises(ValueError, match="mandatory immutable flag capability"):
        m4._consume_authorization(instance, record, trusted_consumed_root=independent)

    # Verify no file written
    assert not (independent / f"{record['authorization_id']}.json").exists()

    # 2. Real macOS filesystem test: if running on macOS with chflags, verify immutable prevents deletion
    if sys.platform == "darwin" and hasattr(os, "chflags") and hasattr(stat, "UF_IMMUTABLE"):
        test_file = tmp_path / "immutable_test.txt"
        test_file.write_text("protected content")
        try:
            os.chflags(test_file, stat.UF_IMMUTABLE)
            assert bool(test_file.stat().st_flags & stat.UF_IMMUTABLE)

            # Attempt to delete file -> must be blocked with PermissionError (EPERM)
            with pytest.raises(PermissionError):
                test_file.unlink()

            # Attempt to overwrite file -> must be blocked with PermissionError
            with pytest.raises(PermissionError):
                test_file.write_text("tampered content")
        finally:
            # Clean up flag so tmp_path can be removed
            try:
                os.chflags(test_file, 0)
                test_file.unlink(missing_ok=True)
            except OSError:
                pass


# ==============================================================================
# Attack Exercise D: Authorization, Drive Isolation & TOCTOU
# ==============================================================================

def test_attack_d_authorization_isolation_and_toctou(tmp_path, monkeypatch):
    """Attack D: Attacker tampers with signature, scopes, boundaries, and simulates TOCTOU."""
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)

    # 1. Attacker tampers with record contents after signing
    auth_file = _make_signed_auth(tmp_path, private_key)
    tampered_data = json.loads(auth_file.read_text())
    tampered_data["test_folder"] = "attacker_owned_drive_folder"
    auth_file.write_text(json.dumps(tampered_data))

    with pytest.raises(ValueError, match="signature verification failed"):
        m4._validate_authorization(
            str(auth_file), test_go="M4-TEST-ATTACK", engine_sha="0" * 40,
            expected_account="attacker@example.com", test_folder="attacker_owned_drive_folder",
            isolation_mode="production-boundary",
            production_folders_sha256=hashlib.sha256(json.dumps(["prod_id"]).encode()).hexdigest(),
            signature_path=str(auth_file) + ".sig", public_key_bytes=pub_bytes)

    # 2. TOCTOU Simulation: Revalidation immediately before Drive reparent mutation
    fake_drive = FakeDriveAPI()
    fake_adapter = SimpleNamespace(
        api=fake_drive,
        meta=lambda fid: {"id": fid, "parents": ["source_parent"]},
        reparent=lambda fid, new_p, old_p=None: fake_drive.update(fid, addParents=new_p, removeParents=old_p).execute(),
    )

    valid_auth = True
    def gate_check():
        if not valid_auth:
            raise ValueError("authorization invalidated prior to mutation (TOCTOU caught)")

    authorized_adapter = m4._AuthorizedDriveAdapter(fake_adapter, gate_check)

    # Populate synthetic test file in Fake Drive
    fake_drive.files_db["test_file_1"] = {"id": "test_file_1", "parents": ["source_parent"]}

    # Initial reparent succeeds when authorized
    authorized_adapter.reparent("test_file_1", "dest_parent", "source_parent")
    assert len(fake_drive.mutation_log) == 1

    # Attacker invalidates authorization immediately before second mutation
    valid_auth = False
    with pytest.raises(ValueError, match="TOCTOU caught"):
        m4._authorized_mutation(gate_check, lambda: authorized_adapter.reparent("test_file_2", "dest_parent", "source_parent"))

    # Verify no second mutation occurred
    assert len(fake_drive.mutation_log) == 1
    assert fake_drive.mutation_log[0]["id"] == "test_file_1"
