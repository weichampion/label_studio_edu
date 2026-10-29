"""Local development settings for the school edition: SQLite database and local file storage.

Data lives in LABEL_STUDIO_BASE_DATA_DIR (default: the per-user Label Studio data directory).
Not for production: use ``core.settings.school`` (PostgreSQL + object storage) on the server.
"""

# ruff: noqa: F403,F405

from core.settings.school_base import *
from core.utils.secret_key import generate_secret_key_if_missing

SECRET_KEY = generate_secret_key_if_missing(BASE_DATA_DIR)

DJANGO_DB = DJANGO_DB_SQLITE  # upstream migrations branch on this value
DATABASES = {'default': DATABASES_ALL[DJANGO_DB_SQLITE]}
# File-based test database so `pytest --reuse-db` can skip the (slow) migrations on later runs
DATABASES['default']['TEST'] = {'NAME': os.path.join(BASE_DATA_DIR, 'test_school_local.sqlite3')}
SESSION_COOKIE_SECURE = get_bool_env('SESSION_COOKIE_SECURE', False)
