#!/usr/bin/env python3
"""Generate the synthetic driving scene used by the 3D point cloud templates.

Outputs (standard library only, deterministic):
  label_studio/core/static/samples/pointcloud/scene_000001.pcd          binary PCD, x y z intensity
  label_studio/core/static/samples/pointcloud/scene_000001_front.png    front camera image
  label_studio/core/static/samples/pointcloud/scene_000001_tasks.json   import file with pre-annotations
  label_studio/core/static/templates/lidar-3d-object-detection.png      template thumbnails
  label_studio/core/static/templates/lidar-camera-fusion.png
  label_studio/core/static/samples/pointcloud/sequence_0001/0000NN.pcd       8-frame sequence with ego poses
  label_studio/core/static/samples/pointcloud/sequence_0001_tasks.json      sequence task with keyframed tracks
  label_studio/core/static/templates/lidar-sequence-tracking.png

The LiDAR is simulated as a 32-beam spinning sensor 1.73 m above a flat road (KITTI-like mounting).
Frame: x forward, y left, z up. Usage: python scripts/generate_pointcloud_samples.py
"""

import json
import math
import os
import random
import struct
import zlib

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'label_studio', 'core', 'static'))
SAMPLES = os.path.join(ROOT, 'samples', 'pointcloud')
TEMPLATES = os.path.join(ROOT, 'templates')

SENSOR_HEIGHT = 1.73
GROUND_Z = -SENSOR_HEIGHT
MAX_RANGE = 70.0

# (label, x, y, length, width, height, yaw, intensity)
OBJECTS = [
    ('Car', 12.0, -2.2, 4.4, 1.85, 1.55, 0.0, 0.55),
    ('Car', 22.5, 3.4, 4.1, 1.80, 1.50, math.pi, 0.5),
    ('Car', 7.5, 6.8, 4.6, 1.90, 1.60, math.radians(88), 0.6),
    ('Truck', 34.0, -3.0, 8.5, 2.5, 3.2, 0.0, 0.45),
    ('Pedestrian', 9.0, -6.2, 0.6, 0.6, 1.75, math.radians(90), 0.35),
    ('Pedestrian', 15.5, -6.8, 0.6, 0.6, 1.65, math.radians(-90), 0.35),
    ('Cyclist', 18.0, -0.2, 1.8, 0.6, 1.7, math.radians(5), 0.4),
]
# static scene: building walls along both sides and a few poles (not annotated)
WALLS = [(-10.0, 80.0, 11.0, 6.0), (-10.0, 80.0, -11.5, 6.0)]  # x0, x1, y, height
POLES = [(14.0, -9.0), (28.0, -9.2), (42.0, -9.0), (20.0, 9.5)]

# front camera: KITTI-like axes (x right, y down, z forward), 0.27 m ahead and 0.08 m below the LiDAR
IMAGE_W, IMAGE_H = 960, 540
FX = FY = 620.0
INTRINSIC = [FX, 0.0, IMAGE_W / 2, 0.0, FY, IMAGE_H / 2 - 20, 0.0, 0.0, 1.0]
EXTRINSIC = [
    0.0, -1.0, 0.0, 0.0,
    0.0, 0.0, -1.0, -0.08,
    1.0, 0.0, 0.0, -0.27,
    0.0, 0.0, 0.0, 1.0,
]


def box_hit(ox, oy, oz, dx, dy, dz, obj):
    """Distance along the ray to a yawed box, or None (slab method in the box frame)."""
    _, cx, cy, l, w, h, yaw, _ = obj
    cz = GROUND_Z + h / 2
    c, s = math.cos(yaw), math.sin(yaw)
    px, py, pz = ox - cx, oy - cy, oz - cz
    lo = (c * px + s * py, -s * px + c * py, pz)
    ld = (c * dx + s * dy, -s * dx + c * dy, dz)
    t0, t1 = -math.inf, math.inf
    for o, d, half in zip(lo, ld, (l / 2, w / 2, h / 2)):
        if abs(d) < 1e-12:
            if abs(o) > half:
                return None
            continue
        a, b = (-half - o) / d, (half - o) / d
        if a > b:
            a, b = b, a
        t0, t1 = max(t0, a), min(t1, b)
        if t0 > t1:
            return None
    return t0 if t0 > 0 else None


def simulate():
    rng = random.Random(7)
    elevations = [math.radians(-24.8 + i * (27.8 / 31)) for i in range(32)]
    points = []
    for elev in elevations:
        for step in range(900):
            az = math.radians(step * 0.4)
            dx, dy, dz = math.cos(elev) * math.cos(az), math.cos(elev) * math.sin(az), math.sin(elev)
            best, intensity = MAX_RANGE, None
            if dz < -1e-6:
                t = GROUND_Z / dz
                if t < best:
                    best, intensity = t, 0.08 + 0.12 * rng.random()
                    # lane markings are brighter
                    hx, hy = t * dx, t * dy
                    if abs(abs(hy) - 1.75) < 0.08 and (hx % 6.0) < 3.0:
                        intensity = 0.85
            for obj in OBJECTS:
                t = box_hit(0, 0, 0, dx, dy, dz, obj)
                if t is not None and t < best:
                    best, intensity = t, obj[7] + 0.15 * rng.random()
            for x0, x1, wy, wh in WALLS:
                if abs(dy) > 1e-6:
                    t = wy / dy
                    if 0 < t < best:
                        hx, hz = t * dx, t * dz
                        if x0 <= hx <= x1 and GROUND_Z <= hz <= GROUND_Z + wh:
                            best, intensity = t, 0.25 + 0.1 * rng.random()
            for px, py in POLES:
                # thin vertical cylinder, radius 0.15 m, 5 m tall
                a = dx * dx + dy * dy
                b = -2 * (dx * px + dy * py)
                cc = px * px + py * py - 0.15**2
                disc = b * b - 4 * a * cc
                if a > 1e-9 and disc >= 0:
                    t = (-b - math.sqrt(disc)) / (2 * a)
                    if 0 < t < best and GROUND_Z <= t * dz <= GROUND_Z + 5:
                        best, intensity = t, 0.6
            if intensity is None:
                continue
            r = best + rng.gauss(0, 0.01)
            points.append((r * dx, r * dy, r * dz, min(1.0, intensity)))
    return points


def write_pcd(path, points):
    header = (
        '# .PCD v0.7 - synthetic sample generated by scripts/generate_pointcloud_samples.py\n'
        'VERSION 0.7\nFIELDS x y z intensity\nSIZE 4 4 4 4\nTYPE F F F F\nCOUNT 1 1 1 1\n'
        f'WIDTH {len(points)}\nHEIGHT 1\nVIEWPOINT 0 0 0 1 0 0 0\nPOINTS {len(points)}\nDATA binary\n'
    )
    with open(path, 'wb') as f:
        f.write(header.encode('ascii'))
        for p in points:
            f.write(struct.pack('<ffff', *p))


# ------------------------------------------------------------------------------------------------- images


class Canvas:
    def __init__(self, w, h, color):
        self.w, self.h = w, h
        self.px = bytearray(bytes(color) * (w * h))

    def set(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.px[i : i + 3] = bytes(color)

    def dot(self, x, y, color, r=1):
        for yy in range(int(y) - r + 1, int(y) + r):
            for xx in range(int(x) - r + 1, int(x) + r):
                self.set(xx, yy, color)

    def line(self, x0, y0, x1, y1, color, width=1):
        steps = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        for i in range(steps + 1):
            t = i / steps
            self.dot(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, color, width)

    def fill_convex(self, poly, color):
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        n = len(poly)
        for y in range(max(0, int(min(ys))), min(self.h, int(max(ys)) + 1)):
            for x in range(max(0, int(min(xs))), min(self.w, int(max(xs)) + 1)):
                sign = 0
                inside = True
                for i in range(n):
                    ax, ay = poly[i]
                    bx, by = poly[(i + 1) % n]
                    cross = (bx - ax) * (y - ay) - (by - ay) * (x - ax)
                    if cross != 0:
                        if sign == 0:
                            sign = 1 if cross > 0 else -1
                        elif (cross > 0) != (sign > 0):
                            inside = False
                            break
                if inside:
                    self.set(x, y, color)

    def png(self, path):
        raw = b''.join(b'\x00' + bytes(self.px[y * self.w * 3 : (y + 1) * self.w * 3]) for y in range(self.h))

        def chunk(tag, data):
            body = tag + data
            return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body) & 0xFFFFFFFF)

        with open(path, 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n')
            f.write(chunk(b'IHDR', struct.pack('>IIBBBBB', self.w, self.h, 8, 2, 0, 0, 0)))
            f.write(chunk(b'IDAT', zlib.compress(raw, 9)))
            f.write(chunk(b'IEND', b''))


def turbo(t):
    x = min(1.0, max(0.0, t))
    r = 0.13572138 + x * (4.6153926 + x * (-42.66032258 + x * (132.13108234 + x * (-152.94239396 + x * 59.28637943))))
    g = 0.09140261 + x * (2.19418839 + x * (4.84296658 + x * (-14.18503333 + x * (4.27729857 + x * 2.82956604))))
    b = 0.1066733 + x * (12.64194608 + x * (-60.58204836 + x * (110.36276771 + x * (-89.90310912 + x * 27.34824973))))
    return tuple(int(255 * min(1.0, max(0.0, v))) for v in (r, g, b))


LABEL_COLORS = {
    'Car': (255, 163, 158),
    'Truck': (255, 214, 102),
    'Pedestrian': (212, 56, 13),
    'Cyclist': (255, 192, 105),
}


def corners(obj):
    _, x, y, l, w, h, yaw, _ = obj
    z = GROUND_Z + h / 2
    c, s = math.cos(yaw), math.sin(yaw)
    out = []
    for lz in (-h / 2, h / 2):
        for lx, ly in ((l / 2, w / 2), (l / 2, -w / 2), (-l / 2, -w / 2), (-l / 2, w / 2)):
            out.append((x + c * lx - s * ly, y + s * lx + c * ly, z + lz))
    return out


EDGES = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]


def project(p):
    e = EXTRINSIC
    cx = e[0] * p[0] + e[1] * p[1] + e[2] * p[2] + e[3]
    cy = e[4] * p[0] + e[5] * p[1] + e[6] * p[2] + e[7]
    cz = e[8] * p[0] + e[9] * p[1] + e[10] * p[2] + e[11]
    if cz < 0.5:
        return None
    k = INTRINSIC
    return (k[0] * cx + k[2] * cz) / cz, (k[4] * cy + k[5] * cz) / cz, cz


def hull(points):
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def camera_image(points, path):
    img = Canvas(IMAGE_W, IMAGE_H, (150, 185, 215))
    # ground below the horizon (horizon row = principal point row for a level camera)
    horizon = int(INTRINSIC[5] + FY * 0.08 / 400)
    for y in range(horizon, IMAGE_H):
        shade = 70 + int(40 * (y - horizon) / (IMAGE_H - horizon))
        img.px[y * IMAGE_W * 3 : (y + 1) * IMAGE_W * 3] = bytes((shade, shade, shade + 5)) * IMAGE_W
    # walls
    for x0, x1, wy, wh in WALLS:
        quad = [(x0 if x0 > 1 else 1.0, wy, GROUND_Z), (x1, wy, GROUND_Z), (x1, wy, GROUND_Z + wh), (1.0, wy, GROUND_Z + wh)]
        proj = [project(p) for p in quad]
        if all(proj):
            img.fill_convex(hull([(p[0], p[1]) for p in proj]), (176, 160, 140))
    # objects, far to near
    for obj in sorted(OBJECTS, key=lambda o: -math.hypot(o[1], o[2])):
        proj = [project(c) for c in corners(obj)]
        if not all(proj):
            continue
        color = tuple(int(v * 0.75) for v in LABEL_COLORS.get(obj[0], (200, 200, 200)))
        img.fill_convex(hull([(p[0], p[1]) for p in proj]), color)
    # lidar returns as small depth-coloured dots (shows the calibration is consistent)
    for x, y, z, _ in points[::3]:
        p = project((x, y, z))
        if p:
            img.set(int(p[0]), int(p[1]), turbo(p[2] / 45))
    img.png(path)


def bev_thumbnail(points, path, with_camera=False):
    w, h = 1229, 756
    img = Canvas(w, h, (20, 23, 28))
    scale = 13.0  # px per metre; x forward = up, y left = left
    ox, oy = w / 2, h - 60

    def to_px(x, y):
        return ox - y * scale, oy - x * scale

    for gx in range(-40, 41, 10):
        x0, y0 = to_px(-5, gx)
        x1, y1 = to_px(60, gx)
        img.line(x0, y0, x1, y1, (45, 50, 58))
    for gy in range(0, 61, 10):
        x0, y0 = to_px(gy, -45)
        x1, y1 = to_px(gy, 45)
        img.line(x0, y0, x1, y1, (45, 50, 58))
    for x, y, z, _ in points:
        px, py = to_px(x, y)
        img.dot(px, py, turbo((z - GROUND_Z + 0.3) / 3.5), 1)
    for obj in OBJECTS:
        cs = corners(obj)[:4]
        color = LABEL_COLORS.get(obj[0], (255, 255, 255))
        pts = [to_px(c[0], c[1]) for c in cs]
        for i in range(4):
            img.line(*pts[i], *pts[(i + 1) % 4], color, 2)
        # heading
        fx, fy = (pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2
        cx, cy = to_px(obj[1], obj[2])
        img.line(cx, cy, fx, fy, color, 1)
    # ego vehicle
    img.fill_convex([to_px(2, 0.9), to_px(2, -0.9), to_px(-2, -0.9), to_px(-2, 0.9)], (90, 140, 255))
    if with_camera:
        # camera frustum
        for side in (-1, 1):
            x1, y1 = to_px(0, 0)
            x2, y2 = to_px(40, side * 40 * (IMAGE_W / 2) / FX)
            img.line(x1, y1, x2, y2, (90, 140, 255), 1)
    img.png(path)


def tasks_json(points_url, image_url):
    result = []
    for i, (label, x, y, l, w, h, yaw, _) in enumerate(OBJECTS):
        result.append(
            {
                'id': f'gt{i}',
                'from_name': 'box',
                'to_name': 'pcd',
                'type': 'cuboid',
                'value': {
                    'position': {'x': x, 'y': y, 'z': round(GROUND_Z + h / 2, 4)},
                    'dimensions': {'length': l, 'width': w, 'height': h},
                    'rotation': {'x': 0, 'y': 0, 'z': round(yaw, 6)},
                    'labels': [label],
                },
            }
        )
    return [
        {
            'data': {
                'pointcloud': points_url,
                'cameras': [
                    {
                        'name': 'front',
                        'url': image_url,
                        'intrinsic': INTRINSIC,
                        'extrinsic': EXTRINSIC,
                        'width': IMAGE_W,
                        'height': IMAGE_H,
                    }
                ],
            },
            'predictions': [{'model_version': 'synthetic-ground-truth', 'score': 1.0, 'result': result}],
        }
    ]


# ------------------------------------------------------------------------------------------------- sequence

SEQ_FRAMES = 8
# (label, x0, y0, l, w, h, yaw, intensity, vx, vy, last_frame) in world coordinates, displacement per frame
SEQ_OBJECTS = [
    ('Car', 12.0, -2.2, 4.4, 1.85, 1.55, 0.0, 0.55, 1.3, 0.0, None),
    ('Car', 40.0, 3.4, 4.1, 1.80, 1.50, math.pi, 0.5, -1.2, 0.0, None),
    ('Car', 7.5, 6.8, 4.6, 1.90, 1.60, math.radians(88), 0.6, 0.0, 0.0, None),
    ('Truck', 30.0, -6.0, 8.5, 2.5, 3.2, 0.0, 0.45, 0.0, 0.0, None),
    ('Pedestrian', 18.0, -7.5, 0.6, 0.6, 1.75, math.radians(90), 0.35, 0.0, 0.15, 5),
    ('Cyclist', 20.0, -0.2, 1.8, 0.6, 1.7, 0.0, 0.4, 0.6, 0.0, None),
]


def ego_pose(frame):
    """LiDAR -> world 4x4 (row-major): the ego vehicle drives ~1 m per frame along a gentle left curve."""
    yaw = 0.012 * frame
    x = sum(math.cos(0.012 * i) for i in range(frame))
    y = sum(math.sin(0.012 * i) for i in range(frame))
    c, s = math.cos(yaw), math.sin(yaw)
    return [c, -s, 0.0, x, s, c, 0.0, y, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def seq_object(obj, frame):
    label, x0, y0, l, w, h, yaw, inten, vx, vy, last = obj
    if last is not None and frame > last:
        return None
    return (label, x0 + vx * frame, y0 + vy * frame, l, w, h, yaw, inten)


def to_lidar(pose, x, y, z):
    dx, dy, dz = x - pose[3], y - pose[7], z - pose[11]
    return (pose[0] * dx + pose[4] * dy + pose[8] * dz,
            pose[1] * dx + pose[5] * dy + pose[9] * dz,
            pose[2] * dx + pose[6] * dy + pose[10] * dz)


def lidar_box(obj, pose):
    """World object -> (label, x, y, l, w, h, yaw, intensity) in the frame's LiDAR coordinates."""
    label, x, y, l, w, h, yaw, inten = obj
    lx, ly, _ = to_lidar(pose, x, y, 0.0)
    return (label, lx, ly, l, w, h, yaw - math.atan2(pose[4], pose[0]), inten)


def simulate_frame(frame, rng, beams=24, steps=500):
    pose = ego_pose(frame)
    objects = [lidar_box(o, pose) for o in (seq_object(o, frame) for o in SEQ_OBJECTS) if o]
    # world walls along y = +11 / -11.5 expressed as lidar-frame lines: sample them as thin boxes instead
    walls = []
    for wy in (11.0, -11.5):
        cx, cy, _ = to_lidar(pose, 35.0, wy, 0.0)
        walls.append(('wall', cx, cy, 90.0, 0.2, 6.0, -math.atan2(pose[4], pose[0]), 0.25))
    elevations = [math.radians(-24.8 + i * (27.8 / (beams - 1))) for i in range(beams)]
    points = []
    for elev in elevations:
        for step in range(steps):
            az = 2 * math.pi * step / steps
            dx, dy, dz = math.cos(elev) * math.cos(az), math.cos(elev) * math.sin(az), math.sin(elev)
            best, intensity = MAX_RANGE, None
            if dz < -1e-6:
                t = GROUND_Z / dz
                if t < best:
                    best, intensity = t, 0.08 + 0.12 * rng.random()
            for obj in objects + walls:
                t = box_hit(0, 0, 0, dx, dy, dz, obj)
                if t is not None and t < best:
                    best, intensity = t, obj[7] + 0.15 * rng.random()
            if intensity is None:
                continue
            r = best + rng.gauss(0, 0.01)
            points.append((r * dx, r * dy, r * dz, min(1.0, intensity)))
    return points, objects, pose


def write_pcd_u8(path, points):
    """Binary PCD with float32 xyz and uint8 intensity (13 bytes per point) to keep the samples small."""
    header = (
        '# .PCD v0.7 - synthetic sequence frame generated by scripts/generate_pointcloud_samples.py\n'
        'VERSION 0.7\nFIELDS x y z intensity\nSIZE 4 4 4 1\nTYPE F F F U\nCOUNT 1 1 1 1\n'
        f'WIDTH {len(points)}\nHEIGHT 1\nVIEWPOINT 0 0 0 1 0 0 0\nPOINTS {len(points)}\nDATA binary\n'
    )
    with open(path, 'wb') as f:
        f.write(header.encode('ascii'))
        for x, y, z, i in points:
            f.write(struct.pack('<fffB', x, y, z, int(round(i * 255))))


def keyframe(frame, obj, enabled=True):
    label, x, y, l, w, h, yaw, _ = obj
    return {
        'frame': frame,
        'enabled': enabled,
        'position': {'x': round(x, 4), 'y': round(y, 4), 'z': round(GROUND_Z + h / 2, 4)},
        'dimensions': {'length': l, 'width': w, 'height': h},
        'rotation': {'x': 0, 'y': 0, 'z': round(math.atan2(math.sin(yaw), math.cos(yaw)), 6)},
    }


def sequence_sample():
    rng = random.Random(11)
    folder = os.path.join(SAMPLES, 'sequence_0001')
    os.makedirs(folder, exist_ok=True)
    frames, truth, first_points = [], [], None
    for f in range(SEQ_FRAMES):
        points, objects, pose = simulate_frame(f, rng)
        if first_points is None:
            first_points = points
        write_pcd_u8(os.path.join(folder, f'{f:06d}.pcd'), points)
        frames.append({'pointcloud': f'/static/samples/pointcloud/sequence_0001/{f:06d}.pcd',
                       'pose': [round(v, 9) for v in pose]})
        truth.append({o[0] + str(i): o for i, o in enumerate(lidar_box(seq_object(src, f), pose)
                      if seq_object(src, f) else None for src in [SEQ_OBJECTS[k] for k in range(len(SEQ_OBJECTS))]) if o})
    # keyframes only at the first and last frame (or where an object leaves): the editor interpolates the rest
    result = []
    last = SEQ_FRAMES - 1
    for k, src in enumerate(SEQ_OBJECTS):
        key = src[0] + str(k)
        end = src[10] if src[10] is not None else last
        sequence = [keyframe(0, truth[0][key]), keyframe(end, truth[end][key])] if end > 0 else [keyframe(0, truth[0][key])]
        if src[8] == 0 and src[9] == 0:
            sequence = sequence[:1]  # parked: one keyframe, held in world coordinates
        if end < last:
            sequence.append(keyframe(end + 1, truth[end][key], enabled=False))
        result.append({'id': f'track{k}', 'from_name': 'box', 'to_name': 'pcd', 'type': 'cuboid',
                       'value': {'sequence': sequence, 'framesCount': SEQ_FRAMES, 'labels': [src[0]]}})
    tasks = [{'data': {'sequence': '0001', 'frames': frames},
              'predictions': [{'model_version': 'synthetic-keyframes', 'score': 1.0, 'result': result}]}]
    with open(os.path.join(SAMPLES, 'sequence_0001_tasks.json'), 'w', encoding='utf-8') as f:
        json.dump(tasks, f, indent=1)
    # ground truth for every frame, used by the exporter tests
    with open(os.path.join(SAMPLES, 'sequence_0001_truth.json'), 'w', encoding='utf-8') as f:
        json.dump([{k: list(v[1:7]) for k, v in frame.items()} for frame in truth], f)
    sequence_thumbnail(first_points, os.path.join(TEMPLATES, 'lidar-sequence-tracking.png'))
    return len(frames)


def sequence_thumbnail(points, path):
    w, h = 1229, 756
    img = Canvas(w, h, (20, 23, 28))
    scale = 13.0
    ox, oy = w / 2, h - 60

    def to_px(x, y):
        return ox - y * scale, oy - x * scale

    for x, y, z, _ in points:
        img.dot(*to_px(x, y), turbo((z - GROUND_Z + 0.3) / 3.5), 1)
    pose0 = ego_pose(0)
    for src in SEQ_OBJECTS:
        color = LABEL_COLORS.get(src[0], (255, 255, 255))
        trail = []
        for f in range(SEQ_FRAMES):
            obj = seq_object(src, f)
            if obj:
                trail.append(lidar_box(obj, pose0))
        for a, b in zip(trail, trail[1:]):
            img.line(*to_px(a[1], a[2]), *to_px(b[1], b[2]), color, 1)
        for i, obj in enumerate(trail):
            cs = corners(obj)[:4]
            pts = [to_px(c[0], c[1]) for c in cs]
            for j in range(4):
                img.line(*pts[j], *pts[(j + 1) % 4], color, 2 if i in (0, len(trail) - 1) else 1)
    # ego path
    for f in range(SEQ_FRAMES):
        p = ego_pose(f)
        x, y, _ = to_lidar(pose0, p[3], p[7], 0.0)
        img.dot(*to_px(x, y), (90, 140, 255), 3)
    img.png(path)


# semantic classes of the segmentation sample (SemanticKITTI colors)
SEG_CLASSES = [
    ('Road', '#FF00FF'),
    ('Sidewalk', '#4B004B'),
    ('Building', '#FFC800'),
    ('Pole', '#FFF096'),
    ('Car', '#6496F5'),
    ('Truck', '#501EB4'),
    ('Pedestrian', '#FF1E1E'),
    ('Cyclist', '#C828FF'),
]


def point_class(x, y, z):
    """Ground-truth class of a simulated point (see simulate())."""
    for label, ox, oy, length, width, height, yaw, _ in OBJECTS:
        c, s_ = math.cos(yaw), math.sin(yaw)
        lx = c * (x - ox) + s_ * (y - oy)
        ly = -s_ * (x - ox) + c * (y - oy)
        if abs(lx) <= length / 2 + 0.05 and abs(ly) <= width / 2 + 0.05 and GROUND_Z - 0.05 <= z <= GROUND_Z + height + 0.05:
            return label
    if z < GROUND_Z + 0.05:
        return 'Road' if abs(y) < 6.0 else 'Sidewalk'
    for px, py in POLES:
        if math.hypot(x - px, y - py) < 0.3:
            return 'Pole'
    return 'Building'


def encode_point_labels(codes):
    """Same encoding as the editor (PointCloud/segmentation.ts) and pointcloud3d/formats.py."""
    out = bytearray()

    def varint(v):
        while v >= 0x80:
            out.append((v & 0x7F) | 0x80)
            v >>= 7
        out.append(v)

    i = 0
    while i < len(codes):
        j = i + 1
        while j < len(codes) and codes[j] == codes[i]:
            j += 1
        varint(codes[i])
        varint(j - i)
        i = j
    import base64

    return base64.b64encode(bytes(out)).decode('ascii')


def segmentation_sample(points):
    """Pre-labeled segmentation task for the single scan and the template thumbnail."""
    names = [name for name, _ in SEG_CLASSES]
    codes = [names.index(point_class(x, y, z)) + 1 for x, y, z, _ in points]
    tasks = [
        {
            'data': {'pointcloud': '/static/samples/pointcloud/scene_000001.pcd'},
            'predictions': [
                {
                    'model_version': 'synthetic-ground-truth',
                    'score': 1.0,
                    'result': [
                        {
                            'id': 'seg0',
                            'from_name': 'seg',
                            'to_name': 'pcd',
                            'type': 'pointlabels',
                            'value': {
                                'classes': names,
                                'frames': [{'frame': 0, 'count': len(codes), 'rle': encode_point_labels(codes)}],
                                'pointlabels': sorted({names[c - 1] for c in codes}, key=names.index),
                            },
                        }
                    ],
                }
            ],
        }
    ]
    with open(os.path.join(SAMPLES, 'scene_000001_segmentation_tasks.json'), 'w', encoding='utf-8') as f:
        json.dump(tasks, f, indent=2)

    w, h = 1229, 756
    img = Canvas(w, h, (20, 23, 28))
    scale = 13.0
    ox, oy = w / 2, h - 60
    rgb = [tuple(int(color[i : i + 2], 16) for i in (1, 3, 5)) for _, color in SEG_CLASSES]
    for (x, y, _, _), code in zip(points, codes):
        img.dot(ox - y * scale, oy - x * scale, rgb[code - 1], 2)
    img.fill_convex(
        [(ox - 0.9 * scale, oy - 2 * scale), (ox + 0.9 * scale, oy - 2 * scale), (ox + 0.9 * scale, oy + 2 * scale), (ox - 0.9 * scale, oy + 2 * scale)],
        (230, 230, 230),
    )
    img.png(os.path.join(TEMPLATES, 'lidar-semantic-segmentation.png'))
    return codes


# painted dashed lane lines of the simulated road (see the lane markings in simulate())
LANE_LINES = [('Dashed', 1.75), ('Dashed', -1.75)]


def lane_sample(points):
    """Pre-annotated lane task for the single scan and the template thumbnail."""
    result = []
    for i, (label, y) in enumerate(LANE_LINES):
        result.append(
            {
                'id': f'lane{i}',
                'from_name': 'lane',
                'to_name': 'pcd',
                'type': 'polyline3d',
                'value': {'points': [[float(x), y, GROUND_Z] for x in range(3, 64, 10)], 'labels': [label]},
            }
        )
        result.append(
            {
                'id': f'lane{i}',
                'from_name': 'color',
                'to_name': 'pcd',
                'type': 'choices',
                'value': {'choices': ['White']},
            }
        )
    tasks = [
        {
            'data': {'pointcloud': '/static/samples/pointcloud/scene_000001.pcd'},
            'predictions': [{'model_version': 'synthetic-ground-truth', 'score': 1.0, 'result': result}],
        }
    ]
    with open(os.path.join(SAMPLES, 'scene_000001_lanes_tasks.json'), 'w', encoding='utf-8') as f:
        json.dump(tasks, f, indent=2)

    w, h = 1229, 756
    img = Canvas(w, h, (20, 23, 28))
    scale = 13.0
    ox, oy = w / 2, h - 60
    for x, y, z, intensity in points:
        # intensity coloring, as recommended for lane annotation: paint stands out
        v = int(60 + 195 * min(1.0, intensity / 0.85))
        img.dot(ox - y * scale, oy - x * scale, (v, v, v) if z < GROUND_Z + 0.2 else (70, 80, 95), 1)
    for _, y in LANE_LINES:
        img.line(ox - y * scale, oy - 3 * scale, ox - y * scale, oy - 63 * scale, (250, 219, 20), 3)
    img.fill_convex(
        [(ox - 0.9 * scale, oy - 2 * scale), (ox + 0.9 * scale, oy - 2 * scale), (ox + 0.9 * scale, oy + 2 * scale), (ox - 0.9 * scale, oy + 2 * scale)],
        (90, 140, 255),
    )
    img.png(os.path.join(TEMPLATES, 'lidar-lane-annotation.png'))


def main():
    os.makedirs(SAMPLES, exist_ok=True)
    os.makedirs(TEMPLATES, exist_ok=True)
    points = simulate()
    write_pcd(os.path.join(SAMPLES, 'scene_000001.pcd'), points)
    camera_image(points, os.path.join(SAMPLES, 'scene_000001_front.png'))
    bev_thumbnail(points, os.path.join(TEMPLATES, 'lidar-3d-object-detection.png'))
    bev_thumbnail(points, os.path.join(TEMPLATES, 'lidar-camera-fusion.png'), with_camera=True)
    segmentation_sample(points)
    lane_sample(points)
    with open(os.path.join(SAMPLES, 'scene_000001_tasks.json'), 'w', encoding='utf-8') as f:
        json.dump(
            tasks_json(
                '/static/samples/pointcloud/scene_000001.pcd', '/static/samples/pointcloud/scene_000001_front.png'
            ),
            f,
            indent=2,
        )
    print(f'{len(points)} points written to {SAMPLES}')
    print(f'{sequence_sample()} sequence frames written to {os.path.join(SAMPLES, "sequence_0001")}')


if __name__ == '__main__':
    main()
