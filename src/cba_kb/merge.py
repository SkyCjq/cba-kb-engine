"""Merge new domestic relations onto a frozen baseline without rewriting it."""
from .facts import record_key
from .master import HEADERS

COMPARED = ('player','registration_stage','registration_method','contract_category',
            'contract_term_official','former_club','registration_status','source_url',
            'verification_level')


def _sequence(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def merge_domestic(baseline, additions):
    """Append only relations absent from the baseline; renumber per season and club."""
    existing = {row['record_key']: row for row in baseline}
    counters = {}
    for row in baseline:
        if not row.get('record_key'):
            raise ValueError('Baseline row without record_key')
        key = (row['season'], row['club_id'])
        counters[key] = max(counters.get(key, 0), _sequence(row.get('sequence')))
    merged = [dict(row) for row in baseline]
    added, collisions = [], []
    for row in additions:
        key = record_key(row)
        if key in existing:
            current = existing[key]
            collisions.append({'record_key': key, 'existing': {k: current.get(k) for k in COMPARED},
                               'incoming': {k: row.get(k) for k in COMPARED}})
            continue
        counter = (row['season'], row['club_id'])
        counters[counter] = counters.get(counter, 0) + 1
        row = dict(row)
        row['sequence'] = str(counters[counter])
        merged.append(row)
        added.append(row['record_key'])
    if any(sorted(row) != sorted(HEADERS) for row in merged):
        raise ValueError('Merged rows no longer match the MASTER schema')
    report = {'baseline_rows': len(baseline), 'candidate_rows': len(merged),
              'added': added, 'collisions': collisions,
              'seasons': len({row['season'] for row in merged})}
    return merged, report
