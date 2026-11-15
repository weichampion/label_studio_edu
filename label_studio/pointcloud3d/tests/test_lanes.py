"""3D polylines (<Polyline3D>): config spec, reference-frame transform through ego poses, LANE3D_JSON export."""

import json
import math

from pointcloud3d import formats
from pointcloud3d.tests.test_sequences import load

CONFIG = """
<View>
  <Labels name="lane_type" toName="pcd"><Label value="Dashed"/><Label value="Solid"/></Labels>
  <PointCloud name="pcd" value="$frames"/>
  <Polyline3D name="lane" toName="pcd"/>
  <Choices name="color" toName="pcd" perRegion="true"><Choice value="White"/><Choice value="Yellow"/></Choices>
</View>
"""


def line(id_, points, label='Dashed', frame=None):
    value = {'points': points, 'labels': [label]}
    if frame is not None:
        value['frame'] = frame
    return {'id': id_, 'from_name': 'lane', 'to_name': 'pcd', 'type': 'polyline3d', 'value': value}


def test_spec_and_parsing():
    assert formats.polyline_spec(CONFIG) == [{'control': 'lane', 'object': 'pcd', 'data_key': 'frames'}]
    parsed = {'lane': {'type': 'Polyline3D', 'to_name': ['pcd'], 'inputs': [{'type': 'PointCloud', 'value': 'pc'}]}}
    assert formats.polyline_spec_from_parsed_config(parsed) == [{'control': 'lane', 'object': 'pcd', 'data_key': 'pc'}]
    annotation = {
        'result': [
            line('a', [[0, 1.75, -1.7], {'x': 10, 'y': 1.75, 'z': -1.7}, ['bad']], frame=2),
            line('b', [[0, 0, 0]]),  # a single point is not a line
            {'id': 'a', 'from_name': 'color', 'to_name': 'pcd', 'type': 'choices', 'value': {'choices': ['Yellow']}},
        ]
    }
    [parsed_line] = formats.polylines_from_annotation(annotation, {'lane'})
    assert parsed_line == {
        'id': 'a',
        'label': 'Dashed',
        'attributes': {'color': ['Yellow']},
        'points': [[0.0, 1.75, -1.7], [10.0, 1.75, -1.7]],
        'frame': 2,
    }


def test_lines_follow_the_ego_motion_in_a_sequence():
    """A line drawn in frame 0 must land on the same world position in every other frame."""
    task = load('sequence_0001_tasks.json')[0]
    frames, _ = formats.task_frames(task, 'frames', None)
    poses = [f['pose'] for f in frames]
    drawn = {'frame': 0, 'points': [[5.0, 1.75, -1.73], [25.0, 1.75, -1.73]]}

    def world(points, pose):
        return formats.transform_points(points, pose)

    expected = world(drawn['points'], poses[0])
    for f in range(len(frames)):
        moved = formats.lanes_in_frame(drawn, f, poses)
        for a, b in zip(world(moved, poses[f]), expected):
            assert all(math.isclose(u, v, abs_tol=1e-6) for u, v in zip(a, b)), f
    # the ego vehicle drives forward, so the line comes closer frame by frame
    assert formats.lanes_in_frame(drawn, 7, poses)[0][0] < 5.0 - 5
    # without poses the coordinates are shared by all frames
    assert formats.lanes_in_frame(drawn, 5, [None] * 8) == drawn['points']


def test_lane_export_for_a_sequence(tmp_path):
    task = load('sequence_0001_tasks.json')[0]
    task['id'] = 3
    task['annotations'] = [
        {
            'id': 30,
            'result': [
                line('l1', [[5, 1.75, -1.73], [25, 1.75, -1.73]], 'Dashed', frame=0),
                line('l2', [[5, -1.75, -1.73], [25, -1.75, -1.73]], 'Solid', frame=4),
            ],
        }
    ]
    report = formats.convert_lanes([task], str(tmp_path), formats.polyline_spec(CONFIG))
    assert report['frames'] == 8 and report['lines'] == 16 and report['skipped'] == []
    first = json.loads((tmp_path / 'lanes' / '000000.json').read_text())
    assert first['pointcloud'].endswith('000000.pcd')
    assert [(l['track_id'], l['category']) for l in first['lanes']] == [(0, 'Dashed'), (1, 'Solid')]
    assert first['lanes'][0]['points'][0] == [5.0, 1.75, -1.73]
    # l2 was drawn in frame 4: exactly as stored there, moved elsewhere
    fifth = json.loads((tmp_path / 'lanes' / '000004.json').read_text())
    assert fifth['lanes'][1]['points'][0] == [5.0, -1.75, -1.73]
    assert first['lanes'][1]['points'][0] != [5.0, -1.75, -1.73]
    assert (tmp_path / 'ImageSets' / 'train.txt').read_text().split()[0] == '000000'


def test_lane_export_skips_tasks_without_lines(tmp_path):
    single = {
        'id': 9,
        'data': {'frames': '/data/scan.pcd'},
        'annotations': [{'id': 90, 'result': [line('x', [[1, 2, 3], [4, 5, 6]])]}],
    }
    empty = {'id': 10, 'data': {'frames': '/data/other.pcd'}, 'annotations': [{'id': 100, 'result': []}]}
    report = formats.convert_lanes([single, empty], str(tmp_path), formats.polyline_spec(CONFIG))
    assert report['frames'] == 1 and report['skipped'] == [{'task': 10, 'reason': 'no polylines'}]
    lanes = json.loads((tmp_path / 'lanes' / 'scan.json').read_text())['lanes']
    assert lanes[0]['points'] == [[1, 2, 3], [4, 5, 6]]
