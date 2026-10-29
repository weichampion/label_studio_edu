"""角色枚举与「角色 → 权限点」矩阵。

详见 docs/edu-enterprise/技术方案.md §4。
教育场景映射：owner=机构负责人, admin=教务/平台管理员, manager=老师,
reviewer=助教/审核, annotator=学生。
"""
from django.db import models


class Role(models.TextChoices):
    OWNER = 'owner', 'Owner'
    ADMIN = 'admin', 'Administrator'
    MANAGER = 'manager', 'Manager'
    REVIEWER = 'reviewer', 'Reviewer'
    ANNOTATOR = 'annotator', 'Annotator'


# 数值越大权限越高，用于双层角色（组织级 vs 部门级）取高
ROLE_RANK = {
    Role.ANNOTATOR: 0,
    Role.REVIEWER: 1,
    Role.MANAGER: 2,
    Role.ADMIN: 3,
    Role.OWNER: 4,
}


def max_role(*roles):
    """返回权限最高的角色；忽略 None。"""
    roles = [r for r in roles if r]
    if not roles:
        return None
    return max(roles, key=lambda r: ROLE_RANK.get(r, -1))


OWNER_UP = {Role.OWNER}
ADMIN_UP = OWNER_UP | {Role.ADMIN}
MANAGER_UP = ADMIN_UP | {Role.MANAGER}
REVIEWER_UP = MANAGER_UP | {Role.REVIEWER}
ALL_ROLES = REVIEWER_UP | {Role.ANNOTATOR}

# 权限矩阵（技术方案 §4.2）。
# 注意：annotator/reviewer 的 view 类权限是「允许但仅限自己相关对象」，
# 对象级过滤由 queryset 层与 has_permission 保证（§4.4），矩阵只管角色维度。
ROLE_PERMISSIONS = {
    'organizations.create': ALL_ROLES,  # 与 OSS 保持一致（是否开放注册由部署配置决定）
    'organizations.view': ALL_ROLES,
    'organizations.change': ADMIN_UP,
    'organizations.delete': OWNER_UP,
    'organizations.invite': MANAGER_UP,
    'projects.create': MANAGER_UP,
    'projects.view': ALL_ROLES,
    'projects.change': MANAGER_UP,
    'projects.delete': MANAGER_UP,
    'projects.reset_cache': MANAGER_UP,
    'tasks.create': MANAGER_UP,
    'tasks.view': ALL_ROLES,
    'tasks.change': MANAGER_UP,
    'tasks.delete': MANAGER_UP,
    'views.reset': ALL_ROLES,
    'annotations.create': ALL_ROLES,
    'annotations.view': ALL_ROLES,
    'annotations.change': ALL_ROLES,  # 他人标注的修改在对象级校验中限制为 reviewer+
    'annotations.delete': ALL_ROLES,
    'actions.perform': MANAGER_UP,
    'predictions.any': MANAGER_UP,
    'avatar.any': ALL_ROLES,
    'labels.create': MANAGER_UP,
    'labels.view': ALL_ROLES,
    'labels.change': MANAGER_UP,
    'labels.delete': MANAGER_UP,
    'models.create': MANAGER_UP,
    'models.view': MANAGER_UP,
    'models.change': MANAGER_UP,
    'models.delete': MANAGER_UP,
    'model_provider_connection.create': ADMIN_UP,
    'model_provider_connection.view': MANAGER_UP,
    'model_provider_connection.change': ADMIN_UP,
    'model_provider_connection.delete': ADMIN_UP,
    'webhooks.view': MANAGER_UP,
    'webhooks.change': ADMIN_UP,
    'users.token.any': ALL_ROLES,
    'storages.view': MANAGER_UP,
    'storages.change': MANAGER_UP,
    'storages.sync': MANAGER_UP,
    'views.view': ALL_ROLES,
    'views.create': ALL_ROLES,
    'views.change': ALL_ROLES,
    'views.delete': ALL_ROLES,
    # orgext 自有权限点
    'workspaces.create': ADMIN_UP,
    'workspaces.view': ALL_ROLES,
    'workspaces.change': ADMIN_UP,
    'workspaces.delete': ADMIN_UP,
    'workspaces.invite': MANAGER_UP,
    'assignments.create': MANAGER_UP,
    'assignments.view': ALL_ROLES,
    'assignments.change': MANAGER_UP,
    'assignments.delete': MANAGER_UP,
}
