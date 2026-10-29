"""Role-based permissions for the school edition.

The community edition declares ``permission_required`` on API views but never evaluates it (every
permission is ``is_authenticated``). Here each permission gets a role predicate, and DRF's
``APIView.check_permissions`` is extended so the declared permission is enforced on every API view,
including views that override ``permission_classes``.
"""

import rules
from core.permissions import ViewClassPermission, all_permissions, make_perm
from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.relations import RelatedField
from rest_framework.views import APIView
from school_tenancy.access import (
    active_role,
    can_access_project,
    can_create_projects,
    control_modules,
    creation_template_groups,
    ensure_active_college,
    has_project_feature,
    hidden_controls,
    scope_projects,
)
from school_tenancy.models import MANAGER_ROLES, Role

P = all_permissions

# Students only label the projects they are assigned to; the projects themselves are filtered elsewhere.
STUDENT_PERMISSIONS = {
    P.projects_view,
    P.tasks_view,
    P.annotations_create,
    P.annotations_view,
    P.annotations_change,
    P.annotations_delete,
    P.avatar_any,
    P.labels_view,
    P.views_view,
    P.views_create,
    P.views_change,
    P.views_delete,
    P.organizations_view,
    P.users_token_any,
    P.models_view,
}
FEATURE_PERMISSIONS = {
    P.annotations_create: 'annotate',
    P.annotations_change: 'annotate',
    P.annotations_delete: 'annotate',
    P.tasks_create: 'import_tasks',
    P.tasks_change: 'manage_tasks',
    P.tasks_delete: 'manage_tasks',
    P.predictions_any: 'manage_tasks',
    P.projects_change: 'configure_project',
    P.projects_delete: 'delete_project',
}
COLLEGE_ADMIN_PERMISSIONS = {P.organizations_change, P.organizations_invite, P.organizations_delete}
# Colleges are created by platform administrators (Django superusers), who bypass predicates.
PLATFORM_PERMISSIONS = {P.organizations_create}


@rules.predicate
def is_college_member(user):
    return active_role(user) is not None


@rules.predicate
def is_teacher_or_admin(user):
    return active_role(user) in MANAGER_ROLES


@rules.predicate
def is_college_admin(user):
    return active_role(user) == Role.COLLEGE_ADMIN


@rules.predicate
def may_create_projects(user):
    return can_create_projects(user)


def predicate_for(permission):
    if permission in PLATFORM_PERMISSIONS:
        return rules.always_deny
    if permission in COLLEGE_ADMIN_PERMISSIONS:
        return is_college_admin
    if permission == P.projects_create:
        return may_create_projects
    if permission in STUDENT_PERMISSIONS or permission in FEATURE_PERMISSIONS:
        return is_college_member
    return is_teacher_or_admin


def register_role_permissions():
    for _, permission in all_permissions:
        make_perm(permission, predicate_for(permission), overwrite=True)


def required_permission(view, request):
    permission = getattr(view, 'permission_required', None)
    if not permission:
        return None
    if isinstance(permission, str):
        return permission
    if isinstance(permission, ViewClassPermission):
        return getattr(permission, 'GET' if request.method == 'HEAD' else request.method, None)
    return None


def required_feature(view, request):
    module = type(view).__module__
    name = type(view).__name__
    permission = required_permission(view, request)
    if name == 'ProjectNextTaskAPI':
        return 'annotate'
    if name == 'ProjectModelVersions' and request.method == 'DELETE':
        return 'manage_tasks'
    if module == 'data_export.api':
        return 'export_data'
    if module == 'data_import.api' and permission == P.projects_change:
        return 'import_tasks'
    if name in ('ProjectImportAPI', 'ProjectReimportAPI'):
        return 'import_tasks'
    if module == 'data_manager.api' and name in ('ProjectActionsAPI', 'ProjectActionsFormAPI'):
        if request.method not in SAFE_METHODS:
            return 'manage_tasks'
    if permission in FEATURE_PERMISSIONS:
        return FEATURE_PERMISSIONS[permission]
    return None


def object_project(obj):
    from projects.models import Project

    if isinstance(obj, Project):
        return obj
    return getattr(obj, 'project', None)


def request_projects(view, request):
    """Resolve project references before a handler or serializer can perform a write."""
    from data_manager.models import View
    from projects.models import Project
    from rest_framework.exceptions import NotFound, ValidationError
    from tasks.models import Annotation, AnnotationDraft, Prediction, Task

    projects = {}

    def resolve(model, value):
        if value is None:
            return
        try:
            pk = int(value)
        except (TypeError, ValueError, OverflowError):
            raise ValidationError('项目或任务 ID 必须是整数')
        obj = model.objects.filter(pk=pk).first()
        if obj is None:
            raise NotFound()
        project = object_project(obj)
        if project is not None:
            projects[project.pk] = project

    path = request.path
    pk = getattr(view, 'kwargs', {}).get('pk')
    for prefix, model in (
        ('/api/projects/', Project),
        ('/api/tasks/', Task),
        ('/api/annotations/', Annotation),
        ('/api/drafts/', AnnotationDraft),
        ('/api/predictions/', Prediction),
        ('/api/dm/views/', View),
    ):
        if path.startswith(prefix) and pk is not None:
            resolve(model, pk)
            break
    for data in (request.query_params, request.data if isinstance(request.data, dict) else {}):
        for field, model in (('project', Project), ('task', Task), ('view', View)):
            resolve(model, data.get(field))
    annotation_id = getattr(view, 'kwargs', {}).get('annotation_id')
    if annotation_id is not None:
        resolve(Annotation, annotation_id)
    return list(projects.values())


def check_project_access(view, request, project):
    if not can_access_project(request.user, project):
        view.permission_denied(request, message='无权访问此项目')
    feature = required_feature(view, request)
    if feature and not has_project_feature(request.user, project, feature):
        view.permission_denied(request, message=f'教师未授权当前项目的此项功能（{feature}）')


def check_result_modules(view, request, project):
    """Reject annotation or draft results written through a withheld annotation module."""
    hidden = hidden_controls(request.user, project)
    data = request.data if isinstance(request.data, dict) else {}
    result = data.get('result')
    if not hidden or not isinstance(result, list):
        return
    allowed = set(control_modules(project)) - hidden
    used = {item.get('from_name') for item in result if isinstance(item, dict) and item.get('from_name')}
    if used - allowed:
        view.permission_denied(request, message='教师未授权当前项目的此标注模块')


def check_creation_config(view, request, projects):
    """Students may only create (and reconfigure their own) projects with configs from granted template groups."""
    from school_tenancy.template_groups import CUSTOM_TEMPLATE, config_allowed

    user = request.user
    data = request.data if isinstance(request.data, dict) else {}
    if request.method not in ('POST', 'PUT', 'PATCH') or not data.get('label_config'):
        return
    name = type(view).__name__
    owned = any(project.created_by_id == user.pk for project in projects)
    if not (name == 'ProjectListAPI' or (name == 'ProjectAPI' and owned)):
        return
    groups = creation_template_groups(user)
    if groups is None or CUSTOM_TEMPLATE in groups:
        return
    # Unparsable configs (None) are rejected by the serializer.
    if config_allowed(data['label_config'], groups) is False:
        view.permission_denied(request, message='此标注配置不属于教师授权的模板分类，请从左侧已授权的分类中选择模板')


def check_action_permission(user, action, project):
    from data_manager.actions import check_action_permission as original

    permissions = action.get('permission', [])
    if isinstance(permissions, str):
        permissions = [permissions]
    if active_role(user) == Role.STUDENT and any(p.startswith('annotations.') for p in permissions):
        # Annotation grants are for the student's own answers, not bulk changes to classmates' answers.
        return False
    return has_project_feature(user, project, 'manage_tasks') and original(user, action, project)


def install_api_permission_enforcement():
    if getattr(APIView, '_school_tenancy_enforced', False):
        return
    original_check_permissions = APIView.check_permissions

    def check_permissions(self, request):
        original_check_permissions(self, request)
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated or getattr(self, 'school_tenancy_exempt', False):
            return
        if not user.is_superuser and ensure_active_college(user) is None:
            self.permission_denied(request, message='当前账号未加入任何学院，请联系学院管理员。')
        permission = required_permission(self, request)
        feature = required_feature(self, request)
        projects = request_projects(self, request)
        for project in projects:
            check_project_access(self, request, project)
            if active_role(user) == Role.STUDENT and feature == 'annotate' and request.method not in SAFE_METHODS:
                check_result_modules(self, request, project)
        if active_role(user) == Role.STUDENT and feature and not projects:
            # Resolved objects are checked again below, but never authorize an unscoped write.
            self.permission_denied(request, message='此项功能必须指定已授权的项目')
        if active_role(user) == Role.STUDENT:
            check_creation_config(self, request, projects)
        if permission and not (feature and projects) and not user.has_perm(permission):
            self.permission_denied(request, message=f'当前角色无权执行此操作（{permission}）')

    APIView.check_permissions = check_permissions
    APIView._school_tenancy_enforced = True

    original_check_object_permissions = APIView.check_object_permissions

    def check_object_permissions(self, request, obj):
        original_check_object_permissions(self, request, obj)
        project = object_project(obj)
        if project is not None:
            check_project_access(self, request, project)
        if request.method not in SAFE_METHODS and not may_modify(request.user, obj):
            self.permission_denied(request, message='学生只能修改自己的标注')

    APIView.check_object_permissions = check_object_permissions


def may_modify(user, obj):
    """Students may only change or delete their own annotations and drafts; managers are unrestricted."""
    from tasks.models import Annotation, AnnotationDraft

    if isinstance(obj, Annotation):
        owner_id = obj.completed_by_id
    elif isinstance(obj, AnnotationDraft):
        owner_id = obj.user_id
    else:
        return True
    return active_role(user) in MANAGER_ROLES or owner_id == user.id


def install_related_field_scoping():
    """Restrict serializer foreign keys to projects/tasks/annotations the requesting user can access.

    Community create endpoints accept project or task ids through ModelSerializer fields whose
    queryset is ``Model.objects.all()``; with several colleges that would allow writing into another
    college's project. Fields used without a request in the serializer context are unchanged.
    """
    if getattr(RelatedField, '_school_tenancy_scoped', False):
        return
    from projects.models import Project
    from tasks.models import Annotation, Task

    original_get_queryset = RelatedField.get_queryset

    def get_queryset(self):
        queryset = original_get_queryset(self)
        request = (getattr(self, 'context', None) or {}).get('request')
        user = getattr(request, 'user', None)
        if queryset is None or user is None or not user.is_authenticated:
            return queryset
        model = queryset.model
        if issubclass(model, Project):
            scoped = scope_projects(queryset, user)
            feature = required_feature((getattr(request, 'parser_context', None) or {}).get('view'), request)
            if feature and active_role(user) == Role.STUDENT:
                scoped = scoped.filter(
                    id__in=[p.pk for p in scoped if has_project_feature(user, p, feature)]
                )
            return scoped
        if issubclass(model, Task):
            scoped = scope_projects(Project.objects.all(), user)
            feature = required_feature((getattr(request, 'parser_context', None) or {}).get('view'), request)
            if feature and active_role(user) == Role.STUDENT:
                scoped = scoped.filter(id__in=[p.pk for p in scoped if has_project_feature(user, p, feature)])
            return queryset.filter(project__in=scoped)
        if issubclass(model, Annotation):
            return queryset.filter(task__project__in=scope_projects(Project.objects.all(), user))
        return queryset

    RelatedField.get_queryset = get_queryset
    RelatedField._school_tenancy_scoped = True


class CollegeAdminWritePermission(BasePermission):
    """settings.MEMBER_PERM: only college administrators may change or remove members."""

    def has_object_permission(self, request, view, obj):
        if request.method not in SAFE_METHODS and active_role(request.user) != Role.COLLEGE_ADMIN:
            return False
        return obj.has_permission(request.user)
