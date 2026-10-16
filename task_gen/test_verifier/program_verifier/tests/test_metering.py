import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

import pytest

from task_gen.test_verifier.program_verifier.metering import MeteringRelay, token_counts


@pytest.mark.parametrize('stream', [False, True])
def test_records_wire_usage_once_and_never_auth(tmp_path, stream):
    usage = {'input_tokens': 20, 'output_tokens': 3, 'input_tokens_details': {'cached_tokens': 10}}
    response = {'id': 'response-1', 'model': 'gpt-6.1-sol', 'usage': usage}
    received = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            received.append((self.path, self.headers.get('Authorization'), json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            body = ('data: '+json.dumps({'type': 'response.completed', 'response': response})+'\n\ndata: [DONE]\n\n').encode() if stream else json.dumps(response).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream' if stream else 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers();self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever);thread.start()
    payload = {'model':'gpt-6.1-sol', 'reasoning': {'effort':'high'}, 'stream':stream}
    try:
        with MeteringRelay(f'http://127.0.0.1:{server.server_port}', tmp_path) as relay:
            request = Request(relay.base_url+'/responses', data=json.dumps(payload).encode(), headers={'Authorization':'Bearer secret-test-key', 'Content-Type':'application/json'})
            with urlopen(request) as result: assert result.read()
        records = [json.loads(p.read_text()) for p in tmp_path.glob('*.json')]
        assert len(records) == 1
        row = records[0]
        assert row['usage'] == usage and row['status_code'] == 200
        assert row['reasoning_effort'] == 'high' and row['seconds'] >= 0
        assert token_counts(row['usage']) == {'input':20,'cached_input':10,'output':3}
        assert received == [('/responses', 'Bearer secret-test-key', payload)]
        assert 'secret-test-key' not in next(tmp_path.glob('*.json')).read_text()
    finally:
        server.shutdown();server.server_close();thread.join()


def test_chat_usage_and_unknown_are_not_zero():
    assert token_counts({'prompt_tokens':12,'completion_tokens':4,'prompt_tokens_details':{'cached_tokens':5}}) == {'input':12,'cached_input':5,'output':4}
    assert token_counts(None) == {'input':None,'cached_input':None,'output':None}


def test_http_error_preserves_upstream_body_and_retry_delay(tmp_path):
    from urllib.error import HTTPError
    body = b'{"error":{"message":"temporarily unavailable","code":"busy"}}'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(503)
            self.send_header('Retry-After','7')
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever);thread.start()
    try:
        with MeteringRelay(f'http://127.0.0.1:{server.server_port}',tmp_path) as relay:
            with pytest.raises(HTTPError) as error:
                urlopen(Request(relay.base_url+'/responses',data=b'{"model":"gpt-6.1-sol"}'))
            assert error.value.code==503
            assert error.value.headers.get('Retry-After')=='7'
            assert error.value.read()==body
        row=json.loads(next(tmp_path.glob('*.json')).read_text())
        assert row['status']=='http_error' and row['api_error']['code']=='busy'
        assert row['usage'] is None
    finally:
        server.shutdown();server.server_close();thread.join()
