"""Training on project annotations: learned cluster detector, per-project isolation, HTTP flow, OpenPCDet jobs."""

import ast
import copy
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tracker3d.geometry import box_at, value_from_box
from tracker3d.model import PointCloudTracker, parse_label_config
from tracker3d.server import make_server
from tracker3d.training import BACKGROUND, KNNClassifier, evaluate, examples_from_tasks, write_npy

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
STATIC = os.path.join(REPO, 'label_studio', 'core', 'static') + os.sep
SAMPLES = os.path.join(STATIC, 'samples', 'pointcloud')
ZH = {'Car': '小汽车', 'Truck': '卡车', 'Pedestrian': '行人', 'Cyclist': '骑行者'}
CONFIG = (
    '<View><Labels name="label" toName="pcd">'
    + ''.join(f'<Label value="{v}"/>' for v in ZH.values())
    + '</Labels><PointCloud name="pcd" value="$frames"/><Cuboid name="box" toName="pcd"/></View>'
)


def sequence_task():
    with open(os.path.join(SAMPLES, 'sequence_0001_tasks.json'), encoding='utf-8') as f:
        task = json.load(f)[0]
    task['id'] = 1
    return task


def scan_tasks(frames, base_id):
    """Single-scan tasks (one frame each) annotated with Chinese labels from the sample tracks."""
    seq = sequence_task()
    poses = [f['pose'] for f in seq['data']['frames']]
    tasks = []
    for f in frames:
        result = []
        for r in seq['predictions'][0]['result']:
            b = box_at(r['value']['sequence'], f, poses)
            if b:
                result.append({'id': f'{r["id"]}-{f}', 'from_name': 'box', 'to_name': 'pcd', 'type': 'cuboid',
                               'value': {**value_from_box(b), 'labels': [ZH[r['value']['labels'][0]]]}})
        tasks.append({'id': base_id + f, 'data': {'frames': [seq['data']['frames'][f]['pointcloud']]},
                      'annotations': [{'id': base_id + f, 'result': result}]})
    return tasks


def tracker(model_dir, **env):
    return PointCloudTracker({'LOCAL_ROOTS': '/static/=' + STATIC, 'MODEL_DIR': model_dir, **env})


def count(model, tasks, project):
    return sum(len(model.predict([t], CONFIG, project=project)[0]['result']) for t in tasks)


def test_sequence_annotations_give_one_example_per_frame():
    task = sequence_task()
    task['annotations'] = [{'id': 1, 'result': task['predictions'][0]['result']}]
    spec = parse_label_config(CONFIG.replace('小汽车', 'Car'))
    examples = examples_from_tasks([task], spec)
    assert [len(e['boxes']) for e in examples] == [6, 6, 6, 6, 6, 6, 5, 5]
    assert examples[0]['url'].endswith('000000.pcd')
    task['annotations'][0]['was_cancelled'] = True
    assert examples_from_tasks([task], spec) == []


def test_knn_and_npy():
    X = [[0, 0], [0.1, 0], [0, 0.1], [5, 5], [5.1, 5], [5, 5.1]]
    y = ['a', 'a', 'a', BACKGROUND, BACKGROUND, BACKGROUND]
    knn = KNNClassifier(k=3).fit(X, y)
    assert knn.predict([0.05, 0.05])[0] == 'a'
    assert knn.predict([5, 5])[0] is None
    assert KNNClassifier.from_dict(json.loads(json.dumps(knn.to_dict()))).predict([0, 0])[0] == 'a'
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'p.npy')
        write_npy(path, [(1, 2, 3, 4), (5, 6, 7, 8)])
        data = open(path, 'rb').read()
        header_len = int.from_bytes(data[8:10], 'little')
        header = ast.literal_eval(data[10 : 10 + header_len].decode('latin1'))
        assert data[:6] == b'\x93NUMPY' and (10 + header_len) % 64 == 0
        assert header == {'descr': '<f4', 'fortran_order': False, 'shape': (2, 4)}
        assert len(data) == 10 + header_len + 2 * 4 * 4


def test_training_learns_project_labels_and_stays_in_the_project():
    train, held_out = scan_tasks(range(0, 5), 100), scan_tasks(range(5, 8), 200)
    with tempfile.TemporaryDirectory() as d:
        model = tracker(d)
        # the built-in rules only know English classes: nothing matches this project's labels
        assert count(model, held_out, '7.1') == 0
        job = model.jobs.wait(model.train('7.1', train, CONFIG))
        assert job['status'] == 'completed', job
        assert job['metrics']['samples'][BACKGROUND] > 0
        info = model.registry.current('7')
        metrics = evaluate(info['model'], examples_from_tasks(held_out, parse_label_config(CONFIG)), model.load)
        assert metrics['recall'] >= 0.8 and metrics['precision'] >= 0.6, metrics
        predictions = model.predict(held_out, CONFIG, project='7.1')
        labels = {r['value']['labels'][0] for p in predictions for r in p['result']}
        assert labels <= set(ZH.values()) and {'小汽车', '卡车'} <= labels
        assert all('-p7-' in p['model_version'] for p in predictions)
        # another project (another college) keeps the base model
        assert count(model, held_out, '8.1') == 0
        # the trained model survives a restart
        restarted = tracker(d)
        assert restarted.active('7.1')[0] == info['version']
        assert count(restarted, held_out, '7.1') == count(model, held_out, '7.1')


def test_training_without_annotations_fails_cleanly():
    with tempfile.TemporaryDirectory() as d:
        model = tracker(d)
        tasks = scan_tasks([0], 100)
        tasks[0]['annotations'] = []
        job = model.jobs.wait(model.train('7.1', tasks, CONFIG))
        assert job['status'] == 'failed' and 'No annotated' in job['error']
        assert model.registry.current('7') is None


class FakeLabelStudio(BaseHTTPRequestHandler):
    tasks = []
    token = 'ls-token'

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.headers.get('Authorization') != f'Token {self.token}' or not self.path.startswith('/api/projects/9/export'):
            self.send_response(403)
            self.end_headers()
            return
        body = json.dumps(self.tasks).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def call(url, path, body=None):
    req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, json.loads(r.read())


def test_http_training_flow_and_start_training_webhook():
    FakeLabelStudio.tasks = scan_tasks(range(0, 5), 100)
    ls = ThreadingHTTPServer(('127.0.0.1', 0), FakeLabelStudio)
    threading.Thread(target=ls.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory() as d:
        server = make_server('127.0.0.1', 0, {'LOCAL_ROOTS': '/static/=' + STATIC, 'MODEL_DIR': d,
                                              'LABEL_STUDIO_URL': f'http://127.0.0.1:{ls.server_address[1]}',
                                              'LABEL_STUDIO_API_KEY': 'ls-token'})
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f'http://127.0.0.1:{server.server_address[1]}'
        try:
            # legacy /train with the annotations in the request
            status, body = call(url, '/train', {'project': '7.1700000000', 'label_config': CONFIG,
                                                'annotations': FakeLabelStudio.tasks})
            assert status == 201
            job = body['job']
            for _ in range(600):
                status, state = call(url, '/job_status', {'job': job})
                if state['job_status'] in ('completed', 'failed'):
                    break
                time.sleep(0.05)
            assert state['job_status'] == 'completed', state
            _, versions = call(url, '/versions?project=7.1700000000')
            assert versions['versions'][-1] == state['model_version']
            _, predicted = call(url, '/predict', {'tasks': scan_tasks([6], 300), 'label_config': CONFIG,
                                                  'project': '7.1700000000'})
            assert predicted['model_version'] == state['model_version'] and predicted['results'][0]['result']

            # current Label Studio: START_TRAINING webhook, annotations downloaded from the export API
            status, body = call(url, '/webhook', {'action': 'START_TRAINING',
                                                  'project': {'id': 9, 'label_config': CONFIG}})
            assert status == 201
            job = server.RequestHandlerClass.model.jobs.wait(body['job'])
            assert job['status'] == 'completed' and job['project'] == '9', job
            assert call(url, '/webhook', {'action': 'ANNOTATION_CREATED', 'project': {'id': 9}})[1]['status'] == 'ignored'
        finally:
            server.shutdown()
            ls.shutdown()


def test_openpcdet_fine_tuning_job_with_stub_commands():
    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, 'OpenPCDet')
        os.makedirs(os.path.join(root, 'tools'))
        prepare = f'"{sys.executable}" -c "import sys; open(\'prepared.txt\', \'w\').write(sys.argv[1])" {{dataset_cfg}}'
        train = (
            f'"{sys.executable}" -c "import os, sys; p = os.path.join(\'..\', \'output\', \'custom\', sys.argv[1], \'ckpt\'); '
            f'os.makedirs(p); open(os.path.join(p, \'checkpoint_epoch_10.pth\'), \'w\').write(sys.argv[2])" {{tag}} {{ckpt}}'
        )
        model = tracker(os.path.join(d, 'models'), PCDET_ROOT=root, PCDET_CFG='base.yaml', PCDET_CKPT='base.pth',
                        PCDET_PREPARE_CMD=prepare, PCDET_TRAIN_CMD=train)
        job = model.jobs.wait(model.train('7.1', scan_tasks(range(0, 5), 100), CONFIG))
        assert job['status'] == 'completed', job
        info = model.registry.current('7')
        assert info['kind'] == 'openpcdet' and os.path.exists(info['checkpoint'])
        assert open(info['checkpoint']).read() == 'base.pth'  # fine-tuned from the configured checkpoint
        work = os.path.dirname(info['model_cfg'])
        dataset = os.path.join(work, 'dataset')
        assert len(os.listdir(os.path.join(dataset, 'points'))) == 5
        assert open(os.path.join(dataset, 'ImageSets', 'train.txt')).read().split()
        label_line = open(os.path.join(dataset, 'labels', sorted(os.listdir(os.path.join(dataset, 'labels')))[0]),
                          encoding='utf-8').readline().split()
        assert len(label_line) == 8 and label_line[-1] in ZH.values()
        model_cfg = open(info['model_cfg'], encoding='utf-8').read()
        assert '{{' not in model_cfg and 'anchor_sizes' in model_cfg and json.dumps(sorted(ZH.values())) in model_cfg
        assert open(os.path.join(root, 'tools', 'prepared.txt')).read().endswith('dataset.yaml')
        # the next training continues from the project's own checkpoint
        job = model.jobs.wait(model.train('7.1', scan_tasks(range(0, 5), 100), CONFIG))
        assert open(model.registry.current('7')['checkpoint']).read() == info['checkpoint']


def test_each_project_downloads_with_its_own_token():
    from tracker3d import model as model_module

    model = PointCloudTracker({'MODEL_DIR': tempfile.mkdtemp()})
    model.setup('http://ls:8080', 'token-college-a', project='7.1700000000')
    model.setup('http://ls:8080', 'token-college-b', project='8.1700000000')
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'VERSION .7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\nPOINTS 1\nDATA ascii\n1 2 3\n'

    original = model_module.urllib.request.urlopen
    model_module.urllib.request.urlopen = lambda req, timeout=None: seen.append(req) or Response()
    try:
        model.load('/data/upload/7/a.pcd', 1, project='7.1700000000')
        model.load('/data/upload/7/a.pcd', 1, project='8.1700000000')  # same URL, other project: not shared
    finally:
        model_module.urllib.request.urlopen = original
    assert [r.get_header('Authorization') for r in seen] == ['Token token-college-a', 'Token token-college-b']
