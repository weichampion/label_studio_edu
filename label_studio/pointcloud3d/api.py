"""Model-based tracking for the 3D editor: forwards one track to the project's interactive ML backend.

GET  /api/pointcloud3d/track/?task=<id>        -> {"available": bool, "backend": str | null}
POST /api/pointcloud3d/track/  {"task": id, "result": [<cuboid track result>]}
                                               -> {"sequence": [...], "meta": {...}, "model_version": str}

The ML backend must have "Use for interactive preannotations" enabled (``is_interactive``); see
ml_backends/pointcloud_tracker for a reference implementation. Permissions: viewing the task for GET,
creating annotations for POST (the school edition also checks the student's "annotate" grant and the
3D point cloud annotation module, because the region is sent as ``result``).
"""

import logging

from core.permissions import ViewClassPermission, all_permissions
from django.shortcuts import get_object_or_404
from projects.models import Project
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from tasks.models import Task

logger = logging.getLogger(__name__)
CONTEXT_KEY = 'pointcloud_track'
MAX_KEYFRAMES = 100_000


def interactive_backend(project):
    return project.ml_backends.filter(is_interactive=True).order_by('-id').first()


def _int(value, name):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError({name: 'An integer id is required'})


def validate_track(region):
    """The single cuboid track sent by the editor."""
    if not isinstance(region, dict) or region.get('type') != 'cuboid':
        raise ValidationError({'result': 'Exactly one cuboid track is expected'})
    sequence = (region.get('value') or {}).get('sequence')
    if not isinstance(sequence, list) or not sequence:
        raise ValidationError({'result': 'The cuboid has no keyframes (value.sequence)'})
    return region


def clean_sequence(sequence, frames_count=None):
    """Keyframes returned by the model: dicts with an integer frame and box fields, sorted, deduplicated;
    keyframes outside the sequence are dropped."""
    if not isinstance(sequence, list) or not sequence or len(sequence) > MAX_KEYFRAMES:
        raise ValueError('the model returned no keyframes')
    out = {}
    for k in sequence:
        if not isinstance(k, dict) or not all(key in k for key in ('frame', 'position', 'dimensions', 'rotation')):
            raise ValueError('malformed keyframe')
        frame = int(k['frame'])
        if frame < 0 or (isinstance(frames_count, int) and frame >= frames_count):
            continue  # e.g. an end-of-track marker after the last frame
        out[frame] = {
            'frame': frame,
            'enabled': k.get('enabled', True) is not False,
            'position': {a: float(k['position'][a]) for a in ('x', 'y', 'z')},
            'dimensions': {a: float(k['dimensions'][a]) for a in ('length', 'width', 'height')},
            'rotation': {a: float((k['rotation'] or {}).get(a, 0)) for a in ('x', 'y', 'z')},
        }
    if not any(k['enabled'] for k in out.values()):
        raise ValueError('the model returned no keyframe inside the sequence')
    return [out[f] for f in sorted(out)]


class PointCloudTrackAPI(APIView):
    permission_required = ViewClassPermission(
        GET=all_permissions.tasks_view,
        POST=all_permissions.annotations_create,
    )

    def _task(self, request, task_id):
        task = get_object_or_404(
            Task.objects.select_related('project').filter(project__in=Project.objects.for_user(request.user)),
            pk=_int(task_id, 'task'),
        )
        self.check_object_permissions(request, task)
        return task

    def get(self, request):
        task = self._task(request, request.query_params.get('task'))
        backend = interactive_backend(task.project)
        return Response(
            {
                'available': backend is not None,
                'backend': backend.title if backend else None,
                'model_version': backend.model_version if backend else None,
            }
        )

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        task = self._task(request, data.get('task'))
        results = data.get('result')
        if not isinstance(results, list) or len(results) != 1:
            raise ValidationError({'result': 'Exactly one cuboid track is expected'})
        region = validate_track(results[0])
        backend = interactive_backend(task.project)
        if backend is None:
            return Response(
                {'detail': 'No ML backend for interactive preannotations is connected to this project'},
                status=status.HTTP_404_NOT_FOUND,
            )

        response = backend.interactive_annotating(task, {CONTEXT_KEY: {'region': region}}, user=request.user)
        if response.get('errors'):
            return Response({'detail': '; '.join(map(str, response['errors']))}, status=status.HTTP_502_BAD_GATEWAY)
        prediction = response.get('data') or {}
        candidates = [r for r in prediction.get('result') or [] if isinstance(r, dict) and r.get('type') == 'cuboid']
        track = next((r for r in candidates if r.get('id') == region.get('id')), candidates[0] if candidates else None)
        frames_count = (region.get('value') or {}).get('framesCount')
        try:
            sequence = clean_sequence((track or {}).get('value', {}).get('sequence'), frames_count)
        except (ValueError, TypeError, KeyError) as e:
            logger.info('Tracking backend %s returned an unusable track: %s', backend.pk, e)
            return Response({'detail': f'The model returned an unusable track: {e}'}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(
            {
                'sequence': sequence,
                'meta': prediction.get('meta') or {},
                'model_version': prediction.get('model_version') or backend.model_version,
            }
        )
