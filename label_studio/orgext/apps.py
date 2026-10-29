"""Legacy orgext schema app.

The runtime features (Workspaces, Assignments and the old role matrix) have been replaced by
``school_tenancy``. The app remains installed only so its historical migrations and the
``Project.workspace`` / ``OrganizationMember.role`` columns keep a valid migration graph and
existing databases stay readable.
"""
from django.apps import AppConfig


class OrgextConfig(AppConfig):
    name = 'orgext'
    verbose_name = 'Enterprise extensions (orgext)'
    default_auto_field = 'django.db.models.AutoField'

    def ready(self):
        # orgext has been replaced by school_tenancy. Keep the app installed only so its
        # historical migrations and tables remain valid; do not register role predicates
        # or assignment signals, which would otherwise override the school RBAC.
        return
