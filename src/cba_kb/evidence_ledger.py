"""Append-only, content-addressed runtime evidence for a private instance."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from .common import digest, lock


SCHEMA_VERSION = 1
REQUIREMENT_ID = re.compile(r"[A-Z][A-Z0-9-]*\Z")
EVENT_TYPE = re.compile(r"[A-Z][A-Z0-9_]*\Z")


class EvidenceLedgerError(RuntimeError):
    pass


def canonical_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _clean(data, label):
    from .current_state import security
    if security().scan_bytes(data, label):
        raise EvidenceLedgerError("EVIDENCE_SECRET_FORBIDDEN")


class RuntimeEvidenceLedger:
    """A deterministic append-only ledger rooted in Private Instance."""

    def __init__(self, root, requirement_id):
        if not isinstance(requirement_id, str) or not REQUIREMENT_ID.fullmatch(
            requirement_id,
        ):
            raise EvidenceLedgerError("REQUIREMENT_ID_INVALID")
        self.requirement_id = requirement_id
        self.root = Path(root).resolve()

    @classmethod
    def for_instance(cls, instance, requirement_id):
        expected = (
            Path(instance.root).resolve()
            / "evidence"
            / requirement_id
            / "runtime-ledger"
        )
        return cls(expected, requirement_id)

    def _paths(self):
        if not self.root.exists():
            return []
        return sorted(
            path for path in self.root.iterdir()
            if path.is_file() and re.fullmatch(r"\d{8}-[0-9a-f]{64}\.json", path.name)
        )

    def entries(self):
        entries = []
        for index, path in enumerate(self._paths()):
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise EvidenceLedgerError("EVIDENCE_LEDGER_INVALID") from exc
            if not isinstance(entry, dict):
                raise EvidenceLedgerError("EVIDENCE_LEDGER_INVALID")
            expected_previous = entries[-1]["entry_sha256"] if entries else None
            if (
                entry.get("schema_version") != SCHEMA_VERSION
                or entry.get("requirement_id") != self.requirement_id
                or entry.get("sequence") != index
                or entry.get("previous_entry_sha256") != expected_previous
                or not re.fullmatch(r"[0-9a-f]{64}", entry.get("entry_sha256", ""))
            ):
                raise EvidenceLedgerError("EVIDENCE_LEDGER_CHAIN_MISMATCH")
            core = {
                key: entry[key]
                for key in (
                    "schema_version",
                    "requirement_id",
                    "event_type",
                    "recorded_at",
                    "payload",
                )
            }
            if digest(canonical_bytes(core)) != entry["entry_sha256"]:
                raise EvidenceLedgerError("EVIDENCE_LEDGER_HASH_MISMATCH")
            if path.stem.split("-", 1)[1] != entry["entry_sha256"]:
                raise EvidenceLedgerError("EVIDENCE_LEDGER_HASH_MISMATCH")
            _clean(path.read_bytes(), "runtime-ledger-entry.json")
            entries.append(entry)
        return entries

    def append(self, event_type, payload, *, recorded_at):
        if not isinstance(event_type, str) or not EVENT_TYPE.fullmatch(event_type):
            raise EvidenceLedgerError("EVIDENCE_EVENT_TYPE_INVALID")
        if not isinstance(recorded_at, str) or not recorded_at:
            raise EvidenceLedgerError("EVIDENCE_RECORDED_AT_REQUIRED")
        if not isinstance(payload, dict):
            raise EvidenceLedgerError("EVIDENCE_PAYLOAD_OBJECT_REQUIRED")
        core = {
            "schema_version": SCHEMA_VERSION,
            "requirement_id": self.requirement_id,
            "event_type": event_type,
            "recorded_at": recorded_at,
            "payload": payload,
        }
        encoded_core = canonical_bytes(core)
        _clean(encoded_core, "runtime-ledger-entry.json")
        entry_sha256 = digest(encoded_core)
        self.root.mkdir(parents=True, exist_ok=True)
        with lock(self.root / ".ledger.lock"):
            entries = self.entries()
            sequence = len(entries)
            entry = {
                **core,
                "sequence": sequence,
                "previous_entry_sha256": (
                    entries[-1]["entry_sha256"] if entries else None
                ),
                "entry_sha256": entry_sha256,
            }
            data = canonical_bytes(entry)
            path = self.root / f"{sequence:08d}-{entry_sha256}.json"
            if path.exists():
                if path.read_bytes() != data:
                    raise EvidenceLedgerError("EVIDENCE_LEDGER_CONFLICT")
                return entry
            try:
                descriptor = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                if path.read_bytes() != data:
                    raise EvidenceLedgerError(
                        "EVIDENCE_LEDGER_CONFLICT",
                    ) from None
            return entry

    def verify(self):
        entries = self.entries()
        return {
            "status": "PASS",
            "requirement_id": self.requirement_id,
            "entries": len(entries),
            "head_entry_sha256": (
                entries[-1]["entry_sha256"] if entries else None
            ),
        }


def append_runtime_evidence(
    root,
    requirement_id,
    event_type,
    payload,
    *,
    recorded_at,
):
    return RuntimeEvidenceLedger(root, requirement_id).append(
        event_type,
        payload,
        recorded_at=recorded_at,
    )


def verify_runtime_ledger(root, requirement_id):
    return RuntimeEvidenceLedger(root, requirement_id).verify()


ATTESTATION_SCHEMAS = frozenset({
    "credential_revocation",
    "offsite_backup_restore",
})


def _require_sha256(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise EvidenceLedgerError(f"{label}_SHA256_REQUIRED")
    return value


def validate_credential_revocation_attestation(value, *, recorded_at=None):
    if not isinstance(value, dict):
        raise EvidenceLedgerError("CREDENTIAL_ATTESTATION_OBJECT_REQUIRED")
    required = {
        "schema_version", "recorded_at", "credentials",
        "secret_material_included", "verification_result",
    }
    if not required <= set(value):
        raise EvidenceLedgerError("CREDENTIAL_ATTESTATION_INCOMPLETE")
    if value["schema_version"] != SCHEMA_VERSION:
        raise EvidenceLedgerError("CREDENTIAL_ATTESTATION_VERSION_INVALID")
    if recorded_at is not None and value["recorded_at"] != recorded_at:
        raise EvidenceLedgerError("CREDENTIAL_ATTESTATION_TIME_MISMATCH")
    if not isinstance(value["recorded_at"], str) or not value["recorded_at"]:
        raise EvidenceLedgerError("CREDENTIAL_ATTESTATION_TIME_REQUIRED")
    if value["secret_material_included"] is not False:
        raise EvidenceLedgerError("CREDENTIAL_SECRET_MATERIAL_FORBIDDEN")
    if value["verification_result"] != "REJECTED_OR_NOT_FOUND":
        raise EvidenceLedgerError("CREDENTIAL_OLD_USE_NOT_VERIFIED")
    credentials = value["credentials"]
    if not isinstance(credentials, list) or not credentials:
        raise EvidenceLedgerError("CREDENTIAL_ATTESTATION_EMPTY")
    for item in credentials:
        if not isinstance(item, dict) or set(item) != {
            "credential_id", "status", "verification_attempted",
            "verification_result",
        }:
            raise EvidenceLedgerError("CREDENTIAL_ATTESTATION_ENTRY_INVALID")
        if item["status"] not in {"REVOKED", "NOT_FOUND"}:
            raise EvidenceLedgerError("CREDENTIAL_STATUS_INVALID")
        if item["verification_attempted"] is not True:
            raise EvidenceLedgerError("CREDENTIAL_VERIFICATION_REQUIRED")
        if item["verification_result"] not in {"REJECTED", "NOT_FOUND"}:
            raise EvidenceLedgerError("CREDENTIAL_VERIFICATION_FAILED")
    _clean(canonical_bytes(value), "credential-revocation-attestation.json")
    return {"status": "PASS", "credentials": len(credentials)}


def validate_offsite_restore_attestation(value, *, recorded_at=None):
    if not isinstance(value, dict):
        raise EvidenceLedgerError("OFFSITE_RESTORE_OBJECT_REQUIRED")
    required = {
        "schema_version", "recorded_at", "backup_id",
        "backup_failure_domain", "production_failure_domain",
        "restore_target_empty", "core_bundle_hashes", "readback",
        "secret_material_included",
    }
    if not required <= set(value):
        raise EvidenceLedgerError("OFFSITE_RESTORE_INCOMPLETE")
    if value["schema_version"] != SCHEMA_VERSION:
        raise EvidenceLedgerError("OFFSITE_RESTORE_VERSION_INVALID")
    if recorded_at is not None and value["recorded_at"] != recorded_at:
        raise EvidenceLedgerError("OFFSITE_RESTORE_TIME_MISMATCH")
    if not value.get("backup_id") or not value.get("recorded_at"):
        raise EvidenceLedgerError("OFFSITE_RESTORE_IDENTITY_REQUIRED")
    if (
        not value.get("backup_failure_domain")
        or not value.get("production_failure_domain")
        or value["backup_failure_domain"] == value["production_failure_domain"]
    ):
        raise EvidenceLedgerError("OFFSITE_FAILURE_DOMAIN_INVALID")
    if value.get("restore_target_empty") is not True:
        raise EvidenceLedgerError("OFFSITE_RESTORE_TARGET_NOT_EMPTY")
    hashes = value.get("core_bundle_hashes")
    if not isinstance(hashes, dict) or not hashes:
        raise EvidenceLedgerError("OFFSITE_CORE_BUNDLE_REQUIRED")
    for name, digest_value in hashes.items():
        if not isinstance(name, str) or not name:
            raise EvidenceLedgerError("OFFSITE_BUNDLE_NAME_INVALID")
        _require_sha256(digest_value, "OFFSITE_BUNDLE")
    if value.get("readback") != "PASS":
        raise EvidenceLedgerError("OFFSITE_READBACK_REQUIRED")
    if value.get("secret_material_included") is not False:
        raise EvidenceLedgerError("OFFSITE_SECRET_MATERIAL_FORBIDDEN")
    _clean(canonical_bytes(value), "offsite-restore-attestation.json")
    return {"status": "PASS", "bundles": len(hashes)}


def validate_attestation(kind, value, *, recorded_at=None):
    validators = {
        "credential_revocation": validate_credential_revocation_attestation,
        "offsite_backup_restore": validate_offsite_restore_attestation,
    }
    try:
        validator = validators[kind]
    except KeyError:
        raise EvidenceLedgerError("ATTESTATION_KIND_INVALID") from None
    return validator(value, recorded_at=recorded_at)
