"""Storage configuration tests, independent of database migrations and live campus services."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
VALID_ENV = {
    'DJANGO_SETTINGS_MODULE': 'core.settings.school',
    'SECRET_KEY': 'school-settings-test-' * 4,
    'DJANGO_DB': 'postgresql',
    'POSTGRE_HOST': 'postgres.example.edu.cn',
    'POSTGRE_NAME': 'school_test',
    'POSTGRE_USER': 'school_test',
    'POSTGRE_PASSWORD': 'test-only-not-a-real-password',
    'PGSSLROOTCERT': 'test-ca.pem',
    'STORAGE_TYPE': 's3',
    'STORAGE_AWS_BUCKET_NAME': 'school-test',
    'STORAGE_AWS_ACCESS_KEY_ID': 'test-only',
    'STORAGE_AWS_SECRET_ACCESS_KEY': 'test-only',
    'STORAGE_AWS_ENDPOINT_URL': 'https://s3.example.edu.cn',
    'STORAGE_AWS_REGION_NAME': 'us-east-1',
    'ALLOWED_HOSTS': 'label.example.edu.cn',
    'LABEL_STUDIO_HOST': 'https://label.example.edu.cn',
    'PYTHONPATH': os.pathsep.join((str(REPO), str(REPO / 'label_studio'))),
}

SNAPSHOT = """
import json
from django.conf import settings as s
print(json.dumps({
    'engine': s.DATABASES['default']['ENGINE'],
    'sslmode': s.DATABASES['default']['OPTIONS'].get('sslmode'),
    'storage': s.STORAGES['default']['BACKEND'],
    'sentry': s.SENTRY_DSN,
    'frontend_sentry': s.FRONTEND_SENTRY_DSN,
    'analytics': s.COLLECT_ANALYTICS,
    'versions': s.LATEST_VERSION_CHECK,
    'local_files': s.LOCAL_FILES_SERVING_ENABLED,
    'local_storage': s.ENABLE_LOCAL_FILES_STORAGE,
    'signed': s.AWS_QUERYSTRING_AUTH,
    'sessions': s.SESSION_ENGINE,
    'cors_all': s.CORS_ALLOW_ALL_ORIGINS,
    'verify': s.AWS_S3_VERIFY,
    'tenant_middleware': 'school_tenancy.middleware.TenantMiddleware' in s.MIDDLEWARE,
    'dummy_org_middleware': 'organizations.middleware.DummyGetSessionMiddleware' in s.MIDDLEWARE,
    'project_mixin': s.PROJECT_MIXIN,
}))
"""


class SchoolSettingsTests(unittest.TestCase):
    def run_settings(self, code=SNAPSHOT, overrides=None):
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(('LABEL_STUDIO_', 'HEARTEX_', 'STORAGE_', 'MINIO_', 'AWS_', 'POSTGRE_', 'PGSSL'))
        }
        env.update(VALID_ENV)
        for key, value in (overrides or {}).items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        with tempfile.TemporaryDirectory() as directory:
            env['LABEL_STUDIO_BASE_DATA_DIR'] = directory
            return subprocess.run(
                [sys.executable, '-c', code],
                cwd=REPO / 'label_studio',
                env=env,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )

    def test_external_storage_defaults(self):
        result = self.run_settings()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout.splitlines()[-1]),
            {
                'engine': 'django.db.backends.postgresql',
                'sslmode': 'verify-full',
                'storage': 'core.storage.CustomS3Boto3Storage',
                'sentry': None,
                'frontend_sentry': None,
                'analytics': False,
                'versions': False,
                'local_files': False,
                'local_storage': False,
                'signed': True,
                'sessions': 'django.contrib.sessions.backends.db',
                'cors_all': False,
                'verify': True,
                'tenant_middleware': True,
                'dummy_org_middleware': False,
                'project_mixin': 'school_tenancy.mixins.SchoolProjectMixin',
            },
        )

    def test_single_server_mode_uses_unix_socket_and_loopback_object_storage(self):
        result = self.run_settings(
            code=SNAPSHOT.replace("print(json.dumps({", "print(json.dumps({'host': s.DATABASES['default']['HOST'], 'ssl': s.AWS_S3_USE_SSL,"),
            overrides={
                'POSTGRE_HOST': '/var/run/postgresql',
                'POSTGRE_PASSWORD': None,
                'PGSSLROOTCERT': None,
                'STORAGE_AWS_ENDPOINT_URL': 'http://127.0.0.1:3900',
                'STORAGE_AWS_REGION_NAME': 'garage',
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(snapshot['host'], '/var/run/postgresql')
        self.assertIsNone(snapshot['sslmode'])
        self.assertFalse(snapshot['ssl'])
        self.assertTrue(snapshot['tenant_middleware'])

    def test_invalid_settings_fail_explicitly(self):
        cases = [
            ({'SECRET_KEY': None}, 'SECRET_KEY'),
            ({'SECRET_KEY': 'short'}, 'at least 50'),
            ({'DJANGO_DB': 'sqlite'}, 'requires PostgreSQL'),
            ({'POSTGRE_HOST': None}, 'POSTGRE_HOST'),
            ({'POSTGRE_PASSWORD': None}, 'POSTGRE_PASSWORD'),
            ({'PGSSLROOTCERT': None}, 'PGSSLROOTCERT'),
            ({'STORAGE_TYPE': 'local'}, 'requires object storage'),
            ({'STORAGE_AWS_BUCKET_NAME': None}, 'STORAGE_AWS_BUCKET_NAME'),
            ({'STORAGE_AWS_SECRET_ACCESS_KEY': None}, 'STORAGE_AWS_SECRET_ACCESS_KEY'),
            ({'STORAGE_AWS_ENDPOINT_URL': 'http://s3.example.edu.cn'}, 'only allowed on the loopback'),
            ({'POSTGRE_HOST': '127.0.0.1', 'POSTGRE_PASSWORD': None, 'PGSSLROOTCERT': None}, 'POSTGRE_PASSWORD'),
            ({'STORAGE_AWS_S3_VERIFY': 'false'}, 'verification cannot be disabled'),
            ({'MINIO_STORAGE_ENDPOINT': 'https://s3.example.edu.cn'}, 'legacy MINIO_STORAGE'),
            ({'ALLOWED_HOSTS': '*'}, 'ALLOWED_HOSTS'),
        ]
        for overrides, expected in cases:
            with self.subTest(overrides=overrides):
                result = self.run_settings(overrides=overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('ImproperlyConfigured', result.stderr)
                self.assertIn(expected, result.stderr)

    def test_storage_failures_do_not_fall_back_to_local_files(self):
        result = self.run_settings("""
import django
django.setup()
from unittest.mock import patch
from botocore.exceptions import EndpointConnectionError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from pathlib import Path
from django.conf import settings
with patch('botocore.client.BaseClient._make_api_call',
           side_effect=EndpointConnectionError(endpoint_url='https://unavailable.invalid')):
    try:
        default_storage.save('upload/1/test.txt', ContentFile(b'test'))
    except EndpointConnectionError:
        assert not any(p.is_file() for p in Path(settings.MEDIA_ROOT).rglob('*'))
        print('storage failure propagated')
    else:
        raise AssertionError('Storage failure was hidden')
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('storage failure propagated', result.stdout)

    def test_uploads_and_exports_use_private_s3(self):
        result = self.run_settings("""
import django
django.setup()
import boto3
try:
    from moto import mock_aws
except ImportError:  # moto 4.x
    from moto import mock_s3 as mock_aws
from django.conf import settings
from django.core.files.base import ContentFile
from data_import.models import FileUpload
from data_export.models import Export
from users.models import User
from pathlib import Path

with mock_aws():
    client = boto3.client('s3', endpoint_url=settings.AWS_S3_ENDPOINT_URL)
    client.create_bucket(Bucket=settings.AWS_STORAGE_BUCKET_NAME)
    for field, filename, payload in (
        (FileUpload(project_id=1).file, 'lesson.txt', b'lesson material'),
        (Export().file, 'results.json', b'[{\"result\": []}]'),
        (User().avatar, 'avatar.png', b'test avatar bytes'),
    ):
        field.save(filename, ContentFile(payload), save=False)
        remote = client.get_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=field.name)
        assert remote['Body'].read() == payload
        assert '/storage-data/uploaded/' in field.url
        signed_url = field.storage.url(field.name, storage_url=True)
        assert 'X-Amz-Signature=' in signed_url
        assert len(client.get_object_acl(Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                                         Key=field.name)['Grants']) == 1
        field.delete(save=False)
    assert not any(p.is_file() for p in Path(settings.MEDIA_ROOT).rglob('*'))
print('upload, export and avatar S3 round trips passed')
""", overrides={
            'MOTO_S3_CUSTOM_ENDPOINTS': VALID_ENV['STORAGE_AWS_ENDPOINT_URL'],
            'STORAGE_AWS_S3_SIGNATURE_VERSION': 's3v4',
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('S3 round trips passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
