"""Multi-object tracking in world coordinates (AB3DMOT-style) and single-track extension.

* Motion model: constant-velocity Kalman filter on x and y (world frame, ego motion removed with poses).
* Association: greedy nearest-neighbour on predicted centre distance with class-dependent gates
  (CenterPoint-style greedy matching), detections of incompatible classes are not matched.
* Track management: tentative until ``min_hits`` matches, ended after ``max_age`` missed frames.
* Track-level reasoning: majority class vote corrected by speed (a fast "pedestrian" is a cyclist),
  robust size (upper quantile, completed with class priors), heading from motion when moving,
  centre shifted away from the sensor when only the near side was seen.
"""

import math
from collections import Counter

from .detector import PRIORS
from .geometry import angle_diff, from_world, normalize_angle, to_world

GATES = {'Car': 3.0, 'Truck': 3.5, 'Pedestrian': 1.2, 'Cyclist': 2.0}
COMPATIBLE = {
    'Car': {'Car', 'Truck'},
    'Truck': {'Truck', 'Car'},
    'Pedestrian': {'Pedestrian', 'Cyclist'},
    'Cyclist': {'Cyclist', 'Pedestrian'},
}


class Kalman1D:
    """Constant-velocity Kalman filter for one coordinate: state (position, velocity)."""

    def __init__(self, x, v=0.0, p=1.0, pv=4.0, q=0.3, r=0.25):
        self.x, self.v = x, v
        self.P = [[p, 0.0], [0.0, pv]]
        self.q, self.r = q, r

    def predict(self, dt=1.0):
        self.x += self.v * dt
        p = self.P
        q = self.q
        self.P = [
            [p[0][0] + dt * (p[1][0] + p[0][1]) + dt * dt * p[1][1] + q * dt ** 3 / 3, p[0][1] + dt * p[1][1] + q * dt ** 2 / 2],
            [p[1][0] + dt * p[1][1] + q * dt ** 2 / 2, p[1][1] + q * dt],
        ]

    def update(self, z):
        p = self.P
        s = p[0][0] + self.r
        k0, k1 = p[0][0] / s, p[1][0] / s
        y = z - self.x
        self.x += k0 * y
        self.v += k1 * y
        self.P = [[(1 - k0) * p[0][0], (1 - k0) * p[0][1]], [p[1][0] - k1 * p[0][0], p[1][1] - k1 * p[0][1]]]


class Track:
    def __init__(self, track_id, frame, det_world):
        b = det_world['box']
        self.id = track_id
        self.kx, self.ky = Kalman1D(b['x']), Kalman1D(b['y'])
        self.observations = {frame: det_world}  # frame -> world detection
        self.labels = Counter({det_world['label']: det_world['score']})
        self.hits = 1
        self.missed = 0
        self.last_frame = frame
        self.fixed = {}  # frames whose box is given by the annotator (world boxes)

    @property
    def position(self):
        return self.kx.x, self.ky.x

    @property
    def velocity(self):
        return self.kx.v, self.ky.v

    def predict(self):
        self.kx.predict()
        self.ky.predict()

    def update(self, frame, det_world):
        self.kx.update(det_world['box']['x'])
        self.ky.update(det_world['box']['y'])
        self.observations[frame] = det_world
        self.labels[det_world['label']] += det_world['score']
        self.hits += 1
        self.missed = 0
        self.last_frame = frame

    def label(self, frame_rate=10.0):
        label = self.labels.most_common(1)[0][0]
        speed = math.hypot(*self.velocity) * frame_rate  # m/s
        if label == 'Pedestrian' and speed > 3.5:
            label = 'Cyclist'
        return label


def _gate(label, speed):
    return GATES.get(label, 2.5) + 0.5 * speed


class MultiObjectTracker:
    def __init__(self, min_hits=2, max_age=3, frame_rate=10.0):
        self.min_hits = min_hits
        self.max_age = max_age
        self.frame_rate = frame_rate

    def run(self, detections_per_frame, poses):
        """detections_per_frame[f] = [detection in LiDAR f]; returns finished tracks (with >= min_hits)."""
        active, finished, next_id = [], [], 0
        for frame, detections in enumerate(detections_per_frame):
            pose = poses[frame] if frame < len(poses) else None
            world = [{**d, 'box': to_world(d['box'], pose)} for d in detections]
            for t in active:
                t.predict()
            pairs = []
            for ti, t in enumerate(active):
                speed = math.hypot(*t.velocity)
                for di, d in enumerate(world):
                    if d['label'] not in COMPATIBLE.get(t.labels.most_common(1)[0][0], {d['label']}):
                        continue
                    dist = math.hypot(d['box']['x'] - t.position[0], d['box']['y'] - t.position[1])
                    if dist <= _gate(d['label'], speed):
                        pairs.append((dist, ti, di))
            used_t, used_d = set(), set()
            for dist, ti, di in sorted(pairs):
                if ti in used_t or di in used_d:
                    continue
                active[ti].update(frame, world[di])
                used_t.add(ti)
                used_d.add(di)
            survivors = []
            for ti, t in enumerate(active):
                if ti not in used_t:
                    t.missed += 1
                if t.missed > self.max_age:
                    finished.append(t)
                else:
                    survivors.append(t)
            active = survivors
            for di, d in enumerate(world):
                if di not in used_d:
                    active.append(Track(next_id, frame, d))
                    next_id += 1
        finished.extend(active)
        return [t for t in finished if t.hits >= self.min_hits]


def robust_size(track, label):
    """Size from the upper quantile of the observations, completed with the class prior."""
    obs = list(track.observations.values())
    prior = PRIORS.get(label)

    def q(key):
        values = sorted(o['box'][key] for o in obs)
        return values[int(0.75 * (len(values) - 1))]

    length, width, height = q('length'), q('width'), q('height')
    if prior:
        length = max(length, prior[0] * 0.85)
        width = max(width, prior[1] * 0.85)
    return length, width, height


MOVING_DISPLACEMENT = {'Pedestrian': 0.5, 'Cyclist': 0.8, 'Car': 1.5, 'Truck': 1.5}


def _median(values):
    values = sorted(values)
    n = len(values)
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2


def _extent_along(box, yaw):
    """Extent of a detected box along direction ``yaw`` (its length or width, whichever is aligned)."""
    return box['length'] if abs(math.cos(angle_diff(box['yaw'], yaw))) >= math.cos(math.radians(45)) else box['width']


def track_boxes(track, poses, frame_rate=10.0):
    """{frame: world box} for every observed frame, with the track's label, size and heading applied.

    Static objects get one median pose; moving ones are smoothed with a local constant-velocity fit and
    take their heading from the motion (detections often see only one face, so their yaw is unreliable).
    """
    label = track.label(frame_rate)
    length, width, height = robust_size(track, label)
    frames = sorted(track.observations)
    obs = {f: track.observations[f]['box'] for f in frames}
    span = frames[-1] - frames[0]
    first, last = obs[frames[0]], obs[frames[-1]]
    # moving = the start and end thirds of the track are clearly apart (robust to detection jitter, which is
    # larger for vehicles seen from changing angles than for people)
    third = max(1, len(frames) // 3)
    head, tail = frames[:third], frames[-third:]
    displacement = math.hypot(
        _median([obs[f]['x'] for f in tail]) - _median([obs[f]['x'] for f in head]),
        _median([obs[f]['y'] for f in tail]) - _median([obs[f]['y'] for f in head]),
    )
    min_displacement = MOVING_DISPLACEMENT.get(label, 1.5)
    moving = span > 0 and displacement > min_displacement
    static_yaw = None
    if not moving:
        ref = first['yaw']
        static_yaw = normalize_angle(
            ref + _median([angle_diff(min((o['yaw'], o['yaw'] + math.pi), key=lambda a: abs(angle_diff(a, ref))), ref)
                           for o in obs.values()])
        )

    def local_fit(f):
        """Position at f from a least-squares line through the observations within +-2 frames."""
        window = [g for g in frames if abs(g - f) <= 2]
        if len(window) < 2:
            return obs[f]['x'], obs[f]['y'], None
        mt = sum(window) / len(window)
        mx = sum(obs[g]['x'] for g in window) / len(window)
        my = sum(obs[g]['y'] for g in window) / len(window)
        var = sum((g - mt) ** 2 for g in window)
        vx = sum((g - mt) * (obs[g]['x'] - mx) for g in window) / var
        vy = sum((g - mt) * (obs[g]['y'] - my) for g in window) / var
        return mx + vx * (f - mt), my + vy * (f - mt), math.atan2(vy, vx) if math.hypot(vx, vy) > 0.05 else None

    boxes = {}
    for f in frames:
        b = dict(obs[f])
        if moving:
            x, y, motion_yaw = local_fit(f)
            yaw = motion_yaw if motion_yaw is not None else math.atan2(last['y'] - first['y'], last['x'] - first['x'])
        else:
            x, y, yaw = b['x'], b['y'], static_yaw
        # grow a partially seen box away from the sensor (the sensor is at the frame's pose origin)
        sensor = (poses[f][3], poses[f][7]) if f < len(poses) and poses[f] else (0.0, 0.0)
        ux, uy = math.cos(yaw), math.sin(yaw)
        vx, vy = -uy, ux
        away_u = 1 if ux * (x - sensor[0]) + uy * (y - sensor[1]) > 0 else -1
        away_v = 1 if vx * (x - sensor[0]) + vy * (y - sensor[1]) > 0 else -1
        grow_l = max(0.0, length - _extent_along(b, yaw))
        grow_w = max(0.0, width - _extent_along(b, yaw + math.pi / 2))
        x += away_u * ux * grow_l / 2 + away_v * vx * grow_w / 2
        y += away_u * uy * grow_l / 2 + away_v * vy * grow_w / 2
        z_bottom = b['z'] - b['height'] / 2
        boxes[f] = {**b, 'x': x, 'y': y, 'length': length, 'width': width, 'height': height,
                    'z': z_bottom + height / 2, 'yaw': normalize_angle(yaw)}
    if not moving:
        mx, my = _median([b['x'] for b in boxes.values()]), _median([b['y'] for b in boxes.values()])
        mz = _median([b['z'] for b in boxes.values()])
        for b in boxes.values():
            b.update(x=mx, y=my, z=mz)
    return label, boxes


def suppress_duplicates(tracks_boxes, overlap=0.5, margin=1.0):
    """Drop short tracks that mostly sit on a longer track (the same object split by partial views).

    A track is a duplicate when it is at most half as long as the other one and, in most shared frames, its
    centre lies within the other box enlarged by ``margin`` metres.

    ``tracks_boxes``: list of (track, label, {frame: world box}); returns the kept entries.
    """
    ordered = sorted(tracks_boxes, key=lambda e: -len(e[2]))
    kept = []
    for entry in ordered:
        _, _, boxes = entry
        duplicate = False
        for _, _, other in kept:
            shared = [f for f in boxes if f in other]
            if not shared or len(boxes) > len(other) / 2:
                continue
            inside = 0
            for f in shared:
                b, o = boxes[f], other[f]
                dx, dy = b['x'] - o['x'], b['y'] - o['y']
                c, s_ = math.cos(o['yaw']), math.sin(o['yaw'])
                if abs(c * dx + s_ * dy) <= o['length'] / 2 + margin and abs(-s_ * dx + c * dy) <= o['width'] / 2 + margin:
                    inside += 1
            if inside >= overlap * len(boxes):
                duplicate = True
                break
        if not duplicate:
            kept.append(entry)
    return kept


# --------------------------------------------------------------------------------------------------
# single track extension (interactive "track with model")
# --------------------------------------------------------------------------------------------------


def extend_track(user_boxes, detect, frames_count, poses, label, max_age=3, frame_rate=10.0):
    """Follow one object through the sequence.

    ``user_boxes``: {frame: world box} from the annotator's keyframes (authoritative, never changed).
    ``detect(frame, center_world, radius)``: detections (world boxes) near a point of that frame.
    Runs forward from the last user keyframe and backward from the first one, using a Kalman filter for
    prediction and the nearest compatible detection for correction; between user keyframes, frames whose
    detection disagrees with the interpolation are added too. Returns ({frame: world box} including the user
    boxes, static) where ``static`` means the object doesn't move and only its presence was followed.
    """
    result = dict(user_boxes)
    keys = sorted(user_boxes)
    size = user_boxes[keys[-1]]

    def follow(start, step):
        anchor = user_boxes[start]
        kx, ky = Kalman1D(anchor['x']), Kalman1D(anchor['y'])
        # initial velocity from the user's own keyframes when there are two
        if len(keys) >= 2:
            a, b = (keys[-2], keys[-1]) if step > 0 else (keys[1], keys[0])
            dt = (b - a) * step
            if dt > 0:
                kx.v = (user_boxes[b]['x'] - user_boxes[a]['x']) / dt
                ky.v = (user_boxes[b]['y'] - user_boxes[a]['y']) / dt
        yaw = anchor['yaw']
        missed = 0
        frame = start + step
        while 0 <= frame < frames_count and missed <= max_age:
            kx.predict()
            ky.predict()
            speed = math.hypot(kx.v, ky.v)
            candidates = [
                d for d in detect(frame, (kx.x, ky.x), _gate(label, speed) + 2.0)
                if d['label'] in COMPATIBLE.get(label, {d['label']})
            ]
            best = min(candidates, key=lambda d: math.hypot(d['box']['x'] - kx.x, d['box']['y'] - ky.x), default=None)
            if best and math.hypot(best['box']['x'] - kx.x, best['box']['y'] - ky.x) <= _gate(label, speed):
                b = best['box']
                # the detection is completed to the annotator's size, away from the sensor
                sensor = (poses[frame][3], poses[frame][7]) if frame < len(poses) and poses[frame] else (0.0, 0.0)
                det_yaw = min((b['yaw'], b['yaw'] + math.pi), key=lambda a: abs(angle_diff(a, yaw)))
                # a detection of one face may have its axis across the object: then trust the track heading
                if abs(angle_diff(det_yaw, yaw)) < math.radians(30):
                    yaw = normalize_angle(yaw + 0.5 * angle_diff(det_yaw, yaw))
                ux, uy = math.cos(yaw), math.sin(yaw)
                vx, vy = -uy, ux
                away_u = 1 if ux * (b['x'] - sensor[0]) + uy * (b['y'] - sensor[1]) > 0 else -1
                away_v = 1 if vx * (b['x'] - sensor[0]) + vy * (b['y'] - sensor[1]) > 0 else -1
                grow_l = max(0.0, size['length'] - _extent_along(b, yaw))
                grow_w = max(0.0, size['width'] - _extent_along(b, yaw + math.pi / 2))
                kx.update(b['x'] + away_u * ux * grow_l / 2 + away_v * vx * grow_w / 2)
                ky.update(b['y'] + away_u * uy * grow_l / 2 + away_v * vy * grow_w / 2)
                result[frame] = {
                    **size,
                    'x': kx.x,
                    'y': ky.x,
                    'z': b['z'] - b['height'] / 2 + size['height'] / 2,
                    'yaw': yaw,
                }
                missed = 0
            else:
                missed += 1
            frame += step

    follow(keys[-1], 1)
    follow(keys[0], -1)
    # a parked object: detections only add jitter; the editor already holds the box still in the world
    if len(keys) == 1:
        anchor = user_boxes[keys[0]]
        tracked = [result[f] for f in result if f not in user_boxes]
        if tracked:
            displacement = _median([math.hypot(b['x'] - anchor['x'], b['y'] - anchor['y']) for b in tracked])
            if displacement < MOVING_DISPLACEMENT.get(label, 1.5) / 2:
                last = max(result)
                return {**user_boxes, **({last: anchor} if last != keys[0] else {})}, True
    # between the annotator's keyframes: add measured frames where interpolation is clearly off
    for a, b in zip(keys, keys[1:]):
        for frame in range(a + 1, b):
            t = (frame - a) / (b - a)
            ax, ay = user_boxes[a]['x'], user_boxes[a]['y']
            px, py = ax + (user_boxes[b]['x'] - ax) * t, ay + (user_boxes[b]['y'] - ay) * t
            candidates = [
                d for d in detect(frame, (px, py), _gate(label, 0) + 1.0)
                if d['label'] in COMPATIBLE.get(label, {d['label']})
            ]
            best = min(candidates, key=lambda d: math.hypot(d['box']['x'] - px, d['box']['y'] - py), default=None)
            if best:
                dist = math.hypot(best['box']['x'] - px, best['box']['y'] - py)
                if 0.4 < dist <= _gate(label, 0):
                    result[frame] = {**user_boxes[a], 'x': best['box']['x'], 'y': best['box']['y']}
    return result, False


# --------------------------------------------------------------------------------------------------
# keyframes
# --------------------------------------------------------------------------------------------------


def _interp(boxes, a, b, f):
    t = (f - a) / (b - a)
    ba, bb = boxes[a], boxes[b]
    out = {k: ba[k] + (bb[k] - ba[k]) * t for k in ('x', 'y', 'z', 'length', 'width', 'height')}
    out['yaw'] = normalize_angle(ba['yaw'] + angle_diff(bb['yaw'], ba['yaw']) * t)
    return out


def simplify(boxes, keep=(), position_tol=0.25, yaw_tol=math.radians(6)):
    """Fewest keyframes whose world-space interpolation stays within the tolerances of ``boxes``.

    ``boxes``: {frame: world box} over consecutive or gapped frames; frames in ``keep`` are always kept.
    """
    frames = sorted(boxes)
    if len(frames) <= 2:
        return frames
    chosen = {frames[0], frames[-1], *[f for f in keep if f in boxes]}

    def refine(a, b):
        inner = [f for f in frames if a < f < b]
        worst, worst_err = None, 0.0
        for f in inner:
            i = _interp(boxes, a, b, f)
            err = max(
                math.hypot(i['x'] - boxes[f]['x'], i['y'] - boxes[f]['y']) / position_tol,
                abs(angle_diff(i['yaw'], boxes[f]['yaw'])) / yaw_tol,
            )
            if err > worst_err:
                worst, worst_err = f, err
        if worst is not None and worst_err > 1.0:
            chosen.add(worst)
            refine(a, worst)
            refine(worst, b)

    ordered = sorted(chosen)
    for a, b in zip(ordered, ordered[1:]):
        refine(a, b)
    return sorted(chosen)


def to_sequence(boxes, keyframes, poses, frames_count, size=None, max_gap=None):
    """Editor/export keyframes (each in its own frame's LiDAR coordinates) for world ``boxes``.

    The track ends (disabled keyframe) after its last frame, and across gaps longer than ``max_gap``.
    """
    from .geometry import value_from_box

    def kf(frame, box, enabled=True):
        b = dict(box)
        if size:
            b.update(length=size[0], width=size[1], height=size[2])
        local = from_world(b, poses[frame] if frame < len(poses) else None)
        return {'frame': frame, 'enabled': enabled, **value_from_box(local)}

    seq = []
    for i, f in enumerate(keyframes):
        seq.append(kf(f, boxes[f]))
        nxt = keyframes[i + 1] if i + 1 < len(keyframes) else None
        if max_gap is not None and nxt is not None and nxt - f > max_gap:
            seq.append(kf(f + 1, boxes[f], enabled=False))
    last = keyframes[-1]
    if last + 1 < frames_count:
        seq.append(kf(last + 1, boxes[last], enabled=False))
    return seq
