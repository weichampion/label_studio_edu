"""Training on the project's annotations.

Two models can be trained, one per Label Studio project (models never leave their project, so colleges
in a multi-tenant deployment don't share what they taught the model):

``cluster`` (default, no dependencies)
    The cluster detector's classifier is learned: every proposal of an annotated scan is labelled with the
    annotated box it falls into (or *background*), and a k-nearest-neighbour classifier is fitted on shape
    features. Size priors per label are learned from the annotated boxes. This lets a project use its own
    labels (any language, any classes) and teaches the detector which clusters are scenery.
``openpcdet``
    Fine-tunes an OpenPCDet model: the annotations are exported as an OpenPCDet custom dataset and the
    project's training command is run from the current checkpoint (see README "Fine-tuning OpenPCDet").

Sequence annotations contribute every frame: boxes between keyframes come from the same interpolation the
editor uses.
"""

import glob
import json
import logging
import math
import os
import random
import struct
import subprocess
import threading
import time
import uuid
from collections import Counter, defaultdict

from .detector import ClusterDetector, LearnedClusterDetector, features
from .geometry import box_at, box_from_value, keyframes_sorted, normalize_pose

logger = logging.getLogger(__name__)
BACKGROUND = '__background__'


# --------------------------------------------------------------------------------------------------
# annotations -> per-frame labelled boxes
# --------------------------------------------------------------------------------------------------


def pick_annotation(task):
    """Ground truth first, otherwise the most recently updated annotation that was not skipped."""
    annotations = [a for a in task.get('annotations') or [] if not a.get('was_cancelled')]
    if not annotations:
        return None
    pool = [a for a in annotations if a.get('ground_truth')] or annotations
    return max(pool, key=lambda a: (a.get('updated_at') or a.get('created_at') or '', a.get('id') or 0))


def examples_from_tasks(tasks, spec):
    """[{task, frame, url, boxes: [{box, label}]}] for every annotated scan (boxes in that scan's frame)."""
    examples = []
    for task in tasks:
        annotation = pick_annotation(task)
        if annotation is None:
            continue
        raw = (task.get('data') or {}).get(spec['data_key'])
        if isinstance(raw, list):
            frames = []
            for item in raw:
                item = {'pointcloud': item} if isinstance(item, str) else (item or {})
                frames.append(
                    (
                        str(item.get('pointcloud') or item.get('url') or item.get('lidar') or item.get('path') or ''),
                        normalize_pose(item.get('pose') or item.get('ego_pose') or item.get('lidar_to_world')),
                    )
                )
        else:
            frames = [(str(raw or ''), None)]
        poses = [p for _, p in frames]
        per_frame = defaultdict(list)
        for r in annotation.get('result') or []:
            if r.get('type') != 'cuboid' or r.get('from_name') != spec['cuboid']:
                continue
            value = r.get('value') or {}
            labels = value.get('labels') or []
            if not labels:
                continue
            if isinstance(value.get('sequence'), list):
                keys = keyframes_sorted(value['sequence'])
                for f in range(len(frames)):
                    b = box_at(keys, f, poses)
                    if b is not None:
                        per_frame[f].append({'box': b, 'label': labels[0]})
            elif value.get('position'):
                per_frame[0].append({'box': box_from_value(value), 'label': labels[0]})
        for f, (url, _) in enumerate(frames):
            if url:
                # a scan with no boxes is still informative: everything in it is background
                examples.append({'task': task.get('id'), 'frame': f, 'url': url, 'boxes': per_frame.get(f, [])})
    return examples


def proposal_label(proposal, boxes, margin=0.4):
    """Label of the annotated box that contains the proposal's centroid, or BACKGROUND."""
    cx, cy = proposal['centroid']
    best, best_d = None, None
    for item in boxes:
        b = item['box']
        dx, dy = cx - b['x'], cy - b['y']
        c, s = math.cos(b['yaw']), math.sin(b['yaw'])
        if abs(c * dx + s * dy) <= b['length'] / 2 + margin and abs(-s * dx + c * dy) <= b['width'] / 2 + margin:
            d = math.hypot(dx, dy)
            if best is None or d < best_d:
                best, best_d = item['label'], d
    return best or BACKGROUND


# --------------------------------------------------------------------------------------------------
# classifier
# --------------------------------------------------------------------------------------------------


class KNNClassifier:
    """k-nearest neighbours on standardised features with distance-weighted votes."""

    def __init__(self, k=7, max_per_class=3000, seed=0):
        self.k = k
        self.max_per_class = max_per_class
        self.seed = seed
        self.samples = []
        self.mean = []
        self.std = []

    def fit(self, X, y):
        rng = random.Random(self.seed)
        by_class = defaultdict(list)
        for x, label in zip(X, y):
            by_class[label].append(x)
        kept = []
        for label, xs in by_class.items():
            if len(xs) > self.max_per_class:
                xs = rng.sample(xs, self.max_per_class)
            kept.extend((x, label) for x in xs)
        dims = len(kept[0][0])
        self.mean = [sum(x[i] for x, _ in kept) / len(kept) for i in range(dims)]
        self.std = [
            max(1e-6, math.sqrt(sum((x[i] - self.mean[i]) ** 2 for x, _ in kept) / len(kept))) for i in range(dims)
        ]
        self.samples = [(self._scale(x), label) for x, label in kept]
        return self

    def _scale(self, x):
        return [(v - m) / s for v, m, s in zip(x, self.mean, self.std)]

    def predict(self, x):
        """(label, confidence); label is None for background."""
        if not self.samples:
            return None, 0.0
        q = self._scale(x)
        nearest = sorted(
            ((sum((a - b) ** 2 for a, b in zip(q, s)), label) for s, label in self.samples), key=lambda t: t[0]
        )[: self.k]
        votes = Counter()
        for d2, label in nearest:
            votes[label] += 1.0 / (math.sqrt(d2) + 0.1)
        label, weight = votes.most_common(1)[0]
        confidence = weight / sum(votes.values())
        return (None if label == BACKGROUND else label), confidence

    def to_dict(self):
        return {'k': self.k, 'mean': self.mean, 'std': self.std, 'samples': self.samples}

    @classmethod
    def from_dict(cls, data):
        model = cls(k=data['k'])
        model.mean, model.std = data['mean'], data['std']
        model.samples = [(list(s), label) for s, label in data['samples']]
        return model


class LearnedModel:
    kind = 'cluster'

    def __init__(self, classifier, priors, labels, metrics=None, version=None):
        self.classifier = classifier
        self.priors = priors
        self.labels = labels
        self.metrics = metrics or {}
        self.version = version

    def predict(self, feature_vector):
        return self.classifier.predict(feature_vector)

    def detector(self, min_confidence=0.5):
        return LearnedClusterDetector(self, min_confidence=min_confidence)

    def to_dict(self):
        return {
            'kind': self.kind,
            'version': self.version,
            'labels': self.labels,
            'priors': self.priors,
            'metrics': self.metrics,
            'classifier': self.classifier.to_dict(),
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            KNNClassifier.from_dict(data['classifier']),
            {k: list(v) for k, v in data['priors'].items()},
            data['labels'],
            data.get('metrics'),
            data.get('version'),
        )


def _median(values):
    values = sorted(values)
    n = len(values)
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2


def learn_priors(examples):
    sizes = defaultdict(list)
    for ex in examples:
        for item in ex['boxes']:
            b = item['box']
            sizes[item['label']].append((b['length'], b['width'], b['height']))
    return {
        label: [round(_median([s[i] for s in values]), 3) for i in range(3)] for label, values in sorted(sizes.items())
    }


def labelled_proposals(examples, load, detector):
    X, y = [], []
    for ex in examples:
        for proposal in detector.proposals(load(ex['url'], ex['task'])):
            X.append(features(proposal))
            y.append(proposal_label(proposal, ex['boxes']))
    return X, y


def evaluate(model, examples, load, match_distance=1.5):
    """Detection precision / recall of the trained detector on held-out scans (centre distance + label)."""
    detector = model.detector()
    tp = fp = fn = 0
    per_label = defaultdict(lambda: {'tp': 0, 'fp': 0, 'fn': 0})
    for ex in examples:
        detections = detector.detect(load(ex['url'], ex['task']))
        unmatched = list(range(len(ex['boxes'])))
        for d in sorted(detections, key=lambda d: -d['score']):
            best = None
            for i in unmatched:
                b = ex['boxes'][i]['box']
                dist = math.hypot(d['box']['x'] - b['x'], d['box']['y'] - b['y'])
                if ex['boxes'][i]['label'] == d['label'] and dist <= match_distance and (best is None or dist < best[0]):
                    best = (dist, i)
            if best:
                unmatched.remove(best[1])
                tp += 1
                per_label[d['label']]['tp'] += 1
            else:
                fp += 1
                per_label[d['label']]['fp'] += 1
        for i in unmatched:
            fn += 1
            per_label[ex['boxes'][i]['label']]['fn'] += 1
    return {
        'precision': round(tp / (tp + fp), 3) if tp + fp else None,
        'recall': round(tp / (tp + fn), 3) if tp + fn else None,
        'per_label': dict(per_label),
        'scans': len(examples),
    }


def split_examples(examples, validation_fraction=0.2, seed=0):
    """Hold out whole tasks when there are several (frames of one sequence are too alike), else frames."""
    tasks = sorted({ex['task'] for ex in examples}, key=str)
    rng = random.Random(seed)
    if len(tasks) >= 3:
        held = set(rng.sample(tasks, max(1, int(round(len(tasks) * validation_fraction)))))
        return [e for e in examples if e['task'] not in held], [e for e in examples if e['task'] in held]
    if len(examples) >= 5:
        n_val = max(1, int(round(len(examples) * validation_fraction)))
        return examples[:-n_val], examples[-n_val:]
    return examples, []


def train_cluster_model(examples, load, version, validation_fraction=0.2, k=7):
    if not any(ex['boxes'] for ex in examples):
        raise ValueError('No annotated cuboids to learn from')
    detector = ClusterDetector()
    train, val = split_examples(examples, validation_fraction)
    labels = sorted({item['label'] for ex in examples for item in ex['boxes']})
    priors = learn_priors(examples)
    metrics = {'labels': labels, 'train_scans': len(train), 'validation_scans': len(val)}
    if val:
        X, y = labelled_proposals(train, load, detector)
        if any(label != BACKGROUND for label in y):
            held_out = LearnedModel(KNNClassifier(k=k).fit(X, y), priors, labels, version=version)
            metrics['validation'] = evaluate(held_out, val, load)
    X, y = labelled_proposals(examples, load, detector)
    if not any(label != BACKGROUND for label in y):
        raise ValueError('No detector proposal falls inside an annotated box; check the annotations')
    metrics['samples'] = dict(Counter(y))
    return LearnedModel(KNNClassifier(k=k).fit(X, y), priors, labels, metrics, version)


# --------------------------------------------------------------------------------------------------
# OpenPCDet fine-tuning
# --------------------------------------------------------------------------------------------------


def write_npy(path, rows, columns=4):
    """float32 (N, columns) .npy file, written without numpy."""
    header = "{'descr': '<f4', 'fortran_order': False, 'shape': (%d, %d), }" % (len(rows), columns)
    pad = 64 - (10 + len(header) + 1) % 64
    header = header + ' ' * pad + '\n'
    with open(path, 'wb') as f:
        f.write(b'\x93NUMPY\x01\x00' + struct.pack('<H', len(header)) + header.encode('latin1'))
        for row in rows:
            f.write(struct.pack('<%df' % columns, *(list(row[:columns]) + [0.0] * (columns - len(row)))))


def kitti_class(label, prior):
    """Closest KITTI class for OpenPCDet's evaluation mapping (MAP_CLASS_TO_KITTI)."""
    length = prior[0] if prior else 4.0
    if length <= 1.2:
        return 'Pedestrian'
    if length <= 2.4:
        return 'Cyclist'
    return 'Car'


def export_openpcdet(examples, load, out_dir, validation_fraction=0.2):
    """OpenPCDet custom dataset: points/*.npy, labels/*.txt, ImageSets/{train,val}.txt."""
    for sub in ('points', 'labels', 'ImageSets'):
        os.makedirs(os.path.join(out_dir, sub), exist_ok=True)
    train, val = split_examples(examples, validation_fraction)
    names = {'train': [], 'val': []}
    for split, items in (('train', train), ('val', val or train[-1:])):
        for ex in items:
            name = f'{ex["task"]}_{ex["frame"]:06d}'
            if name not in names['train'] + names['val']:
                write_npy(os.path.join(out_dir, 'points', f'{name}.npy'), load(ex['url'], ex['task']))
                lines = []
                for item in ex['boxes']:
                    b = item['box']
                    values = [b['x'], b['y'], b['z'], b['length'], b['width'], b['height'], b['yaw']]
                    lines.append(' '.join(f'{v:.4f}' for v in values) + ' ' + item['label'].replace(' ', '_'))
                with open(os.path.join(out_dir, 'labels', f'{name}.txt'), 'w', encoding='utf-8') as f:
                    f.write('\n'.join(lines) + ('\n' if lines else ''))
            names[split].append(name)
    for split in ('train', 'val'):
        with open(os.path.join(out_dir, 'ImageSets', f'{split}.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(names[split]) + '\n')
    return names


def anchor_config(priors, bottom=-1.78):
    """ANCHOR_GENERATOR_CONFIG for anchor-based OpenPCDet heads, one entry per class from the learned sizes.

    ``bottom``: ground height in the LiDAR frame (KITTI velodyne: -1.78; set PCDET_ANCHOR_BOTTOM).
    """
    lines = []
    for label, (length, width, height) in priors.items():
        name = label.replace(' ', '_')
        lines += [
            f"- class_name: '{name}'",
            f'  anchor_sizes: [[{length:.2f}, {width:.2f}, {height:.2f}]]',
            '  anchor_rotations: [0, 1.57]',
            f'  anchor_bottom_heights: [{bottom:.2f}]',
            '  align_center: False',
            '  feature_map_stride: 2',
            f'  matched_threshold: {0.6 if length > 2.5 else 0.5}',
            f'  unmatched_threshold: {0.45 if length > 2.5 else 0.35}',
        ]
    return '\n'.join(lines)


def fill_template(text, values):
    for key, value in values.items():
        text = text.replace('{{' + key + '}}', value)
    return text


class OpenPCDetFineTuner:
    """Runs the project's OpenPCDet training from the current checkpoint; returns the new checkpoint path."""

    def __init__(self, env):
        here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'openpcdet')
        self.root = env['PCDET_ROOT']
        self.dataset_template = env.get('PCDET_DATASET_CFG_TEMPLATE', os.path.join(here, 'custom_dataset.yaml.tmpl'))
        self.model_template = env.get('PCDET_MODEL_CFG_TEMPLATE', os.path.join(here, 'pointpillar_custom.yaml.tmpl'))
        self.prepare_cmd = env.get(
            'PCDET_PREPARE_CMD', f'python {os.path.join(here, "create_infos.py")} {{dataset_cfg}} {{class_names}}'
        )
        self.train_cmd = env.get(
            'PCDET_TRAIN_CMD',
            'python train.py --cfg_file {model_cfg} --pretrained_model {ckpt} --epochs {epochs} '
            '--batch_size {batch_size} --extra_tag {tag}',
        )
        self.epochs = env.get('FINETUNE_EPOCHS', '10')
        self.batch_size = env.get('FINETUNE_BATCH_SIZE', '4')
        self.anchor_bottom = float(env.get('PCDET_ANCHOR_BOTTOM', -1.78))

    def run(self, project, examples, load, priors, base_checkpoint, work_dir, log):
        data_dir = os.path.join(work_dir, 'dataset')
        export_openpcdet(examples, load, data_dir)
        class_names = list(priors)
        values = {
            'DATA_PATH': data_dir,
            'CLASS_NAMES': json.dumps([c.replace(' ', '_') for c in class_names]),
            'MAP_CLASS_TO_KITTI': json.dumps({c.replace(' ', '_'): kitti_class(c, priors[c]) for c in class_names}),
            'ANCHOR_GENERATOR_CONFIG': anchor_config(priors, self.anchor_bottom).replace('\n', '\n            '),
        }
        dataset_cfg = os.path.join(work_dir, 'dataset.yaml')
        with open(self.dataset_template, encoding='utf-8') as f, open(dataset_cfg, 'w', encoding='utf-8') as out:
            out.write(fill_template(f.read(), values))
        values['DATASET_CFG'] = dataset_cfg
        model_cfg = os.path.join(work_dir, 'model.yaml')
        with open(self.model_template, encoding='utf-8') as f, open(model_cfg, 'w', encoding='utf-8') as out:
            out.write(fill_template(f.read(), values))
        tag = f'ls_project_{project}_{uuid.uuid4().hex[:10]}'
        args = {
            'dataset_cfg': dataset_cfg,
            'model_cfg': model_cfg,
            'class_names': ','.join(c.replace(' ', '_') for c in class_names),
            'ckpt': base_checkpoint or '',
            'epochs': self.epochs,
            'batch_size': self.batch_size,
            'tag': tag,
        }
        tools = os.path.join(self.root, 'tools')
        for template in (self.prepare_cmd, self.train_cmd):
            command = template.format(**args)
            log.write(f'$ {command}\n')
            log.flush()
            subprocess.run(command, shell=True, cwd=tools, stdout=log, stderr=subprocess.STDOUT, check=True)  # nosec
        found = glob.glob(os.path.join(self.root, 'output', '**', tag, 'ckpt', '*.pth'), recursive=True)
        if not found:
            raise RuntimeError(f'Training finished without a checkpoint under output/**/{tag}/ckpt')
        return max(found, key=os.path.getmtime), model_cfg


# --------------------------------------------------------------------------------------------------
# per-project model registry and background jobs
# --------------------------------------------------------------------------------------------------


class ModelRegistry:
    """Trained models on disk: <MODEL_DIR>/<project>/current.json (+ checkpoints, logs, datasets)."""

    def __init__(self, root):
        self.root = root
        self._cache = {}
        self.lock = threading.Lock()

    def project_dir(self, project):
        safe = ''.join(ch for ch in str(project) if ch.isalnum() or ch in '-_') or 'default'
        path = os.path.join(self.root, safe)
        os.makedirs(path, exist_ok=True)
        return path

    def current(self, project):
        """{'kind', 'version', ...} of the project's trained model, or None."""
        if project is None:
            return None
        with self.lock:
            path = os.path.join(self.project_dir(project), 'current.json')
            if not os.path.exists(path):
                return None
            mtime = os.path.getmtime(path)
            cached = self._cache.get(project)
            if cached and cached[0] == mtime:
                return cached[1]
            with open(path, encoding='utf-8') as f:
                info = json.load(f)
            if info.get('kind') == 'cluster':
                with open(os.path.join(self.project_dir(project), info['file']), encoding='utf-8') as f:
                    info = {**info, 'model': LearnedModel.from_dict(json.load(f))}
            self._cache[project] = (mtime, info)
            return info

    def save(self, project, info, model=None):
        directory = self.project_dir(project)
        if model is not None:
            info = {**info, 'file': f'{info["version"]}.json'}
            with open(os.path.join(directory, info['file']), 'w', encoding='utf-8') as f:
                json.dump(model.to_dict(), f)
        tmp = os.path.join(directory, 'current.json.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
        os.replace(tmp, os.path.join(directory, 'current.json'))
        with open(os.path.join(directory, 'history.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps({k: v for k, v in info.items() if k != 'model'}, ensure_ascii=False) + '\n')

    def versions(self, project):
        path = os.path.join(self.project_dir(project), 'history.jsonl')
        if not os.path.exists(path):
            return []
        with open(path, encoding='utf-8') as f:
            return [json.loads(line)['version'] for line in f if line.strip()]


class TrainingJobs:
    """One background training per project at a time; a request during training queues one more run."""

    def __init__(self):
        self.jobs = {}
        self.running = {}
        self.pending = {}
        self.lock = threading.Lock()

    def start(self, project, work):
        """``work(job)`` runs in a thread and returns (status details). Returns the job id."""
        with self.lock:
            if project in self.running:
                self.pending[project] = work
                return self.running[project]
            job_id = uuid.uuid4().hex[:12]
            self.jobs[job_id] = {'job': job_id, 'project': project, 'status': 'queued', 'created': time.time()}
            self.running[project] = job_id
        threading.Thread(target=self._run, args=(job_id, project, work), daemon=True).start()
        return job_id

    def _run(self, job_id, project, work):
        job = self.jobs[job_id]
        job['status'] = 'running'
        try:
            job.update(work(job) or {})
            job['status'] = 'completed'
        except Exception as e:
            logger.exception('Training for project %s failed', project)
            job.update(status='failed', error=str(e))
        job['finished'] = time.time()
        with self.lock:
            self.running.pop(project, None)
            again = self.pending.pop(project, None)
        if again:
            self.start(project, again)

    def get(self, job_id):
        return self.jobs.get(job_id)

    def wait(self, job_id, timeout=300):
        end = time.time() + timeout
        while time.time() < end:
            job = self.jobs.get(job_id)
            if job and job['status'] in ('completed', 'failed'):
                return job
            time.sleep(0.05)
        return self.jobs.get(job_id)
