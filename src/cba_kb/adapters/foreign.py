"""Shared foreign-registration parsing for the XLSX and OCR table sources."""
import re
from ..aliases import Clubs
from ..facts import EVENT, SNAPSHOT, event_key, name_key, provisional_player_key, snapshot_key, text_number

TITLE_DATE = re.compile(r'(\d{1,2})月(\d{1,2})日')
CANCEL = re.compile(r'^(?:备注：)?\s*(\d{1,2})月(\d{1,2})日\s*(.+?)\s*取消外籍球员\s*'
                    r'(.+?)\s*[（(](.+?)[）)]\s*的注册\s*$')
HEADER_FIELDS = ('球队', '国籍', '场上位置', '球衣号码')
SUB_FIELDS = ('英文名', '中文名')
CANCEL_TYPE = 'registration_cancelled'
SNAPSHOT_VERIFICATION = 'auto_validated'


def cell(row, index):
    return row[index] if index < len(row) else None


def text(value):
    return str(value).strip() if value is not None else ''


def header_index(rows):
    for index, row in enumerate(rows):
        cells = [text(c) for c in row]
        if '球队' in cells and any(c.startswith('外籍球员') for c in cells):
            return index
    raise ValueError('Foreign registration header row not found')


def columns(rows, index):
    head = [text(c) for c in rows[index]]
    sub = [text(c) for c in rows[index + 1]] if index + 1 < len(rows) else []
    mapping = {}
    for name in HEADER_FIELDS:
        mapping[name] = next((i for i, c in enumerate(head) if c.startswith(name)), None)
    for name in SUB_FIELDS:
        mapping[name] = sub.index(name) if name in sub else None
    missing = [k for k, v in mapping.items() if v is None]
    if missing:
        raise ValueError(f'Foreign header changed; missing {missing}')
    return mapping


def infer_year(season, month):
    """Season-boundary year inference; derived years are always flagged."""
    start, end = (int(x) for x in str(season).split('-'))
    if not 1 <= int(month) <= 12:
        raise ValueError(f'Invalid month {month}')
    if int(month) >= 8:
        return start, True, False
    return end, True, int(month) >= 4


def snapshot_as_of(title, season):
    match = TITLE_DATE.search(title or '')
    if not match:
        raise ValueError('Source title does not state the registration deadline date')
    month, day = int(match.group(1)), int(match.group(2))
    year, _, review = infer_year(season, month)
    return f'{year:04d}-{month:02d}-{day:02d}', review


def build(rows, season, source, clubs=None, strict=True, title=None, source_page='Sheet1'):
    """Turn two-row-header table rows into snapshot and cancellation-event records."""
    index = header_index(rows)
    mapping = columns(rows, index)
    as_of, as_of_review = snapshot_as_of(title, season) if title else (None, False)
    clubs = clubs if clubs is not None else Clubs(strict=strict)
    snapshots, events, teams, review, unresolved = [], [], [], [], []
    club_name = None
    def note(name):
        item = {'name': name, 'season': season}
        if name and item not in unresolved:
            unresolved.append(item)
    for number, row in enumerate(rows[index + 2:], index + 3):
        first = text(cell(row, mapping['球队']))
        joined = ' | '.join(text(c) for c in row)
        cancel = CANCEL.match(first)
        if cancel:
            month, day, club_text, name_en, name_zh = cancel.groups()
            year, inferred, needs_review = infer_year(season, month)
            club_id = clubs.resolve(club_text, season, role='event')
            if club_id is None:
                note(club_text)
            if needs_review:
                review.append({'row': number, 'reason': 'event month outside the Oct-Mar window',
                               'text': joined})
            normalized = name_key(name_en)
            record = dict.fromkeys(EVENT)
            record.update({'provisional_player_key': provisional_player_key(
                               {'player_type': 'foreign', 'player_name_en_normalized': normalized}),
                           'sequence': str(number), 'season': season, 'player_type': 'foreign',
                           'club_id': club_id, 'club_source_name': club_text,
                           'player_name_zh': name_zh, 'player_name_en_raw': name_en,
                           'player_name_en_normalized': normalized, 'event_type': CANCEL_TYPE,
                           'event_date': f'{year:04d}-{int(month):02d}-{int(day):02d}',
                           'date_year_inferred': inferred, 'registration_status': '取消注册',
                           'source_file_id': source.get('id'), 'source_url_primary': source.get('url'),
                           'source_page_or_row': f'{source_page}!row {number}',
                           'source_type': source.get('type', 'gdrive'),
                           'extraction_method': source.get('method', 'xlsx merge-header segmentation'),
                           'verification_level': SNAPSHOT_VERIFICATION, 'raw_event_text': joined})
            record['event_key'] = event_key(record)
            events.append(record)
            continue
        if not any(text(c) for c in row):
            continue
        if first:
            club_name = first
        if not text(cell(row, mapping['英文名'])):
            continue
        if club_name not in teams:
            teams.append(club_name)
        club_id = clubs.resolve(club_name, season) if club_name else None
        if club_id is None:
            note(club_name)
        record = dict.fromkeys(SNAPSHOT)
        record.update({'season': season, 'snapshot_as_of': as_of, 'club_id': club_id,
                       'club_source_name': club_name,
                       'name_en_raw': cell(row, mapping['英文名']),
                       'name_en_normalized': name_key(cell(row, mapping['英文名'])),
                       'name_zh_raw': cell(row, mapping['中文名']),
                       'nationality': text(cell(row, mapping['国籍'])) or None,
                       'position': text(cell(row, mapping['场上位置'])) or None,
                       'jersey_number': text_number(cell(row, mapping['球衣号码'])),
                       'status_at_snapshot': '注册在册',
                       'source_file_id': source.get('id'), 'source_url': source.get('url'),
                       'source_page_or_row': f'{source_page}!row {number}',
                       'source_type': source.get('type', 'gdrive'),
                       'extraction_method': source.get('method', 'xlsx merge-header segmentation'),
                       'verification_level': SNAPSHOT_VERIFICATION, 'raw_row_text': joined})
        record['snapshot_key'] = snapshot_key(record)
        snapshots.append(record)
    report = {'season': season, 'snapshot_as_of': as_of, 'teams': len(teams),
              'snapshots': len(snapshots), 'events': len(events),
              'observed_registrations': len(snapshots) + len(events),
              'club_names': teams, 'unresolved_clubs': unresolved, 'review': review}
    if as_of_review:
        report['review'].append({'row': 1,
                                 'reason': 'snapshot date month outside the Oct-Mar window',
                                 'text': title})
    if strict and unresolved:
        raise ValueError(f'Unresolved club names: {sorted({u["name"] for u in unresolved})}')
    return {'snapshots': snapshots, 'events': events, 'report': report}
