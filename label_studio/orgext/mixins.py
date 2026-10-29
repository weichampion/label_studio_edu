"""内核模型 mixin 的 orgext 实现。

设计约定：
- 每个 mixin 都 **继承** 内核原生 mixin，默认行为与 OSS 一致；
- 不设部门 / 不分配时（workspace=NULL），行为与原生 OSS 完全一致（技术方案 §9.4）；
- 通过 core/settings/base.py 的 *_MIXIN 常量指向这里完成接入。
"""
from organizations.mixins import (
    OrganizationMemberMixin as _BaseOrganizationMemberMixin,
    OrganizationMixin as _BaseOrganizationMixin,
)
from projects.mixins import ProjectMixin as _BaseProjectMixin

# 注意：不能在此 import users.mixins —— organizations.models 加载本模块时
# users.mixins 会反向 import organizations.models 造成循环导入。
# 因此 UserMixin 内联 OSS users.mixins.UserMixin 的原始逻辑（3 个小方法）。


class OrganizationMixin(_BaseOrganizationMixin):
    """机构（租户）。"""


class OrganizationMemberMixin(_BaseOrganizationMemberMixin):
    """机构成员：携带组织级角色（role 字段定义在内核模型上，见技术方案 §3.2①）。"""

    @property
    def effective_role(self):
        from orgext.permissions import get_org_role

        return get_org_role(self.user, self.organization_id)


class UserMixin:
    """用户：有效角色计算（组织级 + 部门级取高，技术方案 §4.4）。"""

    def is_project_annotator(self, project):
        # OSS users.mixins.UserMixin 原始行为
        return False

    def has_permission(self, user):
        # OSS users.mixins.UserMixin 原始行为：同组织有效成员即可见
        from organizations.models import OrganizationMember

        return OrganizationMember.objects.filter(
            user=user, organization=user.active_organization, deleted_at__isnull=True
        ).exists()

    @property
    def org_role(self):
        from orgext.permissions import get_org_role

        return get_org_role(self)

    def effective_role(self, obj=None):
        from orgext.permissions import effective_role

        return effective_role(self, obj)

    @property
    def is_annotator(self):
        from orgext.roles import Role

        return self.org_role == Role.ANNOTATOR


class ProjectMixin(_BaseProjectMixin):
    """项目：部门归属可见性。

    - workspace 为空：保持 OSS 行为（放行，隔离由各 API 的组织级 queryset 收口保证，
      且 OSS 存在跨组织协作者 ProjectMember 场景，见技术方案 §9.4）；
    - workspace 非空：须同组织，admin+ 全量可见，其余角色须是该部门成员。
    """

    def has_permission(self, user):
        from orgext.permissions import effective_role
        from orgext.roles import Role

        user.project = self  # link for activity log（保持 OSS 行为）
        if self.workspace_id is None:
            return True
        if user.active_organization_id != self.organization_id:
            return False
        if effective_role(user, self) in (Role.OWNER, Role.ADMIN):
            return True
        return self.workspace.members.filter(user=user).exists()
