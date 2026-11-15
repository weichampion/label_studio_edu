"""3D point cloud annotation (<PointCloud> + <Cuboid>) in the school edition."""

import io
import zipfile

import pytest
from projects.models import Project, ProjectMember
from school_tenancy import access, services
from school_tenancy.template_groups import config_allowed
from tasks.models import Annotation, Task

from .test_tenancy import client_for, w  # noqa: F401

pytestmark = pytest.mark.django_db

CONFIG = (
    '<View>'
    '<Labels name="label" toName="pcd"><Label value="Car"/><Label value="Pedestrian"/></Labels>'
    '<PointCloud name="pcd" value="$pointcloud" images="$cameras"/>'
    '<Cuboid name="box" toName="pcd"/>'
    '<Image name="image" value="$image"/>'
    '<Choices name="weather" toName="image"><Choice value="Sunny"/><Choice value="Rain"/></Choices>'
    '</View>'
)
CUBOID = {
    'id': 'c1',
    'from_name': 'box',
    'to_name': 'pcd',
    'type': 'cuboid',
    'value': {
        'position': {'x': 12.0, 'y': -2.2, 'z': -0.95},
        'dimensions': {'length': 4.4, 'width': 1.85, 'height': 1.55},
        'rotation': {'x': 0, 'y': 0, 'z': 0.0},
        'labels': ['Car'],
    },
}
WEATHER = {'from_name': 'weather', 'to_name': 'image', 'type': 'choices', 'value': {'choices': ['Sunny']}}
CAMERAS = [
    {
        'name': 'front',
        'url': '/static/samples/pointcloud/scene_000001_front.png',
        'intrinsic': [620, 0, 480, 0, 620, 250, 0, 0, 1],
        'extrinsic': [0, -1, 0, 0, 0, 0, -1, -0.08, 1, 0, 0, -0.27, 0, 0, 0, 1],
        'width': 960,
        'height': 540,
    }
]


@pytest.fixture
def lidar(w):
    project = Project.objects.create(
        title='lidar', label_config=CONFIG, organization=w.cs.organization, created_by=w.u['cs_teacher']
    )
    task = Task.objects.create(
        project=project,
        data={'pointcloud': '/data/upload/1/scene_000001.pcd', 'cameras': CAMERAS, 'image': '/static/x.png'},
    )
    services.assign_students(project, [w.u['cs_s1']])
    return project, task


def restrict(w, project, features=('annotate',), modules=None):
    membership = ProjectMember.objects.get(project=project, user=w.u['cs_s1'])
    services.set_student_features(w.u['cs_teacher'], membership, list(features), modules)


def test_pointcloud_is_its_own_annotation_module(w, lidar):
    project, _ = lidar
    assert access.project_modules(project) == ['image', 'pointcloud']
    assert access.control_modules(project)['box'] == {'pointcloud'}
    assert access.control_modules(project)['label'] == {'pointcloud'}


def test_withheld_pointcloud_module_hides_and_rejects_cuboids(w, lidar):
    project, task = lidar
    restrict(w, project, modules=['image'])
    client = client_for(w.u['cs_s1'])
    config = client.get(f'/api/projects/{project.pk}/').json()['school_label_config']
    for withheld in ('name="pcd"', 'name="box"', 'name="label"'):
        assert withheld not in config
    assert 'name="weather"' in config
    url = f'/api/tasks/{task.pk}/annotations/'
    assert client.post(url, {'result': [CUBOID]}, format='json').status_code == 403
    assert client.post(url, {'result': [WEATHER]}, format='json').status_code == 201

    restrict(w, project, modules=['pointcloud'])
    response = client.post(url, {'result': [CUBOID]}, format='json')
    assert response.status_code == 201, response.content
    assert Annotation.objects.get(pk=response.json()['id']).result[0]['type'] == 'cuboid'


def test_cuboid_labels_are_counted_and_protected(w, lidar):
    project, task = lidar
    response = client_for(w.u['cs_s1']).post(f'/api/tasks/{task.pk}/annotations/', {'result': [CUBOID]}, format='json')
    assert response.status_code == 201, response.content
    project.refresh_from_db()
    assert project.summary.created_labels['box'] == {'Car': 1}
    # "Car" is still used by a cuboid, so it can't be removed from the labeling config
    teacher = client_for(w.u['cs_teacher'])
    url = f'/api/projects/{project.pk}/validate'
    assert teacher.post(url, {'label_config': CONFIG}, format='json').status_code == 200
    without_car = CONFIG.replace('<Label value="Car"/>', '')
    assert teacher.post(url, {'label_config': without_car}, format='json').status_code == 400


def test_3d_export_formats_follow_export_grant(w, lidar):
    project, task = lidar
    Annotation.objects.create(task=task, project=project, completed_by=w.u['cs_teacher'], result=[CUBOID])
    teacher = client_for(w.u['cs_teacher'])
    formats = {f['name']: f for f in teacher.get(f'/api/projects/{project.pk}/export/formats').json()}
    assert not formats['OPENPCDET_3D'].get('disabled') and not formats['KITTI_3D'].get('disabled')

    student = client_for(w.u['cs_s1'])
    assert student.get(f'/api/projects/{project.pk}/export?exportType=KITTI_3D').status_code == 403
    restrict(w, project, features=('annotate', 'export_data'))
    for export_type, label_file, first_column in (
        ('OPENPCDET_3D', 'labels/scene_000001.txt', '12.0000'),
        ('KITTI_3D', 'label_2/scene_000001.txt', 'Car'),
    ):
        response = student.get(f'/api/projects/{project.pk}/export?exportType={export_type}')
        assert response.status_code == 200
        archive = zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content)))
        line = archive.read(label_file).decode().splitlines()[0]
        assert line.split()[0] == first_column
        assert 'mapping.json' in archive.namelist()


def test_3d_template_group_is_grantable():
    template = (
        '<View><Labels name="label" toName="pcd"><Label value="Bus"/></Labels>'
        '<PointCloud name="pcd" value="$pointcloud"/><Cuboid name="box" toName="pcd"/></View>'
    )
    assert config_allowed(template, ['3D Point Cloud']) is True
    assert config_allowed(template, ['Computer Vision']) is False


SEQUENCE_CONFIG = (
    '<View>'
    '<Labels name="label" toName="pcd"><Label value="Car"/></Labels>'
    '<PointCloud name="pcd" value="$frames"/>'
    '<Cuboid name="box" toName="pcd"/>'
    '</View>'
)
FRAMES = [
    {'pointcloud': f'/data/upload/1/{i:06d}.pcd', 'pose': [1, 0, 0, float(i), 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]}
    for i in range(4)
]


def keyframe(frame, x, enabled=True):
    return {
        'frame': frame,
        'enabled': enabled,
        'position': {'x': x, 'y': 0.0, 'z': -0.95},
        'dimensions': {'length': 4.4, 'width': 1.85, 'height': 1.55},
        'rotation': {'x': 0, 'y': 0, 'z': 0.0},
    }


TRACK = {
    'id': 't1',
    'from_name': 'box',
    'to_name': 'pcd',
    'type': 'cuboid',
    # world x goes 12 -> 18 while the ego moves 1 m/frame: LiDAR x goes 12 -> 15
    'value': {'sequence': [keyframe(0, 12.0), keyframe(3, 15.0)], 'framesCount': 4, 'labels': ['Car']},
}


def test_sequence_tasks_tracks_and_tracking_export(w):
    project = Project.objects.create(
        title='seq', label_config=SEQUENCE_CONFIG, organization=w.cs.organization, created_by=w.u['cs_teacher']
    )
    teacher = client_for(w.u['cs_teacher'])
    # a list of frames is valid task data for <PointCloud>
    response = teacher.post(f'/api/projects/{project.pk}/import', [{'frames': FRAMES}], format='json')
    assert response.status_code == 201, response.content
    task = Task.objects.get(project=project)
    services.assign_students(project, [w.u['cs_s1']])
    student = client_for(w.u['cs_s1'])
    response = student.post(f'/api/tasks/{task.pk}/annotations/', {'result': [TRACK]}, format='json')
    assert response.status_code == 201, response.content
    project.refresh_from_db()
    assert project.summary.created_labels['box'] == {'Car': 1}

    formats = {f['name']: f for f in teacher.get(f'/api/projects/{project.pk}/export/formats').json()}
    assert not formats['KITTI_TRACKING'].get('disabled')
    response = teacher.get(f'/api/projects/{project.pk}/export?exportType=OPENPCDET_3D')
    archive = zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content)))
    # interpolated in world coordinates: frame 1 is at LiDAR x = 14 - 1 = 13
    assert archive.read('labels/000001.txt').decode().split()[0] == '13.0000'
    assert '000003' in archive.read('tracks.json').decode()

    assert student.get(f'/api/projects/{project.pk}/export?exportType=KITTI_TRACKING').status_code == 403


def test_model_tracking_endpoint(w, monkeypatch):
    from ml.models import MLBackend

    project = Project.objects.create(
        title='seq-ml', label_config=SEQUENCE_CONFIG, organization=w.cs.organization, created_by=w.u['cs_teacher']
    )
    task = Task.objects.create(project=project, data={'frames': FRAMES})
    services.assign_students(project, [w.u['cs_s1']])
    student = client_for(w.u['cs_s1'])
    url = '/api/pointcloud3d/track/'
    track = {**TRACK, 'value': {**TRACK['value'], 'sequence': [keyframe(0, 12.0)]}}

    assert student.get(f'{url}?task={task.pk}').json()['available'] is False
    assert student.post(url, {'task': task.pk, 'result': [track]}, format='json').status_code == 404

    MLBackend.objects.create(project=project, url='http://tracker:9090', title='tracker', is_interactive=True)
    status = student.get(f'{url}?task={task.pk}').json()
    assert status['available'] is True and status['backend'] == 'tracker'

    calls = []

    def fake_interactive(self, task_, context=None, user=None):
        calls.append(context)
        sequence = [keyframe(0, 12.0), keyframe(3, 15.0), keyframe(4, 15.0, enabled=False)]
        return {'data': {'result': [{**track, 'value': {'sequence': sequence}}], 'meta': {'tracked_frames': [1, 2, 3]}}}

    monkeypatch.setattr(MLBackend, 'interactive_annotating', fake_interactive)
    response = student.post(url, {'task': task.pk, 'result': [track]}, format='json')
    assert response.status_code == 200, response.content
    body = response.json()
    assert calls[0]['pointcloud_track']['region']['id'] == 't1'
    assert [k['frame'] for k in body['sequence']] == [0, 3]  # frame 4 is outside the 4-frame sequence
    assert body['meta']['tracked_frames'] == [1, 2, 3]

    # without the annotate grant, or outside the college, the model can't be used
    services.set_student_features(
        w.u['cs_teacher'], ProjectMember.objects.get(project=project, user=w.u['cs_s1']), []
    )
    assert student.post(url, {'task': task.pk, 'result': [track]}, format='json').status_code == 403
    other_college = client_for(w.u['med_teacher'])
    assert other_college.post(url, {'task': task.pk, 'result': [track]}, format='json').status_code in (403, 404)

    # a broken model answer is reported, not passed to the editor
    monkeypatch.setattr(MLBackend, 'interactive_annotating', lambda *a, **k: {'data': {'result': [{'type': 'cuboid'}]}})
    teacher = client_for(w.u['cs_teacher'])
    assert teacher.post(url, {'task': task.pk, 'result': [track]}, format='json').status_code == 502
    assert teacher.post(url, {'task': task.pk, 'result': [{'type': 'labels'}]}, format='json').status_code == 400


# --------------------------------------------------------------------------------------------------
# semantic segmentation (<PointLabels>)
# --------------------------------------------------------------------------------------------------

SEG_CONFIG = (
    '<View>'
    '<PointCloud name="pcd" value="$pointcloud"/>'
    '<PointLabels name="seg" toName="pcd"><Label value="Road"/><Label value="Car"/></PointLabels>'
    '<Image name="image" value="$image"/>'
    '<Choices name="weather" toName="image"><Choice value="Sunny"/><Choice value="Rain"/></Choices>'
    '</View>'
)
# 3 x Car, 2 x Road, 1 unlabeled (codes index "classes")
POINT_LABELS = {
    'id': 's1',
    'from_name': 'seg',
    'to_name': 'pcd',
    'type': 'pointlabels',
    'value': {
        'classes': ['Car', 'Road'],
        'frames': [{'frame': 0, 'count': 6, 'rle': 'AQMCAgAB'}],
        'pointlabels': ['Car', 'Road'],
    },
}


@pytest.fixture
def segmentation(w):
    project = Project.objects.create(
        title='seg', label_config=SEG_CONFIG, organization=w.cs.organization, created_by=w.u['cs_teacher']
    )
    task = Task.objects.create(project=project, data={'pointcloud': '/data/upload/1/scene_000001.pcd', 'image': '/x.png'})
    services.assign_students(project, [w.u['cs_s1']])
    return project, task


def test_point_labels_belong_to_the_pointcloud_module(w, segmentation):
    project, task = segmentation
    assert access.control_modules(project)['seg'] == {'pointcloud'}
    restrict(w, project, modules=['image'])
    client = client_for(w.u['cs_s1'])
    url = f'/api/tasks/{task.pk}/annotations/'
    assert client.post(url, {'result': [POINT_LABELS]}, format='json').status_code == 403
    restrict(w, project, modules=['pointcloud'])
    assert client.post(url, {'result': [POINT_LABELS]}, format='json').status_code == 201


def test_point_labels_are_counted_and_protected(w, segmentation):
    project, task = segmentation
    response = client_for(w.u['cs_s1']).post(
        f'/api/tasks/{task.pk}/annotations/', {'result': [POINT_LABELS]}, format='json'
    )
    assert response.status_code == 201, response.content
    project.refresh_from_db()
    assert project.summary.created_labels['seg'] == {'Car': 1, 'Road': 1}
    teacher = client_for(w.u['cs_teacher'])
    url = f'/api/projects/{project.pk}/validate'
    assert teacher.post(url, {'label_config': SEG_CONFIG}, format='json').status_code == 200
    without_road = SEG_CONFIG.replace('<Label value="Road"/>', '')
    assert teacher.post(url, {'label_config': without_road}, format='json').status_code == 400


def test_semantic_kitti_export_follows_export_grant(w, segmentation, lidar):
    project, task = segmentation
    Annotation.objects.create(task=task, project=project, completed_by=w.u['cs_teacher'], result=[POINT_LABELS])
    teacher = client_for(w.u['cs_teacher'])
    formats = {f['name']: f for f in teacher.get(f'/api/projects/{project.pk}/export/formats').json()}
    assert not formats['SEMANTIC_KITTI'].get('disabled')
    assert formats['OPENPCDET_3D'].get('disabled')
    # the box project doesn't offer the segmentation format
    box_project, _ = lidar
    box_formats = {f['name']: f for f in teacher.get(f'/api/projects/{box_project.pk}/export/formats').json()}
    assert box_formats['SEMANTIC_KITTI'].get('disabled')

    student = client_for(w.u['cs_s1'])
    url = f'/api/projects/{project.pk}/export?exportType=SEMANTIC_KITTI'
    assert student.get(url).status_code == 403
    restrict(w, project, features=('annotate', 'export_data'))
    response = student.get(url)
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content)))
    data = archive.read('labels/scene_000001.label')
    # config order: Road = 1, Car = 2
    assert list(memoryview(data).cast('I')) == [2, 2, 2, 1, 1, 0]


def test_segmentation_template_group_is_grantable():
    template = (
        '<View><PointCloud name="pcd" value="$pointcloud"/>'
        '<PointLabels name="seg" toName="pcd"><Label value="Road"/></PointLabels></View>'
    )
    assert config_allowed(template, ['3D Point Cloud']) is True
    assert config_allowed(template, ['Computer Vision']) is False
