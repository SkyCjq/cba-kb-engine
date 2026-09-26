"""Sanitized read-only diagnostics with bounded retries.

Only read-only calls are retried. Writes stay single-attempt so that a lost
response is resumed through the release journal instead of repeated blindly.

A retryable read failure never implies a missing object: the caller must still
complete exact identity/hash verification before any create path may run.
"""
import importlib
import json
import http.client
import re
import socket
import ssl
import sys
import time
from datetime import datetime, timezone

URL_QUERY = re.compile(r'(https?://[^\s?]+)\?[^\s]+')
SECRET = re.compile(r'(access_token|refresh_token|id_token|client_secret|key|sig|signature)=[^\s&]+')
TRANSIENT_STATUS = (408, 429, 500, 502, 503, 504)
TRANSIENT_TYPES = (TimeoutError, ConnectionError, socket.timeout, socket.gaierror,
                   http.client.HTTPException, ssl.SSLError)
RETRY_ATTEMPTS = 6
RETRY_BASE_SECONDS = 1.0
RETRY_MAX_DELAY = 8.0
# Optional transport-layer libraries: classified only when importable. HTTP
# errors stay status-classified so 401/403/404 can never become retryable.
for _module, _name in (('httplib2', 'HttpLib2Error'),
                       ('google.auth.exceptions', 'TransportError'),
                       ('requests.exceptions', 'ConnectionError')):
    try:
        _type = getattr(importlib.import_module(_module), _name)
    except Exception:
        continue
    if _type not in TRANSIENT_TYPES:
        TRANSIENT_TYPES = TRANSIENT_TYPES + (_type,)

READ_RETRY_STATS = {'attempts': 0, 'retries': 0, 'resets': 0, 'failures': 0}


def read_retry_stats():
    """Deterministic counters for release evidence; never contains payload data."""
    return dict(READ_RETRY_STATS)


def reset_read_retry_stats():
    for key in READ_RETRY_STATS:
        READ_RETRY_STATS[key] = 0


def stage(name, message=''):
    line = f'[{datetime.now(timezone.utc).strftime("%H:%M:%S")}] {name}: {message}'.rstrip()
    print(line, file=sys.stderr, flush=True)
    return line


def scrub(text):
    text = URL_QUERY.sub(r'\1?[redacted]', str(text))
    return SECRET.sub(r'\1=[redacted]', text)


def reason(exception):
    """A loggable reason that never carries URLs with query strings or tokens."""
    status = getattr(getattr(exception, 'resp', None), 'status', None)
    detail = ''
    content = getattr(exception, 'content', None)
    if isinstance(content, bytes):
        content = content.decode('utf-8', 'replace')
    if content:
        try:
            error = json.loads(content).get('error') or {}
        except ValueError:
            error = {}
        status = status or error.get('code')
        detail = str(error.get('message') or '')
    text = type(exception).__name__
    if status is not None:
        text += f' status={status}'
    if detail:
        text += f' message={scrub(detail)[:200]}'
    return text


def transient(exception):
    status = getattr(exception, 'status_code', None)
    if status is None:
        status = getattr(getattr(exception, 'resp', None), 'status', None)
    if status in TRANSIENT_STATUS:
        return True
    return isinstance(exception, TRANSIENT_TYPES)


def retry_read(call, attempts=RETRY_ATTEMPTS, base=RETRY_BASE_SECONDS, label='drive',
               reset=None, max_delay=RETRY_MAX_DELAY, sleep=None):
    """Retry a read-only call with bounded deterministic backoff.

    The `reset` hook must recreate a fresh transport so a known-broken session
    is never reused; it is invoked before every retry, never before a write.
    The original exception is re-raised once the budget is exhausted or the
    failure is deterministic and non-transient.
    """
    for attempt in range(1, attempts + 1):
        READ_RETRY_STATS['attempts'] += 1
        try:
            return call()
        except Exception as exception:
            if attempt == attempts or not transient(exception):
                READ_RETRY_STATS['failures'] += 1
                stage(f'{label} failed', reason(exception))
                raise
            READ_RETRY_STATS['retries'] += 1
            if reset is not None:
                reset()
                READ_RETRY_STATS['resets'] += 1
            delay = min(base * 2 ** (attempt - 1), max_delay)
            stage(f'{label} retry', f'{reason(exception)}; attempt {attempt}/{attempts} in {delay:.1f}s')
            (sleep or time.sleep)(delay)
