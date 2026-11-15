"""Tests on the synthetic 8-frame sequence shipped with Label Studio (ground truth for every frame)."""

import base64
import copy
import json
import math
import os
import threading
import urllib.error
import urllib.request

from tracker3d import model as model_module
from tracker3d.detector import ClusterDetector, min_area_rect
from tracker3d.geometry import box_at
from tracker3d.model import PointCloudTracker, parse_label_config
from tracker3d.pcd_io import lzf_decompress, parse_pcd
from tracker3d.server import make_server
from tracker3d.tracker import simplify

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
STATIC = os.path.join(REPO, 'label_studio', 'core', 'static') + os.sep
SAMPLES = os.path.join(STATIC, 'samples', 'pointcloud')
CONFIG = (
    '<View><Labels name="label" toName="pcd"><Label value="Car"/><Label value="Truck"/>'
    '<Label value="Pedestrian"/><Label value="Cyclist"/></Labels>'
    '<PointCloud name="pcd" value="$frames"/><Cuboid name="box" toName="pcd"/></View>'
)
NAMES = ['Car0', 'Car1', 'Car2', 'Truck3', 'Pedestrian4', 'Cyclist5']


def sample():
    with open(os.path.join(SAMPLES, 'sequence_0001_tasks.json'), encoding='utf-8') as f:
        task = json.load(f)[0]
    with open(os.path.join(SAMPLES, 'sequence_0001_truth.json'), encoding='utf-8') as f:
        truth = json.load(f)
    task['id'] = 1
    return task, truth, [fr['pose'] for fr in task['data']['frames']]


def tracker():
    return PointCloudTracker({'LOCAL_ROOTS': '/static/=' + STATIC})


def errors(sequence, poses, truth, name):
    out = []
    for f in range(len(poses)):
        b, t = box_at(sequence, f, poses), truth[f].get(name)
        out.append(None if b is None and t is None else (math.hypot(b['x'] - t[0], b['y'] - t[1]) if b and t else 'mismatch'))
    return out


def test_pcd_reader_and_lzf():
    with open(os.path.join(SAMPLES, 'sequence_0001', '000000.pcd'), 'rb') as f:
        data = f.read()
    points = parse_pcd(data)
    assert len(points) == int(data.split(b'POINTS ')[1].split()[0])
    assert 0 <= points[0][3] <= 255
    ascii_pcd = b'VERSION .7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\nPOINTS 2\nDATA ascii\n1 2 3\nnan nan nan\n'
    assert parse_pcd(ascii_pcd) == [(1.0, 2.0, 3.0, 0.0)]
    assert lzf_decompress(bytes([2, 97, 98, 99, 32, 2]), 6) == b'abcabc'


def test_min_area_rect():
    pts = [(math.cos(0.3) * x - math.sin(0.3) * y, math.sin(0.3) * x + math.cos(0.3) * y)
           for x in (-2, -1, 0, 1, 2) for y in (-0.9, 0, 0.9)]
    cx, cy, length, width, yaw = min_area_rect(pts)
    assert abs(length - 4) < 0.05 and abs(width - 1.8) < 0.05
    assert abs(math.sin(yaw - 0.3)) < 0.03 and abs(cx) < 1e-6 and abs(cy) < 1e-6


def test_detector_finds_near_objects_without_false_positives():
    task, truth, _ = sample()
    with open(os.path.join(SAMPLES, 'sequence_0001', '000002.pcd'), 'rb') as f:
        detections = ClusterDetector().detect(parse_pcd(f.read()))
    for name, expected in (('Car0', 'Car'), ('Truck3', 'Truck'), ('Pedestrian4', 'Pedestrian')):
        x, y = truth[2][name][:2]
        best = min(detections, key=lambda d: math.hypot(d['box']['x'] - x, d['box']['y'] - y))
        assert best['label'] == expected and math.hypot(best['box']['x'] - x, best['box']['y'] - y) < 1.0
    for d in detections:
        assert min(math.hypot(d['box']['x'] - v[0], d['box']['y'] - v[1]) for v in truth[2].values()) < 2.5


def test_sequence_is_tracked_into_keyframed_tracks():
    task, truth, poses = sample()
    prediction = tracker().predict([task], CONFIG)[0]
    results = prediction['result']
    assert len(results) == 6 and prediction['model_version'].startswith('pointcloud-tracker-')
    matched = {}
    for r in results:
        assert r['type'] == 'cuboid' and r['from_name'] == 'box' and r['to_name'] == 'pcd'
        seq = r['value']['sequence']
        first = next(k for k in seq if k['enabled'])
        b = box_at(seq, first['frame'], poses)
        name = min(NAMES, key=lambda n: math.hypot(b['x'] - truth[first['frame']][n][0], b['y'] - truth[first['frame']][n][1]))
        matched[name] = r
    assert set(matched) == set(NAMES)
    assert {n: matched[n]['value']['labels'][0] for n in NAMES} == {
        'Car0': 'Car', 'Car1': 'Car', 'Car2': 'Car', 'Truck3': 'Truck', 'Pedestrian4': 'Pedestrian', 'Cyclist5': 'Cyclist',
    }
    for name in ('Car0', 'Car1', 'Truck3', 'Pedestrian4', 'Cyclist5'):
        errs = [e for e in errors(matched[name]['value']['sequence'], poses, truth, name) if e is not None]
        # a far object may be picked up a couple of frames late, but is never lost once tracked
        late = next(i for i, e in enumerate(errs) if e != 'mismatch')
        assert late <= 2 and 'mismatch' not in errs[late:], (name, errs)
        assert max(errs[late:]) < 0.5, (name, errs)
    # the pedestrian walks away: the track ends after frame 5
    assert box_at(matched['Pedestrian4']['value']['sequence'], 6, poses) is None
    # keyframes are few: straight motion needs only the ends
    assert len([k for k in matched['Car0']['value']['sequence'] if k['enabled']]) == 2


def track_request(task, index, keep=1):
    region = copy.deepcopy(task['predictions'][0]['result'][index])
    region['value']['sequence'] = region['value']['sequence'][:keep]
    return {'pointcloud_track': {'region': region}}


def test_interactive_extension_follows_moving_objects_and_keeps_user_keyframes():
    task, truth, poses = sample()
    model = tracker()
    for index, name in ((0, 'Car0'), (5, 'Cyclist5'), (1, 'Car1')):
        context = track_request(task, index)
        out = model.predict([task], CONFIG, context=context)[0]
        seq = out['result'][0]['value']['sequence']
        assert seq[0] == context['pointcloud_track']['region']['value']['sequence'][0]
        assert out['result'][0]['id'] == context['pointcloud_track']['region']['id']
        errs = errors(seq, poses, truth, name)
        assert max(errs) < 0.3, (name, errs)
    pedestrian = model.predict([task], CONFIG, context=track_request(task, 4))[0]['result'][0]['value']['sequence']
    assert box_at(pedestrian, 5, poses) is not None and box_at(pedestrian, 6, poses) is None


def test_static_object_is_held_and_user_end_of_track_is_respected():
    task, truth, poses = sample()
    model = tracker()
    truck = model.predict([task], CONFIG, context=track_request(task, 3))[0]
    assert truck['meta']['static'] is True
    assert max(errors(truck['result'][0]['value']['sequence'], poses, truth, 'Truck3')) < 0.05

    context = track_request(task, 0)
    first = context['pointcloud_track']['region']['value']['sequence'][0]
    context['pointcloud_track']['region']['value']['sequence'].append({**first, 'frame': 4, 'enabled': False})
    seq = model.predict([task], CONFIG, context=context)[0]['result'][0]['value']['sequence']
    assert all(k['frame'] < 4 or not k['enabled'] for k in seq)
    assert box_at(seq, 3, poses) is not None and box_at(seq, 4, poses) is None


def test_simplify_keeps_only_needed_keyframes():
    line = {f: {'x': 2.0 * f, 'y': 0.0, 'z': 0, 'length': 4, 'width': 2, 'height': 1.5, 'yaw': 0.0} for f in range(10)}
    assert simplify(line) == [0, 9]
    turn = dict(line)
    turn.update({f: {**line[f], 'y': 1.5 * (f - 5)} for f in range(6, 10)})
    assert 5 in simplify(turn) and len(simplify(turn)) == 3
    assert 3 in simplify(line, keep=[3])


def test_label_config_parsing_and_label_mapping():
    spec = parse_label_config(CONFIG)
    assert spec == {'cuboid': 'box', 'cloud': 'pcd', 'data_key': 'frames', 'bin_dims': 4,
                    'labels': ['Car', 'Truck', 'Pedestrian', 'Cyclist']}
    model = PointCloudTracker({'LABEL_MAP': 'Truck:Car'})
    assert model.to_project_label('Truck', ['car']) == 'car'
    assert model.to_project_label('Tram', ['Car']) is None
    assert parse_label_config('<View><Image name="i" value="$i"/></View>') is None


def test_downloads_are_confined_and_token_stays_with_label_studio(monkeypatch=None):
    model = PointCloudTracker({'LOCAL_ROOTS': '/static/=' + STATIC, 'LABEL_STUDIO_URL': 'http://ls:8080',
                               'LABEL_STUDIO_API_KEY': 'secret'})
    try:
        model._read_url('/static/../../../../etc/passwd')
        raise AssertionError('path traversal was not refused')
    except ValueError:
        pass
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'ok'

    original = model_module.urllib.request.urlopen
    model_module.urllib.request.urlopen = lambda req, timeout=None: seen.append(req) or Response()
    try:
        model._read_url('/data/upload/1/a.pcd')
        model._read_url('https://bucket.s3.amazonaws.com/a.pcd?X-Amz-Signature=1')
        model._read_url('s3://bucket/a.pcd', task_id=7)
    finally:
        model_module.urllib.request.urlopen = original
    assert seen[0].full_url == 'http://ls:8080/data/upload/1/a.pcd' and seen[0].get_header('Authorization') == 'Token secret'
    assert seen[1].get_header('Authorization') is None
    fileuri = base64.urlsafe_b64encode(b's3://bucket/a.pcd').decode()
    assert seen[2].full_url == f'http://ls:8080/tasks/7/resolve/?fileuri={fileuri}'


def test_http_protocol():
    server = make_server('127.0.0.1', 0, {'LOCAL_ROOTS': '/static/=' + STATIC,
                                          'BASIC_AUTH_USER': 'ls', 'BASIC_AUTH_PASS': 'pw'})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_address[1]}'
    auth = {'Authorization': 'Basic ' + base64.b64encode(b'ls:pw').decode(), 'Content-Type': 'application/json'}

    def call(path, body=None, headers=auth):
        req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())

    try:
        try:
            call('/health', headers={})
            raise AssertionError('missing auth accepted')
        except urllib.error.HTTPError as e:
            assert e.code == 401
        assert call('/health')[1]['status'] == 'UP'
        assert call('/setup', {'hostname': 'http://ls:8080', 'access_token': 't'})[1]['model_version']
        task, _, _ = sample()
        status, body = call('/predict', {'tasks': [task], 'label_config': CONFIG, 'params': {'context': None}})
        assert status == 200 and len(body['results']) == 1 and len(body['results'][0]['result']) == 6
        status, body = call('/predict', {'tasks': [task], 'label_config': CONFIG,
                                         'params': {'context': track_request(task, 0)}})
        assert body['results'][0]['result'][0]['id'] == task['predictions'][0]['result'][0]['id']
    finally:
        server.shutdown()
