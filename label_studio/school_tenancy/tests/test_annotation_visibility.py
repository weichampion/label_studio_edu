"""College-wide annotation data: college admins are scoped to their college, superusers see all."""

import pytest
from tasks.models import Annotation
from users.models import User

from .test_tenancy import client_for, w  # noqa: F401

pytestmark = pytest.mark.django_db


def make_annotation(task, user):
    return Annotation.objects.create(
        task=task,
        project=task.project,
        completed_by=user,
        result=[{'from_name': 'label', 'to_name': 'text', 'type': 'choices', 'value': {'choices': ['A']}}],
    )


@pytest.fixture
def data(w):
    cs_a = make_annotation(w.t_cs1, w.u['cs_s1'])
    cs_b = make_annotation(w.t_cs2, w.u['cs_s2'])
    med = make_annotation(w.t_med1, w.u['med_s1'])
    root = User.objects.create_superuser(email='root@t.edu.cn', password='Str0ng!Passw0rd', username='root')
    return {'w': w, 'cs_a': cs_a, 'cs_b': cs_b, 'med': med, 'root': root}


def test_college_admin_only_sees_own_college(data):
    w = data['w']
    client = client_for(w.u['cs_admin'])
    response = client.get('/school/annotations/?format=json')
    assert response.status_code == 200
    body = response.json()
    assert body['count'] == 2
    assert {row['organization'] for row in body['results']} == {w.cs.organization_id}
    assert {row['id'] for row in body['results']} == {data['cs_a'].id, data['cs_b'].id}
    html = client.get('/school/annotations/').content.decode()
    assert '所有标注数据' in html
    assert w.cs1.title in html and w.cs2.title in html
    assert w.med1.title not in html


def test_system_admin_sees_all_and_can_filter_by_college(data):
    w = data['w']
    root = client_for(data['root'])
    response = root.get('/school/annotations/?format=json')
    assert response.status_code == 200
    body = response.json()
    assert body['count'] == 3
    assert {row['id'] for row in body['results']} == {data['cs_a'].id, data['cs_b'].id, data['med'].id}

    filtered = root.get(f'/school/annotations/?format=json&college={w.med.organization_id}')
    assert filtered.status_code == 200
    assert filtered.json()['count'] == 1
    assert filtered.json()['results'][0]['id'] == data['med'].id
    assert root.get('/school/annotations/').status_code == 200


def test_teachers_and_students_cannot_use_global_annotation_view(data):
    w = data['w']
    for key in ('cs_teacher', 'cs_s1', 'cs_s2', 'med_teacher', 'med_s1'):
        assert client_for(w.u[key]).get('/school/annotations/').status_code == 403, key


def test_whoami_exposes_view_all_annotations_only_for_college_admins(data):
    w = data['w']
    admin_permissions = client_for(w.u['cs_admin']).get('/api/current-user/whoami').json()['permissions']
    student_permissions = client_for(w.u['cs_s1']).get('/api/current-user/whoami').json()['permissions']
    assert 'annotations.view_all' in admin_permissions
    assert 'annotations.view_all' not in student_permissions


def test_system_admin_enters_the_annotation_page_from_school_root(data):
    root = client_for(data['root'])
    whoami = root.get('/api/current-user/whoami').json()
    assert whoami['school_role'] == 'system_admin'
    response = root.get('/school/')
    assert response.status_code == 302
    assert response.url.endswith('/school/annotations/')
