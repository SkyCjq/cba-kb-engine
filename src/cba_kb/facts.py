"""Grain definitions for the three published fact products.

One grain per product: domestic roster relation, foreign registration snapshot,
registration event. Provisional keys are matching aids, never identity.
"""
import hashlib
import json
import unicodedata
from .master import HEADERS as DOMESTIC

SNAPSHOT = ['snapshot_key','season','snapshot_as_of','club_id','club_source_name',
            'name_en_raw','name_en_normalized','name_zh_raw','nationality','position',
            'jersey_number','status_at_snapshot','source_file_id','source_url',
            'source_page_or_row','source_type','extraction_method','verification_level',
            'raw_row_text']

EVENT = ['event_key','provisional_player_key','sequence','season','player_type','club_id','club_source_name',
         'player_name_zh','player_name_en_raw','player_name_en_normalized','nationality',
         'event_type','event_date','date_year_inferred','from_club_id','from_club_source_name',
         'registration_method','contract_category','contract_term_official',
         'club_announcement_date','registration_window_deadline','registration_status',
         'notes','source_file_id','source_url_primary','source_url_secondary',
         'source_page_or_row','source_type','extraction_method','verification_level',
         'raw_event_text']

EVENT_TYPES = ('registration_change','registration_cancelled')
VERIFICATION_LEVELS = ('human_verified','official_api','auto_validated','needs_review')


def name_key(text):
    """Normalized matching aid for foreign names; the raw value is always kept too."""
    if text is None:
        return None
    value = unicodedata.normalize('NFKC', str(text)).replace('\u00a0', ' ')
    return ' '.join(value.split()).upper() or None


def text_number(value):
    """Jersey numbers and similar codes stay textual; '01' must not become 1."""
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ValueError('Boolean is not a text code')
    if isinstance(value, (int, float)):
        if float(value) != int(value):
            raise ValueError('Fractional code cannot be preserved as text')
        return str(int(value))
    return str(value).strip() or None


def record_key(row):
    return '|'.join(str(row[k]) for k in ('season','club_id','player'))


def snapshot_key(row):
    return '|'.join(str(row[k]) for k in ('season','snapshot_as_of','club_id','name_en_normalized','nationality'))


def event_key(row):
    return '|'.join(str(row[k]) for k in ('season','player_type','club_id','provisional_player_key',
                                          'event_type','event_date','sequence'))


def provisional_player_key(row):
    if row['player_type'] == 'foreign':
        return ' / '.join(x for x in (row.get('player_name_en_normalized'), row.get('nationality')) if x)
    return row.get('player_name_zh')


def validate(rows, headers, key, required, message):
    seen = set()
    for index, row in enumerate(rows, 1):
        if sorted(row) != sorted(headers):
            raise ValueError(f'Unexpected field set at row {index}')
        for name in required:
            if row.get(name) in (None, ''):
                raise ValueError(f'Missing {name} at row {index}')
        value = key(row)
        if value in seen:
            raise ValueError(f'{message}: {value}')
        seen.add(value)
    return {'rows': len(rows), 'unique': len(seen)}


def validate_snapshots(rows):
    for index, row in enumerate(rows, 1):
        if row.get('jersey_number') is not None and not isinstance(row['jersey_number'], str):
            raise ValueError(f'Jersey number lost text type at row {index}')
    return validate(rows, SNAPSHOT, snapshot_key,
                    ('season','snapshot_as_of','club_id','name_en_raw','source_type','verification_level'),
                    'Duplicate snapshot_key')


def validate_events(rows):
    for index, row in enumerate(rows, 1):
        if row.get('event_type') not in EVENT_TYPES:
            raise ValueError(f'Uncontrolled event_type at row {index}')
        if row.get('verification_level') not in VERIFICATION_LEVELS:
            raise ValueError(f'Uncontrolled verification_level at row {index}')
        if row['event_type'] == 'registration_cancelled' and not row.get('event_date'):
            raise ValueError(f'Cancellation requires event_date at row {index}')
        if row.get('date_year_inferred') is True and not row.get('event_date'):
            raise ValueError(f'Inferred year requires event_date at row {index}')
    return validate(rows, EVENT, event_key,
                    ('season','player_type','club_id','event_type','event_date','source_type'),
                    'Duplicate event_key')


def validate_domestic(rows):
    for index, row in enumerate(rows, 1):
        if row.get('record_key') != record_key(row):
            raise ValueError(f'Record key mismatch at row {index}')
    return validate(rows, DOMESTIC, record_key,
                    ('record_key','season','club_id','player','source_url','verification_level'),
                    'Duplicate record_key')


def fingerprint(rows):
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(',',':')).encode()
    return hashlib.sha256(payload).hexdigest()
