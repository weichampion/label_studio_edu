"""Point cloud semantic segmentation (<PointLabels>): label encoding, config spec, SemanticKITTI export."""

import json
import random
import struct

from pointcloud3d import formats

CONFIG = """
<View>
  <PointCloud name="pcd" value="$frames"/>
  <PointLabels name="seg" toName="pcd">
    <Label value="Road" background="#FF00FF"/>
    <Label value="Car" background="#6496F5"/>
    <Label value="Vegetation" background="#00AF00"/>
  </PointLabels>
</View>
"""
# shared with the editor's __tests__/segmentation.test.ts: 5 unlabeled, 200 x code 3, 2 x code 1, 130 unlabeled
VECTOR = [0] * 5 + [3] * 200 + [1] * 2 + [0] * 130
VECTOR_RLE = 'AAUDyAEBAgCCAQ=='


def test_encoding_matches_the_editor_and_round_trips():
    assert formats.encode_point_labels(VECTOR) == VECTOR_RLE
    assert formats.decode_point_labels(VECTOR_RLE, len(VECTOR)) == VECTOR
    rng = random.Random(3)
    for _ in range(200):
        labels = []
        while len(labels) < 2000:
            labels += [rng.choice([0, 1, 2, 300, 65535])] * rng.randint(1, 400)
        assert formats.decode_point_labels(formats.encode_point_labels(labels), len(labels)) == labels
    assert formats.decode_point_labels('', 0) == []


def test_decoding_rejects_data_for_another_point_count():
    for rle, count in ((VECTOR_RLE, len(VECTOR) + 1), (VECTOR_RLE, len(VECTOR) - 1), ('gA==', 1)):
        try:
            formats.decode_point_labels(rle, count)
        except ValueError:
            continue
        raise AssertionError((rle, count))


def test_segmentation_spec():
    assert formats.segmentation_spec(CONFIG) == [
        {'control': 'seg', 'object': 'pcd', 'data_key': 'frames', 'classes': ['Road', 'Car', 'Vegetation']}
    ]
    assert formats.pointcloud_spec(CONFIG) == []
    parsed = {
        'seg': {
            'type': 'PointLabels',
            'to_name': ['pcd'],
            'inputs': [{'type': 'PointCloud', 'value': 'pointcloud'}],
            'labels': ['Road'],
        }
    }
    assert formats.segmentation_spec_from_parsed_config(parsed) == [
        {'control': 'seg', 'object': 'pcd', 'data_key': 'pointcloud', 'classes': ['Road']}
    ]


def task(frames_labels, classes):
    """A 3-frame sequence task; ``frames_labels`` maps frame -> code list (codes index ``classes``)."""
    return {
        'id': 7,
        'data': {'frames': [f'/data/seq/{i:06d}.pcd' for i in range(3)]},
        'annotations': [
            {
                'id': 70,
                'result': [
                    {
                        'id': 'seg1',
                        'from_name': 'seg',
                        'to_name': 'pcd',
                        'type': 'pointlabels',
                        'value': {
                            'classes': classes,
                            'frames': [
                                {'frame': f, 'count': len(codes), 'rle': formats.encode_point_labels(codes)}
                                for f, codes in frames_labels.items()
                            ],
                            'pointlabels': classes,
                        },
                    }
                ],
            }
        ],
    }


def read_label(path):
    data = path.read_bytes()
    return list(struct.unpack(f'<{len(data) // 4}I', data))


def test_semantic_kitti_export_uses_config_ids(tmp_path):
    # the annotation's own class table is in painting order (Car first); ids in the export follow the config
    frame0 = [1, 1, 0, 2, 2, 2]
    frame2 = [2, 0, 0, 3]
    spec = formats.segmentation_spec(CONFIG)
    report = formats.convert_segmentation([task({0: frame0, 2: frame2}, ['Car', 'Road', 'Removed'])], str(tmp_path), spec)
    assert report['frames'] == 2 and report['points'] == 10 and report['labeled_points'] == 7
    assert report['skipped'] == [{'task': 7, 'frame': 1, 'reason': 'frame not labeled'}]
    assert read_label(tmp_path / 'labels' / '000000.label') == [2, 2, 0, 1, 1, 1]
    # a class that is no longer in the config gets the next free id
    assert read_label(tmp_path / 'labels' / '000002.label') == [1, 0, 0, 4]
    classes = json.loads((tmp_path / 'classes.json').read_text())
    assert classes == {'0': 'unlabeled', '1': 'Road', '2': 'Car', '3': 'Vegetation', '4': 'Removed'}
    assert (tmp_path / 'ImageSets' / 'train.txt').read_text().split() == ['000000', '000002']
    mapping = json.loads((tmp_path / 'mapping.json').read_text())
    assert mapping[1]['sequence_frame'] == 2 and mapping[1]['points'] == 4


def test_semantic_kitti_export_skips_broken_frames(tmp_path):
    broken = task({0: [1, 1, 1]}, ['Road'])
    broken['annotations'][0]['result'][0]['value']['frames'][0]['count'] = 5
    unlabeled = {'id': 8, 'data': {'frames': ['a.pcd']}, 'annotations': [{'id': 80, 'result': []}]}
    report = formats.convert_segmentation([broken, unlabeled], str(tmp_path), formats.segmentation_spec(CONFIG))
    assert report['frames'] == 0
    reasons = [s['reason'] for s in report['skipped']]
    assert "Point labels don't match the point cloud" in reasons and 'no point labels' in reasons
