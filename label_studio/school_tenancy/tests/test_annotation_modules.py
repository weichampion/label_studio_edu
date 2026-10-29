import pytest
from django.core.exceptions import ValidationError
from projects.models import Project, ProjectMember
from school_tenancy import access, services
from tasks.models import Annotation, Task

from .test_tenancy import client_for, w  # noqa: F401

pytestmark = pytest.mark.django_db

MULTI_CONFIG = (
    '<View>'
    '<View><Header value="Text part"/>'
    '<Text name="text" value="$text"/>'
    '<Choices name="sentiment" toName="text"><Choice value="A"/><Choice value="B"/></Choices>'
    '</View>'
    '<Header value="Image part"/>'
    '<Image name="image" value="$image"/>'
    '<RectangleLabels name="box" toName="image"><Label value="Car"/></RectangleLabels>'
    '<Audio name="audio" value="$audio"/>'
    '<Choices name="sound" toName="audio"><Choice value="Speech"/></Choices>'
    '</View>'
)

SENTIMENT = {'from_name': 'sentiment', 'to_name': 'text', 'type': 'choices', 'value': {'choices': ['A']}}
BOX = {
    'id': 'box1',
    'from_name': 'box',
    'to_name': 'image',
    'type': 'rectanglelabels',
    'original_width': 100,
    'original_height': 100,
    'value': {'x': 1, 'y': 1, 'width': 10, 'height': 10, 'rotation': 0, 'rectanglelabels': ['Car']},
}
SOUND = {'from_name': 'sound', 'to_name': 'audio', 'type': 'choices', 'value': {'choices': ['Speech']}}


@pytest.fixture
def multi(w):
    project = Project.objects.create(
        title='multi', label_config=MULTI_CONFIG, organization=w.cs.organization, created_by=w.u['cs_teacher']
    )
    task = Task.objects.create(
        project=project, data={'text': 'hello', 'image': '/static/x.png', 'audio': '/static/x.wav'}
    )
    services.assign_students(project, [w.u['cs_s1'], w.u['cs_s2']])
    return project, task


def restrict(w, project, modules, student=None, features=('annotate',)):
    membership = ProjectMember.objects.get(project=project, user=student or w.u['cs_s1'])
    services.set_student_features(w.u['cs_teacher'], membership, list(features), modules)
    return membership


def test_modules_are_detected_and_default_to_all(w, multi):
    project, _ = multi
    assert access.project_modules(project) == ['text', 'image', 'audio']
    assert access.project_modules(w.cs1) == ['text']
    student = w.u['cs_s1']
    assert access.allowed_modules(student, project) is None
    assert access.hidden_controls(student, project) == set()
    body = client_for(student).get(f'/api/projects/{project.pk}/').json()
    assert body['school_label_config'] is None
    assert body['school_features'] == ['annotate']


def test_restricted_student_sees_and_submits_only_granted_modules(w, multi):
    project, task = multi
    restrict(w, project, ['image'])
    client = client_for(w.u['cs_s1'])
    config = client.get(f'/api/projects/{project.pk}/').json()['school_label_config']
    assert 'name="box"' in config and 'name="image"' in config
    for withheld in ('name="sentiment"', 'name="text"', 'name="sound"', 'name="audio"', 'Text part'):
        assert withheld not in config
    assert 'Image part' in config
    assert client.get(f'/api/dm/project?project={project.pk}').json()['school_label_config'] == config

    url = f'/api/tasks/{task.pk}/annotations/'
    assert client.post(url, {'result': [SENTIMENT]}, format='json').status_code == 403
    assert client.post(url, {'result': [BOX, SOUND]}, format='json').status_code == 403
    assert client.post(f'/api/tasks/{task.pk}/drafts', {'result': [SOUND]}, format='json').status_code == 403
    response = client.post(url, {'result': [BOX]}, format='json')
    assert response.status_code == 201, response.content
    annotation_id = response.json()['id']
    assert client.patch(f'/api/annotations/{annotation_id}/', {'result': [SENTIMENT]}, format='json').status_code == 403
    assert client.patch(f'/api/annotations/{annotation_id}/', {'result': [BOX]}, format='json').status_code == 200
    assert Annotation.objects.get(pk=annotation_id).result[0]['from_name'] == 'box'

    # Another student and the teacher keep every module.
    other = client_for(w.u['cs_s2'])
    assert other.get(f'/api/projects/{project.pk}/').json()['school_label_config'] is None
    assert other.post(url, {'result': [SENTIMENT]}, format='json').status_code == 201
    teacher = client_for(w.u['cs_teacher'])
    assert teacher.get(f'/api/projects/{project.pk}/').json()['school_label_config'] is None
    assert teacher.post(url, {'result': [SOUND]}, format='json').status_code == 201


def test_no_granted_module_disables_annotation(w, multi):
    project, task = multi
    restrict(w, project, [])
    student = w.u['cs_s1']
    assert 'annotate' not in access.project_features(student, project)
    client = client_for(student)
    assert client.post(f'/api/tasks/{task.pk}/annotations/', {'result': [BOX]}, format='json').status_code == 403
    assert client.get(f'/api/projects/{project.pk}/next/').status_code == 403
    restrict(w, project, ['text', 'image', 'audio'])
    assert ProjectMember.objects.get(project=project, user=student).student_permissions.annotation_modules is None
    assert 'annotate' in access.project_features(student, project)


def test_teacher_form_saves_modules_and_validates_them(w, multi):
    project, _ = multi
    student = w.u['cs_s1']
    teacher = client_for(w.u['cs_teacher'])
    page = teacher.get('/school/').content.decode()
    assert 'name="modules"' in page and '图像标注' in page and '音频标注' in page
    url = f'/school/projects/{project.pk}/students/{student.pk}/features/'
    teacher.post(url, {'features': ['annotate', 'export_data'], 'modules_submitted': '1', 'modules': ['audio']})
    grant = ProjectMember.objects.get(project=project, user=student).student_permissions
    assert grant.annotation_modules == ['audio'] and grant.export_data
    teacher.post(url, {'features': ['annotate']})  # a form without module fields keeps the modules
    grant.refresh_from_db()
    assert grant.annotation_modules == ['audio'] and not grant.export_data
    assert '音频标注' in client_for(student).get('/school/').content.decode()
    membership = ProjectMember.objects.get(project=project, user=student)
    with pytest.raises(ValidationError):
        services.set_student_features(w.u['cs_teacher'], membership, ['annotate'], ['video'])
    student_client = client_for(student)
    assert student_client.post(url, {'features': ['annotate'], 'modules_submitted': '1'}).status_code == 403
    grant.refresh_from_db()
    assert grant.annotation_modules == ['audio']
