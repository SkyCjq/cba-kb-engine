#!/usr/bin/env python3
"""M4: Isolated Google Drive end-to-end verification (requires real OAuth).

This script executes the REAL Drive move chain against an ISOLATED test
folder. It is FAIL-CLOSED at every step:

1. Requires --instance-root with valid Drive OAuth (Private Instance).
2. Requires --test-folder: the isolated test folder ID.
3. Requires --test-go: isolated TEST authorization reference.
   NOTE: --test-go authorizes ISOLATED TESTING ONLY. It can never
   authorize production operations. There is no --production-go here.
4. Requires --production-folders: known production folder IDs that TEST
   must never touch (non-empty, validated).
5. Creates synthetic test files INSIDE the test folder only
   (explicit allowlist, not just blacklist).
6. Builds an exact drive_move plan, approves it, applies it with
   test_go + allowlist, and independently readbacks.
7. Cleans up test files; cleanup failures are TRACKED and reported
   (residual file IDs), never silently ignored.

USAGE (only after Human provides test folder + OAuth):
    python scripts/m4_drive_e2e.py \
        --instance-root /path/to/private/instance \
        --test-folder <ISOLATED_TEST_FOLDER_ID> \
        --test-go "M4-TEST-2026-10-16-human-ref" \
        --production-folders "<PROD_FOLDER_ID_1>,<PROD_FOLDER_ID_2>" \
        --receipt /tmp/m4-receipt.json

AUTHORIZATION MODEL:
    This script uses --test-go (isolated TEST authorization) ONLY.
    A TEST reference can never authorize a production write.
    The production_folders guard rejects any TEST operation touching
    production folders. File creation is restricted to an explicit
    allowlist (test folder + created subdirectories).

ACCOUNT ISOLATION:
    Prefer a dedicated test Google account or a separately permissioned
    test environment. The script logs the authenticated user's email
    at startup so the operator can verify it is the test account,
    not a production account. Full Drive scope does NOT imply
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
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


def fail(msg: str, code: int = 1) -> "NoReturn":
    print(f"M4_E2E_FAIL: {msg}", file=sys.stderr)
    sys.exit(code)


def _write_receipt(path: Optional[str], data: Dict[str, Any]) -> None:
    """Write JSON receipt (best-effort; never fails the run)."""
    if not path:
        return
    try:
        Path(path).write_text(json.dumps(data, indent=2, sort_keys=True),
                               encoding="utf-8")
        print(f"M4_E2E: receipt written to {path}")
    except Exception as exc:  # noqa: BLE001
        print(f"M4_E2E_WARN: receipt write failed: {exc}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description="M4 isolated Drive E2E")
    ap.add_argument("--instance-root", required=True,
                    help="Private Instance root with Drive OAuth")
    ap.add_argument("--test-folder", required=True,
                    help="Isolated test folder ID (ALL operations stay inside)")
    ap.add_argument("--test-go", required=True,
                    help="Isolated TEST authorization reference (NOT a production GO)")
    ap.add_argument("--production-folders", required=True,
                    help="Comma-separated known production folder IDs; "
                         "TEST mode refuses to touch them. REQUIRED: "
                         "refuse real Drive writes when production scope "
                         "is unknown.")
    ap.add_argument("--receipt", default=None,
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

    # --- Gate 1b: production scope must be explicitly non-empty ---
    prod_folders = [f.strip() for f in args.production_folders.split(",")
                    if f.strip()]
    if not prod_folders:
        fail("--production-folders must list at least one production "
             "folder ID; refusing real Drive writes with unknown "
             "production scope.")
    test_folder = args.test_folder.strip()
    if not test_folder:
        fail("--test-folder must be non-empty.")
    if test_folder in prod_folders:
        fail("--test-folder must not coincide with a production folder ID; "
             "refusing to run.")

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
        from cba_kb.instance import load_instance
    except ImportError as exc:
        fail(f"cannot import drive modules: {exc}")

    try:
        inst = load_instance(args.instance_root)
    except Exception as exc:
        fail(f"cannot load Private Instance: {exc}")
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
        about = drive.api.about().get(fields="user(emailAddress)").execute(
            num_retries=0)
        authed_email = about.get("user", {}).get("emailAddress", "unknown")
    except Exception as exc:  # noqa: BLE001
        authed_email = f"unavailable: {exc}"
    print(f"M4_E2E: authenticated as: {authed_email}")
    print(f"M4_E2E: VERIFY this is the isolated TEST account, not production.")
    receipt["authenticated_email"] = authed_email
    receipt["oauth_scopes"] = SCOPES
    step("auth_identity_logged", True, authed_email)

    # --- Gate 3: isolation — verify test folder exists and is a folder ---
    try:
        folder_meta = drive.meta(test_folder)
    except Exception as exc:
        fail(f"cannot read test folder {test_folder}: {exc}")
    if folder_meta.get("mimeType") != "application/vnd.google-apps.folder":
        fail(f"--test-folder {test_folder} is not a folder "
             f"(mime={folder_meta.get('mimeType')})")
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
    created_ids: List[str] = []
    dest_id: Optional[str] = None
    file_id: Optional[str] = None

    try:
        from googleapiclient.http import MediaIoBaseUpload
        import io

        # Create source file (allowlist-checked).
        assert_in_scope(test_folder, "source file creation")
        created = drive.api.files().create(
            body={"name": "M4_E2E_SYNTHETIC_report.txt",
                  "parents": [test_folder],
                  "mimeType": "text/plain"},
            media_body=MediaIoBaseUpload(io.BytesIO(content),
                                         mimetype="text/plain"),
            fields="id", supportsAllDrives=True).execute(num_retries=0)
        file_id = created["id"]
        created_ids.append(file_id)
        step("create_test_file", True, file_id)
        receipt["source_file_id"] = file_id
        receipt["source_sha256_local"] = sha256

        # Create destination subfolder (allowlist-checked).
        assert_in_scope(test_folder, "destination folder creation")
        dest = drive.api.files().create(
            body={"name": "M4_E2E_SYNTHETIC_dest",
                  "parents": [test_folder],
                  "mimeType": "application/vnd.google-apps.folder"},
            fields="id", supportsAllDrives=True).execute(num_retries=0)
        dest_id = dest["id"]
        created_ids.append(dest_id)
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
            fail("sha256 evidence unavailable from real API "
                 "(native doc, oversize file, or download failure); "
                 "cannot verify content identity. Failing closed.", code=2)
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
            fail(f"sha256 evidence mismatch: adapter={ev['sha256']} "
                 f"local={sha256}", code=2)
        step("evidence_sha256_match", True)
        if not ev["revision_id"]:
            step("evidence_revision_present", False, "missing")
            fail("revision_id evidence missing from real API", code=2)
        step("evidence_revision_present", True, ev["revision_id"])

        # Build + approve + apply the exact plan.
        move_plan = create_drive_move_plan(
            file_id=file_id, source_id="m4-e2e-s1", doc_id="m4-e2e-d1",
            source_parent=test_folder, destination=dest_id,
            expected_parent_set=[dest_id], disposition="ARCHIVE",
            approval_ref="m4-e2e-human-approval",
            sha256=sha256, revision_id=ev["revision_id"],
            mime_type="text/plain")
        receipt["plan"] = {
            "file_id": file_id,
            "source_parent": test_folder,
            "destination": dest_id,
            "expected_parent_set": [dest_id],
        }
        with tempfile.TemporaryDirectory() as td:
            plan_path = Path(td) / "plan.json"
            ledger_path = Path(td) / "ledger.jsonl"
            plan = dp.create_plan(
                [{"op": "drive_move", "params": {"move_plan": move_plan}}])
            dp.save_plan(plan_path, plan)
            dp.approve_plan(plan_path, "m4-e2e-human", ledger_path)
            step("plan_approved", True, plan["plan_id"][:12])
            ctx = {"drive_adapter": adapter,
                   "test_go": args.test_go,
                   "allowed_folders": [test_folder, dest_id],
                   "production_folders": prod_folders}
            out = dp.apply_plan(plan_path, ledger_path, ctx)
            if out["failed"] is not None:
                step("apply", False, out["failed"]["error"])
                fail(f"apply failed: {out['failed']['error']}", code=2)
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
                fail(f"readback mismatch: parents={final.get('parents')}",
                     code=2)
            step("readback", True, f"parents={[dest_id]}")

        step("all_checks", True)
        receipt["status"] = "PASSED"
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        step("unexpected_error", False, str(exc))
        receipt["status"] = "FAILED"
        receipt["error"] = str(exc)
        raise
    finally:
        # Cleanup: delete test files. TRACK failures; residual IDs are
        # reported and cause a non-zero exit (never silent PASS).
        residuals: List[str] = []
        if not args.keep:
            for fid in reversed(created_ids):
                try:
                    drive.api.files().delete(
                        fileId=fid,
                        supportsAllDrives=True).execute(num_retries=0)
                    print(f"M4_E2E: cleaned up {fid}")
                except Exception as exc:  # noqa: BLE001
                    print(f"M4_E2E_FAIL: cleanup failed for {fid}: {exc}",
                          file=sys.stderr)
                    residuals.append(fid)
        else:
            residuals = list(created_ids)
            print(f"M4_E2E: --keep: {len(residuals)} files retained")
        receipt["residual_file_ids"] = residuals
        _write_receipt(args.receipt, receipt)

    # Final determination: cleanup failures are NOT a full PASS.
    if receipt["residual_file_ids"]:
        print(f"M4_E2E: CLEANUP INCOMPLETE -- residual files: "
              f"{receipt['residual_file_ids']}", file=sys.stderr)
        print(f"M4_E2E: Manual recovery: delete the above file IDs from "
              f"the test folder, then re-run.", file=sys.stderr)
        return 3
    print("M4_E2E: ALL CHECKS PASSED, cleanup complete, no residuals")
    return 0


if __name__ == "__main__":
    sys.exit(main())
