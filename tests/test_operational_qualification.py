import pytest

from cba_kb.operational_qualification import (
    OperationalQualificationError,
    append_cycle,
    ledger_bytes,
    new_ledger,
    project_closeout_state,
    validate_ledger,
)


def test_seven_planned_cycles_are_required_without_rounding_up():
    ledger = new_ledger("REQ-161-CLOSEOUT-01")
    for cycle in range(6):
        append_cycle(
            ledger,
            cycle=cycle,
            observed_change="NONE",
            recorded_at=f"2026-09-{cycle + 1:02d}T00:00:00Z",
        )
    assert validate_ledger(ledger)["status"] == "PENDING"
    with pytest.raises(OperationalQualificationError, match="INCOMPLETE"):
        validate_ledger(ledger, require_complete=True)
    append_cycle(
        ledger,
        cycle=6,
        observed_change="CHANGE_OBSERVED",
        evidence={"business_event": False},
        recorded_at="2026-09-07T00:00:00Z",
    )
    result = validate_ledger(ledger, require_complete=True)
    assert result["status"] == "PASS"
    assert result["project_closeout_state"] == "CLOSED"
    assert ledger_bytes(ledger)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"manual_repair_required": True}, "MANUAL_REPAIR"),
        ({"canonical_publish_allowed": True}, "PUBLISH_FORBIDDEN"),
        ({"planned": False}, "PLANNED_CYCLE"),
        ({"evidence": {"business_event": True}}, "BUSINESS_EVENT"),
    ],
)
def test_invalid_operational_cycles_fail_closed(kwargs, message):
    ledger = new_ledger("REQ-161-CLOSEOUT-01")
    with pytest.raises(OperationalQualificationError, match=message):
        append_cycle(
            ledger,
            cycle=0,
            recorded_at="2026-09-13T00:00:00Z",
            **kwargs,
        )

def test_project_closeout_is_separate_from_release_completion():
    evidence = {
        "watcher_7_cycle": "PASS",
        "credential_revocation": "PASS",
        "offsite_restore": "PASS",
        "consumer_baseline": "PASS",
    }
    assert project_closeout_state(
        release_state="COMPLETE", evidence=evidence,
    ) == "CLOSED"
    evidence["credential_revocation"] = "PENDING"
    assert project_closeout_state(
        release_state="COMPLETE", evidence=evidence,
    ) == "EXIT_EVIDENCE_PENDING"
