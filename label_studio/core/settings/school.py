"""Production settings for the single-server school deployment.

PostgreSQL holds accounts, projects, tasks and annotation results; an S3-compatible object store
(Garage on the same host) holds uploaded files, avatars and exports. Nothing falls back to SQLite or
to permanent local media directories. Multi-tenancy comes from ``school_base``.
"""

# ruff: noqa: F403,F405

import ipaddress
import os
from urllib.parse import urlsplit

from core.settings.school_base import *


def _required_env(name):
    value = get_env(name)
    if not value or not value.strip() or value.strip() == 'change-me':
        raise ImproperlyConfigured(f'{name} must be configured for the school deployment')
    return value


def _is_loopback(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host.strip('[]')).is_loopback
    except ValueError:
        return False


SECRET_KEY = _required_env('SECRET_KEY')
if len(SECRET_KEY) < 50:
    raise ImproperlyConfigured('SECRET_KEY must contain at least 50 characters')

# --- PostgreSQL -----------------------------------------------------------------------------------

if get_env('DJANGO_DB', 'default') not in ('default', 'postgresql'):
    raise ImproperlyConfigured('The school deployment requires PostgreSQL (DJANGO_DB=postgresql)')

_pg_host = _required_env('POSTGRE_HOST')
for _name in ('POSTGRE_NAME', 'POSTGRE_USER'):
    _required_env(_name)

DJANGO_DB = DJANGO_DB_POSTGRESQL  # upstream migrations branch on this value
DATABASES = {'default': DATABASES_ALL[DJANGO_DB_POSTGRESQL].copy()}
if _pg_host.startswith('/'):
    # Unix socket on this host: PostgreSQL authenticates the OS user (peer), no password needed.
    DATABASES['default']['PASSWORD'] = get_env('POSTGRE_PASSWORD', '')
    _pg_options = {}
elif _is_loopback(_pg_host):
    _required_env('POSTGRE_PASSWORD')
    _pg_options = {}
else:
    _required_env('POSTGRE_PASSWORD')
    _pg_options = {'sslmode': 'verify-full', 'sslrootcert': _required_env('PGSSLROOTCERT')}
DATABASES['default']['OPTIONS'] = {**_pg_options, 'connect_timeout': 10}

# --- Object storage -------------------------------------------------------------------------------

if get_env('MINIO_STORAGE_ENDPOINT'):
    raise ImproperlyConfigured('Use STORAGE_TYPE=s3 and STORAGE_AWS_* rather than legacy MINIO_STORAGE_*')

if STORAGES['default']['BACKEND'] != 'core.storage.CustomS3Boto3Storage':
    raise ImproperlyConfigured('The school deployment requires object storage (STORAGE_TYPE=s3)')

for _name in ('STORAGE_AWS_BUCKET_NAME', 'STORAGE_AWS_ACCESS_KEY_ID', 'STORAGE_AWS_SECRET_ACCESS_KEY'):
    _required_env(_name)

_endpoint = urlsplit(_required_env('STORAGE_AWS_ENDPOINT_URL'))
if _endpoint.scheme not in ('http', 'https') or not _endpoint.hostname or _endpoint.username or _endpoint.password:
    raise ImproperlyConfigured('STORAGE_AWS_ENDPOINT_URL must be an http(s) URL without embedded credentials')

if _endpoint.scheme == 'https':
    _verify = get_env('STORAGE_AWS_S3_VERIFY', '')
    if _verify.lower() in ('false', '0'):
        raise ImproperlyConfigured('Object storage TLS certificate verification cannot be disabled')
    AWS_S3_VERIFY = True if _verify.lower() in ('', 'true', '1') else _verify
    AWS_S3_USE_SSL = True
elif _is_loopback(_endpoint.hostname):
    # Object storage on the same host: traffic never leaves the loopback interface.
    AWS_S3_VERIFY = None
    AWS_S3_USE_SSL = False
else:
    raise ImproperlyConfigured('Plain HTTP object storage is only allowed on the loopback interface')

AWS_S3_ADDRESSING_STYLE = get_env('STORAGE_AWS_ADDRESSING_STYLE', 'path')
AWS_S3_SIGNATURE_VERSION = get_env('STORAGE_AWS_S3_SIGNATURE_VERSION', 's3v4')
AWS_QUERYSTRING_AUTH = True
# Omit object ACLs: Garage does not implement them and the bucket itself is private.
AWS_DEFAULT_ACL = None
AWS_S3_CUSTOM_DOMAIN = None

# botocore >= 1.36 adds CRC checksum headers to every request by default; S3-compatible servers
# differ in support for them, while "when_required" works everywhere.
os.environ.setdefault('AWS_REQUEST_CHECKSUM_CALCULATION', 'when_required')
os.environ.setdefault('AWS_RESPONSE_CHECKSUM_VALIDATION', 'when_required')

# Local scratch directories may be used during conversion, but never as permanent storage.
ENABLE_LOCAL_FILES_STORAGE = False
LOCAL_FILES_SERVING_ENABLED = False
STORAGE_PERSISTENCE = True
# Django streams files after its permission check; the stock nginx X-Accel proxy is not used.
USE_NGINX_FOR_UPLOADS = False
USE_NGINX_FOR_EXPORT_DOWNLOADS = False

# --- HTTP / security ------------------------------------------------------------------------------

ALLOWED_HOSTS = get_env_list('ALLOWED_HOSTS')
if not ALLOWED_HOSTS or '*' in ALLOWED_HOSTS:
    raise ImproperlyConfigured('ALLOWED_HOSTS must explicitly list the school application hosts')
CORS_ALLOW_ALL_ORIGINS = False
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
DEBUG = False
DEBUG_PROPAGATE_EXCEPTIONS = False
