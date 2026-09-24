from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from .models import AUTOMATION_VERSION, P2AError, canonical_json_bytes, utc_now


LEDGER_FIELDS = {
    "timestamp_utc", "req_id", "task_id", "canonical_generation", "event", "result",
    "automation_version", "policy_bundle_sha256", "input_binding_sha256", "output_binding_sha256",
}


class AppendOnlyLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        output: list[dict[str, Any]] = []
        for line_no, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise P2AError("LEDGER_INVALID", "Ledger contains invalid JSON", line=line_no) from exc
            if set(event) != LEDGER_FIELDS:
                raise P2AError("LEDGER_INVALID", "Ledger event fields changed", line=line_no)
            output.append(event)
        return output

    def append(
        self,
        *,
        req_id: str,
        task_id: str,
        canonical_generation: int,
        event: str,
        result: str,
        policy_bundle_sha256: str,
        input_binding_sha256: str,
        output_binding_sha256: str,
    ) -> dict[str, Any]:
        candidate = {
            "timestamp_utc": utc_now(),
            "req_id": req_id,
            "task_id": task_id,
            "canonical_generation": canonical_generation,
            "event": event,
            "result": result,
            "automation_version": AUTOMATION_VERSION,
            "policy_bundle_sha256": policy_bundle_sha256,
            "input_binding_sha256": input_binding_sha256,
            "output_binding_sha256": output_binding_sha256,
        }
        for existing in self.events():
            same_key = all(existing[name] == candidate[name] for name in ("req_id", "task_id", "canonical_generation", "event"))
            if not same_key:
                continue
            same_binding = all(existing[name] == candidate[name] for name in ("result", "policy_bundle_sha256", "input_binding_sha256", "output_binding_sha256"))
            if same_binding:
                return existing
            raise P2AError("LEDGER_CONFLICT", "Existing event has different bindings", event=event, task_id=task_id)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as handle:
            handle.write(canonical_json_bytes(candidate))
            handle.flush()
            os.fsync(handle.fileno())
        return candidate

    def contains(self, event: str, *, task_id: str, generation: int) -> bool:
        return any(item["event"] == event and item["task_id"] == task_id and item["canonical_generation"] == generation for item in self.events())
