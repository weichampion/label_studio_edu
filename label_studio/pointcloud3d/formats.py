"""3D cuboid export formats for <PointCloud> + <Cuboid> projects.

Pure standard-library code (no Django / SDK imports) so it can be unit tested on its own.

Annotation value of a ``cuboid`` result, LiDAR frame (x forward, y left, z up; metres / radians)::

    {"position": {"x", "y", "z"}, "dimensions": {"length", "width", "height"},
     "rotation": {"x": 0, "y": 0, "z": yaw}, "labels": ["Car"]}

Sequences: when the task's point cloud field is a list of frames, each ``cuboid`` result is a track::

    {"sequence": [{"frame": 0, "enabled": true, "position": ..., "dimensions": ..., "rotation": ...}, ...],
     "framesCount": 80, "labels": ["Car"]}

Both may carry per-frame attributes declared with ``<FrameAttribute>`` inside ``<Cuboid>``: a list of changes per
attribute, each value holding until the next change (frame 0 for a single scan)::

    "frameAttributes": {"occluded": [{"frame": 0, "value": "0"}, {"frame": 12, "value": "2"}]}

Keyframes are in the LiDAR frame of their own scan. Boxes between keyframes are interpolated, in world
coordinates when frames carry an ego ``pose`` (4x4 LiDAR -> world); a track is absent from a disabled
keyframe until the next enabled one. The editor (PointCloud/sequence.ts) uses exactly the same rules.

Formats
-------
OPENPCDET_3D
    OpenPCDet "custom dataset" layout: ``labels/<frame>.txt`` with one
    ``x y z dx dy dz heading_angle category_name`` line per box (LiDAR frame) and ``ImageSets/train.txt``.
KITTI_3D
    KITTI object format: ``label_2/<frame>.txt`` (camera frame, 15 columns) and ``calib/<frame>.txt``.
    Needs camera calibration in the task (``images`` attribute of <PointCloud>); tasks without it are skipped
    and listed in ``export_report.json``.

KITTI_TRACKING
    KITTI tracking format, one sequence per task: ``label_02/<seq>.txt`` with
    ``frame track_id type truncated occluded alpha x1 y1 x2 y2 h w l x y z rotation_y`` lines,
    ``calib/<seq>.txt`` and, when frames have ego poses, ``poses/<seq>.txt`` (3x4 LiDAR -> world per frame).

SEMANTIC_KITTI
    Semantic segmentation from ``<PointLabels>`` (``pointlabels`` results, see :func:`decode_point_labels`):
    ``labels/<frame>.label`` with one little-endian uint32 per point in scan order (class id in the lower 16 bits,
    0 = unlabeled) and ``classes.json`` (id -> class, ids in config order). Unlabeled frames are listed in
    ``export_report.json``.

LANE3D_JSON
    3D polylines from ``<Polyline3D>`` (``polyline3d`` results: ``points`` in the LiDAR frame of ``frame``):
    ``lanes/<frame>.json`` per point cloud with ``{"lanes": [{"id", "track_id", "category", "attributes",
    "points": [[x, y, z], ...]}]}`` in that frame's coordinates (sequences: moved through the ego poses).

All formats also write ``mapping.json`` and ``export_report.json``; for sequences OPENPCDET_3D adds
``tracks.json`` (track id of every label line). OPENPCDET_3D writes per-box attributes to ``attributes.json``
(same order as the label lines); KITTI formats put attributes named like ``occluded`` / ``truncated`` into
their columns.
"""

import base64
import binascii
import json
import math
import os
import re
import sys
from array import array
from enum import Enum

try:  # defusedxml is a Label Studio dependency; fall back to the stdlib parser for isolated tests
    from defusedxml import ElementTree as _ET
except ImportError:  # pragma: no cover
    from xml.etree import ElementTree as _ET


class PointCloudFormat(Enum):
    OPENPCDET_3D = 101
    KITTI_3D = 102
    KITTI_TRACKING = 103
    SEMANTIC_KITTI = 104
    LANE3D_JSON = 105

    def __str__(self):
        return self.name


FORMAT_INFO = {
    PointCloudFormat.OPENPCDET_3D: {
        'title': 'OpenPCDet 3D (LiDAR frame)',
        'description': 'One labels/<frame>.txt per point cloud with "x y z dx dy dz heading category" lines in the '
        'LiDAR frame, as used by the OpenPCDet custom dataset, plus ImageSets/train.txt and mapping.json.',
        'link': 'https://github.com/open-mmlab/OpenPCDet/blob/master/docs/CUSTOM_DATASET_TUTORIAL.md',
        'tags': ['3D object detection', 'point cloud', 'LiDAR'],
    },
    PointCloudFormat.KITTI_3D: {
        'title': 'KITTI 3D (camera frame)',
        'description': 'KITTI object detection labels (label_2) and calibration files (calib). Boxes are '
        'converted to the first calibrated camera of each task; tasks without calibration are skipped.',
        'link': 'https://www.cvlibs.net/datasets/kitti/eval_object.php?obj_benchmark=3d',
        'tags': ['3D object detection', 'point cloud', 'LiDAR', 'camera'],
    },
    PointCloudFormat.KITTI_TRACKING: {
        'title': 'KITTI Tracking (camera frame)',
        'description': 'KITTI multi-object tracking labels: one label_02/<sequence>.txt per task with frame and '
        'track ids, interpolated between keyframes, plus calib and ego poses. Single scans count as 1-frame sequences.',
        'link': 'https://www.cvlibs.net/datasets/kitti/eval_tracking.php',
        'tags': ['3D tracking', 'point cloud', 'LiDAR', 'camera', 'sequence'],
    },
    PointCloudFormat.SEMANTIC_KITTI: {
        'title': 'SemanticKITTI (point labels)',
        'description': 'Semantic segmentation from <PointLabels>: one labels/<frame>.label per labeled point cloud '
        '(uint32 per point, class id in the lower 16 bits, same point order as the scan) plus classes.json.',
        'link': 'https://semantic-kitti.org/dataset.html#format',
        'tags': ['semantic segmentation', 'point cloud', 'LiDAR'],
    },
}
FORMAT_INFO[PointCloudFormat.LANE3D_JSON] = {
    'title': '3D Lanes JSON (LiDAR frame)',
    'description': '3D polylines from <Polyline3D> (lane lines, road edges): one lanes/<frame>.json per point cloud '
    'with every line as [x, y, z] points in that frame\'s LiDAR coordinates, its type, attributes and track id. '
    'Lines drawn once in a sequence are written to every frame through the ego poses.',
    'link': 'https://github.com/OpenDriveLab/OpenLane',
    'tags': ['lane detection', '3D polyline', 'point cloud', 'LiDAR'],
}
FORMAT_NAMES = {f.name for f in PointCloudFormat}
SEGMENTATION_FORMAT_NAMES = {PointCloudFormat.SEMANTIC_KITTI.name}
LANE_FORMAT_NAMES = {PointCloudFormat.LANE3D_JSON.name}
BOX_FORMAT_NAMES = FORMAT_NAMES - SEGMENTATION_FORMAT_NAMES - LANE_FORMAT_NAMES

OCCLUSION_WORDS = (
    ('fully', 0),
    ('visible', 0),
    ('partly', 1),
    ('partially', 1),
    ('largely', 2),
    ('heavily', 2),
    ('unknown', 3),
)


# --------------------------------------------------------------------------------------------------
# label config
# --------------------------------------------------------------------------------------------------


TRUE_WORDS = {'true', '1', 'yes', 'y'}


def attribute_default(el):
    """Default value of a <FrameAttribute> (same rules as attributeDef in the editor's attributes.ts)."""
    choices = [c.strip() for c in (el.get('choices') or '').split(',') if c.strip()]
    raw = el.get('default')
    raw = raw.strip() if raw is not None else None
    if not choices:
        return raw is not None and raw.lower() in TRUE_WORDS
    return raw if raw in choices else choices[0]


def pointcloud_spec(label_config):
    """[{control, object, data_key, images_key, attributes}] for every <Cuboid> attached to a <PointCloud>.

    ``attributes`` maps the key of every <FrameAttribute> of the cuboid to its default value.
    """
    if not label_config:
        return []
    try:
        root = _ET.fromstring(label_config)
    except Exception:
        return []
    objects = {}
    for el in root.iter():
        if el.tag.lower() == 'pointcloud' and el.get('name'):
            objects[el.get('name')] = {
                'data_key': (el.get('value') or '').lstrip('$') or None,
                'images_key': (el.get('images') or '').lstrip('$') or None,
            }
    spec = []
    for el in root.iter():
        if el.tag.lower() != 'cuboid' or not el.get('name'):
            continue
        attributes = {}
        for child in el:
            key = (child.get('value') or '').strip()
            if str(child.tag).lower() == 'frameattribute' and key and key not in attributes:
                attributes[key] = attribute_default(child)
        for to_name in (el.get('toName') or '').split(','):
            if to_name in objects:
                spec.append({'control': el.get('name'), 'object': to_name, **objects[to_name], 'attributes': attributes})
    return spec


def segmentation_spec(label_config):
    """[{control, object, data_key, classes}] for every <PointLabels> attached to a <PointCloud>."""
    if not label_config:
        return []
    try:
        root = _ET.fromstring(label_config)
    except Exception:
        return []
    objects = {
        el.get('name'): (el.get('value') or '').lstrip('$') or None
        for el in root.iter()
        if el.tag.lower() == 'pointcloud' and el.get('name')
    }
    spec = []
    for el in root.iter():
        if el.tag.lower() != 'pointlabels' or not el.get('name'):
            continue
        classes = []
        for child in el.iter():
            value = child.get('alias') or child.get('value')
            if child.tag.lower() == 'label' and value and value not in classes:
                classes.append(value)
        for to_name in (el.get('toName') or '').split(','):
            if to_name in objects:
                spec.append({'control': el.get('name'), 'object': to_name, 'data_key': objects[to_name], 'classes': classes})
    return spec


def polyline_spec(label_config):
    """[{control, object, data_key}] for every <Polyline3D> attached to a <PointCloud>."""
    if not label_config:
        return []
    try:
        root = _ET.fromstring(label_config)
    except Exception:
        return []
    objects = {
        el.get('name'): (el.get('value') or '').lstrip('$') or None
        for el in root.iter()
        if el.tag.lower() == 'pointcloud' and el.get('name')
    }
    spec = []
    for el in root.iter():
        if el.tag.lower() != 'polyline3d' or not el.get('name'):
            continue
        for to_name in (el.get('toName') or '').split(','):
            if to_name in objects:
                spec.append({'control': el.get('name'), 'object': to_name, 'data_key': objects[to_name]})
    return spec


def polyline_spec_from_parsed_config(parsed):
    """Fallback when only the parsed config is known."""
    spec = []
    for name, info in (parsed or {}).items():
        if str(info.get('type', '')).lower() != 'polyline3d':
            continue
        for to_name, inp in zip(info.get('to_name') or [], info.get('inputs') or []):
            if str(inp.get('type', '')).lower() == 'pointcloud':
                spec.append({'control': name, 'object': to_name, 'data_key': inp.get('value')})
    return spec


def segmentation_spec_from_parsed_config(parsed):
    """Fallback when only the parsed config is known."""
    spec = []
    for name, info in (parsed or {}).items():
        if str(info.get('type', '')).lower() != 'pointlabels':
            continue
        for to_name, inp in zip(info.get('to_name') or [], info.get('inputs') or []):
            if str(inp.get('type', '')).lower() == 'pointcloud':
                spec.append(
                    {'control': name, 'object': to_name, 'data_key': inp.get('value'), 'classes': list(info.get('labels') or [])}
                )
    return spec


def spec_from_parsed_config(parsed):
    """Fallback when only the parsed config is known (no camera key)."""
    spec = []
    for name, info in (parsed or {}).items():
        if str(info.get('type', '')).lower() != 'cuboid':
            continue
        for to_name, inp in zip(info.get('to_name') or [], info.get('inputs') or []):
            if str(inp.get('type', '')).lower() == 'pointcloud':
                spec.append(
                    {
                        'control': name,
                        'object': to_name,
                        'data_key': inp.get('value'),
                        'images_key': None,
                        'attributes': {},
                    }
                )
    return spec


# --------------------------------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------------------------------


def normalize_angle(angle):
    a = math.fmod(angle, 2 * math.pi)
    if a <= -math.pi:
        a += 2 * math.pi
    if a > math.pi:
        a -= 2 * math.pi
    return a


def box_from_value(value):
    p = value.get('position') or {}
    d = value.get('dimensions') or {}
    r = value.get('rotation') or {}
    return {
        'x': float(p.get('x', 0)),
        'y': float(p.get('y', 0)),
        'z': float(p.get('z', 0)),
        'length': float(d.get('length', 0)),
        'width': float(d.get('width', 0)),
        'height': float(d.get('height', 0)),
        'yaw': normalize_angle(float(r.get('z', 0))),
    }


def box_corners(box):
    """8 corners, bottom face first (front-left, front-right, rear-right, rear-left)."""
    c, s = math.cos(box['yaw']), math.sin(box['yaw'])
    l, w, h = box['length'] / 2, box['width'] / 2, box['height'] / 2
    local = [(l, w, -h), (l, -w, -h), (-l, -w, -h), (-l, w, -h), (l, w, h), (l, -w, h), (-l, -w, h), (-l, w, h)]
    return [(box['x'] + c * a - s * b, box['y'] + s * a + c * b, box['z'] + z) for a, b, z in local]


def _flat(m):
    if m is None:
        return None
    if isinstance(m, str):
        try:
            values = [float(v) for v in re.split(r'[\s,]+', m.strip()) if v]
        except ValueError:
            return None
        return values or None
    if isinstance(m, (list, tuple)):
        out = []
        for v in m:
            if isinstance(v, (list, tuple)):
                sub = _flat(v)
                if sub is None:
                    return None
                out.extend(sub)
            else:
                try:
                    out.append(float(v))
                except (TypeError, ValueError):
                    return None
        return out
    return None


def _to4x4(m):
    if len(m) == 16:
        return list(m)
    if len(m) == 12:
        return list(m) + [0.0, 0.0, 0.0, 1.0]
    if len(m) == 9:
        return [m[0], m[1], m[2], 0.0, m[3], m[4], m[5], 0.0, m[6], m[7], m[8], 0.0, 0.0, 0.0, 0.0, 1.0]
    return None


def _mul4(a, b):
    return [sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)]


def normalize_calibration(raw):
    """Same rules as the editor (geometry.normalizeCalibration): {intrinsic: 9|12, extrinsic: 16} or None."""
    if not isinstance(raw, dict):
        return None

    def first(*keys):
        for key in keys:
            if raw.get(key) is not None:
                return raw[key]
        return None

    intrinsic = _flat(first('intrinsic', 'K', 'P', 'P2', 'camera_intrinsic'))
    ext = _flat(first('extrinsic', 'lidar_to_camera', 'Tr_velo_to_cam', 'Tr_velo_cam'))
    extrinsic = _to4x4(ext) if ext else None
    r0 = _flat(first('R0_rect', 'R_rect'))
    if extrinsic and r0 and _to4x4(r0):
        extrinsic = _mul4(_to4x4(r0), extrinsic)
    if not intrinsic or len(intrinsic) not in (9, 12) or not extrinsic:
        return None
    size = first('image_size', 'size')
    width = raw.get('width')
    height = raw.get('height')
    if isinstance(size, (list, tuple)) and len(size) == 2:
        width, height = size
    return {
        'intrinsic': intrinsic,
        'extrinsic': extrinsic,
        'width': float(width) if width else None,
        'height': float(height) if height else None,
    }


def parse_cameras(raw):
    """Same accepted shapes as the editor (geometry.parseCameraImages)."""
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            return parse_cameras(json.loads(raw))
        except ValueError:
            return [{'url': raw, 'name': 'camera', 'calibration': None}]
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict) and (raw.get('url') or raw.get('image')):
        items = [raw]
    elif isinstance(raw, dict):
        items = [{'name': name, **(cam or {})} for name, cam in raw.items() if isinstance(cam, dict) or not cam]
    else:
        return []
    cameras = []
    for index, cam in enumerate(items):
        if isinstance(cam, str):
            cameras.append({'url': cam, 'name': f'camera {index + 1}', 'calibration': None})
            continue
        if not isinstance(cam, dict):
            continue
        url = cam.get('url') or cam.get('image') or cam.get('src')
        if not url:
            continue
        calib_raw = cam.get('calibration') or cam.get('calib') or cam
        cameras.append(
            {
                'url': str(url),
                'name': str(cam.get('name') or cam.get('camera') or f'camera {index + 1}'),
                'calibration': normalize_calibration(calib_raw),
            }
        )
    return cameras


def _apply(e, x, y, z):
    return (
        e[0] * x + e[1] * y + e[2] * z + e[3],
        e[4] * x + e[5] * y + e[6] * z + e[7],
        e[8] * x + e[9] * y + e[10] * z + e[11],
    )


def _pixel(k, p):
    if len(k) == 12:
        u = k[0] * p[0] + k[1] * p[1] + k[2] * p[2] + k[3]
        v = k[4] * p[0] + k[5] * p[1] + k[6] * p[2] + k[7]
        w = k[8] * p[0] + k[9] * p[1] + k[10] * p[2] + k[11]
    else:
        u = k[0] * p[0] + k[1] * p[1] + k[2] * p[2]
        v = k[3] * p[0] + k[4] * p[1] + k[5] * p[2]
        w = k[6] * p[0] + k[7] * p[1] + k[8] * p[2]
    if w <= 1e-6:
        return None
    return u / w, v / w


def to_kitti_camera(box, calib):
    """LiDAR box -> KITTI (location of bottom centre, h w l, rotation_y, alpha, 2D bbox) in camera frame."""
    e = calib['extrinsic']
    bottom = _apply(e, box['x'], box['y'], box['z'] - box['height'] / 2)
    # heading direction rotated into the camera frame; KITTI heading is (cos ry, 0, -sin ry)
    hx, hy = math.cos(box['yaw']), math.sin(box['yaw'])
    v = (e[0] * hx + e[1] * hy, e[4] * hx + e[5] * hy, e[8] * hx + e[9] * hy)
    rotation_y = normalize_angle(math.atan2(-v[2], v[0]))
    alpha = normalize_angle(rotation_y - math.atan2(bottom[0], bottom[2]))

    pixels = []
    for corner in box_corners(box):
        cam = _apply(e, *corner)
        if cam[2] < 0.1:
            continue
        px = _pixel(calib['intrinsic'], cam)
        if px:
            pixels.append(px)
    bbox = None
    if pixels:
        x1 = min(p[0] for p in pixels)
        y1 = min(p[1] for p in pixels)
        x2 = max(p[0] for p in pixels)
        y2 = max(p[1] for p in pixels)
        if calib.get('width') and calib.get('height'):
            x1, x2 = max(0.0, x1), min(calib['width'] - 1, x2)
            y1, y2 = max(0.0, y1), min(calib['height'] - 1, y2)
        if x2 > x1 and y2 > y1:
            bbox = (x1, y1, x2, y2)
    return {
        'location': bottom,
        'rotation_y': rotation_y,
        'alpha': alpha,
        'bbox': bbox,
        'in_front': bottom[2] > 0.1,
    }


# --------------------------------------------------------------------------------------------------
# tasks -> boxes
# --------------------------------------------------------------------------------------------------


def pick_annotation(task):
    """Ground truth first, otherwise the most recently updated annotation that was not skipped."""
    annotations = [a for a in task.get('annotations') or [] if not a.get('was_cancelled')]
    if not annotations:
        return None
    ground_truth = [a for a in annotations if a.get('ground_truth')]
    pool = ground_truth or annotations
    return max(pool, key=lambda a: (a.get('updated_at') or a.get('created_at') or '', a.get('id') or 0))


def _occlusion(choice):
    text = str(choice).strip().lower()
    if text[:1].isdigit():
        return int(text[0])
    for word, level in OCCLUSION_WORDS:
        if word in text:
            return level
    return None


# --------------------------------------------------------------------------------------------------
# sequences (same rules as the editor's PointCloud/sequence.ts)
# --------------------------------------------------------------------------------------------------


def normalize_pose(raw):
    values = _flat(raw) if raw is not None else None
    if not values:
        return None
    if len(values) == 12:
        values = values + [0.0, 0.0, 0.0, 1.0]
    return values if len(values) == 16 else None


def invert_rigid(m):
    rt = [m[0], m[4], m[8], m[1], m[5], m[9], m[2], m[6], m[10]]
    t = [m[3], m[7], m[11]]
    out = []
    for r in range(3):
        row = rt[r * 3 : r * 3 + 3]
        out += row + [-(row[0] * t[0] + row[1] * t[1] + row[2] * t[2])]
    return out + [0.0, 0.0, 0.0, 1.0]


def transform_box(box, m):
    """Rigid transform of a box; only the yaw part of the rotation is applied (boxes stay upright)."""
    x, y, z = _apply(m, box['x'], box['y'], box['z'])
    return {**box, 'x': x, 'y': y, 'z': z, 'yaw': normalize_angle(box['yaw'] + math.atan2(m[4], m[0]))}


def to_world(box, pose):
    return transform_box(box, pose) if pose else box


def from_world(box, pose):
    return transform_box(box, invert_rigid(pose)) if pose else box


def lerp_box(a, b, t):
    out = {k: a[k] + (b[k] - a[k]) * t for k in ('x', 'y', 'z', 'length', 'width', 'height')}
    out['yaw'] = normalize_angle(a['yaw'] + normalize_angle(b['yaw'] - a['yaw']) * t)
    return out


def _keyframes(sequence):
    return sorted((k for k in sequence or [] if isinstance(k, dict) and 'frame' in k), key=lambda k: k['frame'])


def box_at(sequence, frame, poses=()):
    """Box of a track at ``frame`` in that frame's LiDAR coordinates, or None when the object is absent."""
    keys = _keyframes(sequence)
    prev = None
    for k in keys:
        if k['frame'] > frame:
            break
        prev = k
    if prev is None or not prev.get('enabled', True):
        return None
    prev_box = box_from_value(prev)
    if prev['frame'] == frame:
        return prev_box

    def pose(i):
        return poses[i] if 0 <= i < len(poses) else None

    world = to_world(prev_box, pose(prev['frame']))
    nxt = next((k for k in keys if k['frame'] > frame), None)
    if nxt is not None and nxt.get('enabled', True):
        t = (frame - prev['frame']) / (nxt['frame'] - prev['frame'])
        world = lerp_box(world, to_world(box_from_value(nxt), pose(nxt['frame'])), t)
    return from_world(world, pose(frame))


def lifespans(sequence, frames_count):
    ranges, start = [], None
    for k in _keyframes(sequence):
        enabled = k.get('enabled', True)
        if enabled and start is None:
            start = k['frame']
        if not enabled and start is not None:
            ranges.append((start, k['frame'] - 1))
            start = None
    if start is not None:
        ranges.append((start, max(start, frames_count - 1)))
    return [r for r in ranges if r[1] >= r[0]]


def parse_frames(raw):
    """Frame list of a sequence task, or None for a single scan."""
    if not isinstance(raw, list):
        return None
    frames = []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            frames.append({'index': index, 'url': item, 'pose': None, 'cameras': None})
            continue
        item = item if isinstance(item, dict) else {}
        frames.append(
            {
                'index': index,
                'url': str(item.get('pointcloud') or item.get('url') or item.get('lidar') or item.get('path') or ''),
                'pose': normalize_pose(item.get('pose') or item.get('ego_pose') or item.get('lidar_to_world')),
                'cameras': item.get('cameras') or item.get('images'),
            }
        )
    return frames


def task_frames(task, data_key, images_key):
    """([{index, url, pose, cameras}], is_sequence); cameras are parsed, per frame or task-wide."""
    data = task.get('data') or {}
    task_cameras = parse_cameras(data.get(images_key)) if images_key else []
    frames = parse_frames(data.get(data_key))
    if frames is None:
        return [{'index': 0, 'url': str(data.get(data_key) or ''), 'pose': None, 'cameras': task_cameras}], False
    for frame in frames:
        frame['cameras'] = parse_cameras(frame['cameras']) if frame['cameras'] else task_cameras
    return frames, True


# --------------------------------------------------------------------------------------------------
# 3D polylines (same rules as the editor's PointCloud/lanes.ts)
# --------------------------------------------------------------------------------------------------


def normalize_points(raw):
    out = []
    for p in raw if isinstance(raw, list) else []:
        if isinstance(p, dict):
            p = [p.get('x'), p.get('y'), p.get('z', 0)]
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        try:
            x, y = float(p[0]), float(p[1])
            z = float(p[2]) if len(p) > 2 and p[2] is not None else 0.0
        except (TypeError, ValueError):
            continue
        if all(math.isfinite(v) for v in (x, y, z)):
            out.append([x, y, z])
    return out


def transform_points(points, m):
    return [
        [
            m[0] * x + m[1] * y + m[2] * z + m[3],
            m[4] * x + m[5] * y + m[6] * z + m[7],
            m[8] * x + m[9] * y + m[10] * z + m[11],
        ]
        for x, y, z in points
    ]


def polylines_from_annotation(annotation, controls):
    """[{id, label, attributes, points, frame}] from the polyline3d results of the given controls."""
    results = annotation.get('result') or []
    attributes, separate_labels = {}, {}
    for r in results:
        if r.get('type') == 'polyline3d' or not r.get('id'):
            continue
        value = r.get('value') or {}
        if r.get('type') == 'labels' and value.get('labels'):
            separate_labels[r['id']] = value['labels']
            continue
        for key in ('choices', 'text', 'number', 'rating'):
            if key in value:
                attributes.setdefault(r['id'], {})[r.get('from_name')] = value[key]
    lines = []
    for r in results:
        if r.get('type') != 'polyline3d' or r.get('from_name') not in controls:
            continue
        value = r.get('value') or {}
        points = normalize_points(value.get('points'))
        if len(points) < 2:
            continue
        labels = value.get('labels') or separate_labels.get(r.get('id')) or []
        frame = value.get('frame')
        lines.append(
            {
                'id': r.get('id'),
                'label': str(labels[0]) if labels else 'Unknown',
                'attributes': attributes.get(r.get('id'), {}),
                'points': points,
                'frame': frame if isinstance(frame, int) and not isinstance(frame, bool) and frame >= 0 else None,
            }
        )
    return lines


def lanes_in_frame(line, frame, poses):
    """Vertices of ``line`` in the LiDAR coordinates of ``frame``: moved from its reference frame by the ego poses."""
    ref = line['frame'] if line['frame'] is not None else frame
    pose = lambda i: poses[i] if 0 <= i < len(poses) else None  # noqa: E731
    if ref == frame or not pose(ref) or not pose(frame):
        return [list(p) for p in line['points']]
    return transform_points(line['points'], _mul4(invert_rigid(pose(frame)), pose(ref)))


def convert_lanes(tasks, output_dir, lane_spec):
    """LANE3D_JSON export of <Polyline3D> results. Returns a report dict."""
    if not lane_spec:
        raise ValueError('The labeling config has no <Polyline3D> attached to a <PointCloud>')
    os.makedirs(output_dir, exist_ok=True)
    controls = {s['control'] for s in lane_spec}
    data_key = lane_spec[0]['data_key']
    used, listed, mapping = set(), [], []
    report = {'format': PointCloudFormat.LANE3D_JSON.name, 'frames': 0, 'lines': 0, 'skipped': []}
    for task in tasks:
        annotation = pick_annotation(task)
        lines = polylines_from_annotation(annotation, controls) if annotation else []
        if not lines:
            report['skipped'].append({'task': task.get('id'), 'reason': 'no polylines'})
            continue
        frames, is_sequence = task_frames(task, data_key, None)
        poses = [f['pose'] for f in frames]
        # track ids per task, in drawing order
        track_ids = {line['id']: index for index, line in enumerate(lines)}
        for frame in frames:
            suffix = f'{task.get("id")}_{frame["index"]:04d}' if is_sequence else task.get('id')
            name = _unique_name(frame['url'], f'task_{suffix}', used, suffix=suffix)
            content = {
                'frame': name,
                'pointcloud': frame['url'],
                'lanes': [
                    {
                        'id': line['id'],
                        'track_id': track_ids[line['id']],
                        'category': line['label'],
                        'attributes': line['attributes'],
                        'points': [[round(v, 4) for v in p] for p in lanes_in_frame(line, frame['index'], poses)],
                    }
                    for line in lines
                ],
            }
            path = os.path.join(output_dir, 'lanes', f'{name}.json')
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(content, f, ensure_ascii=False, indent=2)
            listed.append(name)
            report['frames'] += 1
            report['lines'] += len(lines)
            mapping.append(
                {
                    'frame': name,
                    'task': task.get('id'),
                    'annotation': annotation.get('id'),
                    'pointcloud': frame['url'],
                    **({'sequence_frame': frame['index']} if is_sequence else {}),
                }
            )
    _write(os.path.join(output_dir, 'ImageSets', 'train.txt'), listed)
    for name, content in (('mapping.json', mapping), ('export_report.json', report)):
        with open(os.path.join(output_dir, name), 'w', encoding='utf-8') as f:
            json.dump(content, f, ensure_ascii=False, indent=2)
    return report


# --------------------------------------------------------------------------------------------------
# point labels (same encoding as the editor's PointCloud/segmentation.ts)
# --------------------------------------------------------------------------------------------------


def encode_point_labels(labels):
    """Run-length pairs (code, run) as unsigned LEB128 varints, base64."""
    out = bytearray()

    def varint(v):
        while v >= 0x80:
            out.append((v & 0x7F) | 0x80)
            v >>= 7
        out.append(v)

    i, n = 0, len(labels)
    while i < n:
        j = i + 1
        while j < n and labels[j] == labels[i]:
            j += 1
        varint(int(labels[i]))
        varint(j - i)
        i = j
    return base64.b64encode(bytes(out)).decode('ascii')


def decode_point_labels(rle, count):
    """List of ``count`` codes (0 = unlabeled). Raises ValueError when the data doesn't describe ``count`` points."""
    try:
        data = base64.b64decode(rle or '', validate=False)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f'Invalid point labels: {e}')
    out = []
    pos = 0

    def read():
        nonlocal pos
        result, shift = 0, 0
        while True:
            if pos >= len(data):
                raise ValueError('Truncated point labels')
            b = data[pos]
            pos += 1
            result |= (b & 0x7F) << shift
            if b < 0x80:
                return result
            shift += 7

    while pos < len(data):
        code, run = read(), read()
        if code > 0xFFFF or len(out) + run > count:
            raise ValueError("Point labels don't match the point cloud")
        out.extend([code] * run)
    if len(out) != count:
        raise ValueError("Point labels don't match the point cloud")
    return out


def segmentation_from_annotation(annotation, controls):
    """{control: {'classes': [...], 'frames': {frame: {'count', 'rle'}}}} from the pointlabels results."""
    out = {}
    for r in annotation.get('result') or []:
        if r.get('type') != 'pointlabels' or r.get('from_name') not in controls:
            continue
        value = r.get('value') or {}
        frames = {}
        raw = value.get('frames') or []
        if isinstance(raw, dict):
            raw = [{**v, 'frame': int(k)} for k, v in raw.items() if str(k).isdigit() and isinstance(v, dict)]
        for entry in raw:
            if (
                isinstance(entry, dict)
                and isinstance(entry.get('frame'), int)
                and isinstance(entry.get('count'), int)
                and isinstance(entry.get('rle'), str)
            ):
                frames[entry['frame']] = {'count': entry['count'], 'rle': entry['rle']}
        out[r['from_name']] = {'classes': [str(c) for c in value.get('classes') or []], 'frames': frames}
    return out


def convert_segmentation(tasks, output_dir, seg_spec):
    """SemanticKITTI-style export of <PointLabels> results. Returns a report dict."""
    if not seg_spec:
        raise ValueError('The labeling config has no <PointLabels> attached to a <PointCloud>')
    os.makedirs(output_dir, exist_ok=True)
    spec = seg_spec[0]
    control, data_key = spec['control'], spec['data_key']
    # ids follow the config order; classes found only in annotations (removed from the config) come after
    class_ids = {name: index + 1 for index, name in enumerate(spec['classes'])}
    used, listed, mapping = set(), [], []
    report = {'format': PointCloudFormat.SEMANTIC_KITTI.name, 'frames': 0, 'points': 0, 'labeled_points': 0, 'skipped': []}
    for task in tasks:
        annotation = pick_annotation(task)
        result = segmentation_from_annotation(annotation, {control}).get(control) if annotation else None
        if not result or not result['frames']:
            report['skipped'].append({'task': task.get('id'), 'reason': 'no point labels'})
            continue
        frames, is_sequence = task_frames(task, data_key, None)
        for name in result['classes']:
            if name not in class_ids:
                class_ids[name] = len(class_ids) + 1
        remap = [0] + [class_ids[name] for name in result['classes']]
        for frame in frames:
            stored = result['frames'].get(frame['index'])
            where = {'task': task.get('id'), **({'frame': frame['index']} if is_sequence else {})}
            if stored is None:
                if is_sequence:
                    report['skipped'].append({**where, 'reason': 'frame not labeled'})
                continue
            try:
                codes = decode_point_labels(stored['rle'], stored['count'])
            except ValueError as e:
                report['skipped'].append({**where, 'reason': str(e)})
                continue
            if any(code >= len(remap) for code in codes):
                report['skipped'].append({**where, 'reason': 'unknown class code'})
                continue
            values = array('I', (remap[code] for code in codes))
            if values.itemsize != 4:  # pragma: no cover - 'I' is 4 bytes on all supported platforms
                values = array('L', values)
            if sys.byteorder != 'little':  # pragma: no cover
                values.byteswap()
            suffix = f'{task.get("id")}_{frame["index"]:04d}' if is_sequence else task.get('id')
            name = _unique_name(frame['url'], f'task_{suffix}', used, suffix=suffix)
            path = os.path.join(output_dir, 'labels', f'{name}.label')
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                values.tofile(f)
            listed.append(name)
            report['frames'] += 1
            report['points'] += len(codes)
            report['labeled_points'] += sum(1 for code in codes if code)
            mapping.append(
                {
                    'frame': name,
                    'task': task.get('id'),
                    'annotation': annotation.get('id'),
                    'pointcloud': frame['url'],
                    'points': len(codes),
                    **({'sequence_frame': frame['index']} if is_sequence else {}),
                }
            )
    _write(os.path.join(output_dir, 'ImageSets', 'train.txt'), listed)
    classes = {'0': 'unlabeled', **{str(i): name for name, i in sorted(class_ids.items(), key=lambda kv: kv[1])}}
    for name, content in (('classes.json', classes), ('mapping.json', mapping), ('export_report.json', report)):
        with open(os.path.join(output_dir, name), 'w', encoding='utf-8') as f:
            json.dump(content, f, ensure_ascii=False, indent=2)
    return report


# --------------------------------------------------------------------------------------------------
# per-frame attributes (same rules as the editor's PointCloud/attributes.ts)
# --------------------------------------------------------------------------------------------------


def _changes(raw):
    out = {}
    for c in raw if isinstance(raw, list) else []:
        frame = c.get('frame') if isinstance(c, dict) else None
        if isinstance(frame, int) and not isinstance(frame, bool) and frame >= 0 and 'value' in c:
            out[frame] = c['value']
    return sorted(out.items())


def attribute_at(changes, frame, fallback=None):
    """Value of the last change at or before ``frame``, otherwise ``fallback``."""
    value = fallback
    for at, v in _changes(changes):
        if at > frame:
            break
        value = v
    return value


def frame_attributes_at(stored, frame, defaults=None):
    """{key: value} in ``frame`` for every declared attribute and every stored one."""
    stored = stored if isinstance(stored, dict) else {}
    defaults = defaults or {}
    out = {}
    for key in list(defaults) + [k for k in stored if k not in defaults]:
        value = attribute_at(stored.get(key), frame, defaults.get(key))
        if value is not None:
            out[key] = value
    return out


# --------------------------------------------------------------------------------------------------
# tasks -> boxes
# --------------------------------------------------------------------------------------------------


def items_from_annotation(annotation, controls, attribute_defaults=None):
    """[{id, label, attributes, frame_attributes, box | sequence}] from the cuboid results of the given controls.

    ``attributes`` are per-region results (<Choices perRegion> ...); ``frame_attributes`` are the stored
    per-frame changes, resolved by :func:`frame_items`. ``attribute_defaults``: {control: {key: default}}.
    """
    results = annotation.get('result') or []
    attributes = {}
    separate_labels = {}
    for r in results:
        if r.get('type') == 'cuboid' or not r.get('id'):
            continue
        value = r.get('value') or {}
        if r.get('type') == 'labels' and value.get('labels'):
            # labels written as a separate result with the same region id (e.g. ML predictions)
            separate_labels[r['id']] = value['labels']
            continue
        for key in ('choices', 'text', 'number', 'rating'):
            if key in value:
                attributes.setdefault(r['id'], {})[r.get('from_name')] = value[key]
    items = []
    for r in results:
        if r.get('type') != 'cuboid' or r.get('from_name') not in controls:
            continue
        value = r.get('value') or {}
        labels = value.get('labels') or separate_labels.get(r.get('id')) or []
        item = {
            'id': r.get('id'),
            'label': str(labels[0]) if labels else 'Unknown',
            'attributes': attributes.get(r.get('id'), {}),
            'frame_attributes': value.get('frameAttributes') if isinstance(value.get('frameAttributes'), dict) else {},
            'attribute_defaults': (attribute_defaults or {}).get(r.get('from_name')) or {},
        }
        if isinstance(value.get('sequence'), list):
            keys = _keyframes(value['sequence'])
            if not any(k.get('enabled', True) for k in keys):
                continue
            item['sequence'] = keys
        elif value.get('position') and value.get('dimensions'):
            item['box'] = box_from_value(value)
        else:
            continue
        items.append(item)
    return items


def boxes_from_annotation(annotation, controls):
    """Single-scan boxes only: [{box, label, attributes, id}]."""
    return [item for item in items_from_annotation(annotation, controls) if 'box' in item]


def track_ids(items):
    """Integer track ids per task, ordered by first appearance."""
    def first(item):
        if 'sequence' in item:
            return min((k['frame'] for k in item['sequence'] if k.get('enabled', True)), default=0)
        return 0

    ordered = sorted(items, key=lambda item: (first(item), str(item['id'])))
    return {item['id']: index for index, item in enumerate(ordered)}


def frame_items(items, frame, poses, sequence_task):
    """Items present in ``frame`` with their box in that frame's coordinates.

    Per-frame attributes in effect in ``frame`` are merged into ``attributes`` (they win over per-region ones).
    """
    out = []
    for item in items:
        if 'sequence' in item:
            box = box_at(item['sequence'], frame, poses)
        else:
            # a single-scan box in a sequence task has no frame; only single scans use it
            box = None if sequence_task else item['box']
        if box is not None:
            per_frame = frame_attributes_at(
                item.get('frame_attributes'), frame if 'sequence' in item else 0, item.get('attribute_defaults')
            )
            out.append({**item, 'box': box, 'attributes': {**item.get('attributes', {}), **per_frame}})
    return out


def _unique_name(url, fallback, used, suffix=None):
    """File stem of ``url`` (or ``fallback``), made unique with ``suffix`` and then a counter."""
    stem = os.path.splitext(os.path.basename(str(url or '').split('?')[0]))[0]
    stem = re.sub(r'[^A-Za-z0-9_.-]+', '_', stem).strip('._') or re.sub(r'[^A-Za-z0-9_.-]+', '_', fallback)
    name = stem if stem not in used else f'{stem}_{suffix or fallback}'
    counter = 2
    while name in used:
        name = f'{stem}_{suffix or fallback}_{counter}'
        counter += 1
    used.add(name)
    return name


def frame_name(task, data_key, used):
    url = (task.get('data') or {}).get(data_key)
    return _unique_name(url if isinstance(url, str) else '', f'task_{task.get("id")}', used, suffix=task.get('id'))


def _category(label):
    return re.sub(r'\s+', '_', label.strip()) or 'Unknown'


def _fmt(v, digits=4):
    return f'{v:.{digits}f}'


def openpcdet_line(item):
    b = item['box']
    values = [b['x'], b['y'], b['z'], b['length'], b['width'], b['height'], b['yaw']]
    return ' '.join(_fmt(v) for v in values) + f' {_category(item["label"])}'


def kitti_line(item, calib):
    b = item['box']
    cam = to_kitti_camera(b, calib)
    occluded = 0
    truncated = 0.0
    for name, value in item['attributes'].items():
        lowered = (name or '').lower()
        first = value[0] if isinstance(value, list) and value else value
        if 'occlu' in lowered:
            level = _occlusion(first[0] if isinstance(first, list) and first else first)
            occluded = level if level is not None else occluded
        elif 'truncat' in lowered:
            try:
                truncated = min(1.0, max(0.0, float(first)))
            except (TypeError, ValueError):
                pass
    x1, y1, x2, y2 = cam['bbox'] or (0.0, 0.0, 0.0, 0.0)
    loc = cam['location']
    fields = [
        _category(item['label']),
        _fmt(truncated, 2),
        str(occluded),
        _fmt(cam['alpha'], 2),
        _fmt(x1, 2),
        _fmt(y1, 2),
        _fmt(x2, 2),
        _fmt(y2, 2),
        _fmt(b['height'], 2),
        _fmt(b['width'], 2),
        _fmt(b['length'], 2),
        _fmt(loc[0], 2),
        _fmt(loc[1], 2),
        _fmt(loc[2], 2),
        _fmt(cam['rotation_y'], 2),
    ]
    return ' '.join(fields)


def kitti_calib_text(calib):
    k = calib['intrinsic']
    p = list(k) if len(k) == 12 else [k[0], k[1], k[2], 0.0, k[3], k[4], k[5], 0.0, k[6], k[7], k[8], 0.0]
    e = calib['extrinsic']

    def row(values):
        return ' '.join(f'{v:.12e}' for v in values)

    lines = [f'P{i}: {row(p)}' for i in range(4)]
    # the rectification is already folded into the extrinsic
    lines.append(f'R0_rect: {row([1, 0, 0, 0, 1, 0, 0, 0, 1])}')
    lines.append(f'Tr_velo_to_cam: {row(e[:12])}')
    lines.append(f'Tr_imu_to_velo: {row([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0])}')
    return '\n'.join(lines) + '\n'


def _first_calibrated(cameras):
    return next((c for c in cameras or [] if c['calibration']), None)


def _write(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + ('\n' if lines else ''))


def convert_tasks(tasks, output_dir, format_name, spec):
    """Write the export for ``tasks`` (Label Studio JSON export) into ``output_dir``. Returns a report dict."""
    if format_name not in BOX_FORMAT_NAMES:
        raise ValueError(f'Unknown 3D box export format: {format_name}')
    if not spec:
        raise ValueError('The labeling config has no <Cuboid> attached to a <PointCloud>')
    os.makedirs(output_dir, exist_ok=True)
    controls = {s['control'] for s in spec}
    attribute_defaults = {s['control']: s.get('attributes') or {} for s in spec}
    data_key = spec[0]['data_key']
    images_key = next((s['images_key'] for s in spec if s['images_key']), None)
    tracking = format_name == PointCloudFormat.KITTI_TRACKING.name
    kitti = format_name == PointCloudFormat.KITTI_3D.name

    used, listed, mapping, tracks_index, attributes_index = set(), [], [], {}, {}
    report = {'format': format_name, 'frames': 0, 'boxes': 0, 'tracks': 0, 'skipped': []}
    for task in tasks:
        annotation = pick_annotation(task)
        if annotation is None:
            report['skipped'].append({'task': task.get('id'), 'reason': 'no annotation'})
            continue
        items = items_from_annotation(annotation, controls, attribute_defaults)
        frames, is_sequence = task_frames(task, data_key, images_key)
        poses = [f['pose'] for f in frames]
        ids = track_ids(items)
        if is_sequence and any('box' in item for item in items):
            report['skipped'].append({'task': task.get('id'), 'reason': 'single-scan boxes in a sequence task'})

        if tracking:
            sequence_camera = next((c for f in frames for c in [_first_calibrated(f['cameras'])] if c), None)
            if sequence_camera is None:
                report['skipped'].append({'task': task.get('id'), 'reason': 'no camera calibration'})
                continue
            data = task.get('data') or {}
            seq_name = _unique_name(
                '', str(data.get('sequence') or data.get('name') or f'{len(listed):04d}'), used
            )
            lines = []
            for frame in frames:
                camera = _first_calibrated(frame['cameras']) or sequence_camera
                for item in frame_items(items, frame['index'], poses, is_sequence):
                    lines.append(f'{frame["index"]} {ids[item["id"]]} {kitti_line(item, camera["calibration"])}')
                    report['boxes'] += 1
            _write(os.path.join(output_dir, 'label_02', f'{seq_name}.txt'), lines)
            _write(
                os.path.join(output_dir, 'calib', f'{seq_name}.txt'),
                kitti_calib_text(sequence_camera['calibration']).splitlines(),
            )
            if any(poses):
                identity = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0]
                _write(
                    os.path.join(output_dir, 'poses', f'{seq_name}.txt'),
                    [' '.join(f'{v:.9e}' for v in (p[:12] if p else identity)) for p in poses],
                )
            listed.append(seq_name)
            report['frames'] += len(frames)
            report['tracks'] += len(ids)
            mapping.append(
                {
                    'sequence': seq_name,
                    'task': task.get('id'),
                    'annotation': annotation.get('id'),
                    'camera': sequence_camera['name'],
                    'frames': [f['url'] for f in frames],
                    'tracks': {str(region): track for region, track in ids.items()},
                }
            )
            continue

        report['tracks'] += len(ids) if is_sequence else 0
        for frame in frames:
            present = frame_items(items, frame['index'], poses, is_sequence)
            calib = camera_name = None
            if kitti:
                camera = _first_calibrated(frame['cameras'])
                if camera is None:
                    report['skipped'].append(
                        {
                            'task': task.get('id'),
                            **({'frame': frame['index']} if is_sequence else {}),
                            'reason': 'no camera calibration',
                        }
                    )
                    continue
                calib, camera_name = camera['calibration'], camera['name']
            suffix = f'{task.get("id")}_{frame["index"]:04d}' if is_sequence else task.get('id')
            name = _unique_name(frame['url'], f'task_{suffix}', used, suffix=suffix)
            if kitti:
                lines = [kitti_line(item, calib) for item in present]
                _write(os.path.join(output_dir, 'calib', f'{name}.txt'), kitti_calib_text(calib).splitlines())
                _write(os.path.join(output_dir, 'label_2', f'{name}.txt'), lines)
            else:
                lines = [openpcdet_line(item) for item in present]
                _write(os.path.join(output_dir, 'labels', f'{name}.txt'), lines)
                if is_sequence:
                    tracks_index[name] = [
                        {'track_id': ids[item['id']], 'region': item['id'], 'label': item['label']} for item in present
                    ]
                if any(item['attributes'] for item in present):
                    attributes_index[name] = [
                        {'region': item['id'], 'label': item['label'], 'attributes': item['attributes']}
                        for item in present
                    ]
            listed.append(name)
            report['frames'] += 1
            report['boxes'] += len(present)
            mapping.append(
                {
                    'frame': name,
                    'task': task.get('id'),
                    'annotation': annotation.get('id'),
                    'pointcloud': frame['url'],
                    **({'sequence_frame': frame['index']} if is_sequence else {}),
                    **({'camera': camera_name} if kitti else {}),
                }
            )

    _write(os.path.join(output_dir, 'ImageSets', 'train.txt'), listed)
    with open(os.path.join(output_dir, 'mapping.json'), 'w', encoding='utf-8') as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    if tracks_index:
        with open(os.path.join(output_dir, 'tracks.json'), 'w', encoding='utf-8') as f:
            json.dump(tracks_index, f, ensure_ascii=False, indent=2)
    if attributes_index:
        with open(os.path.join(output_dir, 'attributes.json'), 'w', encoding='utf-8') as f:
            json.dump(attributes_index, f, ensure_ascii=False, indent=2)
    with open(os.path.join(output_dir, 'export_report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def load_tasks(input_data, is_dir=False):
    """Read a Label Studio JSON export (file, or directory of *.json files)."""
    paths = []
    if is_dir:
        paths = [os.path.join(input_data, n) for n in sorted(os.listdir(input_data)) if n.endswith('.json')]
    else:
        paths = [input_data]
    tasks = []
    for path in paths:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        tasks.extend(data if isinstance(data, list) else [data])
    return tasks
