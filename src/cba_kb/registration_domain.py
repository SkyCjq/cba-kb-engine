"""DRAFT-2 registration-domain entities and source parsers.

The v1.5.1 products remain compatible exports.  This module adds the six
canonical grains required by DRAFT-2 without treating provisional player keys as
identity.  Parsers are deliberately source-shaped: raw labels and row text are
retained, while unresolved aliases and incomplete dates are surfaced for
review instead of guessed.
"""
from __future__ import annotations

import re
from datetime import date

from .facts import name_key

DOMESTIC_REGISTRATIONS = (
    'record_key','season','window_no','player','from_club_id','from_club_source_name',
    'club_id','club_source_name','movement_type','registration_method_official',
    'contract_category','contract_term_official','registration_submission_date',
    'registration_submission_date_status','club_announcement_date',
    'registration_completed_date','registration_status','event_key','source_file_id',
    'source_url_primary','source_url_secondary','source_page_or_row','source_type',
    'extraction_method','verification_status','source_authority','verification_raw',
    'source_authority_raw','raw_record_text',
)
TRANSACTION_WINDOWS = (
    'window_key','season','window_no','window_start','window_end','allowed_methods',
    'ordinary_transfer_allowed','loan_allowed','rule_source_id','rule_source_url',
    'verification_status','source_authority','verification_raw','source_authority_raw',
    'completeness_status','source_file_id','source_url_primary','source_url_secondary',
    'source_page_or_row','source_type','extraction_method','raw_window_text',
)
STATUS_EVENTS = (
    'event_key','event_domain','event_type','event_status','transaction_group_id',
    'season','player_type','club_id','club_source_name','player_name_zh',
    'player_name_en_raw','player_name_en_normalized','from_club_id',
    'from_club_source_name','to_club_id','to_club_source_name','event_date',
    'event_date_status','event_date_precision','date_year_inferred',
    'club_announcement_date','club_announcement_date_status',
    'registration_submission_date','registration_submission_date_status',
    'registration_completed_date','registration_completed_date_status',
    'registration_window_deadline','registration_window_deadline_status',
    'registration_method_official','research_movement_label','contract_category',
    'contract_term_official','notes','supersedes_event_key','correction_reason',
    'source_file_id','source_url_primary','source_url_secondary','source_page_or_row',
    'source_type','extraction_method','verification_status','source_authority',
    'verification_raw','source_authority_raw','raw_event_text',
)
FOREIGN_REGISTRATION_SNAPSHOTS = (
    'snapshot_key','season','snapshot_as_of','club_id','club_source_name',
    'name_en_raw','name_en_normalized','name_zh_raw','nationality','position',
    'jersey_number','status_at_snapshot','source_file_id','source_url',
    'source_page_or_row','source_type','extraction_method','verification_status',
    'source_authority','verification_raw','source_authority_raw','raw_row_text',
)
FOREIGN_PRIORITY_RIGHT_SNAPSHOTS = (
    'right_key','source_registration_season','right_target_season','snapshot_date',
    'snapshot_date_status','club_id','club_source_name','player_name_en_raw',
    'player_name_en_normalized','player_name_zh_raw','right_status','renewal_completed',
    'source_file_id','source_url','source_page_or_row','source_type',
    'extraction_method','verification_status','source_authority','verification_raw',
    'source_authority_raw','raw_right_text','transaction_status',
)
FOREIGN_PRIORITY_RIGHT_TRANSACTIONS = (
    'right_transaction_key','right_target_season','player_name_en_raw',
    'player_name_en_normalized','player_name_zh_raw','from_club_id',
    'from_club_source_name','to_club_id','to_club_source_name','transaction_date',
    'transaction_date_status','official_update_date','official_update_date_status',
    'later_registration_status','source_file_id','source_url_primary',
    'source_url_secondary','source_page_or_row','source_type','extraction_method',
    'verification_status','source_authority','verification_raw','source_authority_raw',
    'raw_transaction_text',
)

TABLES = {
    'domestic_registrations': DOMESTIC_REGISTRATIONS,
    'domestic_transaction_windows': TRANSACTION_WINDOWS,
    'registration_status_events': STATUS_EVENTS,
    'foreign_registration_snapshots': FOREIGN_REGISTRATION_SNAPSHOTS,
    'foreign_priority_right_snapshots': FOREIGN_PRIORITY_RIGHT_SNAPSHOTS,
    'foreign_priority_right_transactions': FOREIGN_PRIORITY_RIGHT_TRANSACTIONS,
}

EVENT_DOMAINS = ('domestic_movement','foreign_registration','foreign_usage','correction')
EVENT_TYPES = (
    'free_agent_claim','player_swap','release_then_claim','status_change_only',
    'rumor_not_completed','registration_change','registration_cancelled',
    'usage_suspended','usage_activated','status_correction',
)
EVENT_STATUSES = (
    'confirmed','pending','uncompleted','corrected','withdrawn','cancelled','system_error',
)
DATE_STATUSES = ('known','pending_evidence','unknown','not_applicable')
DATE_PRECISIONS = ('day','month','season','range','approx','unknown')
RESEARCH_MOVEMENT_LABELS = (
    'free_agent_claim','player_swap','release_then_claim','status_change_only',
    'rumor_not_completed',
)
SOURCE_AUTHORITIES = (
    'A1_official_direct','A2_official_mirror','B1_authoritative_media_reproduction',
    'B2_secondary_cross_check','C_unverified',
)
RIGHT_STATUSES = ('exercised','continued','renewal_completed')

ISO_RE = re.compile(r'(?<!\d)(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)')
CN_DATE_RE = re.compile(r'(20\d{2})年(\d{1,2})月(\d{1,2})日?')
ISO_RANGE_RE = re.compile(
    r'(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\s*(?:至|~|—|-)\s*(\d{1,2})[-/.](\d{1,2})'
)
SEASON_HEADING_RE = re.compile(r'(20\d{2})[-–](20\d{2})')
WINDOW_LABEL_RE = re.compile(r'Window\s+(\d+)\s*[:：]')


def _cell(value):
    return '' if value is None else str(value).strip()


def _pipe(line):
    if not line.lstrip().startswith('|'):
        return None
    return [part.strip() for part in line.strip().strip('|').split('|')]


def markdown_tables(text):
    """Yield (heading, headers, rows, source line numbers) from Markdown tables."""
    lines = text.splitlines()
    heading = ''
    season_context = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith('#'):
            raw_heading = line.lstrip('#').strip()
            detected_season = _season(raw_heading)
            if detected_season:
                season_context = detected_season
            heading = (f'{season_context} | {raw_heading}' if season_context and not detected_season
                       else raw_heading)
        headers = _pipe(line)
        if headers and i + 1 < len(lines) and _pipe(lines[i + 1]):
            divider = _pipe(lines[i + 1])
            if divider and all(set(c) <= set('-: ') for c in divider):
                rows, numbers = [], []
                j = i + 2
                while j < len(lines):
                    row = _pipe(lines[j])
                    if not row:
                        break
                    if len(row) != len(headers):
                        raise ValueError(f'Malformed Markdown table at line {j + 1}')
                    rows.append(dict(zip(headers, row)))
                    numbers.append(j + 1)
                    j += 1
                if rows:
                    yield heading, headers, rows, numbers
                i = j
                continue
        i += 1


def _season(text, fallback=None):
    match = SEASON_HEADING_RE.search(text or '')
    if match:
        return f'{match.group(1)}-{match.group(2)}'
    return fallback


def _date(value):
    value = _cell(value)
    match = ISO_RE.search(value)
    if match:
        return date(*(int(x) for x in match.groups())).isoformat()
    match = CN_DATE_RE.search(value)
    if match:
        return date(*(int(x) for x in match.groups())).isoformat()
    return None


def _date_field(value):
    raw = _cell(value)
    exact = ISO_RE.fullmatch(raw) or CN_DATE_RE.fullmatch(raw)
    parsed = _date(raw) if exact else None
    return parsed, 'known' if parsed else ('pending_evidence' if _cell(value) else 'unknown')


def _date_range(value):
    raw = _cell(value)
    match = ISO_RANGE_RE.search(raw)
    if match:
        year, start_month, start_day, end_month, end_day = match.groups()
        return (date(int(year), int(start_month), int(start_day)).isoformat(),
                date(int(year), int(end_month), int(end_day)).isoformat())
    return None, None


def _authority(raw):
    # These adapters read secondary merged audits, not the cited official pages.
    # Keep the source's A/B labels separate instead of promoting their authority.
    return 'B2_secondary_cross_check'


def _movement_type(raw):
    raw = _cell(raw).lower()
    if '互换' in raw or 'swap' in raw:
        return 'player_swap'
    if 'release_then_claim' in raw or '解除' in raw:
        return 'release_then_claim'
    if 'status_change_only' in raw or '未完成' in raw or '系统乌龙' in raw:
        return 'status_change_only'
    if '认领' in raw or 'claim' in raw:
        return 'free_agent_claim'
    if '传闻' in raw:
        return 'rumor_not_completed'
    return 'registration_change'


def _research_movement_label(value):
    raw = _cell(value)
    if not raw:
        return ''
    normalized = _movement_type(raw)
    if normalized == 'registration_change':
        raise ValueError(f'Uncontrolled research_movement_label: {raw}')
    return normalized


def _split_name(value):
    raw = _cell(value).replace('**', '')
    bracket = re.fullmatch(r'(.+?)\s*[（(]([^()（）]+)[）)]', raw)
    parts = list(bracket.groups()) if bracket else raw.split('/', 1)
    parts = [p.strip() for p in parts if p.strip()]
    en = next((p for p in parts if not re.search(r'[\u3400-\u9fff]', p)), None)
    zh = next((p for p in parts if re.search(r'[\u3400-\u9fff]', p)), None)
    return en, zh


def _resolve(clubs, value, season, role='event'):
    value = _cell(value)
    if not value or '自由球员' in value or value in {'/', '无'}:
        return None
    # Markdown emphasis is presentation, not part of the club's source name.
    lookup = re.sub(r'[*_`]', '', value).strip()
    return clubs.resolve(lookup, season, role=role)


def _base_source(source, page):
    return {'source_file_id': source.get('id'), 'source_url_primary': source.get('url'),
            'source_page_or_row': page, 'source_type': source.get('type', 'gdrive'),
            'extraction_method': source.get('method', 'markdown table parser')}


def parse_domestic_movement(text, source, clubs, include_window_entities=True,
                            legacy_research_defaults=False):
    """Parse the merged domestic movement source into windows, relations and events."""
    windows, relations, events, reports = [], [], [], []
    selected = {'4.2','4.5','5.','6.','7.1','7.2','8.2','9.2'}
    for heading, headers, rows, numbers in markdown_tables(text):
        if not any(token in heading for token in selected):
            continue
        season = _season(heading)
        if not season:
            continue
        # A table with window/rule columns contributes a distinct window entity.
        if '赛季中窗口' in headers or 'window_start' in headers:
            continue
        player_key = next((k for k in headers if '球员' in k), None)
        to_key = next((k for k in headers if k == '新俱乐部'), None)
        status_key = next((k for k in headers if k in {'结果','状态'}), None)
        # Incomplete rows intentionally have no destination club.  They still
        # produce a status event with a preserved source status.
        if not player_key or (not to_key and not status_key):
            continue
        from_key = next((k for k in headers if '前一' in k or '原俱乐部' in k or '最近/原' in k), None)
        method_key = next((k for k in headers if k == '方式'), None)
        research_key = next((k for k in headers if k == '研究标签'), None)
        # A Window 1 label must not hide a separate announcement/date column.
        date_key = next((k for k in ('日期','日期/节点','完成节点','日期/工作口径','关键节点','窗口') if k in headers), None)
        evidence_key = next((k for k in headers if k == '证据等级'), None)
        for row, number in zip(rows, numbers):
            player = _cell(row.get(player_key))
            if not player or player in {'球员','姓名'}:
                continue
            to_name = _cell(row.get(to_key)) if to_key else ''
            from_name = _cell(row.get(from_key)) if from_key else ''
            raw_method = _cell(row.get(method_key)) if method_key else ''
            official_method = raw_method
            research_label = _research_movement_label(
                row.get(research_key) if research_key else None
            )
            if not official_method and research_label in RESEARCH_MOVEMENT_LABELS:
                if legacy_research_defaults:
                    official_method = {
                        'free_agent_claim': '自由球员认领',
                        'release_then_claim': '自由球员认领',
                        'player_swap': '球员互换',
                    }.get(research_label, '')
                else:
                    official_method = ''
            status_raw = _cell(row.get(status_key)) if status_key else ''
            movement = _movement_type(
                ' '.join((official_method, research_label, status_raw, heading))
            )
            status = 'uncompleted' if ('未完成' in heading or '未完成' in status_raw) else 'confirmed'
            event_date, event_date_status = _date_field(row.get(date_key)) if date_key else (None, 'unknown')
            club_announcement_date = (
                _date(row.get(date_key)) if '官宣' in _cell(row.get(date_key)) else None
            )
            from_id = _resolve(clubs, from_name, season, 'event')
            to_id = _resolve(clubs, to_name, season, 'event')
            raw = text.splitlines()[number - 1]
            authority_raw = _cell(row.get(evidence_key)) if evidence_key else ''
            authority = _authority(authority_raw) or 'C_unverified'
            group = None
            if movement == 'player_swap':
                pair = '~'.join(sorted({from_id or from_name, to_id or to_name}))
                group = f'{season}|{event_date or "unknown"}|{pair}|swap'
            event = dict.fromkeys(STATUS_EVENTS)
            event.update(_base_source(source, f'Markdown line {number}'))
            event.update({'event_key': f'{season}|domestic|{to_id or to_name}|{player}|{movement}|{event_date or "pending"}|{number}',
                          'event_domain': 'domestic_movement', 'event_type': movement,
                          'event_status': status, 'transaction_group_id': group,
                          'season': season, 'player_type': 'domestic', 'club_id': to_id,
                          'club_source_name': to_name, 'player_name_zh': player,
                          'from_club_id': from_id, 'from_club_source_name': from_name,
                          'to_club_id': to_id, 'to_club_source_name': to_name,
                          'event_date': event_date, 'event_date_status': event_date_status,
                          'event_date_precision': 'day' if event_date else 'unknown',
                          'date_year_inferred': False,
                          'club_announcement_date': club_announcement_date,
                          'club_announcement_date_status':
                              'known' if club_announcement_date else 'unknown',
                          'registration_submission_date_status': 'pending_evidence',
                          'registration_completed_date_status': 'pending_evidence',
                          'registration_window_deadline_status': 'unknown',
                          'notes': row.get('备注'),
                          'registration_method_official': official_method or None,
                          'research_movement_label': research_label or None,
                          'verification_status': 'auto_validated',
                          'source_authority': authority, 'verification_raw': authority_raw,
                          'source_authority_raw': authority_raw, 'raw_event_text': raw})
            events.append(event)
            # Keep an unresolved source club as a reviewable row.  A missing
            # canonical club id is a data quality issue, not grounds to drop
            # the source fact; the provisional source name remains preserved.
            if status == 'confirmed' and (to_id or to_name) and player:
                relation = dict.fromkeys(DOMESTIC_REGISTRATIONS)
                relation.update({'record_key': f'{season}|{to_id or to_name}|{player}', 'season': season,
                                 'window_no': int(row['窗口'].split()[-1]) if _cell(row.get('窗口')).startswith('Window ') else None,
                                 'player': player, 'from_club_id': from_id,
                                 'club_announcement_date': event['club_announcement_date'],
                                 'registration_submission_date_status': 'pending_evidence',
                                 'from_club_source_name': from_name, 'club_id': to_id,
                                 'club_source_name': to_name, 'movement_type': movement,
                                 'registration_method_official': official_method or None,
                                 'registration_status': status_raw or 'confirmed',
                                 'event_key': event['event_key'], 'verification_status': event['verification_status'],
                                 'source_authority': authority, 'verification_raw': authority_raw,
                                 'source_authority_raw': authority_raw, 'raw_record_text': raw,
                                 **_base_source(source, f'Markdown line {number}')})
                relations.append(relation)
        reports.append({'heading': heading, 'season': season, 'rows': len(rows)})
    # Parse explicit window declarations even if no movement table exists.
    lines = text.splitlines()
    current = None
    pending_window = None

    def window_for(season, number):
        return next((window for window in windows
                     if window['season'] == season and window['window_no'] == number), None)

    def add_window(season, number, line_number, raw):
        window = window_for(season, number)
        if window is None:
            window = dict.fromkeys(TRANSACTION_WINDOWS)
            window.update(_base_source(source, f'Markdown line {line_number}'))
            window.update({
                'season': season, 'window_no': number, 'window_key': f'{season}|{number}',
                'allowed_methods': None, 'ordinary_transfer_allowed': None,
                'loan_allowed': None, 'rule_source_id': source.get('id'),
                'rule_source_url': source.get('url'), 'verification_status': 'pending',
                'source_authority': 'B2_secondary_cross_check',
                'source_authority_raw': 'merged source rule',
                'completeness_status': 'dates_pending', 'raw_window_text': raw,
            })
            windows.append(window)
        else:
            window['raw_window_text'] += '\n' + raw
        return window

    for line_no, line in enumerate(lines, 1):
        heading = _season(line) if line.startswith('#') else None
        if heading:
            current = heading
            pending_window = None
        if not current:
            continue

        label = WINDOW_LABEL_RE.search(line)
        if label:
            if not include_window_entities:
                continue
            number = int(label.group(1))
            start, end = _date_range(line)
            raw_lines = [line]
            if not start and not end:
                for following in lines[line_no:line_no + 40]:
                    if following.startswith('# ') and _season(following):
                        break
                    if (f'Window {number}' in following
                            and not following.lstrip().startswith('|')
                            and ('CBA官网' in following or '官方' in following)):
                        candidate = re.search(
                            rf'Window\s+{number}\s*[（(：:=]\s*([^）)\n]+)',
                            following,
                        )
                        if not candidate:
                            continue
                        start, end = _date_range(candidate.group(1))
                        if start or end:
                            raw_lines.append(following)
                            break
            window = add_window(current, number, line_no, '\n'.join(raw_lines))
            if start:
                window['window_start'] = start
            if end:
                window['window_end'] = end
            if start or end:
                window['completeness_status'] = 'working_date_range'
            continue

        if 'window_start:' not in line and 'window_end:' not in line:
            continue
        if 'window_start:' in line:
            start = _date(line)
            if not start:
                continue
            window_no = len([window for window in windows if window['season'] == current]) + 1
            pending_window = dict.fromkeys(TRANSACTION_WINDOWS)
            pending_window.update(_base_source(source, f'Markdown line {line_no}'))
            pending_window.update({'season': current, 'window_no': window_no, 'window_start': start,
                                   'window_key': f'{current}|{window_no}',
                                   'allowed_methods': None,
                                   'ordinary_transfer_allowed': None, 'loan_allowed': None,
                                   'rule_source_id': source.get('id'), 'rule_source_url': source.get('url'),
                                   'verification_status': 'pending', 'source_authority': 'C_unverified',
                                   'completeness_status': 'pending', 'raw_window_text': line})
            windows.append(pending_window)
            for rule_line in lines[line_no:]:
                if rule_line.startswith('#') or rule_line.startswith('|'):
                    break
                if 'allowed_methods:' in rule_line:
                    pending_window['allowed_methods'] = [
                        _movement_type(item)
                        for item in rule_line.split(':', 1)[1].split('、')
                    ]
                for rule in ('ordinary_transfer_allowed', 'loan_allowed'):
                    if rule + ':' in rule_line:
                        value = rule_line.split(':', 1)[1].strip()
                        if value not in {'true', 'false'}:
                            raise ValueError('Invalid boolean window rule')
                        pending_window[rule] = value == 'true'
                if any(label in rule_line for label in (
                        'allowed_methods:', 'ordinary_transfer_allowed:', 'loan_allowed:')):
                    pending_window['raw_window_text'] += '\n' + rule_line
        elif pending_window is not None and 'window_end:' in line:
            end = _date(line)
            if end:
                pending_window['window_end'] = end
                pending_window['raw_window_text'] += '\n' + line
    windows.sort(key=lambda row: (row['season'], row['window_no']))
    for event in events:
        matches = [w for w in windows if w['season'] == event['season']]
        if len(matches) == 1:
            event['registration_window_deadline'] = matches[0]['window_end']
            event['registration_window_deadline_status'] = (
                'known' if matches[0]['window_end'] else 'unknown'
            )
    return {'domestic_registrations': relations, 'domestic_transaction_windows': windows,
            'registration_status_events': events,
            'report': {'relations': len(relations), 'events': len(events), 'windows': len(windows),
                       'tables': reports}}


def _table_context(text, row_no):
    lines = text.splitlines()
    header = row_no - 3
    start = header
    while start > 0 and not lines[start].startswith('#'):
        start -= 1
    return '\n'.join(lines[start:header])


def _snapshot_date(context, season, registration=False):
    heading = context.splitlines()[0] if context else ''
    if registration:
        cutoff = re.search(r'截至(\d{1,2})月(\d{1,2})日', heading)
        if cutoff:
            month, day = map(int, cutoff.groups())
            year = int(season.split('-')[0 if month >= 8 else 1])
            return date(year, month, day).isoformat()
    for line in context.splitlines():
        if any(label in line for label in ('发布日期', '更新时间', 'official_page_date:', 'snapshot_date:')):
            # Two candidate dates must not silently become one exact day.
            if re.search(r'\d日?\s*/\s*\d', line):
                return None
            return _date(line)
    return None


def _shift_season(season, years):
    return '-'.join(str(int(y) + years) for y in season.split('-'))


def _foreign_record(source, headers, number, raw, context):
    result = dict.fromkeys(headers)
    result.update({
        'source_file_id': source.get('id'), 'source_page_or_row': f'Markdown line {number}',
        'source_type': source.get('type', 'gdrive'), 'extraction_method': 'foreign merged markdown parser',
        'verification_status': 'auto_validated', 'source_authority': 'B2_secondary_cross_check',
        'source_authority_raw': context,
    })
    result['source_url' if 'source_url' in headers else 'source_url_primary'] = source.get('url')
    result[next(k for k in headers if k.startswith('raw_'))] = raw
    return result


def parse_foreign_rights(text, source, clubs):
    lines = text.splitlines()
    rights, transactions, snapshots, events, reports = [], [], [], [], []
    for heading, headers, rows, numbers in markdown_tables(text):
        season = _season(heading)
        context = _table_context(text, numbers[0])
        as_of = _snapshot_date(context, season)
        dest_key = next((k for k in headers if '交易后' in k and ('俱乐部' in k or '球队' in k)), None)
        player_key = next((k for k in headers if '外籍球员' in k), None)
        if dest_key and player_key:
            historical = '目标赛季' in headers
            for row, number in zip(rows, numbers):
                en, zh = _split_name(row[player_key])
                target = _season(row.get('目标赛季')) if historical else season
                if not target or not (en or zh):
                    raise ValueError(f'Missing rights target/player at line {number}')
                from_name = row[next(k for k in headers if '享有' in k or '原权利' in k)]
                dest_name = row[dest_key]
                transferred = dest_name.replace('**', '').strip() not in {'/', '', '无'}
                from_id = _resolve(clubs, from_name, target)
                to_id = _resolve(clubs, dest_name, target) if transferred else None
                token = name_key(en or zh)
                if not historical:
                    right = _foreign_record(source, FOREIGN_PRIORITY_RIGHT_SNAPSHOTS, number, lines[number-1], context)
                    right.update({
                        'right_key': f'{target}|{as_of or "unknown"}|{to_id or from_id or from_name}|{token}',
                        'right_target_season': target, 'snapshot_date': as_of,
                        'snapshot_date_status': 'known' if as_of else 'unknown',
                        'club_id': to_id if transferred else from_id,
                        'club_source_name': dest_name if transferred else from_name,
                        'player_name_en_raw': en, 'player_name_en_normalized': name_key(en) if en else None,
                        'player_name_zh_raw': zh,
                        # Dynamic transfer tables do not state "continued" or renewal.
                        'right_status': None, 'renewal_completed': None,
                        'transaction_status': 'transferred' if transferred else 'not_shown_as_transferred',
                    })
                    rights.append(right)
                if transferred:
                    tx_date, tx_status = _date_field(row.get('官方更新/事件日期')) if historical else (None, 'unknown')
                    tx = _foreign_record(source, FOREIGN_PRIORITY_RIGHT_TRANSACTIONS, number, lines[number-1], context)
                    tx.update({
                        'right_transaction_key': f'{target}|{token}|{from_id or from_name}|{to_id or dest_name}|{tx_date or "pending"}',
                        'right_target_season': target, 'player_name_en_raw': en,
                        'player_name_en_normalized': name_key(en) if en else None, 'player_name_zh_raw': zh,
                        'from_club_id': from_id, 'from_club_source_name': from_name,
                        'to_club_id': to_id, 'to_club_source_name': dest_name,
                        'transaction_date': tx_date, 'transaction_date_status': tx_status,
                        'official_update_date': tx_date if historical else as_of,
                        'official_update_date_status': tx_status if historical else ('known' if as_of else 'unknown'),
                        'verification_raw': row.get('证据结论'),
                    })
                    transactions.append(tx)
            reports.append({'season': season, 'kind': 'historical_transactions' if historical else 'rights_transaction_status', 'rows': len(rows)})
            continue
        if not season:
            continue
        if '暂停使用' in heading and '姓名' in headers:
            for row, number in zip(rows, numbers):
                club_name = row[next(k for k in headers if '俱乐部' in k)]
                en, zh = _split_name(row['姓名'])
                event = _foreign_record(source, STATUS_EVENTS, number, lines[number-1], context)
                event.update({
                    'event_key': f'{season}|usage_suspended|{name_key(en or zh)}|{number}',
                    'event_domain': 'foreign_usage', 'event_type': 'usage_suspended', 'event_status': 'confirmed',
                    'season': season, 'player_type': 'foreign',
                    'club_id': _resolve(clubs, club_name, season), 'club_source_name': club_name,
                    'player_name_zh': zh, 'player_name_en_raw': en, 'player_name_en_normalized': name_key(en) if en else None,
                    'event_date': as_of, 'event_date_status': 'known' if as_of else 'unknown',
                    'event_date_precision': 'day' if as_of else 'unknown',
                    'date_year_inferred': False,
                    'club_announcement_date_status': 'not_applicable',
                    'registration_submission_date_status': 'not_applicable',
                    'registration_completed_date_status': 'not_applicable',
                    'registration_window_deadline_status': 'not_applicable',
                    'notes': row.get('类别'),
                })
                events.append(event)
            reports.append({'season': season, 'kind': 'foreign_usage_suspend', 'rows': len(rows)})
            continue
        if any('英文名' in k for k in headers) and '球衣号码' in headers:
            team_key = next(k for k in headers if '球队' in k or '俱乐部' in k)
            en_key = next(k for k in headers if '英文名' in k)
            club_name = None
            as_of = _snapshot_date(context, season, registration=True)
            for row, number in zip(rows, numbers):
                club_name = _cell(row.get(team_key)) or club_name
                en, zh = _split_name(row.get(en_key))
                zh = row.get('中文名') if '中文名' in headers else zh
                if not en:
                    raise ValueError(f'Missing English registration name at line {number}')
                club_id = _resolve(clubs, club_name, season, 'roster')
                snap = _foreign_record(source, FOREIGN_REGISTRATION_SNAPSHOTS, number, lines[number-1], context)
                snap.update({
                    'snapshot_key': f'{season}|{as_of or "unknown"}|{club_id or club_name}|{name_key(en)}|{row.get("国籍")}',
                    'season': season, 'snapshot_as_of': as_of, 'club_id': club_id, 'club_source_name': club_name,
                    'name_en_raw': en, 'name_en_normalized': name_key(en), 'name_zh_raw': zh,
                    'nationality': row.get('国籍'), 'position': row.get('位置'),
                    'jersey_number': _cell(row.get('球衣号码')).replace('**', ''), 'status_at_snapshot': 'registered',
                })
                snapshots.append(snap)
            reports.append({'season': season, 'kind': 'foreign_registration', 'rows': len(rows)})
            continue
        club_key = next((k for k in headers if k in {'俱乐部','球队'}), None)
        if club_key and player_key and '优先续约权' in heading:
            status_key = next((k for k in headers if any(s in k for s in ('状态','说明','行使'))), None)
            source_season = season if '赛季注册外籍球员' in heading else _shift_season(season, -1)
            target = _shift_season(season, 1) if '赛季注册外籍球员' in heading else season
            for row, number in zip(rows, numbers):
                en, zh = _split_name(row[player_key])
                status_raw = _cell(row.get(status_key)) if status_key else '行使优先续约权'
                status = ('renewal_completed' if '已完成续约' in status_raw else
                          'continued' if '继续行使' in status_raw or '延续保有' in status_raw else
                          'exercised' if '行使' in status_raw or '申请使用' in status_raw else None)
                club_name = row[club_key]
                club_id = _resolve(clubs, club_name, target)
                right = _foreign_record(source, FOREIGN_PRIORITY_RIGHT_SNAPSHOTS, number, lines[number-1], context)
                right.update({
                    'right_key': f'{target}|{as_of or "unknown"}|{club_id or club_name}|{name_key(en or zh)}',
                    'source_registration_season': source_season, 'right_target_season': target,
                    'snapshot_date': as_of, 'snapshot_date_status': 'known' if as_of else 'pending_evidence',
                    'club_id': club_id, 'club_source_name': club_name,
                    'player_name_en_raw': en, 'player_name_en_normalized': name_key(en) if en else None,
                    'player_name_zh_raw': zh, 'right_status': status,
                    'renewal_completed': True if status == 'renewal_completed' else None,
                    'verification_raw': row.get(status_key),
                })
                rights.append(right)
            reports.append({'season': target, 'kind': 'renewal_status', 'rows': len(rows)})
    # Only reconcile an undated observation against a single dated historical
    # event. Same Chinese name here is a within-source join, not a player UID.
    dated = {}
    for tx in transactions:
        if tx['transaction_date'] and tx['player_name_zh_raw']:
            key = (tx['right_target_season'], tx['player_name_zh_raw'], tx['from_club_id'], tx['to_club_id'])
            dated.setdefault(key, []).append(tx)
    kept = []
    for tx in transactions:
        key = (tx['right_target_season'], tx['player_name_zh_raw'], tx['from_club_id'], tx['to_club_id'])
        matches = dated.get(key, [])
        if not tx['transaction_date'] and len(matches) == 1 and tx['from_club_id'] and tx['to_club_id']:
            prior = matches[0]
            if not prior['player_name_en_raw']:
                prior['player_name_en_raw'] = tx['player_name_en_raw']
                prior['player_name_en_normalized'] = tx['player_name_en_normalized']
            prior['raw_transaction_text'] += '\n' + tx['raw_transaction_text']
            prior['source_page_or_row'] += '; ' + tx['source_page_or_row']
        else:
            kept.append(tx)
    transactions = kept
    return {'foreign_priority_right_snapshots': rights, 'foreign_priority_right_transactions': transactions,
            'foreign_registration_snapshots': snapshots, 'registration_status_events': events,
            'report': {'rights_snapshots': len(rights), 'rights_transactions': len(transactions),
                       'foreign_snapshots': len(snapshots), 'events': len(events), 'tables': reports}}


def validate_domain(records):
    """Validate key, raw/provenance, date and enum invariants for DRAFT-2 records."""
    checks = {}
    for name, headers in TABLES.items():
        rows = records.get(name) or []
        if not rows:
            checks[name] = {'rows': 0, 'unique': 0}
            continue
        keys = {'domestic_registrations': 'record_key', 'domestic_transaction_windows': 'window_key',
                'registration_status_events': 'event_key', 'foreign_registration_snapshots': 'snapshot_key',
                'foreign_priority_right_snapshots': 'right_key',
                'foreign_priority_right_transactions': 'right_transaction_key'}
        key = keys[name]
        values = [row.get(key) for row in rows]
        if any(not value for value in values) or len(values) != len(set(values)):
            raise ValueError(f'Duplicate or missing {key}')
        if any(not row.get('raw_' + ('record' if name == 'domestic_registrations' else 'window' if name == 'domestic_transaction_windows' else 'event' if name == 'registration_status_events' else 'row' if name == 'foreign_registration_snapshots' else 'right' if name == 'foreign_priority_right_snapshots' else 'transaction') + '_text') for row in rows):
            raise ValueError(f'{name} requires source raw text')
        if any(not any(row.get(k) for k in ('source_file_id','source_url','source_url_primary','rule_source_url')) for row in rows):
            raise ValueError(f'{name} requires provenance')
        for row in rows:
            if set(row) != set(headers):
                raise ValueError(f'{name} row schema mismatch')
            if row.get('source_authority') not in SOURCE_AUTHORITIES:
                raise ValueError('Uncontrolled source authority')
            for field, value in row.items():
                if field.endswith('_date_status'):
                    if value not in DATE_STATUSES:
                        raise ValueError('Uncontrolled date status')
                    actual = row.get(field.removesuffix('_status'))
                    if (value == 'known') != bool(actual):
                        raise ValueError('Inconsistent date status/value')
                if value and (field.endswith('_date') or field in {'snapshot_as_of','window_start','window_end','registration_window_deadline'}):
                    if _date_field(value)[0] != value:
                        raise ValueError(f'Invalid ISO date in {field}')
            if name == 'domestic_transaction_windows' and row['window_start'] and row['window_end'] and row['window_end'] < row['window_start']:
                raise ValueError('Window end precedes start')
        checks[name] = {'rows': len(rows), 'unique': len(set(values))}
    for row in records.get('registration_status_events') or []:
        if row.get('event_domain') not in EVENT_DOMAINS or row.get('event_type') not in EVENT_TYPES:
            raise ValueError('Uncontrolled event domain/type')
        if row.get('event_status') not in EVENT_STATUSES:
            raise ValueError('Uncontrolled event status')
        if row.get('event_date_precision') not in DATE_PRECISIONS:
            raise ValueError('Uncontrolled event date precision')
        for field in (
            'event_date_status',
            'club_announcement_date_status',
            'registration_submission_date_status',
            'registration_completed_date_status',
            'registration_window_deadline_status',
        ):
            if row.get(field) not in DATE_STATUSES:
                raise ValueError('Uncontrolled date status')
    for row in records.get('foreign_priority_right_snapshots') or []:
        if row.get('right_status') is not None and row.get('right_status') not in RIGHT_STATUSES:
            raise ValueError('Uncontrolled right status')
        if row.get('renewal_completed') is True and row.get('right_status') != 'renewal_completed':
            raise ValueError('Inconsistent renewal status')
        if row.get('transaction_status') not in {None, 'transferred', 'not_shown_as_transferred'}:
            raise ValueError('Uncontrolled transaction status')
    for row in records.get('foreign_priority_right_transactions') or []:
        if _cell(row.get('to_club_source_name')).replace('**', '') in {'/', '', '无'}:
            raise ValueError('Transaction requires destination')
    from .event_closure import validate_event_closure
    checks['registration_status_events']['closure'] = validate_event_closure(records)
    return checks
