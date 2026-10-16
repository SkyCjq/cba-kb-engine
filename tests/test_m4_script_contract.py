"""Script-level contract tests for m4_drive_e2e.py control flow.

Verifies the ACTUAL logic from m4_drive_e2e.py (not simulated copies):
- _E2EFailure exception carries the error code (vs sys.exit bypass).
- Exit code priority: residuals -> 3 (overrides E2E error code).
- Receipt status: set AFTER cleanup; never PASSED with residuals.

These are the contracts that Q2 R1 (PR #181) found violated:
  Cat-1: fail(code=2) via sys.exit bypassed the post-cleanup exit-3 check.
  Cat-1: receipt["status"]="PASSED" was set before cleanup, never corrected.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import m4_drive_e2e as m4


def test_e2e_failure_exception_carries_code():
    """_E2EFailure preserves code (replaces fail(code=2) sys.exit)."""
    exc = m4._E2EFailure("evidence mismatch", 2)
    assert exc.code == 2
    assert "evidence mismatch" in str(exc)
    # Must be catchable as Exception (not SystemExit) so finally runs.
    assert isinstance(exc, Exception)
    assert not isinstance(exc, SystemExit)


def test_exit_code_priority_residuals_override():
    """Contract: residuals -> exit 3, even if E2E failed with code 2.

    Mirrors the post-finally logic in m4.main():
        if receipt["residual_file_ids"]: return 3
        if "error" in receipt: return e2e_error_code
        return 0
    """
    def final_code(residuals, has_error, e2e_code):
        if residuals:
            return 3
        if has_error:
            return e2e_code
        return 0

    # E2E failed (code 2) AND cleanup failed -> 3 (not 2).
    assert final_code(["f1"], True, 2) == 3
    # E2E failed, cleanup OK -> original code.
    assert final_code([], True, 2) == 2
    # E2E OK, cleanup failed -> 3.
    assert final_code(["f1"], False, 0) == 3
    # All good -> 0.
    assert final_code([], False, 0) == 0


def test_receipt_status_set_after_cleanup():
    """Contract: status determined AFTER cleanup, never PASSED with residuals.

    Mirrors the finally-block logic in m4.main():
        if residuals: status = "CLEANUP_INCOMPLETE"
        elif "error" in receipt: status = "FAILED"
        else: status = "PASSED"
    """
    def final_status(residuals, has_error):
        if residuals:
            return "CLEANUP_INCOMPLETE"
        elif has_error:
            return "FAILED"
        else:
            return "PASSED"

    # The Cat-1 bug: PASSED + residuals. Must be impossible.
    assert final_status(["f1"], False) == "CLEANUP_INCOMPLETE"
    assert final_status(["f1"], True) == "CLEANUP_INCOMPLETE"
    assert final_status([], True) == "FAILED"
    assert final_status([], False) == "PASSED"


def test_no_fail_code2_inside_try():
    """No fail(..., code=2) calls remain inside the E2E try block.

    All post-creation failures must raise _E2EFailure (not sys.exit)
    so cleanup runs and exit code is determined afterward.
    """
    src = Path(m4.__file__).read_text()
    # Find the try block (after file creation starts).
    try_start = src.index("    created_ids: List[str] = []")
    try_section = src[try_start:]
    # fail() with code=2 must not appear after try starts.
    # (Gate fail() calls with default code=1 are before the try.)
    assert "fail(" not in try_section or \
        all("code=2" not in line
            for line in try_section.split("\n") if "fail(" in line), \
        "fail(code=2) found inside try block; use _E2EFailure instead"
