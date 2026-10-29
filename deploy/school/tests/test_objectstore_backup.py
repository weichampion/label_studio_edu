"""Tests for bin/objectstore_backup.py against an in-process S3 emulator (moto)."""

import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

try:
    from moto import mock_aws
except ImportError:  # moto 4.x, the version locked by the upstream project
    from moto import mock_s3 as mock_aws

TOOL = Path(__file__).resolve().parents[1] / 'bin' / 'objectstore_backup.py'
ENDPOINT = 'http://127.0.0.1:3900'
BUCKET = 'label-studio'
ENV = {
    'STORAGE_AWS_ENDPOINT_URL': ENDPOINT,
    'STORAGE_AWS_REGION_NAME': 'us-east-1',
    'STORAGE_AWS_BUCKET_NAME': BUCKET,
    'STORAGE_AWS_ACCESS_KEY_ID': 'test-only',
    'STORAGE_AWS_SECRET_ACCESS_KEY': 'test-only-secret',
    'MOTO_S3_CUSTOM_ENDPOINTS': ENDPOINT,
}


def load_tool():
    spec = importlib.util.spec_from_file_location('objectstore_backup', TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ObjectstoreBackupTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, ENV)
        self.env.start()
        self.aws = mock_aws()
        self.aws.start()
        self.tool = load_tool()
        self.client = self.tool.make_client()
        self.client.create_bucket(Bucket=BUCKET)
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self.tmp.name) / 'objects' / BUCKET

    def tearDown(self):
        self.tmp.cleanup()
        self.aws.stop()
        self.env.stop()

    def put(self, key, body):
        self.client.put_object(Bucket=BUCKET, Key=key, Body=body)

    def run_main(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = self.tool.main(list(argv))
        return code, json.loads(out.getvalue())

    def test_mirror_copies_objects_and_skips_unchanged(self):
        self.put('upload/1/a1b2c3d4-作业图片.jpg', b'image-bytes')
        self.put('export/1/result.json', b'[{"result": []}]')
        self.put('avatars/x.png', b'png')

        code, summary = self.run_main('mirror', '--dest', str(self.dest))
        self.assertEqual(code, 0, summary)
        self.assertEqual(summary['copied'], 3)
        self.assertEqual((self.dest / 'upload/1/a1b2c3d4-作业图片.jpg').read_bytes(), b'image-bytes')
        self.assertEqual((self.dest / 'export/1/result.json').read_bytes(), b'[{"result": []}]')

        code, summary = self.run_main('mirror', '--dest', str(self.dest))
        self.assertEqual(code, 0)
        self.assertEqual((summary['copied'], summary['unchanged']), (0, 3))

    def test_mirror_refreshes_changed_objects_and_keeps_deleted_ones(self):
        self.put('upload/1/a.txt', b'v1')
        self.put('upload/1/b.txt', b'keep me')
        self.run_main('mirror', '--dest', str(self.dest))
        local = self.dest / 'upload/1/a.txt'
        os.utime(local, (time.time() - 3600, time.time() - 3600))

        self.put('upload/1/a.txt', b'v2-longer')
        self.client.delete_object(Bucket=BUCKET, Key='upload/1/b.txt')
        code, summary = self.run_main('mirror', '--dest', str(self.dest))

        self.assertEqual(code, 0, summary)
        self.assertEqual(summary['copied'], 1)
        self.assertEqual(local.read_bytes(), b'v2-longer')
        self.assertEqual((self.dest / 'upload/1/b.txt').read_bytes(), b'keep me')

    def test_unsafe_keys_are_rejected_without_writing_outside_destination(self):
        self.put('upload/1/ok.txt', b'ok')
        for key in ('../escape.txt', 'upload/../../escape2.txt', 'upload//double.txt', 'upload/.x.partial-1'):
            self.put(key, b'evil')

        code, summary = self.run_main('mirror', '--dest', str(self.dest))

        self.assertEqual(code, 1)
        self.assertEqual(summary['copied'], 1)
        self.assertEqual(len(summary['errors']), 4)
        written = {p.relative_to(Path(self.tmp.name)).as_posix() for p in Path(self.tmp.name).rglob('*') if p.is_file()}
        self.assertEqual(written, {f'objects/{BUCKET}/upload/1/ok.txt'})

    def test_restore_uploads_missing_objects_and_reports_conflicts(self):
        self.put('upload/1/lost.jpg', b'lost-image')
        self.put('upload/1/same.txt', b'same')
        self.put('upload/1/changed.txt', b'old')
        self.run_main('mirror', '--dest', str(self.dest))
        (self.dest / 'upload/1/.ignored.partial-99').write_bytes(b'partial download')

        self.client.delete_object(Bucket=BUCKET, Key='upload/1/lost.jpg')
        self.put('upload/1/changed.txt', b'newer and different')

        code, summary = self.run_main('restore', '--src', str(self.dest), '--dry-run')
        self.assertEqual(code, 1)
        self.assertEqual(summary['uploaded'], 1)
        with self.assertRaises(self.client.exceptions.ClientError):
            self.client.head_object(Bucket=BUCKET, Key='upload/1/lost.jpg')

        code, summary = self.run_main('restore', '--src', str(self.dest))
        self.assertEqual(code, 1)
        self.assertEqual(summary['uploaded'], 1)
        self.assertEqual(summary['unchanged'], 1)
        self.assertEqual([c['key'] for c in summary['conflicts']], ['upload/1/changed.txt'])
        restored = self.client.get_object(Bucket=BUCKET, Key='upload/1/lost.jpg')
        self.assertEqual(restored['Body'].read(), b'lost-image')
        self.assertEqual(restored['ContentType'], 'image/jpeg')
        self.assertEqual(
            self.client.get_object(Bucket=BUCKET, Key='upload/1/changed.txt')['Body'].read(), b'newer and different'
        )

        code, summary = self.run_main('restore', '--src', str(self.dest), '--overwrite')
        self.assertEqual(code, 0, summary)
        self.assertEqual(self.client.get_object(Bucket=BUCKET, Key='upload/1/changed.txt')['Body'].read(), b'old')

    def test_disabling_tls_verification_is_refused(self):
        with mock.patch.dict(os.environ, {'STORAGE_AWS_S3_VERIFY': 'false'}):
            with self.assertRaises(SystemExit):
                self.tool.make_client()


if __name__ == '__main__':
    unittest.main()
