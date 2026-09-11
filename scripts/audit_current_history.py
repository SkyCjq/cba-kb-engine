"""Audit an explicit, frozen current/evidence inventory; never traverse Drive."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from cba_kb.canonical_registry import load_registry
from cba_kb.current_state import audit_current_history, clean


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('inventory', 'zones', 'manifest', 'registry'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(argv)
    values = {}
    for name in ('inventory', 'zones', 'manifest', 'registry'):
        path = getattr(args, name)
        values[name] = path.read_bytes()
        clean(values[name], path.name)
    report = audit_current_history(
        json.loads(values['inventory']), json.loads(values['zones']),
        values['manifest'], load_registry(values['registry']),
    )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError):
        raise SystemExit('CURRENT_HISTORY_AUDIT_FAILED') from None
