"""orgext REST API：部门 / 成员角色 / 任务分配。

详见 docs/edu-enterprise/技术方案.md §7。
视图级 RBAC 由 settings 里的 orgext.api_permissions.RolePermission
消费 permission_required 完成；对象级由 HasObjectPermission（obj.has_permission）保证。
"""
import logging
import secrets
import string

from core.permissions import ViewClassPermission
from django.shortcuts import get_object_or_404
from orgext.models import Assignment, Workspace, WorkspaceMember
from orgext.permissions import effective_role
from orgext.queue import auto_distribute, bulk_assign
from orgext.roles import MANAGER_UP, Role
from orgext.serializers import (
    AssignmentSerializer,
    AutoAssignSerializer,
    BulkAssignSerializer,
    OrganizationMemberRoleSerializer,
    UserAccountCreateSerializer,
    WorkspaceMemberRoleSerializer,
    WorkspaceMemberSerializer,
    WorkspaceSerializer,
)
from organizations.models import OrganizationMember
from projects.models import Project
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from users.models import User

logger = logging.getLogger(__name__)


def generate_initial_password():
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(12))


def resolve_or_create_org_user(email, organization, default_role=Role.ANNOTATOR):
    """按邮箱找到或创建用户，并确保其为组织活跃成员。返回 (user, created, password)。"""
    user = User.objects.filter(email__iexact=email).first()
    created = False
    password = None
    if user is None:
        password = generate_initial_password()
        user = User.objects.create_user(email=email, password=password, username=email.split('@')[0])
        created = True
    if not OrganizationMember.objects.filter(
        user=user, organization=organization, deleted_at__isnull=True
    ).exists():
        OrganizationMember.objects.create(user=user, organization=organization, role=default_role)
    if user.active_organization_id is None:
        user.active_organization = organization
        user.save(update_fields=['active_organization'])
    return user, created, password


def assert_workspace_member(user, workspace):
    """非 admin+ 必须是该工作区成员，否则 404（老师只可见/只可操作自己的学院）。"""
    from rest_framework.exceptions import NotFound

    if effective_role(user, workspace) in (Role.OWNER, Role.ADMIN):
        return
    if not WorkspaceMember.objects.filter(workspace=workspace, user=user).exists():
        raise NotFound('Workspace not found')


def validate_assignees(project, user_ids):
    """项目归属学院时，被分配人必须是该学院成员；无学院时按组织成员校验。"""
    if not user_ids:
        return
    if project.workspace_id:
        allowed = set(
            WorkspaceMember.objects.filter(workspace_id=project.workspace_id, user_id__in=user_ids).values_list(
                'user_id', flat=True
            )
        )
        invalid = set(user_ids) - allowed
        if invalid:
            raise ValidationError(f'Users {sorted(invalid)} are not members of the project workspace')
    else:
        allowed = set(
            OrganizationMember.objects.filter(
                organization=project.organization, user_id__in=user_ids, deleted_at__isnull=True
            ).values_list('user_id', flat=True)
        )
        invalid = set(user_ids) - allowed
        if invalid:
            raise ValidationError(f'Users {sorted(invalid)} are not members of the organization')


class WorkspaceListAPI(generics.ListCreateAPIView):
    serializer_class = WorkspaceSerializer
    permission_required = ViewClassPermission(GET='workspaces.view', POST='workspaces.create')

    def get_queryset(self):
        user = self.request.user
        qs = Workspace.objects.filter(organization=user.active_organization, is_archived=False)
        # manager 及以下只能看到自己所在的部门；admin+ 全量（技术方案 §6/§7）
        if effective_role(user) not in (Role.OWNER, Role.ADMIN):
            qs = qs.filter(members__user=user)
        return qs.distinct()

    def perform_create(self, serializer):
        serializer.save(organization=self.request.user.active_organization, created_by=self.request.user)


class WorkspaceDetailAPI(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WorkspaceSerializer
    permission_required = ViewClassPermission(
        GET='workspaces.view', PATCH='workspaces.change', PUT='workspaces.change', DELETE='workspaces.delete'
    )

    def get_queryset(self):
        qs = Workspace.objects.filter(organization=self.request.user.active_organization)
        # 非 admin+ 只能看到自己所属的部门（与列表页一致；非本学院 → 404）
        if effective_role(self.request.user) not in (Role.OWNER, Role.ADMIN):
            qs = qs.filter(members__user=self.request.user)
        return qs

    def perform_destroy(self, instance):
        # 归档而非物理删除，项目与成员关系保留
        instance.is_archived = True
        instance.save(update_fields=['is_archived'])


class WorkspaceMemberListAPI(generics.ListCreateAPIView):
    serializer_class = WorkspaceMemberSerializer
    permission_required = ViewClassPermission(GET='workspaces.view', POST='workspaces.invite')

    @property
    def workspace(self):
        workspace = get_object_or_404(
            Workspace, pk=self.kwargs['pk'], organization=self.request.user.active_organization
        )
        assert_workspace_member(self.request.user, workspace)
        return workspace

    def get_queryset(self):
        return WorkspaceMember.objects.filter(workspace=self.workspace).select_related('user')

    def create(self, request, *args, **kwargs):
        """按 email（推荐，老师加学员：没账号自动建号）或 user id（要求已是组织成员）添加成员。"""
        workspace = self.workspace
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        role = data.get('role') or Role.ANNOTATOR
        created_password = None

        if data.get('user'):
            user = data['user']
            if not OrganizationMember.objects.filter(
                user=user, organization=workspace.organization, deleted_at__isnull=True
            ).exists():
                raise ValidationError('User must be a member of the organization first')
        else:
            email = data['email'].strip().lower()
            user, _created, created_password = resolve_or_create_org_user(email, workspace.organization)

        if WorkspaceMember.objects.filter(workspace=workspace, user=user).exists():
            raise ValidationError('User is already a member of this workspace')

        member = WorkspaceMember.objects.create(workspace=workspace, user=user, role=role)
        payload = WorkspaceMemberSerializer(member).data
        if created_password:
            payload['account_created'] = True
            payload['created_password'] = created_password
        return Response(payload, status=status.HTTP_201_CREATED)


class WorkspaceMemberDetailAPI(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WorkspaceMemberSerializer
    permission_required = ViewClassPermission(
        GET='workspaces.view', PATCH='workspaces.invite', DELETE='workspaces.invite'
    )
    lookup_url_kwarg = 'user_pk'
    lookup_field = 'user_id'
    http_method_names = ['get', 'patch', 'delete', 'head']

    def get_queryset(self):
        workspace = get_object_or_404(
            Workspace, pk=self.kwargs['pk'], organization=self.request.user.active_organization
        )
        assert_workspace_member(self.request.user, workspace)
        return WorkspaceMember.objects.filter(workspace=workspace)

    def patch(self, request, *args, **kwargs):
        member = self.get_object()
        serializer = WorkspaceMemberRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member.role = serializer.validated_data['role']
        member.save(update_fields=['role'])
        return Response(WorkspaceMemberSerializer(member).data)


class UserAccountCreateAPI(APIView):
    """POST /api/user-accounts/ — 管理员创建账号（老师等），可同时绑定学院。"""

    permission_required = ViewClassPermission(POST='organizations.change')

    def post(self, request):
        serializer = UserAccountCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        organization = request.user.active_organization
        email = data['email'].strip().lower()

        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('A user with this email already exists')

        password = data['password']
        initial_password = None
        if not password:
            password = generate_initial_password()
            initial_password = password

        user = User.objects.create_user(
            email=email,
            password=password,
            username=email.split('@')[0],
            first_name=data['first_name'],
            last_name=data['last_name'],
        )
        OrganizationMember.objects.create(user=user, organization=organization, role=data['role'])
        user.active_organization = organization
        user.save(update_fields=['active_organization'])

        workspace_id = data.get('workspace')
        if workspace_id:
            workspace = get_object_or_404(Workspace, pk=workspace_id, organization=organization)
            WorkspaceMember.objects.create(workspace=workspace, user=user, role=data['role'])

        payload = {'id': user.id, 'email': user.email, 'role': data['role'], 'workspace': workspace_id}
        if initial_password:
            payload['initial_password'] = initial_password
        return Response(payload, status=status.HTTP_201_CREATED)


class OrganizationMemberRoleAPI(APIView):
    """PATCH /api/organizations/<pk>/memberships/<user_pk>/role — 改组织级角色 [admin+]。"""

    permission_required = ViewClassPermission(PATCH='organizations.change')

    def patch(self, request, pk, user_pk):
        if int(pk) != request.user.active_organization_id:
            from rest_framework.exceptions import NotFound

            raise NotFound('Organization not found')
        member = get_object_or_404(
            OrganizationMember, organization_id=pk, user_id=user_pk, deleted_at__isnull=True
        )
        serializer = OrganizationMemberRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_role = serializer.validated_data['role']
        if member.is_owner:
            return Response({'detail': 'Cannot change role of the organization owner'}, status=status.HTTP_400_BAD_REQUEST)
        if new_role == Role.OWNER:
            return Response({'detail': 'Owner role is bound to the organization creator'}, status=status.HTTP_400_BAD_REQUEST)
        member.role = new_role
        member.save(update_fields=['role'])
        return Response({'user_id': member.user_id, 'role': member.role})


class ProjectAssignmentsAPI(generics.ListCreateAPIView):
    """GET 查看分配情况 / POST 批量分配（技术方案 §5.1/§7）。"""

    serializer_class = AssignmentSerializer
    permission_required = ViewClassPermission(GET='assignments.view', POST='assignments.create')

    @property
    def project(self):
        return get_object_or_404(Project, pk=self.kwargs['pk'], organization=self.request.user.active_organization)

    def get_queryset(self):
        qs = Assignment.objects.filter(task__project=self.project).select_related('user')
        # 学生只能看自己的分配
        if effective_role(self.request.user, self.project) not in MANAGER_UP:
            qs = qs.filter(user=self.request.user)
        return qs

    def create(self, request, *args, **kwargs):
        project = self.project
        serializer = BulkAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        validate_assignees(project, data['user_ids'])

        task_ids = data['task_ids']
        if task_ids:
            task_ids = list(project.tasks.filter(id__in=task_ids).values_list('id', flat=True))
        else:
            task_ids = list(project.tasks.values_list('id', flat=True))
        user_ids = list(
            OrganizationMember.objects.filter(
                organization=project.organization, user_id__in=data['user_ids'], deleted_at__isnull=True
            ).values_list('user_id', flat=True)
        )
        created = bulk_assign(task_ids, user_ids, request.user, assignment_type=data['type'])
        return Response({'created': len(created)}, status=status.HTTP_201_CREATED)


class ProjectAutoAssignAPI(APIView):
    """POST 自动分配：轮询均分（技术方案 §5.1）。"""

    permission_required = ViewClassPermission(POST='assignments.create')

    def post(self, request, pk):
        project = get_object_or_404(Project, pk=pk, organization=request.user.active_organization)
        serializer = AutoAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        validate_assignees(project, data['user_ids'])

        task_ids = data['task_ids']
        if task_ids:
            task_ids = list(project.tasks.filter(id__in=task_ids).values_list('id', flat=True))
        else:
            task_ids = list(project.tasks.values_list('id', flat=True))
        user_ids = list(
            OrganizationMember.objects.filter(
                organization=project.organization, user_id__in=data['user_ids'], deleted_at__isnull=True
            ).values_list('user_id', flat=True)
        )
        created = auto_distribute(
            task_ids, user_ids, request.user, overlap=data['overlap'], assignment_type=data['type']
        )
        return Response({'created': len(created)}, status=status.HTTP_201_CREATED)


class AssignmentDetailAPI(generics.RetrieveDestroyAPIView):
    serializer_class = AssignmentSerializer
    permission_required = ViewClassPermission(GET='assignments.view', DELETE='assignments.delete')

    def get_queryset(self):
        return Assignment.objects.filter(task__project__organization=self.request.user.active_organization)


class MyAssignmentsAPI(generics.ListAPIView):
    """GET /api/users/me/assignments/ — 学生看自己的任务量。"""

    serializer_class = AssignmentSerializer
    permission_required = ViewClassPermission(GET='assignments.view')

    def get_queryset(self):
        qs = Assignment.objects.filter(
            user=self.request.user, task__project__organization=self.request.user.active_organization
        ).select_related('task')
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)
        return qs
