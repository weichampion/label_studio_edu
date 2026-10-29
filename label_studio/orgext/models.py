"""orgext 数据模型：Workspace / WorkspaceMember / Assignment。

详见 docs/edu-enterprise/技术方案.md §3。
内核表扩展（OrganizationMember.role、Project.workspace/assignment_mode）
落在各自内核 app 的 migration 中，模型定义仍在内核文件里（改动已记录在方案 §2 清单）。
"""
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from orgext.roles import Role


class Workspace(models.Model):
    """部门（学院/教研室/班级），组织下的二级层级，支持自嵌套。"""

    organization = models.ForeignKey(
        'organizations.Organization', on_delete=models.CASCADE, related_name='workspaces', help_text='Organization ID'
    )
    title = models.CharField(_('workspace title'), max_length=1000)
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE, null=True, blank=True, related_name='children', help_text='Parent workspace'
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='created_workspaces'
    )
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(_('created at'), auto_now_add=True)
    updated_at = models.DateTimeField(_('updated at'), auto_now=True)

    class Meta:
        ordering = ['pk']

    def __str__(self):
        return f'{self.title} (id={self.pk})'

    def has_permission(self, user):
        return user.active_organization_id == self.organization_id

    def has_user(self, user):
        return self.members.filter(user=user).exists()


class WorkspaceMember(models.Model):
    """部门成员及其部门级角色（可高于组织级角色，取高生效）。"""

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='workspace_memberships')
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.ANNOTATOR)
    created_at = models.DateTimeField(_('created at'), auto_now_add=True)
    updated_at = models.DateTimeField(_('updated at'), auto_now=True)

    class Meta:
        unique_together = ('workspace', 'user')
        ordering = ['pk']

    def has_permission(self, user):
        return user.active_organization_id == self.workspace.organization_id


class Assignment(models.Model):
    """任务分配：具体哪条任务派给谁（ProjectMember 只表达「谁能进项目」）。"""

    class Type(models.TextChoices):
        ANNOTATOR = 'AN', 'Annotator'
        REVIEWER = 'RE', 'Reviewer'

    class Status(models.TextChoices):
        ASSIGNED = 'assigned', 'Assigned'
        IN_PROGRESS = 'in_progress', 'In progress'
        COMPLETED = 'completed', 'Completed'

    task = models.ForeignKey('tasks.Task', on_delete=models.CASCADE, related_name='assignments')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='assignments')
    type = models.CharField(max_length=2, choices=Type.choices, default=Type.ANNOTATOR)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ASSIGNED)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='assignments_made'
    )
    created_at = models.DateTimeField(_('created at'), auto_now_add=True)
    updated_at = models.DateTimeField(_('updated at'), auto_now=True)

    class Meta:
        unique_together = ('task', 'user', 'type')
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['task']),
        ]
        ordering = ['pk']

    def has_permission(self, user):
        return user.active_organization_id == self.task.project.organization_id
