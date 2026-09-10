from cba_kb.oauth import Callback


def test_callback_ignores_unrelated_and_wrong_state():
    app=Callback();app.state='expected'
    statuses=[]
    def response(status, headers):statuses.append(status)
    base={'PATH_INFO':'/', 'wsgi.url_scheme':'http', 'SERVER_NAME':'127.0.0.1',
          'SERVER_PORT':'1234', 'SCRIPT_NAME':''}
    for query in ('', 'code=test&state=wrong', 'state=expected'):
        app(dict(base,QUERY_STRING=query),response)
        assert app.response is None
    app(dict(base,QUERY_STRING='code=test&state=expected'),response)
    assert app.response.endswith('?code=test&state=expected')
    assert statuses==['400 Bad Request']*3+['200 OK']
