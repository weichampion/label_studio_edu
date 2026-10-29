import pytest
from organizations.models import OrganizationMember
from projects.models import Project
from school_tenancy import access, services
from school_tenancy.models import ProjectTeacher, Role, StudentCreatePermission
from school_tenancy.template_groups import CUSTOM_TEMPLATE

from .test_tenancy import CONFIG as TEXT_CONFIG
from .test_tenancy import client_for, make_user, w  # noqa: F401

pytestmark = pytest.mark.django_db

CV = 'Computer Vision'
NLP = 'Natural Language Processing'
IMAGE_CONFIG = (
    '<View><Image name="image" value="$image"/>'
    '<RectangleLabels name="box" toName="image"><Label value="Car"/></RectangleLabels></View>'
)


def member_of(w, user):
    return OrganizationMember.objects.get(user=user, organization=w.cs.organization)


def grant_create(w, groups, student=None, teacher=None):
    client = client_for(teacher or w.u['cs_teacher'])
    member = member_of(w, student or w.u['cs_s1'])
    return client.post(f'/school/members/{member.pk}/create-templates/', {'groups': groups})


def test_students_cannot_create_projects_by_default(w):
    client = client_for(w.u['cs_s1'])
    assert 'projects.create' not in client.get('/api/current-user/whoami').json()['permissions']
    assert client.post('/api/projects/', {'title': 'mine'}, format='json').status_code == 403
    templates = client.get('/api/templates/').json()
    assert templates['groups'] == [] and templates['templates'] == [] and templates['school_custom_template'] is False
    assert '你可以使用以下模板分类' not in client.get('/school/').content.decode()
    teacher_templates = client_for(w.u['cs_teacher']).get('/api/templates/').json()
    assert CV in teacher_templates['groups'] and 'school_custom_template' not in teacher_templates


def test_teacher_grants_template_groups_for_project_creation(w):
    student = w.u['cs_s1']
    assert grant_create(w, [CV]).status_code == 302
    client = client_for(student)
    assert 'projects.create' in client.get('/api/current-user/whoami').json()['permissions']
    templates = client.get('/api/templates/').json()
    assert templates['groups'] == [CV] and templates['school_custom_template'] is False
    assert templates['templates'] and {t['group'] for t in templates['templates']} == {CV}
    assert CV in client.get('/school/').content.decode()

    denied = client.post('/api/projects/', {'title': 'text', 'label_config': TEXT_CONFIG}, format='json')
    assert denied.status_code == 403 and '模板分类' in denied.json()['detail']
    response = client.post('/api/projects/', {'title': 'mine'}, format='json')
    assert response.status_code == 201, response.content
    project = Project.objects.get(pk=response.json()['id'])
    assert project.organization_id == w.cs.organization_id and project.created_by_id == student.pk

    assert client.patch(f'/api/projects/{project.pk}/', {'label_config': TEXT_CONFIG}, format='json').status_code == 403
    response = client.patch(f'/api/projects/{project.pk}/', {'label_config': IMAGE_CONFIG}, format='json')
    assert response.status_code == 200, response.content
    assert client.post('/api/projects/', {'title': 'img', 'label_config': IMAGE_CONFIG}, format='json').status_code == 201

    # Granting the NLP tab allows text configs as well.
    grant_create(w, [CV, NLP])
    assert client.post('/api/projects/', {'title': 'txt', 'label_config': TEXT_CONFIG}, format='json').status_code == 201

    # The student uses every project feature on their own project but gains no teacher authority.
    body = client.get(f'/api/projects/{project.pk}/').json()
    assert set(body['school_features']) == {'annotate', 'import_tasks', 'export_data', 'manage_tasks', 'configure_project', 'delete_project'}
    assert body['school_can_manage'] is False and body['school_label_config'] is None
    assert client.post(f'/api/projects/{project.pk}/import', [{'image': '/x.png'}], format='json').status_code == 201
    assert client.get(f'/api/projects/{project.pk}/export?exportType=JSON').status_code == 200
    assert client.post(f'/school/projects/{project.pk}/students/', {'emails': w.u['cs_s2'].email}).status_code == 403
    other = member_of(w, w.u['cs_s2'])
    assert client.post(f'/school/members/{other.pk}/create-templates/', {'groups': [CV]}).status_code == 403
    assert '我创建的' in client.get('/school/').content.decode()


def test_custom_template_grant_allows_any_config(w):
    grant_create(w, [CUSTOM_TEMPLATE])
    client = client_for(w.u['cs_s1'])
    templates = client.get('/api/templates/').json()
    assert templates['groups'] == [] and templates['school_custom_template'] is True
    for config in (TEXT_CONFIG, IMAGE_CONFIG):
        assert client.post('/api/projects/', {'title': config[:20], 'label_config': config}, format='json').status_code == 201


def test_vision_grant_cannot_be_turned_into_text_tasks(w):
    # The VQA template of "Computer Vision" labels text next to a display-only image; plain text tasks stay denied.
    grant_create(w, [CV])
    client = client_for(w.u['cs_s1'])
    ner = '<View><Labels name="l" toName="t"><Label value="PER"/></Labels><Text name="t" value="$text"/></View>'
    edited_vision = (
        '<View><Image name="img" value="$image"/>'
        '<RectangleLabels name="b" toName="img"><Label value="Cat"/></RectangleLabels>'
        '<Choices name="c" toName="img"><Choice value="ok"/></Choices></View>'
    )
    assert client.post('/api/projects/', {'title': 'ner', 'label_config': ner}, format='json').status_code == 403
    assert client.post('/api/projects/', {'title': 'vision', 'label_config': edited_vision}, format='json').status_code == 201


def test_student_projects_are_visible_to_owner_granting_teacher_and_admin_only(w):
    grant_create(w, [CV, NLP])
    project_id = client_for(w.u['cs_s1']).post('/api/projects/', {'title': 'mine'}, format='json').json()['id']
    assert ProjectTeacher.objects.filter(project_id=project_id, user=w.u['cs_teacher']).exists()
    other_teacher = make_user('t2@t.edu.cn')
    services.add_member(w.cs, other_teacher, Role.TEACHER)
    expectations = {
        'cs_s1': 200, 'cs_teacher': 200, 'cs_admin': 200, 'cs_s2': 403, 'med_teacher': 403, 'med_s1': 403,
    }
    for key, status in expectations.items():
        assert client_for(w.u[key]).get(f'/api/projects/{project_id}/').status_code == status, key
    assert client_for(other_teacher).get(f'/api/projects/{project_id}/').status_code == 403
    teacher_page = client_for(w.u['cs_teacher']).get('/school/').content.decode()
    assert 'mine' in teacher_page and '学生新建项目权限' in teacher_page and 'value="Computer Vision"' in teacher_page


def test_revoke_and_membership_changes_remove_the_create_grant(w):
    student = w.u['cs_s1']
    grant_create(w, [CV])
    client = client_for(student)
    project_id = client.post('/api/projects/', {'title': 'mine'}, format='json').json()['id']
    grant_create(w, [])
    assert not StudentCreatePermission.objects.exists()
    assert client.post('/api/projects/', {'title': 'more'}, format='json').status_code == 403
    assert client.get(f'/api/projects/{project_id}/').status_code == 200  # existing own projects stay

    grant_create(w, [CV])
    member = member_of(w, student)
    services.set_role(member, Role.TEACHER)
    assert not StudentCreatePermission.objects.exists()
    services.set_role(member, Role.STUDENT)
    grant_create(w, [CV])
    services.remove_member(member_of(w, student))
    assert not StudentCreatePermission.objects.exists()
    access.invalidate(student)
    assert not access.can_create_projects(student)


def test_only_college_managers_grant_creation_and_groups_are_validated(w):
    member = member_of(w, w.u['cs_s1'])
    assert client_for(w.u['med_teacher']).post(f'/school/members/{member.pk}/create-templates/', {'groups': [CV]}).status_code == 404
    with pytest.raises(Exception):
        services.set_student_create_groups(w.u['cs_teacher'], member, ['Made Up Group'])
    teacher_member = member_of(w, w.u['cs_teacher'])
    with pytest.raises(Exception):
        services.set_student_create_groups(w.u['cs_admin'], teacher_member, [CV])
    assert grant_create(w, ['Audio/Speech Processing'], teacher=w.u['cs_admin']).status_code == 302
    project_id = client_for(w.u['cs_s1']).post('/api/projects/', {'title': 'by admin grant'}, format='json').json()['id']
    # A college admin sees every project already, so no responsible teacher is added.
    assert not ProjectTeacher.objects.filter(project_id=project_id).exists()
