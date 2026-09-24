from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from automation.models import sha256_bytes
from cba_kb.drive import Drive
from cba_kb.instance import load_instance


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-root", required=True)
    parser.add_argument("--instance-root", required=True)
    parser.add_argument("--pair", action="append", required=True, help="FILE_ID=LOCAL_PATH")
    args = parser.parse_args()
    instance = load_instance(args.engine_root, args.instance_root)
    drive = Drive(args.engine_root, instance=instance)
    results = []
    for pair in args.pair:
        file_id, local_path = pair.split("=", 1)
        remote = drive.get(file_id)
        local = Path(local_path).read_bytes()
        results.append({
            "file_id": file_id, "local_path": local_path, "bytes": len(remote),
            "sha256": sha256_bytes(remote), "exact_byte_match": remote == local,
        })
    status = "PASS" if all(item["exact_byte_match"] for item in results) else "FAIL"
    print(json.dumps({"status": status, "files": results}, sort_keys=True))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
