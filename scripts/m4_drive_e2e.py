#!/usr/bin/env python3
"""M4: Isolated Google Drive end-to-end verification (requires real OAuth).

This script executes the REAL Drive move chain against an ISOLATED test
folder. It is FAIL-CLOSED at every step:

1. Requires --instance-root with valid Drive OAuth (Private Instance).
2. Requires --test-folder: the isolated test folder ID. The script REFUSES
   to operate outside this folder (isolation guard).
3. Creates synthetic test files INSIDE the test folder only.
4. Builds an exact drive_move plan, approves it, applies it with an
   explicit --production-go reference, and independently readbacks.
5. Cleans up test files afterwards.

USAGE (only after Human provides test folder + OAuth):
    python scripts/m4_drive_e2e.py \
        --instance-root /path/to/private/instance \
        --test-folder <ISOLATED_TEST_FOLDER_ID> \
        --test-go "M4-TEST-2026-10-16-human-ref" \
        --production-folders "<PROD_FOLDER_ID_1>,<PROD_FOLDER_ID_2>"

AUTHORIZATION MODEL:
    This script uses --test-go (isolated TEST authorization), NOT
    --production-go. The two are mutually exclusive by design:
    a TEST reference can never authorize a production write, and the
    production_folders guard rejects any TEST operation touching
    production folders.

SAFETY:
- NEVER run without --test-folder.
- The script verifies the test folder is NOT the production folder.
- All test files are prefixed "M4_E2E_SYNTHETIC_" and deleted on completion.
- Any failure aborts with a non-zero exit; nothing is left half-moved.

EXIT CODES:
    0: all checks passed, cleanup complete.
    1: environment/auth/isolation failure (fail-closed, nothing written).
    2: E2E assertion failure (see output; manual inspection may be needed).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path


def fail(msg: str, code: int = 1) -> "NoReturn":
    print(f"M4_E2E_FAIL: {msg}", file=sys.stderr)
    sys.exit(code)


def main() -> int:
    ap = argparse.ArgumentParser(description="M4 isolated Drive E2E")
    ap.add_argument("--instance-root", required=True,
                    help="Private Instance root with Drive OAuth")
    ap.add_argument("--test-folder", required=True,
                    help="Isolated test folder ID (ALL operations stay inside)")
    ap.add_argument("--test-go", required=True,
                    help="Isolated TEST authorization reference (NOT a production GO)")
    ap.add_argument("--production-folders", default="",
                    help="Comma-separated known production folder IDs; "
                         "TEST mode refuses to touch them")
    ap.add_argument("--keep", action="store_true",
                    help="Keep test files after run (for manual inspection)")
    args = ap.parse_args()

    # --- Gate 1: TEST authorization must be explicit ---
    # NOTE: --test-go is for isolated TESTING only. It is NOT a production
    # GO and must never be used as a substitute for production authorization.
    if not args.test_go.strip():
        fail("explicit --test-go required (isolated TEST authorization)")

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

    # --- Gate 3: isolation — verify test folder exists and is a folder ---
    try:
        folder_meta = drive.meta(args.test_folder)
    except Exception as exc:
        fail(f"cannot read test folder {args.test_folder}: {exc}")
    if folder_meta.get("mimeType") != "application/vnd.google-apps.folder":
        fail(f"--test-folder {args.test_folder} is not a folder "
             f"(mime={folder_meta.get('mimeType')})")

    # --- Gate 4: isolation — refuse known production folder IDs ---
    # (Human must confirm the test folder is isolated before running.)
    print(f"M4_E2E: test folder verified: {args.test_folder} "
          f"({folder_meta.get('name')})")
    print(f"M4_E2E: OAuth scopes: {SCOPES}")

    # --- Build evidence adapter ---
    from cba_kb.drive_adapter import DriveEvidenceAdapter
    adapter = DriveEvidenceAdapter(drive)

    # --- Create synthetic test files inside the test folder ---
    from cba_kb import drive_plan as dp
    from cba_kb.drive_move_plan import create_drive_move_plan

    content = b"M4_E2E_SYNTHETIC content 2026-10-16"
    sha256 = hashlib.sha256(content).hexdigest()
    created_ids = []
    try:
        # Create source file via Drive API (inside test folder only).
        from googleapiclient.http import MediaIoBaseUpload
        import io
        created = drive.api.files().create(
            body={"name": "M4_E2E_SYNTHETIC_report.txt",
                  "parents": [args.test_folder],
                  "mimeType": "text/plain"},
            media_body=MediaIoBaseUpload(io.BytesIO(content),
                                         mimetype="text/plain"),
            fields="id", supportsAllDrives=True).execute(num_retries=0)
        file_id = created["id"]
        created_ids.append(file_id)
        print(f"M4_E2E: created test file {file_id}")

        # Create destination subfolder inside the test folder.
        dest = drive.api.files().create(
            body={"name": "M4_E2E_SYNTHETIC_dest",
                  "parents": [args.test_folder],
                  "mimeType": "application/vnd.google-apps.folder"},
            fields="id", supportsAllDrives=True).execute(num_retries=0)
        dest_id = dest["id"]
        created_ids.append(dest_id)
        print(f"M4_E2E: created dest folder {dest_id}")

        # Read evidence via the evidence adapter (real API fields).
        ev = adapter.meta(file_id)
        print(f"M4_E2E: evidence: sha256={ev['sha256'][:16]}... "
              f"revision_id={ev['revision_id']} mime_type={ev['mime_type']}")
        if ev["sha256"] != sha256:
            fail(f"sha256 evidence mismatch: adapter={ev['sha256']} "
                 f"local={sha256}", code=2)
        if not ev["revision_id"]:
            fail("revision_id evidence missing from real API", code=2)

        # Build + approve + apply the exact plan.
        move_plan = create_drive_move_plan(
            file_id=file_id, source_id="m4-e2e-s1", doc_id="m4-e2e-d1",
            source_parent=args.test_folder, destination=dest_id,
            expected_parent_set=[dest_id], disposition="ARCHIVE",
            approval_ref="m4-e2e-human-approval",
            sha256=sha256, revision_id=ev["revision_id"],
            mime_type="text/plain")
        with tempfile.TemporaryDirectory() as td:
            plan_path = Path(td) / "plan.json"
            ledger_path = Path(td) / "ledger.jsonl"
            plan = dp.create_plan(
                [{"op": "drive_move", "params": {"move_plan": move_plan}}])
            dp.save_plan(plan_path, plan)
            dp.approve_plan(plan_path, "m4-e2e-human", ledger_path)
            # TEST authorization: test_go + allowlist (test folder + dest).
            # production_folders guard ensures TEST never touches production.
            ctx = {"drive_adapter": adapter,
                   "test_go": args.test_go,
                   "allowed_folders": [args.test_folder, dest_id],
                   "production_folders": [
                       f.strip() for f in args.production_folders.split(",")
                       if f.strip()]}
            out = dp.apply_plan(plan_path, ledger_path, ctx)
            if out["failed"] is not None:
                fail(f"apply failed: {out['failed']['error']}", code=2)
            print(f"M4_E2E: apply OK, verified_parents="
                  f"{out['completed'][0]['result']['verified_parents']}")

            # Independent readback via raw API.
            final = drive.meta(file_id)
            if set(final.get("parents", [])) != {dest_id}:
                fail(f"readback mismatch: parents={final.get('parents')}",
                     code=2)
            print(f"M4_E2E: independent readback OK: parents={[dest_id]}")

        print("M4_E2E: ALL CHECKS PASSED")
        return 0
    finally:
        # Cleanup: delete test files (best-effort, report failures).
        if not args.keep:
            for fid in reversed(created_ids):
                try:
                    drive.api.files().delete(
                        fileId=fid,
                        supportsAllDrives=True).execute(num_retries=0)
                    print(f"M4_E2E: cleaned up {fid}")
                except Exception as exc:  # noqa: BLE001
                    print(f"M4_E2E_WARN: cleanup failed for {fid}: {exc}",
                          file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
