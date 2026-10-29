"""orgext 路由（技术方案 §7）。"""
from django.urls import path
from orgext import api, views

app_name = 'orgext'

urlpatterns = [
    # SPA 壳页面（与 organizations/projects 的做法一致）
    path('workspaces/', views.workspaces_page, name='workspaces-index'),
    path('workspaces/<int:pk>/', views.workspaces_page, name='workspaces-detail'),
    # 账号创建（管理员建老师等）
    path('api/user-accounts/', api.UserAccountCreateAPI.as_view(), name='user-account-create'),
    # 部门
    path('api/workspaces/', api.WorkspaceListAPI.as_view(), name='workspace-list'),
    path('api/workspaces/<int:pk>/', api.WorkspaceDetailAPI.as_view(), name='workspace-detail'),
    path('api/workspaces/<int:pk>/members/', api.WorkspaceMemberListAPI.as_view(), name='workspace-member-list'),
    path(
        'api/workspaces/<int:pk>/members/<int:user_pk>/',
        api.WorkspaceMemberDetailAPI.as_view(),
        name='workspace-member-detail',
    ),
    # 组织级角色
    path(
        'api/organizations/<int:pk>/memberships/<int:user_pk>/role',
        api.OrganizationMemberRoleAPI.as_view(),
        name='organization-member-role',
    ),
    # 任务分配
    path('api/projects/<int:pk>/assignments/', api.ProjectAssignmentsAPI.as_view(), name='project-assignments'),
    path('api/projects/<int:pk>/assignments/auto/', api.ProjectAutoAssignAPI.as_view(), name='project-auto-assign'),
    path('api/assignments/<int:pk>/', api.AssignmentDetailAPI.as_view(), name='assignment-detail'),
    path('api/users/me/assignments/', api.MyAssignmentsAPI.as_view(), name='my-assignments'),
]
