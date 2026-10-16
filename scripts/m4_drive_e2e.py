#!/usr/bin/env python3
"""M4: Isolated Google Drive end-to-end verification (requires real OAuth).

This script executes the REAL Drive move chain against an ISOLATED test
folder. It is FAIL-CLOSED at every step:

1. Requires --instance-root with valid Drive OAuth (Private Instance).
2. Requires --test-folder: the isolated test folder ID.
3. Requires --test-go: isolated TEST authorization reference.
   NOTE: --test-go authorizes ISOLATED TESTING ONLY. It can never
   authorize production operations. There is no --production-go here.
4. Requires an exact authenticated account and a time-bounded, Human-signed
   approval bound to a clean main commit, folder, account, and isolation mode.
5. Requires either a verified production-folder boundary or the stronger
   dedicated-account proof (sole ownership, My Drive root, exact name, and no
   other accessible non-trashed Drive objects).
6. Creates synthetic test files INSIDE the test folder only
   (explicit allowlist, not just blacklist).
7. Builds an exact drive_move plan, approves it, applies it with
   test_go + allowlist, and independently readbacks.
8. Cleans up test files; cleanup failures are TRACKED and reported
   (residual file IDs), never silently ignored.

USAGE (only after Human provides test folder + OAuth):
    python scripts/m4_drive_e2e.py \
        --instance-root /path/to/private/instance \
        --test-folder <ISOLATED_TEST_FOLDER_ID> \
        --test-go "M4-TEST-2026-10-16-human-ref" \
        --expected-account "m4-test@example.com" \
        --approval-record /secure/path/m4-approval.json \
        --approval-signature /secure/path/m4-approval.json.sig \
        --isolation-mode dedicated-account \
        --receipt /tmp/m4-receipt.json

AUTHORIZATION MODEL:
    This script uses --test-go (isolated TEST authorization) ONLY.
    A TEST reference can never authorize a production write.
    The Human Ed25519 signature and approval are checked for identity, expiry,
    clean commit SHA, folder, and isolation mode before every mutation.
    File creation is restricted to an explicit allowlist.

ACCOUNT ISOLATION:
    Prefer a dedicated test Google account. The script requires exact identity
    equality before any write. Dedicated-account mode additionally verifies
    sole folder ownership, My Drive-root placement, exact name, and an account
    inventory containing no other accessible non-trashed Drive objects.
    Full Drive scope does NOT imply
    permission to access the entire account -- all operations are
    constrained to the explicit folder allowlist.

PARTIAL EXECUTION:
    If the script fails AFTER creating test files (e.g., evidence
    mismatch, apply failure, readback failure), those files EXIST on
    Drive. The script attempts cleanup and reports residual file IDs.
    "Nothing is left half-moved" is NOT guaranteed -- use the receipt
    and residual list for manual audit and recovery.

EXIT CODES:
    0: all checks passed, cleanup complete, no residuals.
    1: environment/auth/isolation failure (fail-closed before writes).
    2: E2E assertion failure (see output + receipt; check residuals).
    3: cleanup incomplete -- residual files remain (see receipt for IDs).
    4: mandatory JSON receipt could not be durably written/read back.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


SYSTEM_GIT = Path("/usr/bin/git")
SYSTEM_APPROVAL_PUBLIC_KEY = Path(
    "/etc/cba-kb/m4_approval_ed25519_public.pem")


def fail(msg: str, code: int = 1) -> "NoReturn":
    print(f"M4_E2E_FAIL: {msg}", file=sys.stderr)
    sys.exit(code)


class _E2EFailure(Exception):
    """E2E step failure AFTER files may exist.

    Unlike fail() (sys.exit), this allows the finally-block cleanup to
    run and the exit code to be determined AFTER cleanup: residual
    files -> exit 3 (takes priority over the original E2E error code).
    """

    def __init__(self, msg: str, code: int = 2):
        super().__init__(msg)
        self.code = code


class _ReceiptWriteFailure(Exception):
    """The mandatory durable M4 receipt could not be written."""


def _write_receipt(path: str, data: Dict[str, Any]) -> None:
    """Atomically write the mandatory JSON receipt or fail closed."""
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Optional[Path] = None
    try:
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
        temporary = Path(temporary_name)
        payload = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
        # Prove that the durable artifact is readable JSON and matches status.
        persisted = json.loads(target.read_text(encoding="utf-8"))
        if persisted.get("status") != data.get("status"):
            raise ValueError("persisted receipt status mismatch")
        print(f"M4_E2E: receipt written to {target}")
    except Exception as exc:  # noqa: BLE001
        raise _ReceiptWriteFailure(f"receipt write failed: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _parse_utc(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"authorization {label} must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"authorization {label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _assert_root_owned_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing or unsafe")
    file_stat = path.stat()
    if hasattr(os, "geteuid") and file_stat.st_uid != 0:
        raise ValueError(f"{label} must be owned by root")
    if stat.S_IMODE(file_stat.st_mode) & 0o022:
        raise ValueError(f"{label} must not be group/world writable")
    return path.resolve()


def _trusted_git() -> str:
    return str(_assert_root_owned_file(SYSTEM_GIT, "system Git executable"))


def _current_git_sha(engine_root: Path) -> str:
    git = _trusted_git()
    subprocess.run(
        [git, "fetch", "origin", "--prune", "--tags"], cwd=engine_root,
        check=True, capture_output=True, text=True, timeout=120)
    sha = subprocess.run(
        [git, "rev-parse", "HEAD"], cwd=engine_root, check=True,
        capture_output=True, text=True).stdout.strip()
    branch = subprocess.run(
        [git, "branch", "--show-current"], cwd=engine_root, check=True,
        capture_output=True, text=True).stdout.strip()
    upstream = subprocess.run(
        [git, "rev-parse", "origin/main"], cwd=engine_root, check=True,
        capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(
        [git, "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=engine_root, check=True, capture_output=True, text=True).stdout.strip()
    if branch != "main" or sha != upstream or dirty:
        raise ValueError(
            "Engine must be clean main at exact origin/main before real Drive writes")
    return sha


def _read_root_trust_anchor(path: Path = SYSTEM_APPROVAL_PUBLIC_KEY) -> bytes:
    trusted = _assert_root_owned_file(path, "M4 Ed25519 public-key trust anchor")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(trusted, flags)
    try:
        opened = os.fstat(descriptor)
        if hasattr(os, "geteuid") and opened.st_uid != 0:
            raise ValueError("opened M4 trust anchor is not root-owned")
        if stat.S_IMODE(opened.st_mode) & 0o022:
            raise ValueError("opened M4 trust anchor is writable by group/world")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            return handle.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _verify_authorization_signature(record_bytes: bytes, signature_path: str,
                                    public_key_bytes: bytes) -> None:
    """Verify Ed25519 over the exact bytes that are subsequently parsed."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        public_key = serialization.load_pem_public_key(public_key_bytes)
        if not isinstance(public_key, Ed25519PublicKey):
            raise ValueError("M4 approval key must be Ed25519")
        signature = base64.b64decode(
            Path(signature_path).read_bytes().strip(), validate=True)
        public_key.verify(signature, record_bytes)
    except (OSError, ValueError, InvalidSignature) as exc:
        raise ValueError("Human authorization signature verification failed") from exc


def _validate_authorization(path: str, *, test_go: str, engine_sha: str,
                            expected_account: str, test_folder: str,
                            isolation_mode: str,
                            production_folders_sha256: str,
                            signature_path: str,
                            public_key_bytes: bytes,
                            now: Optional[datetime] = None) -> Dict[str, Any]:
    """Validate a Human-approved, time-bounded, exact M4 TEST record."""
    try:
        record_bytes = Path(path).read_bytes()
        _verify_authorization_signature(
            record_bytes, signature_path, public_key_bytes)
        record = json.loads(record_bytes)
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read authorization record: {exc}") from exc
    if not isinstance(record, dict):
        raise ValueError("authorization record must be a JSON object")
    approved_by = record.get("approved_by")
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise ValueError("authorization approved_by is required")
    exact = {
        "schema": "cba-kb-m4-test-authorization/v1",
        "approved": True,
        "scope": "M4_ISOLATED_TEST_ONLY",
        "test_go": test_go,
        "engine_sha": engine_sha,
        "expected_account": expected_account.lower(),
        "test_folder": test_folder,
        "isolation_mode": isolation_mode,
        "production_folders_sha256": production_folders_sha256,
        "m5_authorized": False,
        "production_writes_authorized": False,
    }
    for field, expected in exact.items():
        actual = record.get(field)
        if field == "expected_account" and isinstance(actual, str):
            actual = actual.lower()
        if actual != expected:
            raise ValueError(
                f"authorization {field} mismatch: expected {expected!r}")
    approved_at = _parse_utc(record.get("approved_at"), "approved_at")
    expires_at = _parse_utc(record.get("expires_at"), "expires_at")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not approved_at <= current < expires_at:
        raise ValueError("authorization is not currently valid")
    if expires_at - approved_at > timedelta(hours=2):
        raise ValueError("authorization validity window must not exceed two hours")
    record["_record_sha256"] = hashlib.sha256(record_bytes).hexdigest()
    return record


def _consume_authorization(instance: Any, record: Dict[str, Any]) -> Path:
    authorization_id = record.get("authorization_id")
    if not isinstance(authorization_id, str) or not authorization_id:
        raise ValueError("authorization_id is required")
    if (authorization_id != authorization_id.lower()
            or any(ch not in "0123456789abcdef-" for ch in authorization_id)):
        raise ValueError("authorization_id must be lowercase hex/UUID form")
    consumed_root = Path(instance.data_root) / "m4_consumed_authorizations"
    consumed_root.mkdir(parents=True, exist_ok=True)
    os.chmod(consumed_root, 0o700)
    target = consumed_root / f"{authorization_id}.json"
    payload = json.dumps({
        "authorization_id": authorization_id,
        "record_sha256": record["_record_sha256"],
        "consumed_at": datetime.now(timezone.utc).isoformat(),
    }, sort_keys=True).encode("utf-8") + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags, 0o600)
    except FileExistsError as exc:
        raise ValueError("TEST authorization was already consumed") from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return target


def _load_private_instance(instance_root: str, *, engine_root: Path):
    from cba_kb.instance import load_instance
    return load_instance(engine_root, instance_root)


def _verify_authenticated_account(drive: Any, expected_account: str) -> str:
    """Return the exact authenticated account or fail before any write."""
    try:
        about = drive.api.about().get(fields="user(emailAddress)").execute(
            num_retries=0)
        actual = about.get("user", {}).get("emailAddress", "").strip().lower()
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"cannot verify authenticated Google identity: {exc}") from exc
    if actual != expected_account:
        raise ValueError(
            f"authenticated account mismatch: expected {expected_account}, "
            f"got {actual or 'unknown'}")
    return actual


def _list_all_drive_objects(drive: Any) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    page: Optional[str] = None
    while True:
        result = drive.api.files().list(
            q="trashed=false", spaces="drive", corpora="user",
            pageToken=page, pageSize=1000,
            fields="nextPageToken,incompleteSearch,files(id,name,mimeType,parents,driveId)",
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute(num_retries=0)
        if result.get("incompleteSearch"):
            raise ValueError("dedicated-account Drive inventory search was incomplete")
        items.extend(result.get("files", []))
        page = result.get("nextPageToken")
        if not page:
            return items


def _verify_dedicated_account(drive: Any, folder_meta: Dict[str, Any],
                              test_folder: str, expected_account: str,
                              allowed_ids: List[str]) -> None:
    owners = {item.get("emailAddress", "").lower()
              for item in folder_meta.get("owners", [])}
    root_id = drive.api.files().get(
        fileId="root", fields="id", supportsAllDrives=True,
    ).execute(num_retries=0).get("id")
    if folder_meta.get("driveId") or owners != {expected_account}:
        raise ValueError(
            "dedicated-account folder must be solely owned by the approved "
            "account and must not be in a shared drive")
    if folder_meta.get("name") != "CBA-KB-M4-ISOLATED-TEST":
        raise ValueError(
            "dedicated-account folder must be named CBA-KB-M4-ISOLATED-TEST")
    if not root_id or set(folder_meta.get("parents", [])) != {root_id}:
        raise ValueError("dedicated-account folder must be directly under My Drive root")
    inventory = _list_all_drive_objects(drive)
    if {item.get("id") for item in inventory} != set(allowed_ids):
        raise ValueError(
            "dedicated-account inventory differs from the exact approved "
            "test-run object set")


def _is_not_found(exc: Exception) -> bool:
    status = getattr(getattr(exc, "resp", None), "status", None)
    return status == 404


def _find_run_objects(drive: Any, run_marker: str) -> List[str]:
    found: List[str] = []
    page: Optional[str] = None
    query = ("trashed=false and appProperties has { key='cba_m4_run' "
             f"and value='{run_marker}' }}")
    while True:
        result = drive.api.files().list(
            q=query, spaces="drive", corpora="user", pageToken=page,
            pageSize=1000, fields="nextPageToken,files(id)",
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute(num_retries=0)
        found.extend(item["id"] for item in result.get("files", []))
        page = result.get("nextPageToken")
        if not page:
            return sorted(set(found))


def _cleanup_created(drive: Any, created_ids: List[str],
                     run_marker: str) -> List[str]:
    """Delete child before parent, tolerate already-gone IDs, then read back."""
    deletion_failures: List[str] = []
    for file_id in created_ids:  # source file first, destination folder second
        try:
            drive.api.files().delete(
                fileId=file_id, supportsAllDrives=True,
            ).execute(num_retries=0)
            print(f"M4_E2E: cleaned up {file_id}")
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                print(f"M4_E2E: cleanup confirmed already absent {file_id}")
                continue
            deletion_failures.append(file_id)
            print(f"M4_E2E_FAIL: cleanup failed for {file_id}: {exc}",
                  file=sys.stderr)
    try:
        return sorted(set(deletion_failures + _find_run_objects(drive, run_marker)))
    except Exception as exc:  # noqa: BLE001
        print(f"M4_E2E_FAIL: cleanup readback failed: {exc}", file=sys.stderr)
        return sorted(set(deletion_failures + created_ids))


def _authorized_mutation(require_authorization, mutation):
    """Revalidate signed authorization immediately before one mutation."""
    require_authorization()
    return mutation()


class _AuthorizedDriveAdapter:
    """Delegate reads, but gate the actual reparent mutation at call time."""

    def __init__(self, adapter: Any, mutation_gate):
        self._adapter = adapter
        self._mutation_gate = mutation_gate

    def meta(self, file_id: str) -> Dict[str, Any]:
        return self._adapter.meta(file_id)

    def reparent(self, file_id: str, new_parent: str,
                 old_parent: Optional[str] = None) -> Dict[str, Any]:
        self._mutation_gate()
        return self._adapter.reparent(file_id, new_parent, old_parent)


def main() -> int:
    ap = argparse.ArgumentParser(description="M4 isolated Drive E2E")
    ap.add_argument("--instance-root", required=True,
                    help="Private Instance root with Drive OAuth")
    ap.add_argument("--test-folder", required=True,
                    help="Isolated test folder ID (ALL operations stay inside)")
    ap.add_argument("--test-go", required=True,
                    help="Isolated TEST authorization reference (NOT a production GO)")
    ap.add_argument("--expected-account", required=True,
                    help="Exact Google account approved for this isolated test")
    ap.add_argument("--approval-record", required=True,
                    help="Human-approved, time-bounded local TEST authorization JSON")
    ap.add_argument("--approval-signature", required=True,
                    help="base64 Ed25519 signature over the exact approval JSON")
    ap.add_argument("--isolation-mode", required=True,
                    choices=("production-boundary", "dedicated-account"),
                    help="Isolation proof used for this run")
    ap.add_argument("--production-folders", default="",
                    help="Comma-separated known production folder IDs; "
                         "required in production-boundary mode")
    ap.add_argument("--receipt", required=True,
                    help="Path for JSON receipt (test log + evidence)")
    ap.add_argument("--keep", action="store_true",
                    help="Keep test files after run (for manual inspection)")
    args = ap.parse_args()

    receipt: Dict[str, Any] = {
        "script": "m4_drive_e2e.py",
        "test_go": args.test_go,
        "test_folder": args.test_folder,
        "steps": [],
        "residual_file_ids": [],
        "status": "STARTED",
    }

    def step(name: str, ok: bool, detail: str = "") -> None:
        receipt["steps"].append({"name": name, "ok": ok, "detail": detail})
        print(f"M4_E2E: [{'OK' if ok else 'FAIL'}] {name}"
              + (f": {detail}" if detail else ""))

    # --- Gate 1: TEST authorization must be explicit ---
    if not args.test_go.strip():
        fail("explicit --test-go required (isolated TEST authorization)")

    engine_root = Path(__file__).resolve().parents[1]
    expected_account = args.expected_account.strip().lower()
    if not expected_account or "@" not in expected_account:
        fail("--expected-account must be a valid explicit email address")

    # --- Gate 1b: isolation policy ---
    prod_folders = [f.strip() for f in args.production_folders.split(",")
                    if f.strip()]
    if args.isolation_mode == "production-boundary" and not prod_folders:
        fail("--production-folders must list at least one production "
             "folder ID in production-boundary mode.")
    if args.isolation_mode == "dedicated-account" and prod_folders:
        fail("dedicated-account mode must not claim unverifiable production "
             "folder IDs; omit --production-folders")
    test_folder = args.test_folder.strip()
    if not test_folder:
        fail("--test-folder must be non-empty.")
    if test_folder in prod_folders:
        fail("--test-folder must not coincide with a production folder ID; "
             "refusing to run.")
    prod_folders_sha256 = hashlib.sha256(json.dumps(
        sorted(prod_folders), separators=(",", ":")).encode("utf-8")).hexdigest()

    # --- Gate 1c: Private Instance + signed, time-bounded Human approval ---
    try:
        inst = _load_private_instance(args.instance_root, engine_root=engine_root)
        engine_sha = _current_git_sha(engine_root)
    except Exception as exc:  # noqa: BLE001
        fail(f"cannot establish clean Engine/Private Instance boundary: {exc}")
    try:
        approval_public_key = _read_root_trust_anchor()
    except Exception as exc:  # noqa: BLE001
        fail(f"cannot load trusted Human approval key: {exc}")

    def require_current_authorization() -> Dict[str, Any]:
        return _validate_authorization(
            args.approval_record, test_go=args.test_go.strip(),
            engine_sha=engine_sha, expected_account=expected_account,
            test_folder=test_folder, isolation_mode=args.isolation_mode,
            production_folders_sha256=prod_folders_sha256,
            signature_path=args.approval_signature,
            public_key_bytes=approval_public_key)

    try:
        approval = require_current_authorization()
    except Exception as exc:  # noqa: BLE001
        fail(f"invalid TEST authorization: {exc}")
    receipt["engine_sha"] = engine_sha
    receipt["approval"] = {
        "approved_by": approval["approved_by"],
        "approved_at": approval["approved_at"],
        "expires_at": approval["expires_at"],
        "scope": approval["scope"],
        "production_folders_sha256": prod_folders_sha256,
        "authorization_id": approval["authorization_id"],
    }
    step("test_authorization_verified", True, approval["test_go"])
    try:
        _write_receipt(args.receipt, receipt)
    except _ReceiptWriteFailure as exc:
        fail(str(exc))

    # Explicit creation allowlist: test folder only (subdirs added as created).
    # File creation is RESTRICTED to this allowlist -- the production_folders
    # blacklist is defense-in-depth, not the primary control.
    allowed_create_folders: List[str] = [test_folder]

    def assert_in_scope(folder_id: str, what: str) -> None:
        if folder_id not in allowed_create_folders:
            fail(f"{what}: folder {folder_id} not in creation allowlist "
                 f"{allowed_create_folders}; refusing to write outside "
                 f"authorized test scope.")

    # --- Gate 2: OAuth must exist (fail-closed, no interactive auth) ---
    try:
        from cba_kb.drive import Drive, SCOPES
    except ImportError as exc:
        fail(f"cannot import drive modules: {exc}")

    token = Path(inst.credentials_store) / "token.json"
    if not token.exists():
        fail(f"Drive OAuth token missing at {token}; run make auth first. "
             f"M4 cannot proceed without real authorization.")

    try:
        drive = Drive(args.instance_root, instance=inst)
    except Exception as exc:
        fail(f"Drive client construction failed: {exc}")

    # --- Gate 2b: log authenticated identity for account isolation check ---
    try:
        authed_email = _verify_authenticated_account(drive, expected_account)
    except ValueError as exc:
        fail(str(exc))
    print(f"M4_E2E: authenticated as: {authed_email}")
    receipt["authenticated_email"] = authed_email
    receipt["oauth_scopes"] = SCOPES
    step("auth_identity_verified", True, authed_email)

    # --- Gate 3: isolation — verify test folder exists and is a folder ---
    try:
        folder_meta = drive.api.files().get(
            fileId=test_folder,
            fields="id,name,mimeType,parents,trashed,driveId,owners(emailAddress)",
            supportsAllDrives=True).execute(num_retries=0)
    except Exception as exc:
        fail(f"cannot read test folder {test_folder}: {exc}")
    if folder_meta.get("mimeType") != "application/vnd.google-apps.folder":
        fail(f"--test-folder {test_folder} is not a folder "
             f"(mime={folder_meta.get('mimeType')})")
    if folder_meta.get("trashed"):
        fail("--test-folder is trashed")
    if args.isolation_mode == "dedicated-account":
        try:
            _verify_dedicated_account(
                drive, folder_meta, test_folder, expected_account,
                [test_folder])
        except ValueError as exc:
            fail(str(exc))
    existing = drive.list(test_folder)
    if existing:
        fail(f"--test-folder must be empty before the run; found "
             f"{len(existing)} existing object(s)")
    step("test_folder_verified", True,
         f"{test_folder} ({folder_meta.get('name')})")

    # --- Build evidence adapter ---
    from cba_kb.drive_adapter import DriveEvidenceAdapter
    adapter = DriveEvidenceAdapter(drive)

    # --- Create synthetic test files inside the test folder ---
    from cba_kb import drive_plan as dp
    from cba_kb.drive_move_plan import create_drive_move_plan

    content = b"M4_E2E_SYNTHETIC content 2026-10-16"
    sha256 = hashlib.sha256(content).hexdigest()
    run_marker = hashlib.sha256(
        f"{args.test_go.strip()}\0{engine_sha}\0{test_folder}".encode("utf-8")
    ).hexdigest()
    receipt["run_marker"] = run_marker
    created_ids: List[str] = []
    live_run_ids: List[str] = [test_folder]
    dest_id: Optional[str] = None
    file_id: Optional[str] = None
    e2e_error_code: int = 0
    e2e_error_msg: str = ""

    def require_mutation_gate() -> Dict[str, Any]:
        current_approval = require_current_authorization()
        if args.isolation_mode == "dedicated-account":
            current_folder = drive.api.files().get(
                fileId=test_folder,
                fields="id,name,mimeType,parents,trashed,driveId,owners(emailAddress)",
                supportsAllDrives=True).execute(num_retries=0)
            if current_folder.get("trashed"):
                raise ValueError("dedicated-account test folder became trashed")
            _verify_dedicated_account(
                drive, current_folder, test_folder, expected_account,
                live_run_ids)
        return current_approval

    try:
        from googleapiclient.http import MediaIoBaseUpload
        import io

        # Pre-allocate exact IDs so a successful create with a lost response
        # remains discoverable and cleanable.
        _consume_authorization(inst, approval)
        step("test_authorization_consumed", True, approval["authorization_id"])
        allocated = drive.api.files().generateIds(
            count=2, space="drive", type="files",
        ).execute(num_retries=0).get("ids", [])
        if len(allocated) != 2 or len(set(allocated)) != 2:
            raise _E2EFailure("Drive did not allocate two unique cleanup IDs", 2)
        file_id, dest_id = allocated
        created_ids.extend([file_id, dest_id])

        # Create source file (allowlist-checked).
        assert_in_scope(test_folder, "source file creation")
        created = _authorized_mutation(
            require_mutation_gate,
            lambda: drive.api.files().create(
                body={"id": file_id,
                      "name": "M4_E2E_SYNTHETIC_report.txt",
                      "parents": [test_folder],
                      "mimeType": "text/plain",
                      "appProperties": {"cba_m4_run": run_marker}},
                media_body=MediaIoBaseUpload(io.BytesIO(content),
                                             mimetype="text/plain"),
                fields="id", supportsAllDrives=True).execute(num_retries=0))
        if created.get("id") != file_id:
            raise _E2EFailure("source create returned an unexpected ID", 2)
        live_run_ids.append(file_id)
        step("create_test_file", True, file_id)
        receipt["source_file_id"] = file_id
        receipt["source_sha256_local"] = sha256

        # Create destination subfolder (allowlist-checked).
        assert_in_scope(test_folder, "destination folder creation")
        dest = _authorized_mutation(
            require_mutation_gate,
            lambda: drive.api.files().create(
                body={"id": dest_id,
                      "name": "M4_E2E_SYNTHETIC_dest",
                      "parents": [test_folder],
                      "mimeType": "application/vnd.google-apps.folder",
                      "appProperties": {"cba_m4_run": run_marker}},
                fields="id", supportsAllDrives=True).execute(num_retries=0))
        if dest.get("id") != dest_id:
            raise _E2EFailure("destination create returned an unexpected ID", 2)
        live_run_ids.append(dest_id)
        allowed_create_folders.append(dest_id)
        step("create_dest_folder", True, dest_id)
        receipt["dest_folder_id"] = dest_id

        # Capture pre-move metadata for comparison.
        pre_meta = drive.meta(file_id)
        receipt["pre_move_metadata"] = {
            "parents": pre_meta.get("parents"),
            "mimeType": pre_meta.get("mimeType"),
            "headRevisionId": pre_meta.get("headRevisionId"),
            "md5Checksum": pre_meta.get("md5Checksum"),
        }
        step("pre_move_metadata_captured", True,
             f"parents={pre_meta.get('parents')}")

        # Read evidence via the evidence adapter (real API fields).
        ev = adapter.meta(file_id)
        if not isinstance(ev.get("sha256"), str) or not ev["sha256"]:
            step("evidence_read", False, "sha256 unavailable")
            raise _E2EFailure(
                "sha256 evidence unavailable from real API "
                "(native doc, oversize file, or download failure); "
                "cannot verify content identity. Failing closed.", 2)
        step("evidence_read", True,
             f"sha256={ev['sha256'][:16]}... revision={ev['revision_id']}")
        receipt["evidence"] = {
            "sha256": ev["sha256"],
            "revision_id": ev["revision_id"],
            "mime_type": ev["mime_type"],
        }
        if ev["sha256"] != sha256:
            step("evidence_sha256_match", False,
                 f"adapter={ev['sha256'][:16]}... local={sha256[:16]}...")
            raise _E2EFailure(
                f"sha256 evidence mismatch: adapter={ev['sha256']} "
                f"local={sha256}", 2)
        step("evidence_sha256_match", True)
        if not ev["revision_id"]:
            step("evidence_revision_present", False, "missing")
            raise _E2EFailure("revision_id evidence missing from real API", 2)
        step("evidence_revision_present", True, ev["revision_id"])

        # Build + approve + apply the exact plan.
        move_plan = create_drive_move_plan(
            file_id=file_id, source_id="m4-e2e-s1", doc_id="m4-e2e-d1",
            source_parent=test_folder, destination=dest_id,
            expected_parent_set=[dest_id], disposition="ARCHIVE",
            approval_ref=args.test_go.strip(),
            sha256=sha256, revision_id=ev["revision_id"],
            mime_type="text/plain")
        receipt["plan"] = {
            "file_id": file_id,
            "source_parent": test_folder,
            "destination": dest_id,
            "expected_parent_set": [dest_id],
            "approval_ref": args.test_go.strip(),
        }
        with tempfile.TemporaryDirectory() as td:
            plan_path = Path(td) / "plan.json"
            ledger_path = Path(td) / "ledger.jsonl"
            plan = dp.create_plan(
                [{"op": "drive_move", "params": {"move_plan": move_plan}}])
            dp.save_plan(plan_path, plan)
            dp.approve_plan(plan_path, approval["approved_by"], ledger_path)
            step("plan_approved", True, plan["plan_id"][:12])
            receipt["plan"]["plan_id"] = plan["plan_id"]
            receipt["plan"]["approved_by"] = approval["approved_by"]
            guarded_adapter = _AuthorizedDriveAdapter(
                adapter, require_mutation_gate)
            ctx = {"drive_adapter": guarded_adapter,
                   "test_go": args.test_go,
                   "allowed_folders": [test_folder, dest_id],
                   "production_folders": prod_folders}
            out = _authorized_mutation(
                require_mutation_gate,
                lambda: dp.apply_plan(plan_path, ledger_path, ctx))
            if out["failed"] is not None:
                step("apply", False, out["failed"]["error"])
                raise _E2EFailure(f"apply failed: {out['failed']['error']}", 2)
            step("apply", True,
                 f"verified_parents="
                 f"{out['completed'][0]['result']['verified_parents']}")
            receipt["apply_result"] = out["completed"][0]["result"]

            # Independent readback via raw API.
            final = drive.meta(file_id)
            receipt["post_move_metadata"] = {
                "parents": final.get("parents"),
                "mimeType": final.get("mimeType"),
                "headRevisionId": final.get("headRevisionId"),
                "md5Checksum": final.get("md5Checksum"),
            }
            if set(final.get("parents", [])) != {dest_id}:
                step("readback", False,
                     f"parents={final.get('parents')}")
                raise _E2EFailure(
                    f"readback mismatch: parents={final.get('parents')}", 2)
            step("readback", True, f"parents={[dest_id]}")

        step("all_checks", True)
        # NOTE: receipt["status"] is set AFTER cleanup (see below).
        # Do NOT set PASSED here -- cleanup may fail.
    except _E2EFailure as exc:
        # E2E step failed AFTER files may exist. Record the error;
        # do NOT exit yet -- finally-block cleanup must run first,
        # and the exit code is determined AFTER cleanup (residuals -> 3).
        step("e2e_failure", False, str(exc))
        receipt["error"] = str(exc)
        e2e_error_code = exc.code
        e2e_error_msg = str(exc)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        step("unexpected_error", False, str(exc))
        receipt["error"] = str(exc)
        e2e_error_code = 2
        e2e_error_msg = str(exc)
    finally:
        # Cleanup: delete test files. TRACK failures; residual IDs are
        # reported and cause exit 3 (never silent PASS).
        residuals: List[str] = []
        if not args.keep:
            residuals = _cleanup_created(drive, created_ids, run_marker)
        else:
            try:
                residuals = _find_run_objects(drive, run_marker)
            except Exception:
                residuals = list(created_ids)
            print(f"M4_E2E: --keep: {len(residuals)} files retained")
        receipt["residual_file_ids"] = residuals
        # Set final status AFTER cleanup (never before).
        if residuals:
            receipt["status"] = "CLEANUP_INCOMPLETE"
        elif "error" in receipt:
            receipt["status"] = "FAILED"
        else:
            receipt["status"] = "PASSED"
        try:
            _write_receipt(args.receipt, receipt)
        except _ReceiptWriteFailure as exc:
            receipt["status"] = "RECEIPT_WRITE_FAILED"
            receipt["error"] = str(exc)
            e2e_error_code = 4
            e2e_error_msg = str(exc)
            print(f"M4_E2E_FAIL: {exc}", file=sys.stderr)

    # Final determination AFTER cleanup:
    # - receipt failure -> exit 4 (takes priority; formal evidence is missing)
    # - residuals exist -> exit 3 (takes priority over ordinary E2E errors)
    # - E2E failed but cleanup OK -> original error code
    # - all good -> exit 0
    if receipt.get("status") == "RECEIPT_WRITE_FAILED":
        print(f"M4_E2E_FAIL: {e2e_error_msg}", file=sys.stderr)
        return 4
    if receipt["residual_file_ids"]:
        print(f"M4_E2E: CLEANUP INCOMPLETE -- residual files: "
              f"{receipt['residual_file_ids']}", file=sys.stderr)
        print(f"M4_E2E: Manual recovery: delete the above file IDs from "
              f"the test folder, then re-run.", file=sys.stderr)
        return 3
    if "error" in receipt:
        print(f"M4_E2E_FAIL: {e2e_error_msg}", file=sys.stderr)
        return e2e_error_code
    print("M4_E2E: ALL CHECKS PASSED, cleanup complete, no residuals")
    return 0


if __name__ == "__main__":
    sys.exit(main())
