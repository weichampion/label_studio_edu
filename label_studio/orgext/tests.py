"""orgext 单元测试：角色矩阵 / 双层角色 / 部门可见性 / 任务分配队列。"""
import pytest
from organizations.models import Organization, OrganizationMember
from orgext.models import Assignment, Workspace, WorkspaceMember
from orgext.permissions import effective_role, get_org_role
from orgext.queue import auto_distribute, bulk_assign, filter_assigned
from orgext.roles import ROLE_PERMISSIONS, Role, max_role
from projects.models import Project
from rest_framework.test import APIClient
from tasks.models import Annotation, Task
from users.models import User


@pytest.fixture
def org_setup(db):
    teacher = User.objects.create(email='teacher@edu.test')
    student = User.objects.create(email='student@edu.test')
    org = Organization.create_organization(created_by=teacher, title='智能学院')
    OrganizationMember.objects.create(user=student, organization=org, role=Role.ANNOTATOR)
    for u in (teacher, student):
        u.active_organization = org
        u.save(update_fields=['active_organization'])
    return org, teacher, student


def test_max_role():
    assert max_role(Role.ANNOTATOR, Role.MANAGER) == Role.MANAGER
    assert max_role(None, Role.REVIEWER) == Role.REVIEWER
    assert max_role() is None


def test_org_creator_is_owner(org_setup):
    org, teacher, student = org_setup
    assert get_org_role(teacher) == Role.OWNER
    assert get_org_role(student) == Role.ANNOTATOR


def test_role_matrix_view_level(org_setup):
    org, teacher, student = org_setup
    assert teacher.has_perm('projects.create')
    assert not student.has_perm('projects.create')
    assert student.has_perm('annotations.create')
    assert not student.has_perm('actions.perform')
    assert not student.has_perm('assignments.create')


def test_matrix_covers_all_core_permissions():
    from core.permissions import all_permissions

    core_perms = {perm for _, perm in all_permissions}
    missing = core_perms - set(ROLE_PERMISSIONS)
    assert not missing, f'ROLE_PERMISSIONS missing core permissions: {missing}'


def test_workspace_role_elevation(org_setup):
    org, teacher, student = org_setup
    ws = Workspace.objects.create(organization=org, title='2024级3班', created_by=teacher)
    WorkspaceMember.objects.create(workspace=ws, user=student, role=Role.MANAGER)
    project = Project.objects.create(title='p', organization=org, workspace=ws)

    assert effective_role(student) == Role.ANNOTATOR
    assert effective_role(student, project) == Role.MANAGER
    assert student.has_perm('projects.change', project)
    assert not student.has_perm('projects.change')


def test_workspace_manager_can_patch_project_via_api_but_not_create(org_setup):
    """回归用例：RolePermission.has_permission() 曾经在「组织角色低但部门角色被
    提升」时提前 403，has_object_permission() 根本没机会用 effective_role(user, obj)
    做精确判断。这里必须真正走 DRF 的 dispatch（force_authenticate + client.patch），
    不能像 test_workspace_role_elevation 那样直接调用 has_perm(perm, obj) ——
    那样会绕过 has_permission() 这一步，测不出这个 bug。
    """
    org, teacher, student = org_setup
    ws = Workspace.objects.create(organization=org, title='ws', created_by=teacher)
    WorkspaceMember.objects.create(workspace=ws, user=student, role=Role.MANAGER)
    project = Project.objects.create(title='p', organization=org, workspace=ws)
    other_project = Project.objects.create(title='root', organization=org)  # 不在任何部门

    client = APIClient()
    client.force_authenticate(user=student)

    resp = client.patch(f'/api/projects/{project.id}/', {'description': 'updated'}, format='json')
    assert resp.status_code == 200, resp.content

    # 部门级提升不能跨部门生效
    resp = client.patch(f'/api/projects/{other_project.id}/', {'description': 'nope'}, format='json')
    assert resp.status_code == 403

    # 部门级提升也不能顶替组织级「建项目」权限
    resp = client.post('/api/projects/', {'title': 'brand new'}, format='json')
    assert resp.status_code == 403


def test_workspace_project_visibility(org_setup):
    org, teacher, student = org_setup
    outsider = User.objects.create(email='outsider@edu.test', active_organization=org)
    OrganizationMember.objects.create(user=outsider, organization=org, role=Role.ANNOTATOR)
    ws = Workspace.objects.create(organization=org, title='ws', created_by=teacher)
    WorkspaceMember.objects.create(workspace=ws, user=student, role=Role.ANNOTATOR)
    project = Project.objects.create(title='p', organization=org, workspace=ws)

    assert project.has_permission(teacher)
    assert project.has_permission(student)
    assert not project.has_permission(outsider)

    # 无部门项目：保持 OSS 行为，组织内可见
    root_project = Project.objects.create(title='root', organization=org)
    assert root_project.has_permission(outsider)


def test_assignment_queue_filter(org_setup):
    org, teacher, student = org_setup
    project = Project.objects.create(title='p', organization=org, assignment_mode='manual')
    tasks = [Task.objects.create(project=project, data={'i': i}) for i in range(4)]

    bulk_assign([tasks[0].id, tasks[1].id], [student.id], teacher)
    qs, flag = filter_assigned(Task.objects.filter(project=project), student, project)
    assert flag is True
    assert set(qs.values_list('id', flat=True)) == {tasks[0].id, tasks[1].id}

    # 幂等：重复分配不新增
    created = bulk_assign([tasks[0].id], [student.id], teacher)
    assert created == []

    # 抢单模式：保持 OSS 行为
    project.assignment_mode = 'auto'
    qs2, flag2 = filter_assigned(Task.objects.filter(project=project), student, project)
    assert flag2 is None
    assert qs2.count() == 4


def test_auto_distribute_round_robin(org_setup):
    org, teacher, student = org_setup
    other = User.objects.create(email='other@edu.test', active_organization=org)
    OrganizationMember.objects.create(user=other, organization=org)
    project = Project.objects.create(title='p', organization=org, assignment_mode='manual')
    tasks = [Task.objects.create(project=project, data={'i': i}) for i in range(4)]

    created = auto_distribute([t.id for t in tasks], [student.id, other.id], teacher)
    assert len(created) == 4
    assert Assignment.objects.filter(user=student).count() == 2
    assert Assignment.objects.filter(user=other).count() == 2


def _task(project, i=0):
    return Task.objects.create(project=project, data={'i': i})


def _annotate(task, project, user, **extra):
    return Annotation.objects.create(task=task, project=project, result=[], completed_by=user, **extra)


def test_annotation_save_marks_assignment_completed(org_setup):
    org, teacher, student = org_setup
    project = Project.objects.create(title='p', organization=org, assignment_mode='manual')
    task = _task(project)
    assignment = Assignment.objects.create(
        task=task, user=student, type=Assignment.Type.ANNOTATOR, assigned_by=teacher
    )
    _annotate(task, project, student)
    assignment.refresh_from_db()
    assert assignment.status == Assignment.Status.COMPLETED


def test_annotation_without_completed_by_sets_in_progress(org_setup):
    org, teacher, student = org_setup
    project = Project.objects.create(title='p', organization=org, assignment_mode='manual')
    task = _task(project)
    assignment = Assignment.objects.create(
        task=task, user=student, type=Assignment.Type.ANNOTATOR, assigned_by=teacher
    )
    Annotation.objects.create(task=task, project=project, result=[], updated_by=student)
    assignment.refresh_from_db()
    assert assignment.status == Assignment.Status.IN_PROGRESS


def test_annotation_delete_reverts_to_assigned(org_setup):
    org, teacher, student = org_setup
    project = Project.objects.create(title='p', organization=org, assignment_mode='manual')
    task = _task(project)
    assignment = Assignment.objects.create(
        task=task, user=student, type=Assignment.Type.ANNOTATOR, assigned_by=teacher
    )
    annotation = _annotate(task, project, student)
    assert Assignment.objects.get(pk=assignment.pk).status == Assignment.Status.COMPLETED
    annotation.delete()
    assignment.refresh_from_db()
    assert assignment.status == Assignment.Status.ASSIGNED


def test_reviewer_assignment_completed_by_review_annotation(org_setup):
    org, teacher, student = org_setup
    reviewer = User.objects.create(email='rev@edu.test', active_organization=org)
    OrganizationMember.objects.create(user=reviewer, organization=org, role=Role.REVIEWER)
    project = Project.objects.create(title='p', organization=org, assignment_mode='manual')
    task = _task(project)
    assignment = Assignment.objects.create(
        task=task, user=reviewer, type=Assignment.Type.REVIEWER, assigned_by=teacher
    )
    _annotate(task, project, reviewer)
    assignment.refresh_from_db()
    assert assignment.status == Assignment.Status.COMPLETED


def test_reviewer_queue_only_after_annotations_done(org_setup):
    org, teacher, student = org_setup
    reviewer = User.objects.create(email='rev@edu.test', active_organization=org)
    OrganizationMember.objects.create(user=reviewer, organization=org, role=Role.REVIEWER)
    project = Project.objects.create(title='p', organization=org, assignment_mode='manual')
    t1, t2 = _task(project, 1), _task(project, 2)
    Assignment.objects.create(task=t1, user=student, type=Assignment.Type.ANNOTATOR, assigned_by=teacher)
    Assignment.objects.create(task=t1, user=reviewer, type=Assignment.Type.REVIEWER, assigned_by=teacher)
    Assignment.objects.create(task=t2, user=reviewer, type=Assignment.Type.REVIEWER, assigned_by=teacher)
    t1.is_labeled = True
    t1.save(update_fields=['is_labeled'])

    qs, flag = filter_assigned(Task.objects.filter(project=project), reviewer, project)
    assert flag is True
    assert set(qs.values_list('id', flat=True)) == {t1.id}

    # 未标注的 RE 任务不出队
    Assignment.objects.filter(task=t1, user=reviewer).update(status=Assignment.Status.COMPLETED)
    qs2, _ = filter_assigned(Task.objects.filter(project=project), reviewer, project)
    assert set(qs2.values_list('id', flat=True)) == set()

    # 学生同时持有 AN 与 RE 行：AN 阶段优先
    Assignment.objects.create(task=t1, user=student, type=Assignment.Type.REVIEWER, assigned_by=teacher)
    qs3, _ = filter_assigned(Task.objects.filter(project=project), student, project)
    assert set(qs3.values_list('id', flat=True)) == {t1.id}


def _fresh(user):
    """force_authenticate 复用同一对象会命中 effective_role 的对象级缓存，重新取对象模拟新请求。"""
    return User.objects.get(pk=user.pk)


def test_admin_creates_user_account_with_role_and_workspace(org_setup):
    org, teacher, student = org_setup
    ws = Workspace.objects.create(organization=org, title='财经学院', created_by=teacher)
    client = APIClient()
    client.force_authenticate(user=teacher)

    resp = client.post(
        '/api/user-accounts/',
        {
            'email': 'wang@edu.test',
            'first_name': '王',
            'last_name': '老师',
            'password': 'Passw0rd123',
            'role': 'manager',
            'workspace': ws.id,
        },
        format='json',
    )
    assert resp.status_code == 201, resp.content
    user = User.objects.get(email='wang@edu.test')
    assert user.check_password('Passw0rd123')
    assert user.active_organization_id == org.id
    assert OrganizationMember.objects.get(user=user, organization=org, deleted_at__isnull=True).role == Role.MANAGER
    assert WorkspaceMember.objects.filter(workspace=ws, user=user, role=Role.MANAGER).exists()


def test_admin_create_user_generates_initial_password(org_setup):
    org, teacher, student = org_setup
    client = APIClient()
    client.force_authenticate(user=teacher)

    resp = client.post('/api/user-accounts/', {'email': 'liu@edu.test', 'role': 'manager'}, format='json')
    assert resp.status_code == 201, resp.content
    password = resp.json()['initial_password']
    assert len(password) >= 8
    assert User.objects.get(email='liu@edu.test').check_password(password)


def test_manager_cannot_create_user_account(org_setup):
    org, teacher, student = org_setup
    OrganizationMember.objects.filter(user=student, organization=org).update(role=Role.MANAGER)
    client = APIClient()
    client.force_authenticate(user=_fresh(student))

    resp = client.post('/api/user-accounts/', {'email': 'x@edu.test'}, format='json')
    assert resp.status_code == 403


def test_teacher_adds_student_by_email_creates_account(org_setup):
    org, teacher, student = org_setup
    ws = Workspace.objects.create(organization=org, title='ws', created_by=teacher)
    client = APIClient()
    client.force_authenticate(user=teacher)

    resp = client.post(
        f'/api/workspaces/{ws.id}/members/', {'email': 'newstudent@edu.test', 'role': 'annotator'}, format='json'
    )
    assert resp.status_code == 201, resp.content
    data = resp.json()
    assert data['account_created'] is True
    assert data['user_name'] == 'newstudent'  # username fallback（无姓名时）

    user = User.objects.get(email='newstudent@edu.test')
    assert user.check_password(data['created_password'])
    assert user.active_organization_id == org.id
    assert OrganizationMember.objects.filter(
        user=user, organization=org, role=Role.ANNOTATOR, deleted_at__isnull=True
    ).exists()
    assert WorkspaceMember.objects.filter(workspace=ws, user=user, role=Role.ANNOTATOR).exists()


def test_workspace_add_existing_org_member_by_email(org_setup):
    org, teacher, student = org_setup
    ws = Workspace.objects.create(organization=org, title='ws', created_by=teacher)
    client = APIClient()
    client.force_authenticate(user=teacher)

    resp = client.post(f'/api/workspaces/{ws.id}/members/', {'email': student.email}, format='json')
    assert resp.status_code == 201, resp.content
    assert 'created_password' not in resp.json()
    assert resp.json()['user'] == student.id
    assert WorkspaceMember.objects.filter(workspace=ws, user=student).exists()

    # 重复加入 → 400
    resp2 = client.post(f'/api/workspaces/{ws.id}/members/', {'email': student.email}, format='json')
    assert resp2.status_code == 400


def test_workspace_member_role_patch_and_permissions(org_setup):
    org, teacher, student = org_setup
    ws = Workspace.objects.create(organization=org, title='ws', created_by=teacher)
    WorkspaceMember.objects.create(workspace=ws, user=student, role=Role.ANNOTATOR)
    client = APIClient()

    client.force_authenticate(user=teacher)
    resp = client.patch(f'/api/workspaces/{ws.id}/members/{student.id}/', {'role': 'reviewer'}, format='json')
    assert resp.status_code == 200, resp.content
    assert resp.json()['role'] == 'reviewer'
    assert WorkspaceMember.objects.get(workspace=ws, user=student).role == Role.REVIEWER

    # annotator 无 workspaces.invite 权限
    client.force_authenticate(user=_fresh(student))
    resp2 = client.patch(f'/api/workspaces/{ws.id}/members/{student.id}/', {'role': 'manager'}, format='json')
    assert resp2.status_code == 403


def test_annotator_cannot_add_workspace_member(org_setup):
    org, teacher, student = org_setup
    ws = Workspace.objects.create(organization=org, title='ws', created_by=teacher)
    client = APIClient()
    client.force_authenticate(user=_fresh(student))

    resp = client.post(f'/api/workspaces/{ws.id}/members/', {'email': 'nobody@edu.test'}, format='json')
    assert resp.status_code == 403


def _make_manager(org, ws, email):
    user = User.objects.create(email=email, active_organization=org)
    OrganizationMember.objects.create(user=user, organization=org, role=Role.MANAGER)
    WorkspaceMember.objects.create(workspace=ws, user=user, role=Role.MANAGER)
    return user


def test_manager_sees_only_own_workspace_members_in_memberships(org_setup):
    org, teacher, student = org_setup
    ws_a = Workspace.objects.create(organization=org, title='A', created_by=teacher)
    ws_b = Workspace.objects.create(organization=org, title='B', created_by=teacher)
    mgr_a = _make_manager(org, ws_a, 'mgra@edu.test')
    mgr_b = _make_manager(org, ws_b, 'mgrb@edu.test')
    stu_a = User.objects.create(email='stua@edu.test', active_organization=org)
    OrganizationMember.objects.create(user=stu_a, organization=org, role=Role.ANNOTATOR)
    WorkspaceMember.objects.create(workspace=ws_a, user=stu_a, role=Role.ANNOTATOR)

    client = APIClient()
    client.force_authenticate(user=mgr_a)
    resp = client.get(f'/api/organizations/{org.id}/memberships', {'page_size': 100})
    assert resp.status_code == 200, resp.content
    emails = {m['user']['email'] for m in resp.json()['results']}
    assert 'mgra@edu.test' in emails  # 自己
    assert 'stua@edu.test' in emails  # 本学院学员
    assert 'mgrb@edu.test' not in emails  # 其他学院老师
    assert 'teacher@edu.test' not in emails  # owner 非本学院成员
    assert 'student@edu.test' not in emails  # 其他学院学员


def test_annotator_sees_only_self_in_memberships(org_setup):
    org, teacher, student = org_setup
    client = APIClient()
    client.force_authenticate(user=_fresh(student))
    resp = client.get(f'/api/organizations/{org.id}/memberships', {'page_size': 100})
    assert resp.status_code == 200
    emails = {m['user']['email'] for m in resp.json()['results']}
    assert emails == {'student@edu.test'}


def test_manager_cannot_access_other_workspace(org_setup):
    org, teacher, student = org_setup
    ws_a = Workspace.objects.create(organization=org, title='A', created_by=teacher)
    ws_b = Workspace.objects.create(organization=org, title='B', created_by=teacher)
    mgr_a = _make_manager(org, ws_a, 'mgra@edu.test')
    WorkspaceMember.objects.create(workspace=ws_b, user=student, role=Role.ANNOTATOR)

    client = APIClient()
    client.force_authenticate(user=mgr_a)
    assert client.get(f'/api/workspaces/{ws_b.id}/').status_code == 404
    assert client.get(f'/api/workspaces/{ws_b.id}/members/').status_code == 404
    resp = client.post(f'/api/workspaces/{ws_b.id}/members/', {'email': 'x@edu.test'}, format='json')
    assert resp.status_code == 404
    # 自己学院正常
    assert client.get(f'/api/workspaces/{ws_a.id}/members/').status_code == 200


def test_assign_restricted_to_workspace_members(org_setup):
    org, teacher, student = org_setup
    ws = Workspace.objects.create(organization=org, title='A', created_by=teacher)
    outsider = User.objects.create(email='outsider2@edu.test', active_organization=org)
    OrganizationMember.objects.create(user=outsider, organization=org, role=Role.ANNOTATOR)
    project = Project.objects.create(title='p', organization=org, workspace=ws, assignment_mode='manual')
    task = Task.objects.create(project=project, data={'i': 0})

    client = APIClient()
    client.force_authenticate(user=teacher)
    # 组织成员但不在项目学院 → 400
    resp = client.post(
        f'/api/projects/{project.id}/assignments/',
        {'task_ids': [task.id], 'user_ids': [outsider.id]},
        format='json',
    )
    assert resp.status_code == 400, resp.content
    # 学院成员 → 201
    WorkspaceMember.objects.create(workspace=ws, user=student, role=Role.ANNOTATOR)
    resp2 = client.post(
        f'/api/projects/{project.id}/assignments/',
        {'task_ids': [task.id], 'user_ids': [student.id]},
        format='json',
    )
    assert resp2.status_code == 201, resp2.content


def test_users_list_scoped_by_role(org_setup):
    org, teacher, student = org_setup
    ws_a = Workspace.objects.create(organization=org, title='A', created_by=teacher)
    ws_b = Workspace.objects.create(organization=org, title='B', created_by=teacher)
    mgr_a = _make_manager(org, ws_a, 'mgra@edu.test')
    _make_manager(org, ws_b, 'mgrb@edu.test')

    client = APIClient()
    # manager：本学院∪自己，不含其他学院/owner
    client.force_authenticate(user=mgr_a)
    resp = client.get('/api/users')
    assert resp.status_code == 200, resp.content
    emails = {u['email'] for u in resp.json()}
    assert 'mgra@edu.test' in emails
    assert 'teacher@edu.test' not in emails
    assert 'mgrb@edu.test' not in emails

    # annotator：仅自己
    client.force_authenticate(user=_fresh(student))
    resp2 = client.get('/api/users')
    assert resp2.status_code == 200
    assert {u['email'] for u in resp2.json()} == {'student@edu.test'}

    # admin/owner：全量
    client.force_authenticate(user=teacher)
    resp3 = client.get('/api/users')
    assert 'mgrb@edu.test' in {u['email'] for u in resp3.json()}
