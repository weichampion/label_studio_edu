"""Tenant isolation and role tests for the school edition (run with core.settings.school_local)."""

import time
from types import SimpleNamespace

import pytest
from organizations.models import OrganizationMember
from projects.models import Project, ProjectMember
from rest_framework.test import APIClient
from school_tenancy import access, services
from school_tenancy.models import College, MemberRole, Role
from tasks.models import Annotation, Prediction, Task
from users.models import User

pytestmark = pytest.mark.django_db

CONFIG = (
    '<View><Text name="text" value="$text"/>'
    '<Choices name="label" toName="text"><Choice value="A"/><Choice value="B"/></Choices></View>'
)
PASSWORD = 'Str0ng!Passw0rd'
DENIED = (403, 404)


def make_user(email):
    return User.objects.create_user(email=email, password=PASSWORD, username=email.split('@')[0])


@pytest.fixture
def w(settings):
    settings.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
    cs = services.create_college('cs', '计算机学院')
    med = services.create_college('med', '医学院')
    users = {}
    for name, college, role in [
        ('cs_admin', cs, Role.COLLEGE_ADMIN),
        ('cs_teacher', cs, Role.TEACHER),
        ('cs_s1', cs, Role.STUDENT),
        ('cs_s2', cs, Role.STUDENT),
        ('med_admin', med, Role.COLLEGE_ADMIN),
        ('med_teacher', med, Role.TEACHER),
        ('med_s1', med, Role.STUDENT),
    ]:
        users[name] = make_user(f'{name}@t.edu.cn')
        services.add_member(college, users[name], role)
    users['shared'] = make_user('shared@t.edu.cn')
    services.add_member(cs, users['shared'], Role.TEACHER)
    services.add_member(med, users['shared'], Role.STUDENT)

    def project(title, college, creator):
        p = Project.objects.create(title=title, label_config=CONFIG, organization=college.organization, created_by=creator)
        return p, Task.objects.create(project=p, data={'text': title})

    cs1, t_cs1 = project('cs-1', cs, users['cs_teacher'])
    cs2, t_cs2 = project('cs-2', cs, users['cs_teacher'])
    med1, t_med1 = project('med-1', med, users['med_teacher'])
    services.assign_students(cs1, [users['cs_s1']])
    services.assign_students(med1, [users['med_s1']])
    services.set_project_teacher(users['cs_admin'], cs1, users['shared'])
    services.set_project_teacher(users['cs_admin'], cs2, users['shared'])
    return SimpleNamespace(cs=cs, med=med, u=users, cs1=cs1, cs2=cs2, med1=med1, t_cs1=t_cs1, t_cs2=t_cs2, t_med1=t_med1)


def client_for(user):
    client = APIClient()
    client.force_login(user)
    # users.functions.login records this; the inactivity middleware logs out sessions without it
    session = client.session
    session['last_login'] = time.time()
    session.save()
    return client


def project_titles(client):
    response = client.get('/api/projects/')
    assert response.status_code == 200, response.content
    data = response.json()
    return {p['title'] for p in (data['results'] if isinstance(data, dict) else data)}


def annotation_payload():
    return {'result': [{'from_name': 'label', 'to_name': 'text', 'type': 'choices', 'value': {'choices': ['A']}}]}


# --- Tenant isolation -----------------------------------------------------------------------------


def test_project_list_is_scoped_by_college_and_role(w):
    assert project_titles(client_for(w.u['cs_admin'])) == {'cs-1', 'cs-2'}
    assert project_titles(client_for(w.u['cs_teacher'])) == {'cs-1', 'cs-2'}
    assert project_titles(client_for(w.u['cs_s1'])) == {'cs-1'}
    assert project_titles(client_for(w.u['cs_s2'])) == set()
    assert project_titles(client_for(w.u['med_teacher'])) == {'med-1'}
    assert project_titles(client_for(w.u['med_s1'])) == {'med-1'}


def test_other_college_resources_are_not_reachable(w):
    client = client_for(w.u['cs_admin'])
    assert client.get(f'/api/projects/{w.med1.id}/').status_code in DENIED
    assert client.get(f'/api/tasks/{w.t_med1.id}/').status_code in DENIED
    assert client.get(f'/api/tasks/?project={w.med1.id}').status_code in DENIED
    assert client.get(f'/api/tasks/{w.t_med1.id}/annotations/').status_code in DENIED
    assert client.get(f'/api/projects/{w.med1.id}/export?exportType=JSON').status_code in DENIED
    assert client.patch(f'/api/projects/{w.med1.id}/', {'title': 'hacked'}, format='json').status_code in DENIED
    assert client.delete(f'/api/projects/{w.med1.id}/').status_code in DENIED
    w.med1.refresh_from_db()
    assert w.med1.title == 'med-1'

    own = client_for(w.u['med_teacher'])
    assert own.get(f'/api/projects/{w.med1.id}/').status_code == 200
    assert own.get(f'/api/tasks/{w.t_med1.id}/').status_code == 200
    assert own.get(f'/api/projects/{w.med1.id}/export?exportType=JSON').status_code == 200


def test_cannot_write_into_another_college_by_id(w):
    client = client_for(w.u['cs_teacher'])
    response = client.post('/api/tasks/', {'project': w.med1.id, 'data': {'text': 'x'}}, format='json')
    assert response.status_code in DENIED + (400,)
    response = client.post(f'/api/tasks/{w.t_med1.id}/annotations/', annotation_payload(), format='json')
    assert response.status_code in DENIED
    response = client.post('/api/dm/views/', {'project': w.med1.id, 'data': {}}, format='json')
    assert response.status_code in DENIED + (400,)
    response = client.post(
        '/api/predictions/', {'task': w.t_med1.id, 'result': [], 'model_version': 'x'}, format='json'
    )
    assert response.status_code in DENIED + (400,)
    assert Task.objects.filter(project=w.med1).count() == 1
    assert not Annotation.objects.filter(task=w.t_med1).exists()
    assert not Prediction.objects.filter(task=w.t_med1).exists()
    assert not w.med1.views.exists()

    # the same calls work inside the teacher's own college
    assert client.post('/api/tasks/', {'project': w.cs1.id, 'data': {'text': 'y'}}, format='json').status_code == 201
    assert client.post('/api/dm/views/', {'project': w.cs1.id, 'data': {}}, format='json').status_code == 201


# --- Roles ----------------------------------------------------------------------------------------


def test_student_only_reaches_assigned_projects(w):
    client = client_for(w.u['cs_s1'])
    assert client.get(f'/api/projects/{w.cs1.id}/').status_code == 200
    assert client.get(f'/api/tasks/{w.t_cs1.id}/').status_code == 200
    assert client.get(f'/api/projects/{w.cs2.id}/').status_code in DENIED
    assert client.get(f'/api/tasks/{w.t_cs2.id}/').status_code in DENIED
    assert client.get(f'/api/tasks/?project={w.cs2.id}').status_code in DENIED


def test_student_can_annotate_but_not_manage(w):
    client = client_for(w.u['cs_s1'])
    response = client.post(f'/api/tasks/{w.t_cs1.id}/annotations/', annotation_payload(), format='json')
    assert response.status_code == 201, response.content
    assert Annotation.objects.get(task=w.t_cs1).completed_by == w.u['cs_s1']
    own_id = response.json()['id']
    assert client.patch(f'/api/annotations/{own_id}/', annotation_payload(), format='json').status_code == 200

    # a classmate's annotation in the same project can be read but not changed or deleted
    services.assign_students(w.cs1, [w.u['cs_s2']])
    classmate = client_for(w.u['cs_s2'])
    assert classmate.patch(f'/api/annotations/{own_id}/', {'result': []}, format='json').status_code == 403
    assert classmate.delete(f'/api/annotations/{own_id}/').status_code == 403
    assert Annotation.objects.filter(pk=own_id).exists()
    # teachers may correct student annotations
    teacher = client_for(w.u['cs_teacher'])
    assert teacher.patch(f'/api/annotations/{own_id}/', annotation_payload(), format='json').status_code == 200

    assert client.post('/api/projects/', {'title': 'mine', 'label_config': CONFIG}, format='json').status_code == 403
    assert client.patch(f'/api/projects/{w.cs1.id}/', {'title': 'x'}, format='json').status_code == 403
    assert client.delete(f'/api/projects/{w.cs1.id}/').status_code == 403
    assert client.post(f'/api/projects/{w.cs1.id}/import', [{'text': 'x'}], format='json').status_code == 403
    assert client.get(f'/api/projects/{w.cs1.id}/export?exportType=JSON').status_code == 403
    assert Project.objects.get(pk=w.cs1.pk).title == 'cs-1'


def test_teacher_creates_projects_in_active_college_only(w):
    client = client_for(w.u['cs_teacher'])
    response = client.post('/api/projects/', {'title': 'new', 'label_config': CONFIG}, format='json')
    assert response.status_code == 201, response.content
    assert Project.objects.get(pk=response.json()['id']).organization_id == w.cs.organization_id
    assert client.post(f'/api/projects/{w.cs1.id}/import', [{'text': 'z'}], format='json').status_code == 201


def test_user_list_hides_classmates_from_students(w):
    student = client_for(w.u['cs_s1'])
    response = student.get(f'/api/users?project={w.cs1.id}')
    assert response.status_code == 200
    assert {u['email'] for u in response.json()} == {'cs_s1@t.edu.cn'}
    other = w.u['cs_s2']
    assert student.patch(f'/api/users/{other.id}/', {'first_name': 'x'}, format='json').status_code in DENIED

    teacher_emails = {u['email'] for u in client_for(w.u['cs_teacher']).get('/api/users').json()}
    assert {'cs_s1@t.edu.cn', 'cs_s2@t.edu.cn', 'cs_admin@t.edu.cn'} <= teacher_emails
    assert not any(e.startswith('med_') for e in teacher_emails)
    assert client_for(w.u['cs_teacher']).patch(f'/api/users/{other.id}/', {'first_name': 'x'}, format='json').status_code in DENIED


# --- Membership lifecycle -------------------------------------------------------------------------


def test_user_in_two_colleges_switches_context(w):
    client = client_for(w.u['shared'])
    assert project_titles(client) == {'cs-1', 'cs-2'}  # teacher in cs (joined first)

    response = client.post('/api/school/switch-college/', {'organization_id': w.med.organization_id}, format='json')
    assert response.status_code == 200
    assert response.json()['active_college']['role'] == Role.STUDENT
    assert project_titles(client) == set()  # student in med, not assigned yet

    services.assign_students(w.med1, [w.u['shared']])
    assert project_titles(client) == {'med-1'}
    assert client.get(f'/api/projects/{w.cs1.id}/').status_code in DENIED

    bad = client.post('/api/school/switch-college/', {'organization_id': 999999}, format='json')
    assert bad.status_code == 400


def test_removed_member_loses_access(w):
    member = OrganizationMember.objects.get(user=w.u['cs_s1'], organization=w.cs.organization)
    services.remove_member(member)
    assert not ProjectMember.objects.filter(user=w.u['cs_s1']).exists()

    client = client_for(w.u['cs_s1'])
    assert client.get('/api/projects/').status_code == 403
    page = client.get('/projects/')
    assert page.status_code == 302 and page['Location'].endswith('/school/no-college/')
    assert client.get('/school/no-college/').status_code == 200


def test_disabled_college_blocks_its_members(w):
    College.objects.filter(pk=w.med.pk).update(is_active=False)
    assert client_for(w.u['med_teacher']).get('/api/projects/').status_code == 403
    # a user who also belongs to an active college falls back to it
    assert project_titles(client_for(w.u['shared'])) == {'cs-1', 'cs-2'}


def test_cannot_remove_or_demote_last_college_admin(w):
    member = OrganizationMember.objects.get(user=w.u['cs_admin'], organization=w.cs.organization)
    with pytest.raises(Exception):
        services.set_role(member, Role.TEACHER)
    with pytest.raises(Exception):
        services.remove_member(member)
    assert MemberRole.objects.get(member=member).role == Role.COLLEGE_ADMIN


def test_invite_link_registers_a_student_of_that_college_only(w, settings):
    client = APIClient()
    response = client.post(
        f'/user/signup/?token={w.med.organization.token}', {'email': 'new@t.edu.cn', 'password': PASSWORD}
    )
    assert response.status_code == 302, response.content
    user = User.objects.get(email='new@t.edu.cn')
    assert access.college_ids_for(user) == [w.med.organization_id]
    assert access.role_in(user, w.med.organization_id) == Role.STUDENT

    for open_signup in (False, True):
        settings.DISABLE_SIGNUP_WITHOUT_LINK = not open_signup
        rejected = APIClient().post('/user/signup/?token=wrong', {'email': 'x@t.edu.cn', 'password': PASSWORD})
        assert rejected.status_code == 403
        assert not User.objects.filter(email='x@t.edu.cn').exists()


def test_platform_admin_without_college_sees_no_tenant_data(w):
    admin = User.objects.create_superuser(email='root@t.edu.cn', password=PASSWORD)
    client = client_for(admin)
    assert client.get('/api/projects/').status_code == 403
    assert client.get(f'/api/projects/{w.cs1.id}/').status_code == 403
    assert client.get('/admin/').status_code == 200


# --- Workbench pages ------------------------------------------------------------------------------


def test_workbench_actions_respect_roles(w):
    teacher = client_for(w.u['cs_teacher'])
    assert teacher.post('/school/members/', {'email': 'a@t.edu.cn', 'role': Role.TEACHER}).status_code == 403
    teacher.post(f'/school/projects/{w.cs2.id}/students/', {'emails': 'cs_s2@t.edu.cn, med_s1@t.edu.cn'})
    assert not ProjectMember.objects.filter(project=w.cs2).exists()  # rejected as a whole: med_s1 is not in cs
    teacher.post(f'/school/projects/{w.cs2.id}/students/', {'emails': 'cs_s2@t.edu.cn'})
    assert ProjectMember.objects.filter(project=w.cs2, user=w.u['cs_s2']).exists()
    assert teacher.post(f'/school/projects/{w.med1.id}/students/', {'emails': 'cs_s2@t.edu.cn'}).status_code == 404

    student = client_for(w.u['cs_s1'])
    assert student.post(f'/school/projects/{w.cs1.id}/students/', {'emails': 'cs_s2@t.edu.cn'}).status_code == 403
    page = student.get('/school/')
    assert page.status_code == 200 and '成员管理' not in page.content.decode()

    admin = client_for(w.u['cs_admin'])
    response = admin.post('/school/members/', {'email': 'newt@t.edu.cn', 'role': Role.TEACHER, 'password': PASSWORD})
    assert response.status_code == 302
    assert access.role_in(User.objects.get(email='newt@t.edu.cn'), w.cs.organization_id) == Role.TEACHER
    # an existing teacher of another college joins without losing the first membership
    admin.post('/school/members/', {'email': 'med_teacher@t.edu.cn', 'role': Role.STUDENT})
    assert set(access.college_ids_for(w.u['med_teacher'])) == {w.cs.organization_id, w.med.organization_id}
    page = admin.get('/school/')
    assert page.status_code == 200 and '成员管理' in page.content.decode()
