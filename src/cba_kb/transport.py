"""Sanitized read-only diagnostics with bounded retries.

Only read-only calls are retried. Writes stay single-attempt so that a lost
response is resumed through the release journal instead of repeated blindly.
"""
import json
import http.client
import re
import socket
import sys
import time
from datetime import datetime, timezone

URL_QUERY = re.compile(r'(https?://[^\s?]+)\?[^\s]+')
SECRET = re.compile(r'(access_token|refresh_token|id_token|client_secret|key|sig|signature)=[^\s&]+')
TRANSIENT_STATUS = (408, 429, 500, 502, 503, 504)
TRANSIENT_TYPES = (TimeoutError, ConnectionError, socket.timeout, socket.gaierror,
                   http.client.IncompleteRead, http.client.RemoteDisconnected)


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


def retry_read(call, attempts=4, base=0.5, label='drive', reset=None):
    """Retry a read-only call; the original exception is re-raised when it gives up."""
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as exception:
            if attempt == attempts or not transient(exception):
                stage(f'{label} failed', reason(exception))
                raise
            if reset is not None:
                reset()
            delay = base * 2 ** (attempt - 1)
            stage(f'{label} retry', f'{reason(exception)}; attempt {attempt}/{attempts} in {delay:.1f}s')
            time.sleep(delay)
