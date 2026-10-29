"""Django admin 注册：方便在没有前端部门管理页之前直接测试/管理数据。"""
from django.contrib import admin
from orgext.models import Assignment, Workspace, WorkspaceMember


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'organization', 'parent', 'is_archived', 'created_by', 'created_at')
    list_filter = ('organization', 'is_archived')
    search_fields = ('title',)


@admin.register(WorkspaceMember)
class WorkspaceMemberAdmin(admin.ModelAdmin):
    list_display = ('id', 'workspace', 'user', 'role', 'created_at')
    list_filter = ('role', 'workspace')
    search_fields = ('user__email',)


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'task', 'user', 'type', 'status', 'assigned_by', 'created_at')
    list_filter = ('type', 'status')
    search_fields = ('user__email',)
