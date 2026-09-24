from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from automation.models import canonical_json_bytes, sha256_bytes
from cba_kb.drive import Drive
from cba_kb.instance import load_instance


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-root", required=True)
    parser.add_argument("--instance-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    instance = load_instance(args.engine_root, args.instance_root)
    production = instance.read_json("production.json")
    status_id = production["status_id"]
    drive = Drive(args.engine_root, instance=instance)
    meta = drive.meta(status_id)
    raw = drive.get(status_id)
    status = json.loads(raw)
    artifact_ids = sorted(item["id"] for item in status.get("artifacts", []))
    snapshot = {
        "release_status_file_id": status_id,
        "release_status_version": int(meta["version"]),
        "release_status_modified_time": meta["modifiedTime"],
        "release_status_bytes": len(raw),
        "release_status_sha256": sha256_bytes(raw),
        "state": status["state"],
        "current_release_id": status["current_release_id"],
        "pending_release_id": status.get("pending_release_id"),
        "artifact_count": len(artifact_ids),
        "artifact_ids_sha256": sha256_bytes(("\n".join(artifact_ids) + "\n").encode()),
    }
    Path(args.output).write_bytes(canonical_json_bytes(snapshot))
    print(json.dumps(snapshot, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
