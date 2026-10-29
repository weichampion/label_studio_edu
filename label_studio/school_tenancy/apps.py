from django.apps import AppConfig


class SchoolTenancyConfig(AppConfig):
    name = 'school_tenancy'
    verbose_name = '学校多租户'
    default_auto_field = 'django.db.models.AutoField'

    def ready(self):
        from school_tenancy import signals  # noqa: F401
        from school_tenancy.permissions import (
            install_api_permission_enforcement,
            install_related_field_scoping,
            register_role_permissions,
        )

        register_role_permissions()
        install_api_permission_enforcement()
        install_related_field_scoping()
