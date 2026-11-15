"""3D object detectors.

All cluster-based detectors share three steps:

1. ``proposals``: ground removal, range-adaptive BEV clustering, minimum-area rectangle per cluster;
2. classification: hand-written size rules (``ClusterDetector``) or a classifier learned from the project's
   annotations (``LearnedClusterDetector``, see training.py) which also learns a background class;
3. ``complete``: a LiDAR often sees only the near face of an object, so the box is grown towards the class
   size prior, away from the sensor.

``OpenPCDetDetector`` wraps any OpenPCDet model (PointPillars, SECOND, PV-RCNN, CenterPoint ...).

A detection is ``{'box': {x, y, z, length, width, height, yaw}, 'label': str, 'score': float}`` in the
LiDAR frame of the scan.
"""

import math
from collections import defaultdict

# typical object sizes (length, width, height); learned per project when a model is trained
PRIORS = {
    'Car': (4.4, 1.85, 1.55),
    'Truck': (8.0, 2.5, 3.0),
    'Pedestrian': (0.6, 0.6, 1.75),
    'Cyclist': (1.8, 0.6, 1.7),
}


def min_area_rect(xy, step_deg=1.0):
    """(cx, cy, length, width, yaw) of the minimum-area rectangle around 2D points, length >= width."""
    best = None
    steps = int(90 / step_deg)
    for k in range(steps):
        a = math.radians(k * step_deg)
        c, s = math.cos(a), math.sin(a)
        us = [c * x + s * y for x, y in xy]
        vs = [-s * x + c * y for x, y in xy]
        u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
        area = (u1 - u0) * (v1 - v0)
        if best is None or area < best[0] - 1e-9:
            best = (area, a, u0, u1, v0, v1)
    _, a, u0, u1, v0, v1 = best
    cu, cv = (u0 + u1) / 2, (v0 + v1) / 2
    c, s = math.cos(a), math.sin(a)
    cx, cy = c * cu - s * cv, s * cu + c * cv
    du, dv = u1 - u0, v1 - v0
    if du >= dv:
        return cx, cy, du, dv, a
    return cx, cy, dv, du, a + math.pi / 2


def classify(length, width, height, face_facing_sensor=False):
    """Rule-based label from cluster size, or None for scenery (walls, poles, vegetation).

    ``face_facing_sensor``: the cluster is a thin surface roughly perpendicular to the line of sight, i.e.
    most likely the rear or front of a vehicle rather than the side of a cyclist.
    """
    if height < 0.9 or height > 4.2 or length > 14:
        return None
    thin = width <= 0.6
    if thin and face_facing_sensor and 1.4 <= length <= 2.7:
        return 'Truck' if height > 2.4 else 'Car'
    if height <= 2.2 and length <= 1.1 and width <= 1.1 and height >= 1.2:
        return 'Pedestrian'
    if height <= 2.2 and 1.2 <= length <= 2.3 and width <= 1.1 and height >= 1.2:
        return 'Cyclist'
    if 3.0 <= length <= 6.2 and 1.3 <= width <= 2.5 and height <= 2.4:
        return 'Car'
    if length >= 5.0 and width >= 1.8 and height >= 2.0:
        return 'Truck'
    return None


def features(proposal):
    """Numeric description of a proposal, used by the learned classifier."""
    p = proposal
    return [
        p['length'],
        p['width'],
        p['height'],
        math.log1p(p['points']),
        p['range'] / 50.0,
        p['mean_height'] / max(p['height'], 0.1),
        1.0 if p['facing'] else 0.0,
    ]


class ClusterDetector:
    def __init__(self, cell=0.25, ground_threshold=0.25, min_points=6, max_range=70.0, ground_cell=4.0, priors=None):
        self.cell = cell
        self.ground_threshold = ground_threshold
        self.min_points = min_points
        self.max_range = max_range
        self.ground_cell = ground_cell
        self.priors = dict(PRIORS if priors is None else priors)

    # ------------------------------------------------------------------------------------- proposals

    def _ground(self, points):
        """Ground height per coarse cell (low percentile), falling back to the global estimate."""
        cells = defaultdict(list)
        for x, y, z, *_ in points:
            cells[(math.floor(x / self.ground_cell), math.floor(y / self.ground_cell))].append(z)
        all_z = sorted(z for zs in cells.values() for z in zs)
        global_ground = all_z[int(len(all_z) * 0.05)] if all_z else 0.0
        ground = {}
        for key, zs in cells.items():
            zs.sort()
            g = zs[int(len(zs) * 0.1)]
            # a cell full of a vehicle has no ground returns: don't let it lift the ground
            ground[key] = g if g < global_ground + 0.6 else global_ground
        return ground, global_ground

    def proposals(self, points, center=None, radius=None):
        """Clusters above the ground with their rectangle and shape statistics."""
        if center is not None and radius is not None:
            r2 = radius * radius
            points = [p for p in points if (p[0] - center[0]) ** 2 + (p[1] - center[1]) ** 2 <= r2]
        points = [p for p in points if p[0] * p[0] + p[1] * p[1] <= self.max_range ** 2]
        if not points:
            return []
        ground, global_ground = self._ground(points)

        def ground_at(x, y):
            return ground.get((math.floor(x / self.ground_cell), math.floor(y / self.ground_cell)), global_ground)

        cells = defaultdict(list)
        for p in points:
            g = ground_at(p[0], p[1])
            if g + self.ground_threshold < p[2] < g + 4.5:
                cells[(math.floor(p[0] / self.cell), math.floor(p[1] / self.cell))].append(p)

        # union-find over occupied cells; the link distance grows with range (azimuth spacing grows)
        parent = {c: c for c in cells}

        def find(c):
            while parent[c] != c:
                parent[c] = parent[parent[c]]
                c = parent[c]
            return c

        for (i, j) in cells:
            rng = math.hypot((i + 0.5) * self.cell, (j + 0.5) * self.cell)
            reach = max(0.5, 2.5 * rng * math.radians(0.75))
            k = max(1, int(math.ceil(reach / self.cell)))
            for di in range(-k, k + 1):
                for dj in range(-k, k + 1):
                    other = (i + di, j + dj)
                    if other in parent and (di * di + dj * dj) * self.cell ** 2 <= reach * reach:
                        ra, rb = find((i, j)), find(other)
                        if ra != rb:
                            parent[ra] = rb
        clusters = defaultdict(list)
        for c, pts in cells.items():
            clusters[find(c)].extend(pts)

        out = []
        for pts in clusters.values():
            if len(pts) < self.min_points:
                continue
            cx, cy, length, width, yaw = min_area_rect([(p[0], p[1]) for p in pts])
            if length > 20:
                continue
            bottom = ground_at(cx, cy)
            height = max(p[2] for p in pts) - bottom
            rng = math.hypot(cx, cy) or 1.0
            # angle between the cluster's long side and the line of sight (90 deg = surface faces the sensor)
            facing = abs(math.cos(yaw) * cx + math.sin(yaw) * cy) / rng < math.cos(math.radians(55))
            out.append(
                {
                    'x': cx,
                    'y': cy,
                    'length': length,
                    'width': width,
                    'height': height,
                    'yaw': yaw,
                    'bottom': bottom,
                    'range': rng,
                    'facing': facing,
                    'points': len(pts),
                    'mean_height': sum(p[2] for p in pts) / len(pts) - bottom,
                    'centroid': (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)),
                }
            )
        return out

    # ------------------------------------------------------------------------------------- boxes

    def complete(self, proposal, label, score):
        """Detection for a classified proposal, grown towards the label's size prior away from the sensor."""
        p = proposal
        cx, cy, length, width, yaw = p['x'], p['y'], p['length'], p['width'], p['yaw']
        prior = self.priors.get(label)
        if prior:
            # only the rear / front of a vehicle is visible: a thin, wide surface facing the sensor
            if prior[0] >= 2.5 and width <= 0.6 and length < prior[0] * 0.6 and p['facing']:
                yaw += math.pi / 2
                if math.cos(yaw) * cx + math.sin(yaw) * cy < 0:
                    yaw += math.pi
                width = max(length, prior[1] * 0.9)
                cx += math.cos(yaw) * (prior[0] / 2)
                cy += math.sin(yaw) * (prior[0] / 2)
                length = prior[0]
            else:
                grow_l = max(0.0, prior[0] * 0.85 - length)
                grow_w = max(0.0, prior[1] * 0.85 - width)
                if grow_l or grow_w:
                    ux, uy = math.cos(yaw), math.sin(yaw)
                    vx, vy = -uy, ux
                    sign_u = 1 if ux * cx + uy * cy > 0 else -1
                    sign_v = 1 if vx * cx + vy * cy > 0 else -1
                    cx += sign_u * ux * grow_l / 2 + sign_v * vx * grow_w / 2
                    cy += sign_u * uy * grow_l / 2 + sign_v * vy * grow_w / 2
                    length += grow_l
                    width += grow_w
        box = {
            'x': cx,
            'y': cy,
            'z': p['bottom'] + p['height'] / 2,
            'length': length,
            'width': width,
            'height': p['height'],
            'yaw': math.atan2(math.sin(yaw), math.cos(yaw)),
        }
        return {'box': box, 'label': label, 'score': round(score, 3), 'points': p['points']}

    def classify_proposal(self, proposal):
        """(label, score) or None."""
        label = classify(proposal['length'], proposal['width'], proposal['height'], proposal['facing'])
        if label is None:
            return None
        score = min(0.9, 0.3 + 0.02 * proposal['points']) * (1.0 if proposal['range'] < 50 else 0.7)
        return label, score

    def detect(self, points, center=None, radius=None):
        detections = []
        for proposal in self.proposals(points, center, radius):
            result = self.classify_proposal(proposal)
            if result:
                detections.append(self.complete(proposal, *result))
        return detections


class LearnedClusterDetector(ClusterDetector):
    """Cluster proposals classified by a model trained on the project's annotations (training.py)."""

    def __init__(self, model, min_confidence=0.5):
        super().__init__(priors={label: tuple(size) for label, size in model.priors.items()})
        self.model = model
        self.min_confidence = min_confidence

    def classify_proposal(self, proposal):
        label, confidence = self.model.predict(features(proposal))
        if label is None or confidence < self.min_confidence:
            return None
        return label, confidence


class OpenPCDetDetector:
    """Any OpenPCDet model; follows tools/demo.py of OpenPCDet. Boxes are (x, y, z, dx, dy, dz, heading)."""

    def __init__(self, cfg_file, ckpt, score_threshold=0.3, device='cuda'):
        import numpy as np  # noqa: F401  (fail early with a clear error)
        import torch
        from pcdet.config import cfg, cfg_from_yaml_file
        from pcdet.datasets import DatasetTemplate
        from pcdet.models import build_network, load_data_to_gpu
        from pcdet.utils import common_utils

        cfg_from_yaml_file(cfg_file, cfg)
        logger = common_utils.create_logger()

        class _Dataset(DatasetTemplate):
            def __init__(self):
                super().__init__(dataset_cfg=cfg.DATA_CONFIG, class_names=cfg.CLASS_NAMES, training=False, logger=logger)

        self.torch = torch
        self.load_data_to_gpu = load_data_to_gpu
        self.dataset = _Dataset()
        self.class_names = list(cfg.CLASS_NAMES)
        self.model = build_network(model_cfg=cfg.MODEL, num_class=len(self.class_names), dataset=self.dataset)
        self.model.load_params_from_file(filename=ckpt, logger=logger, to_cpu=device != 'cuda')
        if device == 'cuda':
            self.model.cuda()
        self.model.eval()
        self.score_threshold = score_threshold

    def detect(self, points, center=None, radius=None):
        import numpy as np

        arr = np.asarray([p[:4] for p in points], dtype=np.float32)
        data = self.dataset.prepare_data(data_dict={'points': arr, 'frame_id': 0})
        batch = self.dataset.collate_batch([data])
        with self.torch.no_grad():
            self.load_data_to_gpu(batch)
            pred = self.model.forward(batch)[0][0]
        detections = []
        boxes = pred['pred_boxes'].cpu().numpy()
        for b, s, lab in zip(boxes, pred['pred_scores'].cpu().numpy(), pred['pred_labels'].cpu().numpy()):
            if s < self.score_threshold:
                continue
            x, y, z, dx, dy, dz, heading = (float(v) for v in b[:7])
            if center is not None and radius is not None and math.hypot(x - center[0], y - center[1]) > radius:
                continue
            detections.append(
                {
                    'box': {'x': x, 'y': y, 'z': z, 'length': dx, 'width': dy, 'height': dz, 'yaw': heading},
                    'label': self.class_names[int(lab) - 1],
                    'score': round(float(s), 3),
                }
            )
        return detections


def build_detector(env, checkpoint=None):
    """Base detector: OpenPCDet when PCDET_CFG + a checkpoint are given, otherwise rule-based clustering."""
    ckpt = checkpoint or env.get('PCDET_CKPT')
    if env.get('PCDET_CFG') and ckpt:
        return OpenPCDetDetector(
            env['PCDET_CFG'],
            ckpt,
            score_threshold=float(env.get('SCORE_THRESHOLD', 0.3)),
            device=env.get('DEVICE', 'cuda'),
        )
    return ClusterDetector()
