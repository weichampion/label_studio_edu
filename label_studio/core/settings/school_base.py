"""Common settings for the school edition: one college = one tenant (Organization).

Imported by ``school_local`` (SQLite + local files, development) and ``school`` (PostgreSQL + object
storage, production). The community ``label_studio`` settings are not imported because they enable a
third-party Sentry DSN during import.
"""

# ruff: noqa: F403,F405

import json

from core.settings.base import *

SENTRY_DSN = None
FRONTEND_SENTRY_DSN = None
COLLECT_ANALYTICS = False
LATEST_VERSION_CHECK = False

INSTALLED_APPS = [*INSTALLED_APPS, 'school_tenancy']

# The tenant middleware replaces the community DummyGetSessionMiddleware, which assigns every user to
# the first organization in the database.
MIDDLEWARE = [
    *MIDDLEWARE,
    'school_tenancy.middleware.TenantMiddleware',
    'core.middleware.UpdateLastActivityMiddleware',
]
if INACTIVITY_SESSION_TIMEOUT_ENABLED:
    MIDDLEWARE.append('core.middleware.InactivitySessionTimeoutMiddleWare')

# orgext is kept installed only for its legacy migration graph; these are the OSS mixins.
ORGANIZATION_MIXIN = 'organizations.mixins.OrganizationMixin'
USER_MIXIN = 'users.mixins.UserMixin'
ORGANIZATION_MEMBER_MIXIN = 'organizations.mixins.OrganizationMemberMixin'
PROJECT_MIXIN = 'school_tenancy.mixins.SchoolProjectMixin'
PROJECT_QUERYSET_SCOPE = 'school_tenancy.access.scope_projects'
USER_QUERYSET_SCOPE = 'school_tenancy.access.scope_users'
TEMPLATE_LIST_SCOPE = 'school_tenancy.template_groups.scope_templates'
SAVE_USER = 'school_tenancy.signup.save_user'
MEMBER_PERM = 'school_tenancy.permissions.CollegeAdminWritePermission'
WHOAMI_USER_SERIALIZER = 'school_tenancy.serializers.SchoolWhoAmISerializer'
DATA_MANAGER_CHECK_ACTION_PERMISSION = 'school_tenancy.permissions.check_action_permission'

# Accounts are created by college administrators, LDAP (later) or a college invite link.
DISABLE_SIGNUP_WITHOUT_LINK = True

SESSION_ENGINE = 'django.contrib.sessions.backends.db'
ADD_DEFAULT_ML_BACKENDS = False
LOGGING['root']['level'] = get_env('LOG_LEVEL', 'WARNING')
DEBUG = get_bool_env('DEBUG', False)
DEBUG_PROPAGATE_EXCEPTIONS = get_bool_env('DEBUG_PROPAGATE_EXCEPTIONS', False)
EDITOR_KEYMAP = json.dumps(get_env('EDITOR_KEYMAP'))

# Same feature-flag behaviour as the community edition (all flags on, offline, optional file).
FEATURE_FLAGS_DEFAULT_VALUE = True
FEATURE_FLAGS_OFFLINE = get_bool_env('FEATURE_FLAGS_OFFLINE', True)
FEATURE_FLAGS_FILE = get_env('FEATURE_FLAGS_FILE', 'feature_flags.json')
try:
    from core.utils.io import find_node

    find_node('label_studio', FEATURE_FLAGS_FILE, 'file')
    FEATURE_FLAGS_FROM_FILE = True
except IOError:
    FEATURE_FLAGS_FROM_FILE = False
STORAGE_PERSISTENCE = get_bool_env('STORAGE_PERSISTENCE', True)
