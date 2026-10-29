"""角色谓词：覆写 OSS 默认的 is_authenticated 权限绑定。

详见 docs/edu-enterprise/技术方案.md §4.3 / §4.4。
在 OrgextConfig.ready() 里调用 register_role_permissions()。
"""
import logging

import rules

from orgext.roles import ROLE_PERMISSIONS, Role, max_role

logger = logging.getLogger(__name__)


def get_org_role(user, organization_id=None):
    """组织级角色。组织创建者恒为 owner；非成员返回 None。"""
    from organizations.models import OrganizationMember

    organization_id = organization_id or user.active_organization_id
    if not organization_id:
        return None
    role = (
        OrganizationMember.objects.filter(user=user, organization_id=organization_id, deleted_at__isnull=True)
        .values_list('role', flat=True)
        .first()
    )
    if role is None:
        return None
    own = getattr(user, 'own_organization', None)
    if own is not None and own.id == organization_id:
        return Role.OWNER
    return role


def _resolve_workspace(obj):
    """从任意对象上解析所属 Workspace（Workspace/WorkspaceMember/Project/Task/标注等）。"""
    from orgext.models import Workspace, WorkspaceMember

    if obj is None:
        return None
    if isinstance(obj, Workspace):
        return obj
    if isinstance(obj, WorkspaceMember):
        return obj.workspace
    project = getattr(obj, 'project', None) or (obj if obj.__class__.__name__ == 'Project' else None)
    if project is None:
        task = getattr(obj, 'task', None)
        project = getattr(task, 'project', None)
    return getattr(project, 'workspace', None)


def effective_role(user, obj=None):
    """有效角色 = max(组织级角色, 对象所属部门的部门级角色)。技术方案 §4.4。"""
    if not getattr(user, 'is_authenticated', False):
        return None
    cache_key = '_orgext_org_role_%s' % (user.active_organization_id or 0)
    if not hasattr(user, cache_key):
        setattr(user, cache_key, get_org_role(user))
    base = getattr(user, cache_key)

    # OSS 兼容兜底（技术方案 §9.4）：OSS 存在跨组织协作者（ProjectMember），
    # 这类用户在当前组织没有成员记录。OSS 对其全放行；这里降级为最低角色
    # annotator（只可看/标注），比 OSS 更严但不破坏协作者流程。
    if base is None:
        base = Role.ANNOTATOR

    workspace = _resolve_workspace(obj)
    if workspace is not None and workspace.organization_id == user.active_organization_id:
        ws_role = workspace.members.filter(user=user).values_list('role', flat=True).first()
        return max_role(base, ws_role)
    return base


def role_allows(perm_name):
    allowed = ROLE_PERMISSIONS.get(perm_name, set())

    @rules.predicate
    def pred(user, obj=None):
        return effective_role(user, obj) in allowed

    return pred


def visible_user_ids(user, organization_id=None):
    """当前用户可见的用户 id 集合（按学院隔离）。

    admin+ 返回 None（全量）；manager 返回"本组织所属工作区成员 ∪ 自己"；
    其余角色仅自己。供组织成员列表 / 用户列表等按角色收口复用。
    """
    from orgext.models import WorkspaceMember

    org_id = organization_id or user.active_organization_id
    role = get_org_role(user, org_id)
    if role in (Role.OWNER, Role.ADMIN):
        return None
    if role == Role.MANAGER:
        workspace_ids = WorkspaceMember.objects.filter(user=user, workspace__organization_id=org_id).values_list(
            'workspace_id', flat=True
        )
        ids = set(
            WorkspaceMember.objects.filter(workspace_id__in=list(workspace_ids)).values_list('user_id', flat=True)
        )
        ids.add(user.id)
        return ids
    return {user.id}


def register_role_permissions():
    """把矩阵里的每个权限点绑定为角色谓词，覆盖 OSS 的 is_authenticated。"""
    from core.permissions import make_perm

    for perm_name in ROLE_PERMISSIONS:
        make_perm(perm_name, rules.is_authenticated & role_allows(perm_name), overwrite=True)
    logger.info('orgext: registered %d role-based permissions', len(ROLE_PERMISSIONS))
