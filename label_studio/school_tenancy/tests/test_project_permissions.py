import pytest
from django.core.exceptions import ValidationError
from projects.models import Project, ProjectMember
from school_tenancy import access, services
from school_tenancy.models import PROJECT_FEATURES, Role
from tasks.models import Annotation, Task

from .test_tenancy import annotation_payload, client_for, make_user, w  # noqa: F401

pytestmark = pytest.mark.django_db


def grant(w, features, project=None, student=None):
    project = project or w.cs1
    student = student or w.u['cs_s1']
    services.assign_students(project, [student])
    membership = ProjectMember.objects.get(project=project, user=student)
    services.set_student_features(w.u['cs_teacher'], membership, features)
    return membership


def test_assignment_defaults_to_annotation_only_and_is_not_reset(w):
    student = w.u['cs_s1']
    assert access.project_features(student, w.cs1) == ['annotate']
    grant(w, ['annotate', 'export_data'])
    services.assign_students(w.cs1, [student])
    assert set(access.project_features(student, w.cs1)) == {'annotate', 'export_data'}
    services.unassign_student(w.cs1, student)
    assert access.project_features(student, w.cs1) == []
    services.assign_students(w.cs1, [student])
    assert access.project_features(student, w.cs1) == ['annotate']


def test_feature_grant_is_per_student_and_project_and_revocable(w):
    grant(w, ['annotate', 'export_data'])
    services.assign_students(w.cs2, [w.u['cs_s1']])
    services.assign_students(w.cs1, [w.u['cs_s2']])
    student = client_for(w.u['cs_s1'])
    assert student.get(f'/api/projects/{w.cs1.pk}/export?exportType=JSON').status_code == 200
    assert student.get(f'/api/projects/{w.cs2.pk}/export?exportType=JSON').status_code == 403
    other = client_for(w.u['cs_s2'])
    assert other.get(f'/api/projects/{w.cs1.pk}/export?exportType=JSON').status_code == 403
    grant(w, ['annotate'])
    assert student.get(f'/api/projects/{w.cs1.pk}/export?exportType=JSON').status_code == 403


def test_read_only_student_cannot_submit_or_change_old_annotation(w):
    client = client_for(w.u['cs_s1'])
    result = client.post(f'/api/tasks/{w.t_cs1.pk}/annotations/', annotation_payload(), format='json')
    assert result.status_code == 201, result.content
    annotation_id = result.json()['id']
    grant(w, [])
    assert client.get(f'/api/tasks/{w.t_cs1.pk}/').status_code == 200
    assert client.post(f'/api/tasks/{w.t_cs1.pk}/annotations/', annotation_payload(), format='json').status_code == 403
    assert client.patch(f'/api/annotations/{annotation_id}/', annotation_payload(), format='json').status_code == 403
    assert client.delete(f'/api/annotations/{annotation_id}/').status_code == 403
    assert client.get(f'/api/projects/{w.cs1.pk}/next/').status_code == 403
    assert Annotation.objects.filter(pk=annotation_id).exists()


def test_import_does_not_grant_project_configuration_or_other_project_access(w):
    grant(w, ['annotate', 'import_tasks'])
    client = client_for(w.u['cs_s1'])
    response = client.post(f'/api/projects/{w.cs1.pk}/import', [{'text': 'imported'}], format='json')
    assert response.status_code == 201, response.content
    assert Task.objects.filter(project=w.cs1, data={'text': 'imported'}).exists()
    assert client.patch(f'/api/projects/{w.cs1.pk}/', {'title': 'forbidden'}, format='json').status_code == 403
    assert client.delete(f'/api/tasks/{w.t_cs1.pk}/').status_code == 403
    assert client.post('/api/tasks/', {'project': w.med1.pk, 'data': {'text': 'bad'}}, format='json').status_code == 403


def test_configuration_cannot_move_project_or_delegate_teacher_authority(w):
    grant(w, ['configure_project'])
    client = client_for(w.u['cs_s1'])
    response = client.patch(
        f'/api/projects/{w.cs1.pk}/',
        {'title': 'student-configured', 'organization': w.med.organization_id},
        format='json',
    )
    assert response.status_code == 200, response.content
    w.cs1.refresh_from_db()
    assert w.cs1.title == 'student-configured'
    assert w.cs1.organization_id == w.cs.organization_id
    assert not access.can_manage_project(w.u['cs_s1'], w.cs1)
    assert client.post(
        f'/school/projects/{w.cs1.pk}/students/{w.u["cs_s1"].pk}/features/',
        {'features': ['export_data']},
    ).status_code == 403
    assert client.post('/api/projects/', {'title': 'not granted'}, format='json').status_code == 403
    assert client.delete(f'/api/projects/{w.cs1.pk}/').status_code == 403


def test_manage_tasks_and_delete_project_are_separate_explicit_grants(w):
    grant(w, ['manage_tasks'])
    client = client_for(w.u['cs_s1'])
    assert client.patch(f'/api/tasks/{w.t_cs1.pk}/', {'data': {'text': 'updated'}}, format='json').status_code == 200
    assert client.delete(f'/api/tasks/{w.t_cs1.pk}/').status_code == 204
    assert client.delete(f'/api/projects/{w.cs1.pk}/').status_code == 403
    grant(w, ['delete_project'])
    assert client.delete(f'/api/projects/{w.cs1.pk}/').status_code == 204
    assert not Project.objects.filter(pk=w.cs1.pk).exists()


def test_read_only_api_declarations_do_not_bypass_feature_checks(w):
    client = client_for(w.u['cs_s1'])
    response = client.delete(f'/api/projects/{w.cs1.pk}/model-versions/', {'model_version': 'v1'}, format='json')
    assert response.status_code == 403
    assert client.get(f'/api/dm/actions/?project={w.cs1.pk}').json() == []
    assert client.post(
        f'/api/dm/actions/?project={w.cs1.pk}&id=delete_tasks', {'selectedItems': {'all': True}}, format='json'
    ).status_code == 403
    grant(w, ['manage_tasks'])
    from school_tenancy.permissions import check_action_permission

    assert not check_action_permission(w.u['cs_s1'], {'permission': 'annotations.delete'}, w.cs1)


def test_teacher_cannot_access_or_authorize_another_teachers_project(w):
    teacher = make_user('other-teacher@t.edu.cn')
    services.add_member(w.cs, teacher, Role.TEACHER)
    client = client_for(teacher)
    assert client.get(f'/api/projects/{w.cs1.pk}/').status_code == 403
    assert client.post(f'/school/projects/{w.cs1.pk}/students/', {'emails': w.u['cs_s2'].email}).status_code == 404
    assert client.post(
        f'/school/projects/{w.cs1.pk}/students/{w.u["cs_s1"].pk}/features/', {'features': ['export_data']}
    ).status_code == 404
    assert w.cs1.title.encode() not in client.get('/school/').content
    services.set_project_teacher(w.u['cs_admin'], w.cs1, teacher)
    assert client.get(f'/api/projects/{w.cs1.pk}/').status_code == 200
    response = client.post(
        f'/school/projects/{w.cs1.pk}/students/{w.u["cs_s1"].pk}/features/', {'features': ['annotate', 'export_data']}
    )
    assert response.status_code == 302
    assert access.has_project_feature(w.u['cs_s1'], w.cs1, 'export_data')
    with pytest.raises(ValidationError):
        services.set_project_teacher(teacher, w.cs1, w.u['shared'])
    services.set_project_teacher(w.u['cs_admin'], w.cs1, teacher, enabled=False)
    assert client.get(f'/api/projects/{w.cs1.pk}/').status_code == 403
    from webhooks.models import Webhook

    webhook = Webhook.objects.create(
        organization=w.cs.organization, project=w.cs1, url='https://example.test/hook'
    )
    assert client.get('/api/webhooks/').json() == []
    assert client.get(f'/api/webhooks/{webhook.pk}/').status_code == 404


def test_college_admin_can_manage_all_and_cannot_designate_foreign_teacher(w):
    services.set_project_teacher(w.u['cs_admin'], w.cs1, w.u['shared'])
    membership = grant(w, ['annotate'])
    services.set_student_features(w.u['cs_admin'], membership, ['export_data'])
    assert access.project_features(w.u['cs_s1'], w.cs1) == ['export_data']
    with pytest.raises(ValidationError):
        services.set_project_teacher(w.u['cs_admin'], w.cs1, w.u['med_teacher'])
    with pytest.raises(ValidationError):
        services.set_student_features(w.u['med_admin'], membership, ['export_data'])
    with pytest.raises(ValidationError):
        services.set_student_features(w.u['cs_teacher'], membership, ['made_up_permission'])


def test_frontend_permission_response_and_teacher_form(w):
    client = client_for(w.u['cs_s1'])
    response = client.get(f'/api/projects/{w.cs1.pk}/')
    assert response.status_code == 200
    assert response.json()['school_features'] == ['annotate']
    assert response.json()['school_can_manage'] is False
    dm = client.get(f'/api/dm/project?project={w.cs1.pk}').json()
    assert dm['school_features'] == ['annotate']
    assert dm['can_manage_annotations'] is True
    assert dm['can_delete_tasks'] is False
    assert dm['can_manage_tasks'] is False
    assert client.get('/api/projects/?include=id,title,school_features,school_can_manage').status_code == 200
    me = client.get('/api/current-user/whoami').json()
    assert me['school_role'] == Role.STUDENT
    assert 'projects.create' not in me['permissions']
    assert 'projects.change' not in me['permissions']
    assert 'webhooks.change' not in me['permissions']
    teacher = client_for(w.u['cs_teacher'])
    form = teacher.get('/school/').content.decode()
    assert '保存功能权限' in form
    for name, _ in PROJECT_FEATURES:
        assert f'value="{name}"' in form
    teacher.post(
        f'/school/projects/{w.cs1.pk}/students/{w.u["cs_s1"].pk}/features/',
        {'features': ['annotate', 'export_data']},
    )
    assert set(client.get(f'/api/projects/{w.cs1.pk}/').json()['school_features']) == {'annotate', 'export_data'}
    assert not client.get(f'/api/projects/{w.cs1.pk}/').json()['school_can_manage']
