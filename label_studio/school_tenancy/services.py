"""College, membership and assignment operations used by views, management commands and signup."""

from core.feature_flags import flag_set
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from organizations.models import Organization, OrganizationMember
from school_tenancy import access
from school_tenancy.models import (
    MANAGER_ROLES,
    PROJECT_FEATURES,
    College,
    MemberRole,
    ProjectTeacher,
    Role,
    StudentCreatePermission,
    StudentProjectPermission,
)


def create_college(code, title):
    with transaction.atomic():
        organization = Organization.objects.create(title=title, created_by=_create_system_account(code, title))
        college = College.objects.create(organization=organization, code=code)
        add_member(college, organization.created_by, Role.TEACHER)
    # Same API token defaults as organizations.functions.create_organization
    if flag_set('fflag__feature_develop__prompts__dia_1829_jwt_token_auth'):
        organization.jwt.api_tokens_enabled = True
        organization.jwt.legacy_api_tokens_enabled = settings.LABEL_STUDIO_ENABLE_LEGACY_API_TOKEN
        organization.jwt.save()
    return college


def _create_system_account(code, title):
    """Owner account of a college organization.

    Community code relies on ``organization.created_by`` (exports use its API token, storage sync and
    feature flags use it as the acting user). The account cannot log in with a password.
    """
    from users.models import User

    user = User.objects.create_user(
        email=f'college-{code}@system.invalid', password=None, username=f'college-{code}', first_name=f'{title}系统账号'
    )
    return user


def ensure_system_account(college):
    """Backfill the owner account for colleges created before it existed."""
    organization = college.organization
    if organization.created_by_id is None:
        organization.created_by = _create_system_account(college.code, organization.title)
        organization.save(update_fields=['created_by'])
    add_member(college, organization.created_by, Role.TEACHER)


def is_system_account(member):
    return member.user_id == member.organization.created_by_id


def add_member(college, user, role=Role.STUDENT):
    """Add (or re-activate) a member of the college with the given role."""
    organization = college.organization
    with transaction.atomic():
        member, _ = OrganizationMember.objects.get_or_create(user=user, organization=organization)
        if member.deleted_at is not None:
            member.deleted_at = None
            member.save(update_fields=['deleted_at'])
        MemberRole.objects.update_or_create(member=member, defaults={'role': role})
        if user.active_organization_id is None:
            user.active_organization = organization
            user.save(update_fields=['active_organization'])
    access.invalidate(user)
    return member


def _active_admin_count(organization):
    return MemberRole.objects.filter(
        member__organization=organization, member__deleted_at__isnull=True, role=Role.COLLEGE_ADMIN
    ).count()


def set_role(member, role):
    if is_system_account(member):
        raise ValidationError('学院系统账号的角色不能修改')
    current = access.role_in(member.user, member.organization_id)
    if current == Role.COLLEGE_ADMIN and role != Role.COLLEGE_ADMIN and _active_admin_count(member.organization) <= 1:
        raise ValidationError('不能撤销学院唯一管理员的角色')
    MemberRole.objects.update_or_create(member=member, defaults={'role': role})
    if role != Role.STUDENT:
        StudentCreatePermission.objects.filter(member=member).delete()
    access.invalidate(member.user)


def remove_member(member):
    """Remove a member from one college only; other colleges, the avatar and other data are untouched."""
    from projects.models import ProjectMember

    user = member.user
    if is_system_account(member):
        raise ValidationError('学院系统账号不能移除')
    if access.role_in(user, member.organization_id) == Role.COLLEGE_ADMIN and _active_admin_count(member.organization) <= 1:
        raise ValidationError('不能移除学院唯一的管理员')
    with transaction.atomic():
        ProjectMember.objects.filter(user=user, project__organization=member.organization).delete()
        ProjectTeacher.objects.filter(user=user, project__organization=member.organization).delete()
        StudentCreatePermission.objects.filter(member=member).delete()
        user.task_locks.filter(task__project__organization=member.organization).delete()
        member.deleted_at = timezone.now()
        member.save(update_fields=['deleted_at'])
    access.invalidate(user)
    access.ensure_active_college(user)


def assign_students(project, users):
    """Give students access to a project. Every user must be an active student of the project's college."""
    from projects.models import ProjectMember

    for user in users:
        if access.role_in(user, project.organization_id) != Role.STUDENT:
            raise ValidationError(f'{user.email} 不是本学院的学生')
    for user in users:
        membership, _ = ProjectMember.objects.update_or_create(project=project, user=user, defaults={'enabled': True})
        StudentProjectPermission.objects.get_or_create(membership=membership)


def unassign_student(project, user):
    from projects.models import ProjectMember

    ProjectMember.objects.filter(project=project, user=user).delete()


def set_student_features(actor, membership, features, modules=None):
    """Save a student's project features; ``modules`` (annotation modules) is left unchanged when None."""
    if not access.can_manage_project(actor, membership.project):
        raise ValidationError('仅项目创建教师、负责教师或学院管理员可授权')
    if access.role_in(membership.user, membership.project.organization_id) != Role.STUDENT:
        raise ValidationError('只能配置本学院有效学生的功能权限')
    names = {name for name, _ in PROJECT_FEATURES}
    if not set(features) <= names:
        raise ValidationError('包含未知的功能权限')
    defaults = {name: name in features for name in names}
    if modules is not None:
        present = set(access.project_modules(membership.project))
        if not set(modules) <= present:
            raise ValidationError('包含当前项目没有的标注模块')
        # Granting every current module keeps the default "all modules", which also covers modules added later.
        defaults['annotation_modules'] = None if set(modules) >= present else sorted(set(modules))
    StudentProjectPermission.objects.update_or_create(membership=membership, defaults=defaults)


def set_student_create_groups(actor, member, groups):
    """Allow a student to create projects from the given template groups; no group revokes the grant."""
    from school_tenancy.template_groups import CUSTOM_TEMPLATE, available_groups

    org_id = member.organization_id
    if access.ensure_active_college(actor) != org_id or access.role_in(actor, org_id) not in MANAGER_ROLES:
        raise ValidationError('仅本学院教师或学院管理员可授权学生新建项目')
    if access.role_in(member.user, org_id) != Role.STUDENT:
        raise ValidationError('只能给本学院有效学生授权新建项目')
    if not set(groups) <= set(available_groups()) | {CUSTOM_TEMPLATE}:
        raise ValidationError('包含未知的模板分类')
    if groups:
        StudentCreatePermission.objects.update_or_create(
            member=member, defaults={'template_groups': sorted(set(groups)), 'granted_by': actor}
        )
    else:
        StudentCreatePermission.objects.filter(member=member).delete()


def set_project_teacher(actor, project, teacher, enabled=True):
    if access.active_role(actor) != Role.COLLEGE_ADMIN or not access.can_manage_project(actor, project):
        raise ValidationError('仅学院管理员可指定负责教师')
    if access.role_in(teacher, project.organization_id) != Role.TEACHER:
        raise ValidationError('负责教师必须是本学院的有效教师')
    if enabled:
        ProjectTeacher.objects.get_or_create(project=project, user=teacher)
    else:
        ProjectTeacher.objects.filter(project=project, user=teacher).delete()
