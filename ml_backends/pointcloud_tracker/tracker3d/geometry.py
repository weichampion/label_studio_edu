"""Boxes, poses and keyframe interpolation.

Same conventions as the Label Studio editor (web/libs/editor/src/tags/object/PointCloud/sequence.ts) and the
exporter (label_studio/pointcloud3d/formats.py): LiDAR frame x forward, y left, z up; yaw CCW around +z;
poses are 4x4 row-major LiDAR -> world; interpolation between keyframes happens in world coordinates.
"""

import math


def normalize_angle(a):
    a = math.fmod(a, 2 * math.pi)
    if a <= -math.pi:
        a += 2 * math.pi
    if a > math.pi:
        a -= 2 * math.pi
    return a


def angle_diff(a, b):
    return normalize_angle(a - b)


def flat(values):
    out = []
    for v in values or []:
        if isinstance(v, (list, tuple)):
            out.extend(flat(v))
        else:
            out.append(float(v))
    return out


def normalize_pose(raw):
    if raw is None:
        return None
    try:
        values = flat(raw)
    except (TypeError, ValueError):
        return None
    if len(values) == 12:
        values += [0.0, 0.0, 0.0, 1.0]
    return values if len(values) == 16 else None


def apply(m, x, y, z):
    return (
        m[0] * x + m[1] * y + m[2] * z + m[3],
        m[4] * x + m[5] * y + m[6] * z + m[7],
        m[8] * x + m[9] * y + m[10] * z + m[11],
    )


def invert_rigid(m):
    rt = [m[0], m[4], m[8], m[1], m[5], m[9], m[2], m[6], m[10]]
    t = [m[3], m[7], m[11]]
    out = []
    for r in range(3):
        row = rt[r * 3 : r * 3 + 3]
        out += row + [-(row[0] * t[0] + row[1] * t[1] + row[2] * t[2])]
    return out + [0.0, 0.0, 0.0, 1.0]


def transform_box(box, m):
    x, y, z = apply(m, box['x'], box['y'], box['z'])
    return {**box, 'x': x, 'y': y, 'z': z, 'yaw': normalize_angle(box['yaw'] + math.atan2(m[4], m[0]))}


def to_world(box, pose):
    return transform_box(box, pose) if pose else box


def from_world(box, pose):
    return transform_box(box, invert_rigid(pose)) if pose else box


def lerp_box(a, b, t):
    out = {k: a[k] + (b[k] - a[k]) * t for k in ('x', 'y', 'z', 'length', 'width', 'height')}
    out['yaw'] = normalize_angle(a['yaw'] + angle_diff(b['yaw'], a['yaw']) * t)
    return out


def box_from_value(v):
    p, d, r = v.get('position') or {}, v.get('dimensions') or {}, v.get('rotation') or {}
    return {
        'x': float(p.get('x', 0)),
        'y': float(p.get('y', 0)),
        'z': float(p.get('z', 0)),
        'length': float(d.get('length', 1)),
        'width': float(d.get('width', 1)),
        'height': float(d.get('height', 1)),
        'yaw': normalize_angle(float(r.get('z', 0))),
    }


def value_from_box(b):
    def r(v, n=4):
        return round(float(v), n)

    return {
        'position': {'x': r(b['x']), 'y': r(b['y']), 'z': r(b['z'])},
        'dimensions': {'length': r(b['length']), 'width': r(b['width']), 'height': r(b['height'])},
        'rotation': {'x': 0, 'y': 0, 'z': r(normalize_angle(b['yaw']), 6)},
    }


def keyframes_sorted(sequence):
    return sorted((k for k in sequence or [] if isinstance(k, dict) and 'frame' in k), key=lambda k: k['frame'])


def enabled(k):
    return k is not None and k.get('enabled', True) is not False


def box_at(sequence, frame, poses=()):
    """Box of a track at ``frame`` (that frame's LiDAR coordinates) or None; same rules as the editor."""
    keys = keyframes_sorted(sequence)
    prev = None
    for k in keys:
        if k['frame'] > frame:
            break
        prev = k
    if not enabled(prev):
        return None
    prev_box = box_from_value(prev)
    if prev['frame'] == frame:
        return prev_box

    def pose(i):
        return poses[i] if 0 <= i < len(poses) else None

    world = to_world(prev_box, pose(prev['frame']))
    nxt = next((k for k in keys if k['frame'] > frame), None)
    if enabled(nxt):
        t = (frame - prev['frame']) / (nxt['frame'] - prev['frame'])
        world = lerp_box(world, to_world(box_from_value(nxt), pose(nxt['frame'])), t)
    return from_world(world, pose(frame))


def points_in_box(points, box, margin=0.0):
    c, s = math.cos(box['yaw']), math.sin(box['yaw'])
    hl, hw, hh = box['length'] / 2 + margin, box['width'] / 2 + margin, box['height'] / 2 + margin
    out = []
    for p in points:
        dx, dy = p[0] - box['x'], p[1] - box['y']
        if abs(p[2] - box['z']) > hh:
            continue
        if abs(c * dx + s * dy) <= hl and abs(-s * dx + c * dy) <= hw:
            out.append(p)
    return out


def bev_iou(a, b, samples=24):
    """Approximate BEV IoU of two yawed boxes by sampling a grid over box a (adequate for matching)."""
    hits = 0
    ca, sa = math.cos(a['yaw']), math.sin(a['yaw'])
    cb, sb = math.cos(b['yaw']), math.sin(b['yaw'])
    for i in range(samples):
        for j in range(samples):
            lx = (i + 0.5) / samples * a['length'] - a['length'] / 2
            ly = (j + 0.5) / samples * a['width'] - a['width'] / 2
            x, y = a['x'] + ca * lx - sa * ly, a['y'] + sa * lx + ca * ly
            dx, dy = x - b['x'], y - b['y']
            if abs(cb * dx + sb * dy) <= b['length'] / 2 and abs(-sb * dx + cb * dy) <= b['width'] / 2:
                hits += 1
    inter = hits / (samples * samples) * a['length'] * a['width']
    union = a['length'] * a['width'] + b['length'] * b['width'] - inter
    return inter / union if union > 0 else 0.0
