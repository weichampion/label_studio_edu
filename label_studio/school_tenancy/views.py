import json
import re

from core.middleware import enforce_csrf_checks
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from organizations.models import Organization, OrganizationMember
from projects.models import Project, ProjectMember
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from school_tenancy import access, services, template_groups
from school_tenancy.permissions import VIEW_ALL_ANNOTATIONS
from school_tenancy.models import (
    ANNOTATION_MODULES,
    MANAGER_ROLES,
    PROJECT_FEATURES,
    College,
    Role,
    StudentCreatePermission,
)
from tasks.models import Annotation
from users.models import User

EMAIL_SPLIT = re.compile(r'[\s,;，；]+')
MODULE_LABELS = dict(ANNOTATION_MODULES)


def college_summary(user):
    active_id = access.ensure_active_college(user)
    colleges = []
    for member in access.valid_memberships(user).select_related('organization__college', 'school_role'):
        role_row = getattr(member, 'school_role', None)
        role = role_row.role if role_row else Role.STUDENT
        colleges.append(
            {
                'id': member.organization_id,
                'title': member.organization.title,
                'code': member.organization.college.code,
                'role': role,
                'role_label': Role(role).label,
                'active': member.organization_id == active_id,
            }
        )
    active = next((c for c in colleges if c['active']), None)
    return {'email': user.email, 'active_college': active, 'colleges': colleges}


def _manager_scope(request):
    org_id = access.ensure_active_college(request.user)
    role = access.role_in(request.user, org_id)
    return (org_id, role) if role in MANAGER_ROLES else (None, None)


def _admin_scope(request):
    org_id = access.ensure_active_college(request.user)
    return org_id if access.role_in(request.user, org_id) == Role.COLLEGE_ADMIN else None


def _student_members(org_id):
    return (
        OrganizationMember.objects.filter(organization_id=org_id, deleted_at__isnull=True)
        .filter(Q(school_role__isnull=True) | Q(school_role__role=Role.STUDENT))
        .select_related('user')
        .order_by('user__email')
    )


def _student_project_row(user, project):
    features = access.project_features(user, project)
    allowed = access.allowed_modules(user, project)
    modules = [name for name in access.project_modules(project) if allowed is None or name in allowed]
    return {
        'project': project,
        'owned': project.created_by_id == user.pk,
        'features': [label for name, label in PROJECT_FEATURES if name in features],
        'modules': [MODULE_LABELS[name] for name in modules] if 'annotate' in features else [],
    }


def _create_grant_rows(org_id):
    grants = {
        grant.member_id: grant
        for grant in StudentCreatePermission.objects.filter(member__organization_id=org_id).select_related('granted_by')
    }
    options = template_groups.available_groups() + [template_groups.CUSTOM_TEMPLATE]
    rows = []
    for member in _student_members(org_id):
        grant = grants.get(member.id)
        granted = set(grant.template_groups) if grant else set()
        rows.append({
            'member': member,
            'granted_by': grant.granted_by if grant else None,
            'groups': [
                {'name': group, 'label': template_groups.group_label(group), 'enabled': group in granted}
                for group in options
            ],
        })
    return rows


@login_required
def all_annotations(request):
    """College-wide annotation data. College admins see their college; superusers see every college."""
    user = request.user
    if not user.has_perm(VIEW_ALL_ANNOTATIONS):
        raise PermissionDenied

    queryset = access.scope_all_annotations(user)
    college_id = request.GET.get('college') or ''
    project_id = request.GET.get('project') or ''
    annotator = (request.GET.get('annotator') or '').strip()
    if user.is_superuser and college_id:
        queryset = queryset.filter(project__organization_id=college_id)
    if project_id:
        queryset = queryset.filter(project_id=project_id)
    if annotator:
        queryset = queryset.filter(
            Q(completed_by__email__icontains=annotator) | Q(completed_by__username__icontains=annotator)
        )
    queryset = queryset.select_related('project', 'project__organization', 'task', 'completed_by').order_by('-id')
    total = queryset.count()
    page_obj = Paginator(queryset, 100).get_page(request.GET.get('page'))

    rows = [
        {
            'id': annotation.id,
            'project': annotation.project,
            'task_id': annotation.task_id,
            'annotator': annotation.completed_by,
            'created_at': annotation.created_at,
            'updated_at': annotation.updated_at,
            'result': json.dumps(annotation.result or [], ensure_ascii=False),
        }
        for annotation in page_obj
    ]

    if request.GET.get('format') == 'json':
        return JsonResponse(
            {
                'count': total,
                'page': page_obj.number,
                'pages': page_obj.paginator.num_pages,
                'results': [
                    {
                        'id': row['id'],
                        'project': row['project'].id,
                        'project_title': row['project'].title,
                        'organization': row['project'].organization_id,
                        'organization_title': row['project'].organization.title,
                        'task': row['task_id'],
                        'annotator': row['annotator'].email if row['annotator'] else None,
                        'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                        'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None,
                        'result': json.loads(row['result']),
                    }
                    for row in rows
                ],
            }
        )

    params = request.GET.copy()
    params.pop('page', None)
    params.pop('format', None)
    context = {
        'rows': rows,
        'total': total,
        'page_obj': page_obj,
        'query': params.urlencode(),
        'is_system_admin': user.is_superuser,
        'college_id': college_id,
        'project_id': project_id,
        'annotator': annotator,
        'colleges': (
            Organization.objects.filter(college__isnull=False).select_related('college').order_by('title')
            if user.is_superuser
            else []
        ),
    }
    return render(request, 'school_tenancy/all_annotations.html', context)


@login_required
def workbench(request):
    user = request.user
    org_id = access.ensure_active_college(user)
    if org_id is None and user.is_superuser:
        return redirect('school_tenancy:all-annotations')
    summary = college_summary(user)
    role = access.role_in(user, org_id)
    college = College.objects.select_related('organization').get(organization_id=org_id)
    context = {'summary': summary, 'college': college, 'role': role, 'role_label': Role(role).label}

    if role in MANAGER_ROLES:
        projects = Project.objects.for_user(user).order_by('-created_at').prefetch_related(
            'members__user', 'members__student_permissions', 'school_teachers__user'
        )
        context['projects'] = []
        for project in projects:
            modules = access.project_modules(project)
            students = []
            for membership in project.members.all():
                if not membership.enabled or access.role_in(membership.user, org_id) != Role.STUDENT:
                    continue
                grant = getattr(membership, 'student_permissions', None)
                granted_modules = grant.annotation_modules if grant else None
                students.append({
                    'membership': membership,
                    'features': [
                        {'name': name, 'label': label, 'enabled': getattr(grant, name) if grant else name == 'annotate'}
                        for name, label in PROJECT_FEATURES
                    ],
                    'modules': [
                        {
                            'name': name,
                            'label': MODULE_LABELS[name],
                            'enabled': granted_modules is None or name in granted_modules,
                        }
                        for name in modules
                    ],
                })
            context['projects'].append({
                'project': project,
                'students': students,
                'teachers': project.school_teachers.all(),
            })
        context['students'] = [m.user for m in _student_members(org_id)]
        context['create_grants'] = _create_grant_rows(org_id)
    else:
        context['student_projects'] = [
            _student_project_row(user, project) for project in Project.objects.for_user(user).order_by('-created_at')
        ]
        granted = access.creation_template_groups(user) or set()
        context['create_groups'] = [
            template_groups.group_label(group)
            for group in template_groups.available_groups() + [template_groups.CUSTOM_TEMPLATE]
            if group in granted
        ]

    if role == Role.COLLEGE_ADMIN:
        members = (
            OrganizationMember.objects.filter(organization_id=org_id, deleted_at__isnull=True)
            .exclude(user_id=F('organization__created_by_id'))  # hide the college system account
            .select_related('user', 'school_role')
            .order_by('user__email')
        )
        context['members'] = [
            {'member': m, 'role': m.school_role.role if hasattr(m, 'school_role') else Role.STUDENT} for m in members
        ]
        context['roles'] = Role.choices
        context['invite_url'] = (
            request.build_absolute_uri(reverse('user-signup')) + '?token=' + college.organization.token
        )
    return render(request, 'school_tenancy/workbench.html', context)


def no_college(request):
    if not request.user.is_authenticated:
        return redirect('user-login')
    return render(request, 'school_tenancy/no_college.html', {'user': request.user})


@enforce_csrf_checks
@login_required
@require_POST
def switch_college(request):
    try:
        organization_id = int(request.POST.get('organization_id', ''))
    except ValueError:
        organization_id = None
    if organization_id and access.switch_college(request.user, organization_id):
        messages.success(request, '已切换学院')
        if request.POST.get('then') == 'projects':
            return redirect('projects:project-index')
    else:
        messages.error(request, '无法切换到该学院')
    return redirect('school_tenancy:workbench')


@enforce_csrf_checks
@login_required
@require_POST
def project_students_add(request, project_id):
    org_id, _ = _manager_scope(request)
    if org_id is None:
        return HttpResponseForbidden('仅教师和学院管理员可分配学生')
    project = get_object_or_404(Project.objects.for_user(request.user), pk=project_id)
    emails = {e.strip().lower() for e in EMAIL_SPLIT.split(request.POST.get('emails', '')) if e.strip()}
    users = list(User.objects.filter(email__in=emails))
    missing = emails - {u.email.lower() for u in users}
    if missing:
        messages.error(request, '以下账号不存在：' + '、'.join(sorted(missing)))
        return redirect('school_tenancy:workbench')
    try:
        services.assign_students(project, users)
        if users:
            messages.success(request, f'已为「{project.title}」分配 {len(users)} 名学生')
    except ValidationError as exc:
        messages.error(request, '；'.join(exc.messages))
    return redirect('school_tenancy:workbench')


@enforce_csrf_checks
@login_required
@require_POST
def project_student_remove(request, project_id, user_id):
    org_id, _ = _manager_scope(request)
    if org_id is None:
        return HttpResponseForbidden('仅教师和学院管理员可调整分配')
    project = get_object_or_404(Project.objects.for_user(request.user), pk=project_id)
    services.unassign_student(project, get_object_or_404(User, pk=user_id))
    messages.success(request, '已取消分配')
    return redirect('school_tenancy:workbench')


@enforce_csrf_checks
@login_required
@require_POST
def student_features(request, project_id, user_id):
    project = get_object_or_404(Project.objects.for_user(request.user), pk=project_id)
    if not access.can_manage_project(request.user, project):
        return HttpResponseForbidden('仅负责教师和学院管理员可授权')
    membership = get_object_or_404(ProjectMember, project=project, user_id=user_id, enabled=True)
    try:
        modules = request.POST.getlist('modules') if request.POST.get('modules_submitted') else None
        services.set_student_features(request.user, membership, request.POST.getlist('features'), modules)
        messages.success(request, f'已保存 {membership.user.email} 在「{project.title}」的功能权限')
    except ValidationError as exc:
        messages.error(request, '；'.join(exc.messages))
    return redirect('school_tenancy:workbench')


@enforce_csrf_checks
@login_required
@require_POST
def project_teacher(request, project_id):
    project = get_object_or_404(Project.objects.for_user(request.user), pk=project_id)
    teacher = get_object_or_404(User, email__iexact=request.POST.get('email', '').strip())
    try:
        services.set_project_teacher(request.user, project, teacher, enabled=request.POST.get('remove') != '1')
        messages.success(request, '已更新项目负责教师')
    except ValidationError as exc:
        messages.error(request, '；'.join(exc.messages))
    return redirect('school_tenancy:workbench')


@enforce_csrf_checks
@login_required
@require_POST
def student_create_templates(request, member_id):
    org_id, _ = _manager_scope(request)
    if org_id is None:
        return HttpResponseForbidden('仅教师和学院管理员可授权学生新建项目')
    member = get_object_or_404(OrganizationMember, pk=member_id, organization_id=org_id, deleted_at__isnull=True)
    try:
        services.set_student_create_groups(request.user, member, request.POST.getlist('groups'))
        messages.success(request, f'已保存 {member.user.email} 的新建项目权限')
    except ValidationError as exc:
        messages.error(request, '；'.join(exc.messages))
    return redirect('school_tenancy:workbench')


@enforce_csrf_checks
@login_required
@require_POST
def member_add(request):
    org_id = _admin_scope(request)
    if org_id is None:
        return HttpResponseForbidden('仅学院管理员可管理成员')
    college = College.objects.select_related('organization').get(organization_id=org_id)
    email = request.POST.get('email', '').strip().lower()
    role = request.POST.get('role', Role.STUDENT)
    password = request.POST.get('password', '')
    if not email or role not in Role.values:
        messages.error(request, '请填写邮箱并选择角色')
        return redirect('school_tenancy:workbench')

    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        if not password:
            messages.error(request, '新账号需要设置初始密码')
            return redirect('school_tenancy:workbench')
        try:
            validate_password(password)
        except ValidationError as exc:
            messages.error(request, '；'.join(exc.messages))
            return redirect('school_tenancy:workbench')
        user = User.objects.create_user(
            email=email,
            password=password,
            username=email.split('@')[0],
            first_name=request.POST.get('name', '').strip(),
        )
    services.add_member(college, user, role)
    messages.success(request, f'已将 {email} 设为 {Role(role).label}')
    return redirect('school_tenancy:workbench')


@enforce_csrf_checks
@login_required
@require_POST
def member_role(request, member_id):
    org_id = _admin_scope(request)
    if org_id is None:
        return HttpResponseForbidden('仅学院管理员可管理成员')
    member = get_object_or_404(OrganizationMember, pk=member_id, organization_id=org_id, deleted_at__isnull=True)
    role = request.POST.get('role')
    if role not in Role.values:
        messages.error(request, '无效的角色')
        return redirect('school_tenancy:workbench')
    try:
        services.set_role(member, role)
        messages.success(request, f'已将 {member.user.email} 设为 {Role(role).label}')
    except ValidationError as exc:
        messages.error(request, '；'.join(exc.messages))
    return redirect('school_tenancy:workbench')


@enforce_csrf_checks
@login_required
@require_POST
def member_remove(request, member_id):
    org_id = _admin_scope(request)
    if org_id is None:
        return HttpResponseForbidden('仅学院管理员可管理成员')
    member = get_object_or_404(OrganizationMember, pk=member_id, organization_id=org_id, deleted_at__isnull=True)
    if member.user_id == request.user.id:
        messages.error(request, '不能移除自己')
        return redirect('school_tenancy:workbench')
    try:
        services.remove_member(member)
        messages.success(request, f'已将 {member.user.email} 移出学院')
    except ValidationError as exc:
        messages.error(request, '；'.join(exc.messages))
    return redirect('school_tenancy:workbench')


class CurrentCollegeAPI(APIView):
    """Current user, active college and role, and the colleges the user can switch to."""

    permission_classes = (IsAuthenticated,)
    school_tenancy_exempt = True

    def get(self, request):
        return Response(college_summary(request.user))


class SwitchCollegeAPI(APIView):
    permission_classes = (IsAuthenticated,)
    school_tenancy_exempt = True

    def post(self, request):
        try:
            organization_id = int(request.data.get('organization_id'))
        except (TypeError, ValueError):
            return Response({'detail': 'organization_id 必须是整数'}, status=status.HTTP_400_BAD_REQUEST)
        if not access.switch_college(request.user, organization_id):
            return Response({'detail': '不是该学院的有效成员'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(college_summary(request.user))
