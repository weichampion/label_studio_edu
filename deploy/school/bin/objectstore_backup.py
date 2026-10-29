#!/usr/bin/env python3
"""Back up and restore the Label Studio object storage bucket.

mirror  : copy every object to a local directory on the backup disk. Copy-only: objects deleted
          from the bucket are kept in the backup, so older database dumps stay restorable.
restore : upload objects from that directory that are missing in the bucket. Objects that exist
          with a different size are reported as conflicts and left untouched unless --overwrite.

Connection settings come from the same environment as the application (STORAGE_AWS_*).
"""

import argparse
import json
import mimetypes
import os
import sys
from pathlib import Path, PurePosixPath

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

PARTIAL_MARKER = '.partial-'


def make_client():
    endpoint = os.environ['STORAGE_AWS_ENDPOINT_URL']
    verify = os.environ.get('STORAGE_AWS_S3_VERIFY', '')
    if verify.lower() in ('', 'true', '1'):
        verify = True
    elif verify.lower() in ('false', '0'):
        raise SystemExit('Object storage TLS certificate verification cannot be disabled')
    return boto3.client(
        's3',
        endpoint_url=endpoint,
        region_name=os.environ.get('STORAGE_AWS_REGION_NAME') or 'us-east-1',
        aws_access_key_id=os.environ['STORAGE_AWS_ACCESS_KEY_ID'],
        aws_secret_access_key=os.environ['STORAGE_AWS_SECRET_ACCESS_KEY'],
        verify=verify if endpoint.startswith('https://') else None,
        config=Config(
            signature_version='s3v4',
            s3={'addressing_style': 'path'},
            request_checksum_calculation='when_required',
            response_checksum_validation='when_required',
            retries={'max_attempts': 5, 'mode': 'standard'},
        ),
    )


def local_path_for_key(root: Path, key: str) -> Path:
    """Map an object key to a path below root, rejecting keys that could escape it."""
    if not key or '\x00' in key or '\\' in key or key.startswith('/'):
        raise ValueError(f'unsafe object key: {key!r}')
    parts = key.split('/')
    if any(part in ('', '.', '..') for part in parts) or parts[-1].startswith('.') and PARTIAL_MARKER in parts[-1]:
        raise ValueError(f'unsafe object key: {key!r}')
    return root.joinpath(*PurePosixPath(key).parts)


def mirror(client, bucket: str, dest: Path) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    stats = {'copied': 0, 'unchanged': 0, 'errors': []}
    paginator = client.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get('Contents', []):
            key, size = obj['Key'], obj['Size']
            if key.endswith('/') and size == 0:
                stats['unchanged'] += 1
                continue
            modified = obj['LastModified'].timestamp()
            tmp = None
            try:
                path = local_path_for_key(dest, key)
                if path.is_file():
                    st = path.stat()
                    if st.st_size == size and st.st_mtime >= modified - 1:
                        stats['unchanged'] += 1
                        continue
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(f'.{path.name}{PARTIAL_MARKER}{os.getpid()}')
                client.download_file(bucket, key, str(tmp))
                if tmp.stat().st_size != size:
                    raise OSError(f'size mismatch after download: expected {size}, got {tmp.stat().st_size}')
                os.replace(tmp, path)
                os.utime(path, (modified, modified))
                stats['copied'] += 1
            except (ValueError, OSError, ClientError, BotoCoreError) as exc:
                stats['errors'].append({'key': key, 'error': str(exc)})
            finally:
                if tmp is not None and tmp.exists():
                    tmp.unlink()
    return stats


def restore(client, bucket: str, src: Path, overwrite=False, dry_run=False) -> dict:
    stats = {'uploaded': 0, 'unchanged': 0, 'conflicts': [], 'errors': []}
    for path in sorted(p for p in src.rglob('*') if p.is_file()):
        if path.name.startswith('.') and PARTIAL_MARKER in path.name:
            continue
        key = path.relative_to(src).as_posix()
        try:
            local_path_for_key(src, key)
            size = path.stat().st_size
            try:
                remote_size = client.head_object(Bucket=bucket, Key=key)['ContentLength']
            except ClientError as exc:
                if exc.response.get('Error', {}).get('Code') not in ('404', 'NoSuchKey', 'NotFound'):
                    raise
                remote_size = None
            if remote_size == size:
                stats['unchanged'] += 1
                continue
            if remote_size is not None and not overwrite:
                stats['conflicts'].append({'key': key, 'backup_size': size, 'bucket_size': remote_size})
                continue
            if not dry_run:
                content_type = mimetypes.guess_type(key)[0]
                extra = {'ContentType': content_type} if content_type else None
                client.upload_file(str(path), bucket, key, ExtraArgs=extra)
            stats['uploaded'] += 1
        except (ValueError, OSError, ClientError, BotoCoreError) as exc:
            stats['errors'].append({'key': key, 'error': str(exc)})
    return stats


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p_mirror = sub.add_parser('mirror', help='copy bucket objects to the backup directory')
    p_mirror.add_argument('--dest', type=Path, required=True)
    p_restore = sub.add_parser('restore', help='upload objects missing from the bucket')
    p_restore.add_argument('--src', type=Path, required=True)
    p_restore.add_argument('--overwrite', action='store_true', help='replace bucket objects that differ')
    p_restore.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)

    bucket = os.environ['STORAGE_AWS_BUCKET_NAME']
    client = make_client()
    if args.command == 'mirror':
        stats = mirror(client, bucket, args.dest)
        failed = bool(stats['errors'])
    else:
        if not args.src.is_dir():
            parser.error(f'{args.src} is not a directory')
        stats = restore(client, bucket, args.src, overwrite=args.overwrite, dry_run=args.dry_run)
        failed = bool(stats['errors'] or stats['conflicts'])
    print(json.dumps({'command': args.command, 'bucket': bucket, **stats}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
