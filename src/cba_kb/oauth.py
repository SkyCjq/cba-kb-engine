"""Loopback OAuth receiver that ignores empty/unrelated browser connections."""
import time
import webbrowser
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server, WSGIRequestHandler
from wsgiref.util import request_uri


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass  # Callback URLs contain one-time authorization codes.


class Callback:
    def __init__(self):
        self.state = None
        self.response = None

    def __call__(self, environ, start_response):
        query = parse_qs(environ.get('QUERY_STRING', ''))
        valid = (environ.get('PATH_INFO') == '/' and self.state is not None
                 and query.get('state') == [self.state]
                 and bool(query.get('code') or query.get('error')))
        if valid:
            self.response = request_uri(environ)
            status, message = '200 OK', 'Google response received. Return to Codex for token verification.'
        else:
            status, message = '400 Bad Request', 'Waiting for a valid Google OAuth callback.'
        start_response(status, [('Content-Type', 'text/plain; charset=utf-8')])
        return [message.encode()]


def authorize(client, scopes):
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(client), scopes)
    callback = Callback()
    with make_server('127.0.0.1', 0, callback, handler_class=QuietHandler) as server:
        flow.redirect_uri = f'http://127.0.0.1:{server.server_port}/'
        url, callback.state = flow.authorization_url(access_type='offline', prompt='consent')
        if not webbrowser.open(url, new=1, autoraise=True):
            raise RuntimeError('Browser could not open Google authorization')
        deadline = time.monotonic() + 900
        server.timeout = 1
        while callback.response is None:
            if time.monotonic() >= deadline:
                raise RuntimeError('Google callback not received within 15 minutes; run make auth again')
            server.handle_request()
        flow.fetch_token(authorization_response=callback.response.replace('http://', 'https://', 1), timeout=60)
    return flow.credentials
