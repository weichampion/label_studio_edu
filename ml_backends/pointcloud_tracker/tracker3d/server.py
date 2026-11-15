"""Label Studio ML backend HTTP API (standard library only).

Endpoints used by Label Studio: GET /health, POST /setup, POST /predict, GET /versions, POST /webhook
(START_TRAINING, and ANNOTATION_* when TRAIN_ON_ANNOTATIONS=1), POST /train, POST /job_status. Optional basic auth: BASIC_AUTH_USER / BASIC_AUTH_PASS (set the same in Label Studio's
"Connect model" dialog).
"""

import base64
import hmac
import json
import logging
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .model import PointCloudTracker

logger = logging.getLogger(__name__)
MAX_BODY = 64 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    model = None
    lock = threading.Lock()
    auth = None

    def log_message(self, fmt, *args):  # route to logging instead of stderr
        logger.info('%s - %s', self.address_string(), fmt % args)

    def _send(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        if not self.auth:
            return True
        header = self.headers.get('Authorization', '')
        expected = 'Basic ' + base64.b64encode(self.auth.encode()).decode()
        return hmac.compare_digest(header, expected)

    def _body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length > MAX_BODY:
            raise ValueError('Request too large')
        return json.loads(self.rfile.read(length) or b'{}')

    def do_GET(self):
        if not self._authorized():
            return self._send(401, {'error': 'Unauthorized'})
        path = self.path.split('?')[0].rstrip('/')
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if path in ('', '/health'):
            return self._send(200, {'status': 'UP', 'model_class': 'PointCloudTracker'})
        if path == '/versions':
            project = (query.get('project') or [None])[0]
            return self._send(200, {'versions': self.model.versions(project), 'message': ''})
        return self._send(404, {'error': 'Not found'})

    def do_POST(self):
        if not self._authorized():
            return self._send(401, {'error': 'Unauthorized'})
        path = self.path.split('?')[0].rstrip('/')
        try:
            data = self._body()
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, {'error': str(e)})
        try:
            if path == '/setup':
                self.model.setup(data.get('hostname'), data.get('access_token'), data.get('project'))
                return self._send(200, {'model_version': self.model.active(data.get('project'))[0]})
            if path == '/predict':
                params = data.get('params') or {}
                if params.get('hostname'):
                    self.model.setup(params.get('hostname'))
                with self.lock:  # the caches are shared; detection itself is CPU bound anyway
                    results = self.model.predict(
                        data.get('tasks') or [],
                        data.get('label_config') or '',
                        context=params.get('context'),
                        project=data.get('project'),
                    )
                version = results[0]['model_version'] if results else self.model.model_version
                return self._send(200, {'results': results, 'model_version': version})
            if path == '/train':
                # older Label Studio: annotated tasks are sent with the request
                job = self.model.train(data.get('project'), data.get('annotations') or [], data.get('label_config') or '')
                return self._send(201, {'job': job})
            if path == '/webhook':
                action = data.get('action')
                project = data.get('project') or {}
                train_on_annotations = self.model.env.get('TRAIN_ON_ANNOTATIONS', '0') == '1'
                if action == 'START_TRAINING' or (
                    train_on_annotations and action in ('ANNOTATION_CREATED', 'ANNOTATION_UPDATED', 'ANNOTATIONS_DELETED')
                ):
                    return self._send(201, {'job': self.model.train_from_label_studio(project)})
                return self._send(200, {'status': 'ignored', 'action': action})
            if path == '/job_status':
                job = self.model.jobs.get(data.get('job'))
                if job is None:
                    return self._send(404, {'error': 'Unknown job'})
                return self._send(200, {'job_status': job['status'], **job})
            if path == '/validate':
                return self._send(200, {})
        except Exception as e:  # report the problem to Label Studio instead of dropping the connection
            logger.exception('Request to %s failed', path)
            return self._send(500, {'error': str(e)})
        return self._send(404, {'error': 'Not found'})


def make_server(host='0.0.0.0', port=9090, env=None):
    env = dict(os.environ if env is None else env)
    handler = type('BoundHandler', (Handler,), {})
    handler.model = PointCloudTracker(env)
    user, password = env.get('BASIC_AUTH_USER'), env.get('BASIC_AUTH_PASS')
    handler.auth = f'{user}:{password}' if user and password else None
    return ThreadingHTTPServer((host, port), handler)


def main():
    logging.basicConfig(level=os.environ.get('LOG_LEVEL', 'INFO'))
    server = make_server(os.environ.get('HOST', '0.0.0.0'), int(os.environ.get('PORT', 9090)))
    logger.info('Point cloud tracker listening on %s:%s', *server.server_address[:2])
    server.serve_forever()


if __name__ == '__main__':
    main()
