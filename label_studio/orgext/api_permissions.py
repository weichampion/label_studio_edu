"""视图级 RBAC 闸门。

OSS 里各 API 视图声明了 `permission_required`（str 或 ViewClassPermission），
但开源版没有任何 DRF 权限类去消费它（权限点全被绑成 is_authenticated，检查形同虚设）。
本类读取该声明并走 django-rules（已被 orgext 覆写为角色谓词）做校验，
加入 settings.REST_FRAMEWORK['DEFAULT_PERMISSION_CLASSES'] 后全局生效。

未声明 permission_required 的视图保持 OSS 行为（仅 IsAuthenticated）。
"""
from rest_framework import mixins
from rest_framework.permissions import BasePermission

# 只有这几种 DRF mixin，在对应 HTTP 方法下才保证会调用 self.get_object()
# （从而触发 check_object_permissions() → has_object_permission()）。
# 其它情况（创建/列表、或我们自己写的绕过 get_object() 的自定义 APIView，
# 比如 orgext.api 里那些用 get_object_or_404 直接取 project/workspace 的视图）
# 没有这个保证，必须在 has_permission() 这一步就做严格判断，否则会出现
# "在部门 A 被提升的 manager 越权操作部门 B 对象" 这种漏洞。
_DETAIL_MIXIN_BY_METHOD = {
    'GET': mixins.RetrieveModelMixin,
    'PUT': mixins.UpdateModelMixin,
    'PATCH': mixins.UpdateModelMixin,
    'DELETE': mixins.DestroyModelMixin,
}


class RolePermission(BasePermission):
    def _required_perm(self, request, view):
        perm = getattr(view, 'permission_required', None)
        if perm is None or isinstance(perm, str):
            return perm
        # ViewClassPermission。注意不能用 isinstance：内核模块可能同时以
        # core.permissions 与 label_studio.core.permissions 两个路径被加载，
        # 跨路径的类对象不相等，这里按 HTTP 方法名取字段做鸭子类型判断。
        return getattr(perm, request.method, None)

    def _defers_to_object_check(self, request, view):
        mixin_cls = _DETAIL_MIXIN_BY_METHOD.get(request.method)
        if mixin_cls is None or not isinstance(view, mixin_cls):
            return False
        lookup = getattr(view, 'lookup_url_kwarg', None) or getattr(view, 'lookup_field', 'pk')
        return lookup in getattr(view, 'kwargs', {})

    def has_permission(self, request, view):
        perm = self._required_perm(request, view)
        if not perm:
            return True
        if self._defers_to_object_check(request, view):
            # 这里还没有具体对象（比如 PATCH /api/projects/2/ 还没 fetch 到
            # project 2），按组织级角色判断会误杀「组织角色低但部门角色被
            # 提升」的用户（技术方案 §4.4）。视图确定会调用 get_object()，
            # 精确判断交给下面的 has_object_permission()。
            return True
        return request.user.has_perm(perm)

    def has_object_permission(self, request, view, obj):
        perm = self._required_perm(request, view)
        if not perm:
            return True
        return request.user.has_perm(perm, obj)
