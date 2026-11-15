"""Per-frame cuboid attributes (<FrameAttribute>): config defaults, value lookup, export columns."""

import json

from pointcloud3d import formats
from pointcloud3d.tests.test_sequences import CAMERA, sample_task

CONFIG = """
<View>
  <Labels name="label" toName="pcd"><Label value="Car"/></Labels>
  <PointCloud name="pcd" value="$frames"/>
  <Cuboid name="box" toName="pcd">
    <FrameAttribute value="occluded" title="Occlusion" choices="0, 1, 2, 3" default="1"/>
    <FrameAttribute value="truncated" choices="0,0.5,1"/>
    <FrameAttribute value="moving"/>
    <FrameAttribute value="parked" default="yes"/>
    <FrameAttribute value="moving" default="true"/>
  </Cuboid>
</View>
"""


def test_spec_reads_frame_attribute_defaults():
    [spec] = formats.pointcloud_spec(CONFIG)
    # same rules as the editor: a default must be one of the choices, yes/no attributes default to "no",
    # the first declaration of a key wins
    assert spec['attributes'] == {'occluded': '1', 'truncated': '0', 'moving': False, 'parked': True}


def test_attribute_at_holds_values_until_the_next_change():
    changes = [{'frame': 12, 'value': '2'}, {'frame': 0, 'value': '0'}, {'frame': 20, 'value': '0'}]
    assert [formats.attribute_at(changes, f, 'x') for f in (0, 11, 12, 19, 20, 99)] == ['0', '0', '2', '2', '0', '0']
    assert formats.attribute_at([{'frame': 5, 'value': True}], 4, False) is False
    assert formats.attribute_at(None, 3, 'd') == 'd'
    # invalid entries are ignored, a repeated frame keeps the later entry (like the editor)
    noisy = [{'frame': -1, 'value': 'a'}, {'frame': 'x', 'value': 'b'}, {'frame': 2}, {'frame': 2, 'value': 'c'}]
    assert formats.attribute_at(noisy, 3, 'd') == 'c'
    stored = {'occluded': [{'frame': 3, 'value': '2'}], 'extra': [{'frame': 0, 'value': 'x'}]}
    assert formats.frame_attributes_at(stored, 1, {'occluded': '0', 'moving': False}) == {
        'occluded': '0',
        'moving': False,
        'extra': 'x',
    }


def with_attributes(task):
    """Car0 is occluded (2) in frames 2..4 and truncated from frame 6; Car1 never had attributes set."""
    result = task['annotations'][0]['result']
    cuboids = [r for r in result if r['type'] == 'cuboid']
    cuboids[0]['value']['frameAttributes'] = {
        'occluded': [{'frame': 0, 'value': '0'}, {'frame': 2, 'value': '2'}, {'frame': 5, 'value': '0'}],
        'truncated': [{'frame': 0, 'value': '0'}, {'frame': 6, 'value': '0.5'}],
    }
    return task, cuboids[0]['id'], cuboids[1]['id']


def test_kitti_tracking_columns_follow_frame_attributes(tmp_path):
    task, _, _ = with_attributes(sample_task())
    for frame in task['data']['frames']:
        frame['cameras'] = [CAMERA]
    spec = formats.pointcloud_spec(CONFIG)
    formats.convert_tasks([task], str(tmp_path), 'KITTI_TRACKING', spec)
    rows = [r.split() for r in (tmp_path / 'label_02' / '0001.txt').read_text().splitlines()]
    car0 = {int(r[0]): (r[3], r[4]) for r in rows if r[1] == '0'}
    assert [car0[f][1] for f in range(8)] == ['0', '0', '2', '2', '2', '0', '0', '0']
    assert [car0[f][0] for f in range(8)] == ['0.00'] * 6 + ['0.50'] * 2
    # a track without stored attributes gets the configured default (occluded = 1)
    car1 = [r[4] for r in rows if r[1] == '1']
    assert car1 and set(car1) == {'1'}


def test_openpcdet_export_writes_attributes_per_box(tmp_path):
    task, car0, car1 = with_attributes(sample_task())
    formats.convert_tasks([task], str(tmp_path), 'OPENPCDET_3D', formats.pointcloud_spec(CONFIG))
    index = json.loads((tmp_path / 'attributes.json').read_text())
    labels = (tmp_path / 'labels' / '000003.txt').read_text().splitlines()
    assert len(index['000003']) == len(labels)
    by_region = {e['region']: e['attributes'] for e in index['000003']}
    assert by_region[car0] == {'occluded': '2', 'truncated': '0', 'moving': False, 'parked': True}
    assert by_region[car1]['occluded'] == '1'
    assert {e['region']: e['attributes'] for e in index['000006']}[car0]['truncated'] == '0.5'


def test_without_frame_attributes_nothing_extra_is_written(tmp_path):
    spec = [{'control': 'box', 'object': 'pcd', 'data_key': 'frames', 'images_key': None}]
    formats.convert_tasks([sample_task()], str(tmp_path), 'OPENPCDET_3D', spec)
    assert not (tmp_path / 'attributes.json').exists()
