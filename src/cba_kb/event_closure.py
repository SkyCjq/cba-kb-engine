"""Registration-event closure rules for canonical EVENTS and cross-table QA.

This module keeps the canonical event product authoritative while preserving
legacy rows as auditable projections.  It never creates snapshot facts from
events and never fills an unknown date from a neighbouring field or row.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
import json
from pathlib import Path

from .facts import name_key
from .registration_domain import (
    DATE_PRECISIONS,
    DATE_STATUSES,
    EVENT_DOMAINS,
    EVENT_STATUSES,
    EVENT_TYPES,
    RESEARCH_MOVEMENT_LABELS,
    SOURCE_AUTHORITIES,
    STATUS_EVENTS,
)


LEGACY_TARGETS = (
    'domestic_registrations',
    'registration_status_events',
    'foreign_registration_snapshots',
    'foreign_priority_right_snapshots',
    'foreign_priority_right_transactions',
    'domestic_transaction_windows',
    'none',
)
MAPPING_ROLES = (
    'relationship_projection',
    'event_projection',
    'snapshot_projection',
    'compatibility_only',
    'deprecated',
)
MATCH_STATUSES = (
    'exact_match',
    'semantic_match',
    'canonical_split',
    'legacy_only',
    'conflict',
    'deprecated',
)
EDGE_FIELDS = (
    'legacy_source_id',
    'legacy_record_key',
    'target_entity',
    'canonical_key',
    'mapping_role',
    'match_status',
    'field_diff_summary',
    'review_status',
)


def _text(value):
    return '' if value is None else str(value).strip()


def _event_token(row):
    return (name_key(row.get('player_name_en_raw'))
            or name_key(row.get('player_name_en_normalized'))
            or _text(row.get('player_name_zh')))


def upgrade_event_row(row):
    """Map v1.5.2 event fields into the v1.5.3 canonical event schema."""
    upgraded = dict.fromkeys(STATUS_EVENTS)
    for field in STATUS_EVENTS:
        if field in row and field not in {
            'registration_method_official',
            'research_movement_label',
            'event_date_precision',
            'club_announcement_date_status',
            'registration_window_deadline_status',
        }:
            upgraded[field] = row.get(field)

    legacy_method = _text(row.get('registration_method'))
    official = _text(row.get('registration_method_official'))
    research = _text(row.get('research_movement_label'))
    if legacy_method:
        if legacy_method in RESEARCH_MOVEMENT_LABELS:
            research = research or legacy_method
        else:
            official = official or legacy_method
    upgraded['registration_method_official'] = official or None
    upgraded['research_movement_label'] = research or None

    status = upgraded.get('event_status')
    upgraded['event_status'] = {'not_completed': 'uncompleted',
                                'retracted': 'withdrawn'}.get(status, status)
    if not upgraded.get('event_date_precision'):
        upgraded['event_date_precision'] = 'day' if upgraded.get('event_date') else 'unknown'
    for date_field, status_field in (
        ('club_announcement_date', 'club_announcement_date_status'),
        ('registration_window_deadline', 'registration_window_deadline_status'),
    ):
        if not upgraded.get(status_field):
            upgraded[status_field] = 'known' if upgraded.get(date_field) else 'unknown'
    return upgraded


def upgrade_records(records):
    """Return a copy with canonical event fields upgraded to v1.5.3 semantics."""
    result = {
        name: [dict(row) for row in rows]
        for name, rows in records.items()
        if isinstance(rows, list)
    }
    result['registration_status_events'] = [
        upgrade_event_row(row) for row in (records.get('registration_status_events') or [])
    ]
    return result


def compose_candidate_records(live_records, domestic_records):
    """Replace domestic grains from the frozen source while preserving foreign facts."""
    live = upgrade_records(live_records)
    domestic = upgrade_records(domestic_records)
    result = {name: [dict(row) for row in (live.get(name) or [])] for name in live}
    for table in ('domestic_registrations', 'registration_status_events'):
        result[table] = [dict(row) for row in (domestic.get(table) or [])]
    domestic_windows = [dict(row) for row in (domestic.get('domestic_transaction_windows') or [])]
    domestic_window_keys = {row.get('window_key') for row in domestic_windows}
    result['domestic_transaction_windows'] = [
        dict(row) for row in (live.get('domestic_transaction_windows') or [])
        if row.get('window_key') not in domestic_window_keys
    ] + domestic_windows
    result['registration_status_events'] += [
        dict(row) for row in (live.get('registration_status_events') or [])
        if row.get('event_domain') != 'domestic_movement'
    ]
    return result


def _expected_date_status(field, row):
    status_field = field + '_status'
    return row.get(status_field)


def validate_event_closure(records):
    """Validate lifecycle, date, provenance and supersession invariants."""
    events = records.get('registration_status_events') or []
    by_key = {}
    for index, row in enumerate(events, 1):
        key = row.get('event_key')
        if not key or key in by_key:
            raise ValueError(f'Duplicate or missing event_key at row {index}')
        by_key[key] = row

    for index, row in enumerate(events, 1):
        if row.get('event_domain') not in EVENT_DOMAINS:
            raise ValueError(f'Uncontrolled event_domain at row {index}')
        if row.get('event_type') not in EVENT_TYPES:
            raise ValueError(f'Uncontrolled event_type at row {index}')
        if row.get('event_status') not in EVENT_STATUSES:
            raise ValueError(f'Uncontrolled event_status at row {index}')
        if row.get('event_date_precision') not in DATE_PRECISIONS:
            raise ValueError(f'Uncontrolled event_date_precision at row {index}')
        if row.get('source_authority') not in SOURCE_AUTHORITIES:
            raise ValueError(f'Uncontrolled source_authority at row {index}')
        if row.get('research_movement_label') not in (None, *RESEARCH_MOVEMENT_LABELS):
            raise ValueError(f'Uncontrolled research_movement_label at row {index}')
        if row.get('research_movement_label') and (
                row.get('research_movement_label') == row.get('registration_method_official')):
            raise ValueError(f'Official and research labels must remain separate at row {index}')

        for field in (
            'event_date',
            'club_announcement_date',
            'registration_submission_date',
            'registration_completed_date',
            'registration_window_deadline',
        ):
            status = _expected_date_status(field, row)
            if status not in DATE_STATUSES:
                raise ValueError(f'Uncontrolled {field}_status at row {index}')
            if (status == 'known') != bool(row.get(field)):
                raise ValueError(f'Inconsistent {field} status/value at row {index}')

        if not row.get('event_date'):
            if row.get('event_date_precision') != 'unknown':
                raise ValueError(f'Unavailable event_date must remain unknown at row {index}')
        elif row.get('event_date_precision') == 'unknown':
            raise ValueError(f'Known event_date requires precision at row {index}')

        if row.get('registration_completed_date') and not row.get('event_date'):
            # A completed date is not an event date.  Both fields may exist,
            # but neither field is inferred from the other.
            pass
        if row.get('registration_window_deadline') == row.get('event_date') and row.get('event_date'):
            # Equal literals are allowed only when both date fields are
            # independently present in the source row.
            if row.get('event_date_status') != 'known' or row.get('registration_window_deadline_status') != 'known':
                raise ValueError(f'Deadline cannot substitute for event_date at row {index}')

        if not row.get('source_file_id') or not (
                row.get('source_url_primary') or row.get('source_url_secondary')):
            raise ValueError(f'Event provenance requires source_file_id and source URL at row {index}')
        if not row.get('source_page_or_row'):
            raise ValueError(f'Event provenance requires source_page_or_row at row {index}')

        supersedes = row.get('supersedes_event_key')
        if supersedes:
            if supersedes == key:
                raise ValueError(f'Event cannot supersede itself at row {index}')
            if supersedes not in by_key:
                raise ValueError(f'Unknown supersedes_event_key at row {index}')

    for key, row in by_key.items():
        supersedes = row.get('supersedes_event_key')
        if supersedes and by_key[supersedes].get('event_status') != 'corrected':
            raise ValueError(f'Superseded event must remain corrected: {supersedes}')

    # Detect cycles without mutating the historical rows.
    for start in by_key:
        seen = set()
        current = start
        while current:
            if current in seen:
                raise ValueError(f'Supersession cycle at {start}')
            seen.add(current)
            current = by_key[current].get('supersedes_event_key')

    return {
        'rows': len(events),
        'effective_rows': sum(not row.get('supersedes_event_key') for row in events),
        'corrected_rows': sum(row.get('event_status') == 'corrected' for row in events),
        'domains': dict(sorted(Counter(row.get('event_domain') for row in events).items())),
    }


def _edge(legacy_source_id, legacy_key, target, canonical_key, role, status,
          diff='', review='accepted'):
    if target not in LEGACY_TARGETS or role not in MAPPING_ROLES or status not in MATCH_STATUSES:
        raise ValueError('Invalid reconciliation vocabulary')
    return {
        'legacy_source_id': legacy_source_id,
        'legacy_record_key': legacy_key,
        'target_entity': target,
        'canonical_key': canonical_key,
        'mapping_role': role,
        'match_status': status,
        'field_diff_summary': diff,
        'review_status': review,
    }


def _find_unique(rows, predicate, label):
    matches = [row for row in rows if predicate(row)]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, f'missing canonical {label}'
    return None, f'ambiguous canonical {label}: {len(matches)} matches'


def reconcile_legacy_events(legacy_rows, canonical_records, legacy_source_id='legacy-events'):
    """Reconcile legacy rows to 0/1/N canonical representations."""
    records = upgrade_records(canonical_records)
    events = records.get('registration_status_events') or []
    relations = records.get('domestic_registrations') or []
    edges = []
    classifications = Counter()
    unresolved = []
    legacy_keys = [_text(row.get('event_key')) for row in legacy_rows]
    if any(not key for key in legacy_keys) or len(legacy_keys) != len(set(legacy_keys)):
        raise ValueError('Legacy keys must be present and unique')

    for legacy in legacy_rows:
        key = _text(legacy.get('event_key'))
        token = _event_token(legacy)
        season = legacy.get('season')
        club_id = legacy.get('club_id')
        player_type = legacy.get('player_type')
        event_type = legacy.get('event_type')
        from_club = legacy.get('from_club_id')

        if player_type == 'foreign' and event_type == 'registration_cancelled':
            event, reason = _find_unique(
                events,
                lambda row: (
                    row.get('player_type') == 'foreign'
                    and row.get('event_type') == 'registration_cancelled'
                    and row.get('season') == season
                    and row.get('club_id') == club_id
                    and _event_token(row) == token
                ),
                'foreign cancellation event',
            )
            if event is None:
                edges.append(_edge(legacy_source_id, key, 'none', '', 'deprecated',
                                   'legacy_only', reason, 'needs_review'))
                unresolved.append({'legacy_record_key': key, 'reason': reason})
                classifications['legacy_only'] += 1
                continue
            edges.append(_edge(legacy_source_id, key, 'registration_status_events',
                               event['event_key'], 'event_projection', 'exact_match',
                               'event_type, season, club and player agree'))
            classifications['exact_match'] += 1
            continue

        if player_type == 'domestic' and from_club == 'bayi':
            event, reason = _find_unique(
                events,
                lambda row: (
                    row.get('player_type') == 'domestic'
                    and row.get('season') == season
                    and row.get('club_id') == club_id
                    and _event_token(row) == token
                    and row.get('event_domain') == 'domestic_movement'
                ),
                'domestic movement event',
            )
            relation, relation_reason = _find_unique(
                relations,
                lambda row: (
                    row.get('season') == season
                    and row.get('club_id') == club_id
                    and _text(row.get('player')) == _text(legacy.get('player_name_zh'))
                ),
                'domestic relationship',
            )
            if event is None or relation is None:
                reason = '; '.join(item for item in (reason, relation_reason) if item)
                edges.append(_edge(legacy_source_id, key, 'none', '', 'deprecated',
                                   'legacy_only', reason, 'needs_review'))
                unresolved.append({'legacy_record_key': key, 'reason': reason})
                classifications['legacy_only'] += 1
                continue
            edges.append(_edge(legacy_source_id, key, 'domestic_registrations',
                               relation['record_key'], 'relationship_projection',
                               'semantic_match',
                               'legacy registration_change projected as relationship'))
            edges.append(_edge(legacy_source_id, key, 'registration_status_events',
                               event['event_key'], 'event_projection',
                               'semantic_match',
                               'legacy registration_change projected as movement event'))
            classifications['canonical_split'] += 1
            continue

        event, reason = _find_unique(
            events,
            lambda row: (
                row.get('season') == season
                and row.get('club_id') == club_id
                and _event_token(row) == token
                and row.get('event_type') == event_type
            ),
            'event',
        )
        if event is None:
            edges.append(_edge(legacy_source_id, key, 'none', '', 'deprecated',
                               'legacy_only', reason, 'needs_review'))
            unresolved.append({'legacy_record_key': key, 'reason': reason})
            classifications['legacy_only'] += 1
        else:
            edges.append(_edge(legacy_source_id, key, 'registration_status_events',
                               event['event_key'], 'event_projection', 'exact_match',
                               'matching canonical event'))
            classifications['exact_match'] += 1

    edges.sort(key=lambda row: (row['legacy_record_key'], row['target_entity'],
                                row['canonical_key'], row['mapping_role']))
    edge_keys = [
        (row['legacy_source_id'], row['legacy_record_key'], row['target_entity'],
         row['canonical_key'], row['mapping_role'])
        for row in edges
    ]
    if len(edge_keys) != len(set(edge_keys)):
        raise ValueError('Reconciliation mapping edges must be unique')
    mapped = len({row['legacy_record_key'] for row in edges
                  if row['target_entity'] != 'none'})
    conflict_count = sum(row['match_status'] == 'conflict' for row in edges)
    summary = {
        'legacy_row_count': len(legacy_rows),
        'mapping_edge_count': len(edges),
        'mapped_legacy_count': mapped,
        'unmapped_legacy_count': len(legacy_rows) - mapped,
        'exact_match_count': classifications['exact_match'],
        'semantic_match_count': classifications['semantic_match'],
        'canonical_split_count': classifications['canonical_split'],
        'conflict_count': conflict_count,
        'unexplained_conflict_count': conflict_count,
        'deprecated_count': classifications['deprecated'],
        'legacy_only_count': classifications['legacy_only'],
        'counts_by_target_entity': dict(sorted(Counter(
            row['target_entity'] for row in edges).items())),
        'counts_by_mapping_role': dict(sorted(Counter(
            row['mapping_role'] for row in edges).items())),
        'unresolved': unresolved,
    }
    return edges, summary


def write_reconciliation_reports(edges, summary, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / 'v1.5.3-event-reconciliation.csv'
    json_path = output_dir / 'v1.5.3-event-reconciliation-summary.json'
    with csv_path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=EDGE_FIELDS, lineterminator='\n')
        writer.writeheader()
        writer.writerows(edges)
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    return csv_path, json_path


def _master_player(row):
    return _text(row.get('player') or row.get('player_name_zh'))


def _master_club(row):
    return row.get('club_id') or row.get('club')


def _event_matches_change(events, player, season, to_club):
    return any(
        _text(row.get('player_name_zh')) == player
        and row.get('season') == season
        and row.get('to_club_id') == to_club
        and row.get('event_domain') == 'domestic_movement'
        for row in events
    )


def cross_table_qa(records, master_rows=None, club_resolver=None):
    """Return domain-scoped QA signals without creating or changing facts."""
    rows = upgrade_records(records)
    events = rows.get('registration_status_events') or []
    master_rows = [dict(row) for row in (master_rows or [])]
    signals = []
    warnings = []

    by_player = defaultdict(list)
    for row in master_rows:
        player = _master_player(row)
        if player:
            by_player[player].append(row)

    for player, snapshots in sorted(by_player.items()):
        snapshots.sort(key=lambda row: (_text(row.get('season')), _text(_master_club(row))))
        previous = None
        for current in snapshots:
            current_club = _master_club(current)
            if previous is not None:
                previous_club = _master_club(previous)
                if previous_club != current_club:
                    season = current.get('season')
                    if not _event_matches_change(events, player, season, current_club):
                        warnings.append({
                            'signal_type': 'event_gap_candidate',
                            'severity': 'warning',
                            'event_domain': 'domestic_movement',
                            'player': player,
                            'season': season,
                            'from_club_id': previous_club,
                            'to_club_id': current_club,
                            'evidence_hint': 'adjacent snapshots changed club without matching event',
                        })
                    former_raw = _text(current.get('former_club'))
                    former_id = current.get('former_club_id')
                    if not former_id and former_raw and club_resolver is not None:
                        try:
                            former_id = club_resolver.resolve(former_raw, season, role='event')
                        except Exception:
                            former_id = None
                    if former_id and former_id not in {previous_club, current_club}:
                        signals.append({
                            'signal_type': 'relationship_gap_signal',
                            'severity': 'research',
                            'event_domain': 'domestic_movement',
                            'player': player,
                            'season': season,
                            'from_snapshot_season': previous.get('season'),
                            'to_snapshot_season': season,
                            'from_club_id': previous_club,
                            'to_club_id': current_club,
                            'evidence_hint': f'former_cba_club = {former_id}',
                            'inferred_event_date': None,
                            'creates_snapshot': False,
                        })
            previous = current

    for row in events:
        domain = row.get('event_domain')
        if domain == 'foreign_registration':
            if row.get('event_type') == 'registration_cancelled':
                if not row.get('club_id') or not _event_token(row):
                    warnings.append({
                        'signal_type': 'event_provenance_warning',
                        'severity': 'warning',
                        'event_domain': domain,
                        'event_key': row.get('event_key'),
                        'evidence_hint': 'cancellation requires player and club provenance',
                    })
                # Cancellation has no destination requirement and must not be
                # evaluated as a domestic club transfer.
                if row.get('to_club_id'):
                    pass
            if not row.get('source_file_id') or not (
                    row.get('source_url_primary') or row.get('source_url_secondary')):
                warnings.append({
                    'signal_type': 'event_provenance_warning',
                    'severity': 'warning',
                    'event_domain': domain,
                    'event_key': row.get('event_key'),
                    'evidence_hint': 'missing source file or URL',
                })
        elif domain == 'foreign_usage':
            if not row.get('club_id') or not _event_token(row) or not row.get('event_status'):
                warnings.append({
                    'signal_type': 'event_domain_warning',
                    'severity': 'warning',
                    'event_domain': domain,
                    'event_key': row.get('event_key'),
                    'evidence_hint': 'usage event requires player, club, status and source',
                })
        elif domain == 'domestic_movement':
            # Domestic event dates are validated independently by the
            # canonical event validator.  QA only reports gaps.
            pass

    return {
        'signals': signals,
        'warnings': warnings,
        'counts_by_domain': dict(sorted(Counter(
            row.get('event_domain') for row in events).items())),
        'signal_count': len(signals),
        'warning_count': len(warnings),
    }


def semantic_fingerprint(records):
    rows = upgrade_records(records)
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    import hashlib
    return hashlib.sha256(payload).hexdigest()
