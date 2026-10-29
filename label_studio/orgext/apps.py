"""orgext — 教育企业版扩展 app（任务分配 / 角色权限 / 多租户部门）。

本 app 通过 core.settings 里的 *_MIXIN 与 load_func 扩展点接入 Label Studio 内核，
避免直接修改内核代码，便于跟随上游升级。
"""
from django.apps import AppConfig


class OrgextConfig(AppConfig):
    name = 'orgext'
    verbose_name = 'Enterprise extensions (orgext)'
    default_auto_field = 'django.db.models.AutoField'

    def ready(self):
        from orgext.permissions import register_role_permissions

        register_role_permissions()

        import orgext.signals  # noqa: F401
