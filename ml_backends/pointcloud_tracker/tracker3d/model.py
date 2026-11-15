"""Label Studio ML backend logic: tasks in, cuboid predictions out.

* Retrieve predictions (Data Manager "Retrieve Predictions", or "Predictions" auto-annotation):
  single scans -> detected boxes; sequences -> detection on every frame + multi-object tracking,
  returned as keyframed tracks (the editor interpolates between keyframes exactly like the tracker did).
* Interactive ("Track With Model" in the 3D editor): ``params.context.pointcloud_track`` holds one track;
  it is followed through the sequence and returned with the annotator's keyframes unchanged.
* Training ("Start Training" in the project's model settings, or /train): a model is learned from the
  project's annotations and used for that project only (see training.py).
"""

import base64
import json
import logging
import math
import os
import time
import urllib.parse
import urllib.request
import uuid
from collections import OrderedDict
from xml.etree import ElementTree

from . import __version__
from .detector import build_detector
from .geometry import box_from_value, keyframes_sorted, normalize_pose, to_world, value_from_box
from .pcd_io import parse_point_cloud
from .tracker import MultiObjectTracker, extend_track, simplify, suppress_duplicates, to_sequence, track_boxes
from .training import (
    ModelRegistry,
    OpenPCDetFineTuner,
    TrainingJobs,
    examples_from_tasks,
    learn_priors,
    train_cluster_model,
)

logger = logging.getLogger(__name__)


class LRU(OrderedDict):
    def __init__(self, size):
        super().__init__()
        self.size = size

    def put(self, key, value):
        self[key] = value
        self.move_to_end(key)
        while len(self) > self.size:
            self.popitem(last=False)
        return value


def parse_label_config(label_config):
    """Names and data keys of the first <PointCloud> that has a <Cuboid>, plus the label values."""
    root = ElementTree.fromstring(label_config)
    clouds = {e.get('name'): e for e in root.iter() if e.tag.lower() == 'pointcloud' and e.get('name')}
    cuboid = next(
        (e for e in root.iter() if e.tag.lower() == 'cuboid' and e.get('toName') in clouds), None
    )
    if cuboid is None:
        return None
    cloud = clouds[cuboid.get('toName')]
    labels_tag = next(
        (e for e in root.iter() if e.tag.lower() == 'labels' and cloud.get('name') in (e.get('toName') or '').split(',')),
        None,
    )
    labels = [l.get('value') for l in labels_tag.iter() if l.tag.lower() == 'label'] if labels_tag is not None else []
    return {
        'cuboid': cuboid.get('name'),
        'cloud': cloud.get('name'),
        'data_key': (cloud.get('value') or '').lstrip('$'),
        'bin_dims': int(cloud.get('binDims') or 4),
        'labels': [l for l in labels if l],
    }


class PointCloudTracker:
    def __init__(self, env=None):
        env = dict(os.environ if env is None else env)
        self.env = env
        self._detector = None  # built on first use: loading a deep model is slow and needs its dependencies
        kind = 'openpcdet' if env.get('PCDET_CFG') and env.get('PCDET_CKPT') else 'cluster'
        self.model_version = env.get('MODEL_VERSION') or f'pointcloud-tracker-{__version__}-{kind}'
        # models trained on a project's annotations, used for that project only
        self.project_tokens = {}
        self.registry = ModelRegistry(env.get('MODEL_DIR') or os.path.join(os.getcwd(), 'models'))
        self.jobs = TrainingJobs()
        self._project_detectors = {}
        self.frame_rate = float(env.get('FRAME_RATE', 10))
        self.max_age = int(env.get('MAX_AGE', 3))
        self.min_hits = int(env.get('MIN_HITS', 2))
        self.position_tol = float(env.get('KEYFRAME_POSITION_TOL', 0.25))
        self.hostname = env.get('LABEL_STUDIO_URL', '').rstrip('/')
        self.access_token = env.get('LABEL_STUDIO_API_KEY', '')
        # "/static/=/srv/label-studio/static,/data/=/srv/media" : read these URL prefixes from disk
        self.local_roots = [
            tuple(item.split('=', 1)) for item in env.get('LOCAL_ROOTS', '').split(',') if '=' in item
        ]
        # "Van:Car,Bus:Truck" : rename model classes to the project's labels
        self.label_map = dict(item.split(':', 1) for item in env.get('LABEL_MAP', '').split(',') if ':' in item)
        self.clouds = LRU(int(env.get('CACHE_FRAMES', 64)))
        self.detections = LRU(int(env.get('CACHE_FRAMES', 64)) * 4)

    @property
    def detector(self):
        if self._detector is None:
            self._detector = build_detector(self.env)
        return self._detector

    # ------------------------------------------------------------------------------------------ io

    def setup(self, hostname=None, access_token=None, project=None):
        """Label Studio sends its address and the project creator's token on connect and on health checks.

        Tokens are kept per project: one tracker serving several colleges must download each project's data
        with that project's own token. LABEL_STUDIO_API_KEY, when set, is used for every project.
        """
        if hostname and not self.env.get('LABEL_STUDIO_URL'):
            self.hostname = hostname.rstrip('/')
        if access_token and not self.env.get('LABEL_STUDIO_API_KEY'):
            key = self.project_key(project)
            if key is not None:
                self.project_tokens[key] = access_token
            else:
                self.access_token = access_token

    def token(self, project=None):
        if self.env.get('LABEL_STUDIO_API_KEY'):
            return self.env['LABEL_STUDIO_API_KEY']
        return self.project_tokens.get(self.project_key(project)) or self.access_token

    def _read_url(self, url, task_id=None, project=None):
        for prefix, root in self.local_roots:
            if url.startswith(prefix):
                path = os.path.realpath(os.path.join(root, url[len(prefix) :].split('?')[0]))
                if not path.startswith(os.path.realpath(root)):
                    raise ValueError(f'Refusing to read outside {root}')
                with open(path, 'rb') as f:
                    return f.read()
        parsed = urllib.parse.urlparse(url)
        headers = {}
        token = self.token(project)
        if parsed.scheme in ('http', 'https'):
            full = url
            # only send the Label Studio token to Label Studio itself, never to storage URLs
            if self.hostname and url.startswith(self.hostname):
                headers['Authorization'] = f'Token {token}'
        elif parsed.scheme and parsed.scheme not in ('file',):
            # cloud storage URI (s3://, gs://, azure-blob://): let Label Studio resolve it
            if not (self.hostname and task_id):
                raise ValueError(f'Cannot resolve {url}: LABEL_STUDIO_URL and a task id are needed')
            fileuri = base64.urlsafe_b64encode(url.encode()).decode()
            full = f'{self.hostname}/tasks/{task_id}/resolve/?fileuri={fileuri}'
            headers['Authorization'] = f'Token {token}'
        else:
            if not self.hostname:
                raise ValueError(f'Cannot download {url}: set LABEL_STUDIO_URL or LOCAL_ROOTS')
            full = self.hostname + (url if url.startswith('/') else '/' + url)
            headers['Authorization'] = f'Token {token}'
        request = urllib.request.Request(full, headers=headers)
        with urllib.request.urlopen(request, timeout=float(self.env.get('DOWNLOAD_TIMEOUT', 60))) as response:  # nosec
            return response.read()

    def load(self, url, task_id=None, bin_dims=4, project=None):
        # cached per project: a scan downloaded for one project is never served to another
        key = (self.project_key(project), url)
        if key in self.clouds:
            return self.clouds[key]
        return self.clouds.put(key, parse_point_cloud(self._read_url(url, task_id, project), url, bin_dims))

    @staticmethod
    def project_key(project):
        """Label Studio sends the project as "<id>.<created timestamp>" (or as a dict in webhooks)."""
        if isinstance(project, dict):
            project = project.get('id')
        if project is None or project == '':
            return None
        return str(project).split('.')[0]

    def active(self, project=None):
        """(model_version, detector) for a project: its trained model if any, else the base detector."""
        info = self.registry.current(self.project_key(project))
        if not info:
            return self.model_version, self.detector
        version = info['version']
        if version not in self._project_detectors:
            if info['kind'] == 'cluster':
                detector = info['model'].detector(float(self.env.get('MIN_CONFIDENCE', 0.5)))
            else:
                detector = build_detector({**self.env, 'PCDET_CFG': info['model_cfg']}, checkpoint=info['checkpoint'])
            self._project_detectors[version] = detector
        return version, self._project_detectors[version]

    def detect(self, url, task_id=None, bin_dims=4, project=None):
        version, detector = self.active(project)
        key = (self.project_key(project), version, url)
        if key not in self.detections:
            self.detections.put(key, detector.detect(self.load(url, task_id, bin_dims, project)))
        return self.detections[key]

    # ------------------------------------------------------------------------------------------ labels

    def to_project_label(self, label, project_labels):
        label = self.label_map.get(label, label)
        if not project_labels:
            return label
        lowered = {l.lower(): l for l in project_labels}
        return lowered.get(label.lower())

    def to_model_label(self, label):
        reverse = {v.lower(): k for k, v in self.label_map.items()}
        name = reverse.get((label or '').lower(), label or '')
        for known in ('Car', 'Truck', 'Pedestrian', 'Cyclist'):
            if known.lower() == name.lower():
                return known
        return name

    # ------------------------------------------------------------------------------------------ predict

    @staticmethod
    def frames_of(task, spec):
        raw = (task.get('data') or {}).get(spec['data_key'])
        if isinstance(raw, list):
            frames = []
            for item in raw:
                if isinstance(item, str):
                    frames.append({'url': item, 'pose': None})
                else:
                    item = item or {}
                    frames.append(
                        {
                            'url': str(item.get('pointcloud') or item.get('url') or item.get('lidar') or item.get('path') or ''),
                            'pose': normalize_pose(item.get('pose') or item.get('ego_pose') or item.get('lidar_to_world')),
                        }
                    )
            return frames, True
        return [{'url': str(raw or ''), 'pose': None}], False

    def predict(self, tasks, label_config, context=None, project=None):
        spec = parse_label_config(label_config)
        if spec is None:
            raise ValueError('The labeling config has no <Cuboid> on a <PointCloud>')
        if context and context.get('pointcloud_track'):
            return [self.extend(tasks[0], spec, context['pointcloud_track'], project)]
        return [self.predict_task(task, spec, project) for task in tasks]

    def predict_task(self, task, spec, project=None):
        frames, is_sequence = self.frames_of(task, spec)
        poses = [f['pose'] for f in frames]
        results, scores = [], []
        version = self.active(project)[0]
        detections = [self.detect(f['url'], task.get('id'), spec['bin_dims'], project) for f in frames]
        if not is_sequence:
            for i, d in enumerate(detections[0]):
                label = self.to_project_label(d['label'], spec['labels'])
                if label is None:
                    continue
                results.append(self._result(f'det-{task.get("id")}-{i}', spec, value_from_box(d['box']), label))
                scores.append(d['score'])
        else:
            tracker = MultiObjectTracker(
                min_hits=min(self.min_hits, len(frames)), max_age=self.max_age, frame_rate=self.frame_rate
            )
            tracks = [(t, *track_boxes(t, poses, self.frame_rate)) for t in tracker.run(detections, poses)]
            for track, model_label, boxes in sorted(suppress_duplicates(tracks), key=lambda e: e[0].id):
                label = self.to_project_label(model_label, spec['labels'])
                if label is None:
                    continue
                keyframes = simplify(boxes, position_tol=self.position_tol)
                sequence = to_sequence(boxes, keyframes, poses, len(frames))
                value = {'sequence': sequence, 'framesCount': len(frames)}
                results.append(self._result(f'trk-{task.get("id")}-{track.id}', spec, value, label))
                scores.append(sum(o['score'] for o in track.observations.values()) / len(track.observations))
        return {
            'result': results,
            'score': round(sum(scores) / len(scores), 3) if scores else 0.0,
            'model_version': version,
        }

    def extend(self, task, spec, request, project=None):
        """Follow one track (the annotator's keyframes) through the whole sequence."""
        frames, is_sequence = self.frames_of(task, spec)
        if not is_sequence:
            raise ValueError('Tracking needs a sequence task')
        poses = [f['pose'] for f in frames]
        region = request.get('region') or {}
        value = region.get('value') or {}
        keys = keyframes_sorted(value.get('sequence'))
        user = {k['frame']: to_world(box_from_value(k), poses[k['frame']]) for k in keys if k.get('enabled', True) is not False}
        if not user:
            raise ValueError('The track has no enabled keyframe')
        # an explicit end of track set by the annotator limits the forward search
        last_enabled = max(user)
        end = next((k['frame'] for k in keys if k['frame'] > last_enabled and k.get('enabled', True) is False), None)
        frames_count = end if end is not None else len(frames)
        labels = value.get('labels') or []
        model_label = self.to_model_label(labels[0]) if labels else ''

        def detect_near(frame, center, radius):
            out = []
            for d in self.detect(frames[frame]['url'], task.get('id'), spec['bin_dims'], project):
                w = to_world(d['box'], poses[frame])
                if math.hypot(w['x'] - center[0], w['y'] - center[1]) <= radius:
                    out.append({**d, 'box': w})
            return out

        boxes, static = extend_track(user, detect_near, frames_count, poses, model_label, max_age=self.max_age,
                                     frame_rate=self.frame_rate)
        size = user[max(user)]
        keyframes = simplify(boxes, keep=list(user), position_tol=self.position_tol)
        sequence = to_sequence(boxes, keyframes, poses, len(frames),
                               size=(size['length'], size['width'], size['height']))
        # the annotator's keyframes are returned exactly as they were sent
        by_frame = {k['frame']: k for k in sequence}
        for k in keys:
            if k['frame'] in user:
                by_frame[k['frame']] = k
        sequence = [by_frame[f] for f in sorted(by_frame)]
        added = sorted(set(boxes) - set(user))
        result = self._result(region.get('id') or 'track', spec, {'sequence': sequence, 'framesCount': len(frames)},
                              labels[0] if labels else None)
        return {
            'result': [result],
            'score': 1.0,
            'model_version': self.active(project)[0],
            'meta': {'tracked_frames': added, 'first': min(boxes), 'last': max(boxes), 'static': static},
        }

    # ------------------------------------------------------------------------------------------ training

    def train(self, project, tasks, label_config):
        """Start training on exported tasks (with annotations) in the background; returns the job id."""
        key = self.project_key(project)
        if key is None:
            raise ValueError('A project id is required for training')
        spec = parse_label_config(label_config)
        if spec is None:
            raise ValueError('The labeling config has no <Cuboid> on a <PointCloud>')
        return self.jobs.start(key, lambda job: self._train(key, tasks, spec, job))

    def train_from_label_studio(self, project):
        """START_TRAINING webhook: fetch the project's annotated tasks from Label Studio, then train."""
        key = self.project_key(project)
        label_config = project.get('label_config') if isinstance(project, dict) else None
        if key is None or not label_config:
            raise ValueError('The webhook did not include the project id and labeling config')
        if not self.hostname:
            raise ValueError('LABEL_STUDIO_URL is needed to download annotations')
        url = f'{self.hostname}/api/projects/{key}/export?exportType=JSON&download_all_tasks=false'
        request = urllib.request.Request(url, headers={'Authorization': f'Token {self.token(key)}'})
        with urllib.request.urlopen(request, timeout=float(self.env.get('EXPORT_TIMEOUT', 600))) as response:  # nosec
            tasks = json.loads(response.read())
        return self.train(key, tasks, label_config)

    def _train(self, key, tasks, spec, job):
        examples = examples_from_tasks(tasks, spec)
        job['scans'] = len(examples)
        if not examples:
            raise ValueError('No annotated point clouds to learn from')

        def load(url, task_id):
            return self.load(url, task_id, spec['bin_dims'], key)

        # unique even for two trainings within one second
        version = f'{self.model_version}-p{key}-{time.strftime("%Y%m%d%H%M%S")}-{uuid.uuid4().hex[:4]}'
        if self.env.get('PCDET_ROOT') and self.env.get('PCDET_CFG'):
            current = self.registry.current(key)
            base_ckpt = current.get('checkpoint') if current and current.get('kind') == 'openpcdet' else None
            priors = learn_priors(examples)
            work_dir = os.path.join(self.registry.project_dir(key), version)
            os.makedirs(work_dir, exist_ok=True)
            with open(os.path.join(work_dir, 'train.log'), 'w', encoding='utf-8') as log:
                checkpoint, model_cfg = OpenPCDetFineTuner(self.env).run(
                    key, examples, load, priors, base_ckpt or self.env.get('PCDET_CKPT'), work_dir, log
                )
            info = {
                'kind': 'openpcdet',
                'version': version,
                'checkpoint': checkpoint,
                'model_cfg': model_cfg,
                'labels': list(priors),
                'created': time.time(),
            }
            self.registry.save(key, info)
            return {'model_version': version, 'kind': 'openpcdet'}
        model = train_cluster_model(examples, load, version, k=int(self.env.get('KNN_K', 7)))
        self.registry.save(key, {'kind': 'cluster', 'version': version, 'metrics': model.metrics,
                                 'labels': model.labels, 'created': time.time()}, model)
        return {'model_version': version, 'kind': 'cluster', 'metrics': model.metrics}

    def versions(self, project):
        key = self.project_key(project)
        return [self.model_version] + (self.registry.versions(key) if key else [])

    @staticmethod
    def _result(region_id, spec, value, label):
        value = dict(value)
        if label:
            value['labels'] = [label]
        return {
            'id': region_id,
            'from_name': spec['cuboid'],
            'to_name': spec['cloud'],
            'type': 'cuboid',
            'value': value,
            'origin': 'prediction',
        }


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False)
