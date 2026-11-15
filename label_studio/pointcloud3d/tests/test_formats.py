"""Tests for the 3D export formats. Pure functions: no database needed."""

import json
import math
import os

from pointcloud3d import formats

SAMPLE_TASKS = os.path.join(
    os.path.dirname(__file__), '..', '..', 'core', 'static', 'samples', 'pointcloud', 'scene_000001_tasks.json'
)

CONFIG = (
    '<View>'
    '<Labels name="label" toName="pcd"><Label value="Car"/><Label value="Pedestrian"/></Labels>'
    '<PointCloud name="pcd" value="$pointcloud" images="$cameras"/>'
    '<Cuboid name="box" toName="pcd"/>'
    '<Choices name="occlusion" toName="pcd" perRegion="true"><Choice value="0 - Fully visible"/>'
    '<Choice value="2 - Largely occluded"/></Choices>'
    '</View>'
)

# KITTI-style front camera: x right, y down, z forward
EXTRINSIC = [0, -1, 0, 0, 0, 0, -1, -0.08, 1, 0, 0, -0.27, 0, 0, 0, 1]
INTRINSIC = [620, 0, 480, 0, 620, 250, 0, 0, 1]


def cuboid(rid, x, y, z, l, w, h, yaw, label):
    return {
        'id': rid,
        'from_name': 'box',
        'to_name': 'pcd',
        'type': 'cuboid',
        'value': {
            'position': {'x': x, 'y': y, 'z': z},
            'dimensions': {'length': l, 'width': w, 'height': h},
            'rotation': {'x': 0, 'y': 0, 'z': yaw},
            'labels': [label],
        },
    }


def sample_tasks():
    with open(SAMPLE_TASKS, encoding='utf-8') as f:
        tasks = json.load(f)
    for i, task in enumerate(tasks):
        task['id'] = i + 1
        task['annotations'] = [{'id': 10 + i, 'result': task['predictions'][0]['result']}]
    return tasks


def test_spec_reads_cuboid_and_camera_keys():
    spec = formats.pointcloud_spec(CONFIG)
    assert spec == [
        {'control': 'box', 'object': 'pcd', 'data_key': 'pointcloud', 'images_key': 'cameras', 'attributes': {}}
    ]
    assert formats.pointcloud_spec('<View><Image name="i" value="$i"/></View>') == []
    assert formats.pointcloud_spec('not xml') == []


def test_spec_from_parsed_config():
    parsed = {
        'box': {'type': 'Cuboid', 'to_name': ['pcd'], 'inputs': [{'type': 'PointCloud', 'value': 'pointcloud'}]},
        'label': {'type': 'Labels', 'to_name': ['pcd'], 'inputs': [{'type': 'PointCloud', 'value': 'pointcloud'}]},
    }
    assert formats.spec_from_parsed_config(parsed) == [
        {'control': 'box', 'object': 'pcd', 'data_key': 'pointcloud', 'images_key': None, 'attributes': {}}
    ]


def test_normalize_angle():
    assert math.isclose(formats.normalize_angle(3 * math.pi), math.pi)
    assert math.isclose(formats.normalize_angle(-math.pi), math.pi)
    assert math.isclose(formats.normalize_angle(-0.5), -0.5)


def test_box_corners_follow_heading():
    box = {'x': 1, 'y': 2, 'z': 0, 'length': 4, 'width': 2, 'height': 2, 'yaw': math.pi / 2}
    corners = formats.box_corners(box)
    # front-left bottom corner of a box heading along +y: front is +y, left is -x
    assert all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(corners[0], (0, 4, -1)))
    assert all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(corners[6], (2, 0, 1)))


def test_calibration_generic_and_kitti_forms():
    generic = formats.normalize_calibration({'intrinsic': INTRINSIC, 'extrinsic': EXTRINSIC})
    assert generic['intrinsic'] == [float(v) for v in INTRINSIC] and len(generic['extrinsic']) == 16
    kitti = formats.normalize_calibration(
        {
            'P2': ' '.join(str(v) for v in [620, 0, 480, 0, 0, 620, 250, 0, 0, 0, 1, 0]),
            'R0_rect': [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
            'Tr_velo_to_cam': EXTRINSIC[:12],
        }
    )
    assert len(kitti['intrinsic']) == 12
    assert [round(v, 6) for v in kitti['extrinsic']] == [float(v) for v in EXTRINSIC]
    assert formats.normalize_calibration({'intrinsic': [1, 2]}) is None
    assert formats.normalize_calibration(None) is None


def test_parse_cameras_shapes():
    cams = formats.parse_cameras({'front': {'url': 'a.png', 'intrinsic': INTRINSIC, 'extrinsic': EXTRINSIC}})
    assert cams[0]['name'] == 'front' and cams[0]['calibration'] is not None
    assert formats.parse_cameras('b.png') == [{'url': 'b.png', 'name': 'camera', 'calibration': None}]
    assert formats.parse_cameras(json.dumps([{'image': 'c.png'}]))[0]['url'] == 'c.png'
    assert formats.parse_cameras(None) == []


def test_kitti_conversion_matches_openpcdet_convention():
    calib = formats.normalize_calibration({'intrinsic': INTRINSIC, 'extrinsic': EXTRINSIC})
    for yaw in (0.0, 0.7, math.pi / 2, -2.5):
        box = {'x': 12, 'y': -2, 'z': -0.95, 'length': 4.4, 'width': 1.8, 'height': 1.5, 'yaw': yaw}
        cam = formats.to_kitti_camera(box, calib)
        # with the standard velodyne->camera rotation, rotation_y = -yaw - pi/2
        expected = formats.normalize_angle(-yaw - math.pi / 2)
        assert math.isclose(math.cos(cam['rotation_y']), math.cos(expected), abs_tol=1e-9)
        assert math.isclose(math.sin(cam['rotation_y']), math.sin(expected), abs_tol=1e-9)
        # location is the bottom centre: lidar z - h/2 = -1.7 -> camera y = 1.7 - 0.08
        loc = cam['location']
        assert math.isclose(loc[0], 2.0) and math.isclose(loc[1], 1.7 - 0.08) and math.isclose(loc[2], 12 - 0.27)
        expected_alpha = formats.normalize_angle(cam['rotation_y'] - math.atan2(loc[0], loc[2]))
        assert math.isclose(cam['alpha'], expected_alpha)
        x1, y1, x2, y2 = cam['bbox']
        assert x1 < 480 + 620 * 2 / 11.73 < x2 and y1 < y2


def test_box_behind_camera_has_no_2d_box():
    calib = formats.normalize_calibration({'intrinsic': INTRINSIC, 'extrinsic': EXTRINSIC})
    box = {'x': -10, 'y': 0, 'z': -1, 'length': 4, 'width': 2, 'height': 1.5, 'yaw': 0}
    cam = formats.to_kitti_camera(box, calib)
    assert cam['bbox'] is None and not cam['in_front']


def test_pick_annotation_prefers_ground_truth_and_skips_cancelled():
    task = {
        'annotations': [
            {'id': 1, 'updated_at': '2026-01-03', 'result': []},
            {'id': 2, 'updated_at': '2026-01-01', 'ground_truth': True, 'result': []},
            {'id': 3, 'updated_at': '2026-01-05', 'was_cancelled': True, 'result': []},
        ]
    }
    assert formats.pick_annotation(task)['id'] == 2
    task['annotations'][1]['ground_truth'] = False
    assert formats.pick_annotation(task)['id'] == 1
    assert formats.pick_annotation({'annotations': []}) is None


def test_boxes_collect_per_region_attributes_and_split_labels():
    annotation = {
        'result': [
            cuboid('a', 1, 2, 3, 4, 2, 1.5, 0.1, 'Car'),
            {'id': 'a', 'from_name': 'occlusion', 'to_name': 'pcd', 'type': 'choices',
             'value': {'choices': ['2 - Largely occluded']}},
            {**cuboid('b', 0, 0, 0, 1, 1, 1, 0, 'x'), 'value': {
                'position': {'x': 0, 'y': 0, 'z': 0}, 'dimensions': {'length': 1, 'width': 1, 'height': 1},
                'rotation': {'z': 0}}},
            {'id': 'b', 'from_name': 'label', 'to_name': 'pcd', 'type': 'labels', 'value': {'labels': ['Pedestrian']}},
            {'id': 'c', 'from_name': 'other', 'to_name': 'pcd', 'type': 'cuboid', 'value': {}},
        ]
    }
    boxes = formats.boxes_from_annotation(annotation, {'box'})
    assert [b['label'] for b in boxes] == ['Car', 'Pedestrian']
    assert boxes[0]['attributes'] == {'occlusion': ['2 - Largely occluded']}
    calib = formats.normalize_calibration({'intrinsic': INTRINSIC, 'extrinsic': EXTRINSIC})
    line = formats.kitti_line(boxes[0], calib).split()
    assert len(line) == 15 and line[0] == 'Car' and line[2] == '2'


def test_openpcdet_line():
    item = {'box': formats.box_from_value(cuboid('a', 1, 2, 3, 4, 2, 1.5, 0.1, 'Traffic cone')['value']),
            'label': 'Traffic cone'}
    assert formats.openpcdet_line(item) == '1.0000 2.0000 3.0000 4.0000 2.0000 1.5000 0.1000 Traffic_cone'


def test_convert_sample_scene_openpcdet(tmp_path):
    report = formats.convert_tasks(sample_tasks(), str(tmp_path), 'OPENPCDET_3D', formats.pointcloud_spec(CONFIG))
    assert report['frames'] == 1 and report['boxes'] == 7 and report['skipped'] == []
    lines = (tmp_path / 'labels' / 'scene_000001.txt').read_text().splitlines()
    assert len(lines) == 7 and lines[0].endswith(' Car')
    assert (tmp_path / 'ImageSets' / 'train.txt').read_text().split() == ['scene_000001']
    mapping = json.loads((tmp_path / 'mapping.json').read_text())
    assert mapping[0]['task'] == 1 and mapping[0]['pointcloud'].endswith('scene_000001.pcd')


def test_convert_sample_scene_kitti(tmp_path):
    tasks = sample_tasks()
    tasks.append({'id': 99, 'data': {'pointcloud': '/data/x.pcd'}, 'annotations': [{'id': 5, 'result': []}]})
    report = formats.convert_tasks(tasks, str(tmp_path), 'KITTI_3D', formats.pointcloud_spec(CONFIG))
    assert report['frames'] == 1 and report['skipped'] == [{'task': 99, 'reason': 'no camera calibration'}]
    rows = [r.split() for r in (tmp_path / 'label_2' / 'scene_000001.txt').read_text().splitlines()]
    assert len(rows) == 7 and all(len(r) == 15 for r in rows)
    first = rows[0]
    # the first car is 12 m ahead, 2.2 m right: camera x = 2.2 + 0, z = 12 - 0.27
    assert math.isclose(float(first[11]), 2.2, abs_tol=0.01) and math.isclose(float(first[13]), 11.73, abs_tol=0.01)
    assert math.isclose(float(first[14]), -math.pi / 2, abs_tol=0.01)
    # its 2D box lies inside the 960x540 image
    x1, y1, x2, y2 = map(float, first[4:8])
    assert 0 <= x1 < x2 <= 959 and 0 <= y1 < y2 <= 539
    calib = (tmp_path / 'calib' / 'scene_000001.txt').read_text()
    assert calib.startswith('P0: ') and 'Tr_velo_to_cam: ' in calib


def test_duplicate_frame_names_get_task_suffix():
    used = set()
    task = {'id': 7, 'data': {'pc': '/data/upload/1/000123.pcd?x=1'}}
    assert formats.frame_name(task, 'pc', used) == '000123'
    assert formats.frame_name(task, 'pc', used) == '000123_7'
