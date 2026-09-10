"""Adapter for the manually cross-checked midseason domestic transfer source."""
import re
from pathlib import Path
from ..aliases import Clubs
from ..facts import EVENT, event_key, provisional_player_key
from ..master import HEADERS as DOMESTIC

BLOCK = re.compile(r'^##\s+(.*?)\s*/\s*(.*?)\s*$')
FIELD = re.compile(r'^-\s+([a-z_]+):\s*(.*?)\s*$')
WINDOW = re.compile(r'^(\d{4}-\d{2}-\d{2})')
DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
VOCABULARY = {'manually_verified': 'human_verified'}
REQUIRED = ('record_key', 'season', 'club_id', 'player', 'transaction_date')


def parse(text):
    blocks, current = [], None
    for line in text.splitlines():
        heading = BLOCK.match(line)
        if heading:
            current = {'club_official': heading.group(1), 'player': heading.group(2),
                       'fields': {}, 'raw': [line]}
            blocks.append(current)
            continue
        if current is not None:
            current['raw'].append(line)
            field = FIELD.match(line)
            if field:
                current['fields'][field.group(1)] = field.group(2)
    return blocks


def extract(path, season, source, clubs=None, strict=True, source_page=None):
    """Emit domestic roster relations plus their registration events."""
    clubs = clubs if clubs is not None else Clubs(strict=strict)
    blocks = parse(Path(path).read_text())
    domestic, events, report = [], [], {'season': season, 'records': len(blocks), 'clubs': [],
                                        'window_deadline': None, 'notice_deadline_written': 0}
    for number, block in enumerate(blocks, 1):
        fields = block['fields']
        missing = [name for name in REQUIRED if not fields.get(name)]
        if missing:
            raise ValueError(f'Block {number} missing {missing}')
        key = '|'.join(fields[name] for name in ('season', 'club_id', 'player'))
        if key != fields['record_key']:
            raise ValueError(f'Block {number} record_key does not match season|club_id|player')
        if str(fields['season']) != str(season):
            raise ValueError(f'Block {number} season {fields["season"]} outside {season}')
        if fields['club_id'] not in clubs.clubs:
            raise ValueError(f'Block {number} unknown club_id {fields["club_id"]}')
        if not DATE.match(fields['transaction_date']):
            raise ValueError(f'Block {number} transaction_date is not an ISO date')
        window = WINDOW.match(fields.get('notice_deadline', ''))
        if not window:
            raise ValueError(f'Block {number} registration window deadline not found')
        window_deadline = window.group(1)
        report['window_deadline'] = window_deadline
        if fields['club_id'] not in report['clubs']:
            report['clubs'].append(fields['club_id'])
        former = fields.get('former_club')
        row = dict.fromkeys(DOMESTIC)
        row.update({'record_key': fields['record_key'], 'season': fields['season'],
                    'club_id': fields['club_id'], 'club_official': block['club_official'],
                    'sequence': str(fields.get('sequence', number)), 'player': fields['player'],
                    'registration_stage': fields.get('registration_stage'),
                    'registration_method': fields.get('registration_method'),
                    'contract_category': fields.get('contract_category'),
                    'contract_term_official': fields.get('contract_term_official'),
                    'former_club': former,
                    'notice_deadline': None,
                    'registration_status': fields.get('registration_status'),
                    'remarks': fields.get('remarks'),
                    'source_file_id': source.get('id'),
                    'source_url': fields.get('source_url'),
                    'source_page': source_page,
                    'source_type': fields.get('source_type', 'web_cross_checked'),
                    'extraction_method': fields.get('extraction_method'),
                    'verification_level': VOCABULARY.get(fields.get('verification_level'),
                                                         fields.get('verification_level'))})
        domestic.append(row)
        event = dict.fromkeys(EVENT)
        event.update({'provisional_player_key': provisional_player_key(
                          {'player_type': 'domestic', 'player_name_zh': fields['player']}),
                      'sequence': str(fields.get('sequence', number)), 'season': fields['season'],
                      'player_type': 'domestic', 'club_id': fields['club_id'],
                      'club_source_name': block['club_official'],
                      'player_name_zh': fields['player'],
                      'from_club_id': clubs.resolve(former, fields['season'], role='event') if former else None,
                      'from_club_source_name': former,
                      'registration_method': fields.get('registration_method'),
                      'contract_category': fields.get('contract_category'),
                      'contract_term_official': fields.get('contract_term_official'),
                      'event_type': 'registration_change',
                      'event_date': fields['transaction_date'], 'date_year_inferred': False,
                      'club_announcement_date': fields.get('club_announcement_date'),
                      'registration_window_deadline': window_deadline,
                      'registration_status': fields.get('registration_status'),
                      'notes': fields.get('remarks'), 'source_file_id': source.get('id'),
                      'source_url_primary': fields.get('source_url'),
                      'source_url_secondary': fields.get('source_url_secondary'),
                      'source_page_or_row': source_page or (f'source block {number}'),
                      'source_type': fields.get('source_type', 'web_cross_checked'),
                      'extraction_method': fields.get('extraction_method'),
                      'verification_level': VOCABULARY.get(fields.get('verification_level'),
                                                           fields.get('verification_level')),
                      'raw_event_text': '\n'.join(block['raw']).strip()})
        event['event_key'] = event_key(event)
        events.append(event)
    report['events'] = len(events)
    return {'domestic': domestic, 'events': events, 'report': report}
