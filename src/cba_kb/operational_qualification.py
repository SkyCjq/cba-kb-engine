"""Watcher operational-qualification ledger without manufacturing cycles."""
from __future__ import annotations

from .evidence_ledger import canonical_bytes


SCHEMA_VERSION = 1
REQUIRED_CYCLES = 7
SOURCE_CONTRACT_REVISION = "r5-20260912-detail-discovery"


class OperationalQualificationError(RuntimeError):
    pass


def new_ledger(requirement_id):
    return {
        "schema_version": SCHEMA_VERSION,
        "requirement_id": requirement_id,
        "source_contract_revision": SOURCE_CONTRACT_REVISION,
        "required_cycles": REQUIRED_CYCLES,
        "cycles": [],
        "project_closeout_state": "EXIT_EVIDENCE_PENDING",
    }


def append_cycle(
    ledger,
    *,
    cycle,
    planned=True,
    manual_repair_required=False,
    canonical_publish_allowed=False,
    observed_change=None,
    evidence=None,
    recorded_at,
):
    if (
        not isinstance(ledger, dict)
        or ledger.get("schema_version") != SCHEMA_VERSION
        or ledger.get("source_contract_revision") != SOURCE_CONTRACT_REVISION
        or not isinstance(ledger.get("cycles"), list)
    ):
        raise OperationalQualificationError("WATCHER_OQ_LEDGER_INVALID")
    if not isinstance(cycle, int) or isinstance(cycle, bool) or cycle != len(
        ledger["cycles"],
    ):
        raise OperationalQualificationError("WATCHER_OQ_CYCLE_ORDER_INVALID")
    if cycle >= REQUIRED_CYCLES:
        raise OperationalQualificationError("WATCHER_OQ_CYCLE_LIMIT")
    if not isinstance(recorded_at, str) or not recorded_at:
        raise OperationalQualificationError("WATCHER_OQ_RECORDED_AT_REQUIRED")
    if not planned:
        raise OperationalQualificationError("WATCHER_OQ_PLANNED_CYCLE_REQUIRED")
    if manual_repair_required:
        raise OperationalQualificationError("WATCHER_OQ_MANUAL_REPAIR")
    if canonical_publish_allowed:
        raise OperationalQualificationError("WATCHER_OQ_PUBLISH_FORBIDDEN")
    if observed_change not in (None, "NONE", "CHANGE_OBSERVED"):
        raise OperationalQualificationError("WATCHER_OQ_CHANGE_INVALID")
    if isinstance(evidence, dict) and evidence.get("business_event") is True:
        raise OperationalQualificationError("WATCHER_OQ_BUSINESS_EVENT_FORBIDDEN")
    item = {
        "cycle": cycle,
        "planned": True,
        "manual_repair_required": False,
        "canonical_publish_allowed": False,
        "observed_change": observed_change or "NONE",
        "business_event": False,
        "recorded_at": recorded_at,
        "evidence": evidence,
    }
    ledger["cycles"].append(item)
    return item


def validate_ledger(ledger, *, require_complete=False):
    if (
        not isinstance(ledger, dict)
        or ledger.get("schema_version") != SCHEMA_VERSION
        or ledger.get("source_contract_revision") != SOURCE_CONTRACT_REVISION
        or ledger.get("required_cycles") != REQUIRED_CYCLES
        or not isinstance(ledger.get("cycles"), list)
    ):
        raise OperationalQualificationError("WATCHER_OQ_LEDGER_INVALID")
    cycles = ledger["cycles"]
    if len(cycles) > REQUIRED_CYCLES:
        raise OperationalQualificationError("WATCHER_OQ_CYCLE_LIMIT")
    for index, item in enumerate(cycles):
        if (
            not isinstance(item, dict)
            or item.get("cycle") != index
            or item.get("planned") is not True
            or item.get("manual_repair_required") is not False
            or item.get("canonical_publish_allowed") is not False
            or item.get("business_event") is not False
            or not item.get("recorded_at")
        ):
            raise OperationalQualificationError("WATCHER_OQ_CYCLE_INVALID")
    complete = len(cycles) == REQUIRED_CYCLES
    if require_complete and not complete:
        raise OperationalQualificationError("WATCHER_OQ_INCOMPLETE")
    return {
        "status": "PASS" if complete else "PENDING",
        "qualified_cycles": len(cycles),
        "required_cycles": REQUIRED_CYCLES,
        "manual_repair_required": False,
        "canonical_publish_allowed": False,
        "project_closeout_state": (
            "CLOSED" if complete else "EXIT_EVIDENCE_PENDING"
        ),
    }


def ledger_bytes(ledger):
    validate_ledger(ledger)
    return canonical_bytes(ledger)


def project_closeout_state(*, release_state, evidence):
    """Long-horizon evidence is separate from production release completion."""
    if release_state != "COMPLETE":
        return "EXIT_EVIDENCE_PENDING"
    required = {
        "watcher_7_cycle": "PASS",
        "credential_revocation": "PASS",
        "offsite_restore": "PASS",
        "consumer_baseline": "PASS",
    }
    if not isinstance(evidence, dict):
        return "EXIT_EVIDENCE_PENDING"
    for name, expected in required.items():
        if evidence.get(name) != expected:
            return "EXIT_EVIDENCE_PENDING"
    return "CLOSED"
