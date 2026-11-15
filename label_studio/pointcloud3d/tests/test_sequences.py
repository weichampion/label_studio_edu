"""Sequence (multi-frame) export: keyframe interpolation with ego poses, lifespans, tracking formats."""

import json
import math
import os

from pointcloud3d import formats

SAMPLES = os.path.join(os.path.dirname(__file__), '..', '..', 'core', 'static', 'samples', 'pointcloud')
SPEC = [{'control': 'box', 'object': 'pcd', 'data_key': 'frames', 'images_key': None}]
CAMERA = {
    'name': 'front',
    'url': 'front.png',
    'intrinsic': [620, 0, 480, 0, 620, 250, 0, 0, 1],
    'extrinsic': [0, -1, 0, 0, 0, 0, -1, -0.08, 1, 0, 0, -0.27, 0, 0, 0, 1],
}


def load(name):
    with open(os.path.join(SAMPLES, name), encoding='utf-8') as f:
        return json.load(f)


def sample_task():
    task = load('sequence_0001_tasks.json')[0]
    task['id'] = 5
    task['annotations'] = [{'id': 50, 'result': task['predictions'][0]['result']}]
    return task


def test_rigid_inverse_round_trip():
    yaw = 0.3
    pose = [math.cos(yaw), -math.sin(yaw), 0, 4, math.sin(yaw), math.cos(yaw), 0, -2, 0, 0, 1, 0.5, 0, 0, 0, 1]
    box = {'x': 1, 'y': 2, 'z': 3, 'length': 4, 'width': 2, 'height': 1, 'yaw': 0.1}
    back = formats.from_world(formats.to_world(box, pose), pose)
    for key in box:
        assert math.isclose(back[key], box[key], abs_tol=1e-9)
    assert math.isclose(formats.to_world(box, pose)['yaw'], 0.4)
    assert formats.normalize_pose(pose[:12]) == [float(v) for v in pose]
    assert formats.normalize_pose([1, 2]) is None


def test_interpolation_with_ego_poses_reproduces_every_frame():
    """Keyframes only at the first/last frame; ego drives a curve. World-space interpolation must be exact."""
    task = sample_task()
    truth = load('sequence_0001_truth.json')
    frames, is_sequence = formats.task_frames(task, 'frames', None)
    poses = [f['pose'] for f in frames]
    assert is_sequence and len(frames) == 8 and all(poses)
    items = formats.items_from_annotation(task['annotations'][0], {'box'})
    names = ['Car0', 'Car1', 'Car2', 'Truck3', 'Pedestrian4', 'Cyclist5']
    for item, name in zip(items, names):
        for f in range(8):
            box = formats.box_at(item['sequence'], f, poses)
            expected = truth[f].get(name)
            if expected is None:
                assert box is None, (name, f)
                continue
            x, y, length, width, height, yaw = expected
            assert box is not None, (name, f)
            assert math.isclose(box['x'], x, abs_tol=2e-3) and math.isclose(box['y'], y, abs_tol=2e-3), (name, f)
            assert math.isclose(box['length'], length) and math.isclose(box['height'], height)
            assert abs(formats.normalize_angle(box['yaw'] - yaw)) < 1e-4, (name, f)


def test_without_poses_interpolation_is_linear_and_hold_is_static():
    seq = [
        {'frame': 2, 'enabled': True, 'position': {'x': 0, 'y': 0, 'z': 0},
         'dimensions': {'length': 4, 'width': 2, 'height': 1.5}, 'rotation': {'z': 3.0}},
        {'frame': 6, 'enabled': True, 'position': {'x': 8, 'y': 4, 'z': 0},
         'dimensions': {'length': 4, 'width': 2, 'height': 1.5}, 'rotation': {'z': -3.0}},
        {'frame': 9, 'enabled': False, 'position': {'x': 8, 'y': 4, 'z': 0},
         'dimensions': {'length': 4, 'width': 2, 'height': 1.5}, 'rotation': {'z': -3.0}},
    ]
    assert formats.box_at(seq, 1) is None
    mid = formats.box_at(seq, 4)
    assert math.isclose(mid['x'], 4) and math.isclose(mid['y'], 2)
    # yaw goes the short way round through +-pi, not through 0
    assert abs(abs(mid['yaw']) - math.pi) < 0.3
    assert formats.box_at(seq, 8)['x'] == 8
    assert formats.box_at(seq, 9) is None
    assert formats.lifespans(seq, 12) == [(2, 8)]
    assert formats.lifespans(seq[:2], 12) == [(2, 11)]


def test_openpcdet_export_of_a_sequence(tmp_path):
    report = formats.convert_tasks([sample_task()], str(tmp_path), 'OPENPCDET_3D', SPEC)
    # 6 objects in 6 frames, the pedestrian has left for the last 2
    assert report['frames'] == 8 and report['boxes'] == 6 * 6 + 5 * 2 and report['tracks'] == 6
    assert sorted(os.listdir(tmp_path / 'labels')) == [f'{i:06d}.txt' for i in range(8)]
    tracks = json.loads((tmp_path / 'tracks.json').read_text())
    assert [t['track_id'] for t in tracks['000007']] == [0, 1, 2, 3, 5]
    assert tracks['000000'][4]['label'] == 'Pedestrian'
    mapping = json.loads((tmp_path / 'mapping.json').read_text())
    assert mapping[3]['sequence_frame'] == 3 and mapping[3]['pointcloud'].endswith('000003.pcd')


def test_kitti_tracking_export(tmp_path):
    task = sample_task()
    no_camera = formats.convert_tasks([task], str(tmp_path / 'a'), 'KITTI_TRACKING', SPEC)
    assert no_camera['skipped'] == [{'task': 5, 'reason': 'no camera calibration'}]

    for frame in task['data']['frames']:
        frame['cameras'] = [CAMERA]
    report = formats.convert_tasks([task], str(tmp_path / 'b'), 'KITTI_TRACKING', SPEC)
    out = tmp_path / 'b'
    assert report['tracks'] == 6 and report['boxes'] == 46
    rows = [r.split() for r in (out / 'label_02' / '0001.txt').read_text().splitlines()]
    assert len(rows) == 46 and all(len(r) == 17 for r in rows)
    assert {int(r[0]) for r in rows} == set(range(8))
    pedestrian = [int(r[0]) for r in rows if r[2] == 'Pedestrian']
    assert pedestrian == list(range(6)) and {r[1] for r in rows if r[2] == 'Pedestrian'} == {'4'}
    assert len((out / 'poses' / '0001.txt').read_text().splitlines()) == 8
    assert (out / 'calib' / '0001.txt').read_text().startswith('P0: ')
    assert (out / 'ImageSets' / 'train.txt').read_text().split() == ['0001']


def test_single_scan_counts_as_one_frame_sequence_for_tracking(tmp_path):
    task = load('scene_000001_tasks.json')[0]
    task['id'] = 1
    task['annotations'] = [{'id': 2, 'result': task['predictions'][0]['result']}]
    spec = [{'control': 'box', 'object': 'pcd', 'data_key': 'pointcloud', 'images_key': 'cameras'}]
    report = formats.convert_tasks([task], str(tmp_path), 'KITTI_TRACKING', spec)
    rows = (tmp_path / 'label_02' / '0000.txt').read_text().splitlines()
    assert report['boxes'] == 7 and len(rows) == 7
    assert [r.split()[:2] for r in rows[:2]] == [['0', '0'], ['0', '1']]
