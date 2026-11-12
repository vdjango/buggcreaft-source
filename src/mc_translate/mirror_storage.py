"""Verified object storage; secrets never enter mirror settings or APIs."""
import asyncio
import base64
import errno
import hashlib
import hmac
import json
import os
import re
import threading
import tempfile
import shutil
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import quote, urlsplit


ROUTE_NAMES = ('core', 'loaders', 'mods', 'java', 'libraries', 'assets',
               'room_mods', 'room_resourcepacks', 'room_shaderpacks')
LOCAL_ROUTES = {name: {'mode': 'local', 'profile': '', 'path': name} for name in ROUTE_NAMES}
_SDK_LOCK = threading.Lock()


def profile_unit(profile):
    return 1000 ** 3 if profile.get('quota_unit') == 'GB' else 1024 ** 3


class StorageError(RuntimeError):
    pass


def _https_origin(value, *, qiniu_upload=False):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').lower()
        return (parsed.scheme == 'https' and bool(host) and not parsed.username and not parsed.password
                and parsed.path in ('', '/') and not parsed.query and not parsed.fragment
                and parsed.port in (None, 443)
                and (not qiniu_upload or host.endswith('.qiniup.com'))
                and (qiniu_upload or not host.endswith('.clouddn.com')))
    except ValueError:
        return False


def validate_cloud_settings(settings):
    profiles = settings['qiniu_profiles']
    if not isinstance(profiles, list) or len(profiles) > 64:
        raise ValueError('存储空间配置无效，最多支持 64 个空间。')
    ids = set()
    required = {'id', 'bucket', 'domain', 'prefix', 'upload_host', 'private', 'quota_gib', 'daily_upload_gib'}
    for profile in profiles:
        optional = {'name', 'provider', 'endpoint', 'region', 'addressing_style', 'quota_unit',
                    'monthly_egress_gib', 'egress_threshold', 'tls_auto', 'root', 'reserve_gib'}
        local = isinstance(profile, dict) and profile.get('provider') == 'local'
        fields = required - {'bucket', 'domain', 'upload_host'} if local else required
        if not isinstance(profile, dict) or not fields <= set(profile) or set(profile) - required - optional:
            raise ValueError('invalid cloud profile fields')
        provider = profile.get('provider', 'qiniu')
        if provider not in ('qiniu', 's3', 'rainyun', 'local'):
            raise ValueError('invalid cloud provider')
        if type(profile.get('tls_auto', False)) is not bool or (profile.get('tls_auto') and provider != 'rainyun'):
            raise ValueError('automatic TLS deployment currently requires Rainyun')
        if (not isinstance(profile['id'], str) or not re.fullmatch('[a-z][a-z0-9_-]{0,31}', profile['id'])
                or profile['id'] in ids):
            raise ValueError('invalid or duplicate qiniu profile id')
        ids.add(profile['id'])
        if not isinstance(profile.get('name', ''), str) or len(profile.get('name', '')) > 80:
            raise ValueError('空间名称必须是 80 字以内的文字。')
        if local:
            root = profile.get('root')
            if not isinstance(root, str) or not Path(root).is_absolute() or Path(root).parent == Path(root):
                raise ValueError('本地空间须配置已经挂载的绝对目录。')
            if type(profile.get('reserve_gib', 5)) is not int or not 1 <= profile.get('reserve_gib', 5) <= 100000:
                raise ValueError('invalid local free-space reserve')
        elif not isinstance(profile['bucket'], str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,62}', profile['bucket']):
            raise ValueError('invalid qiniu bucket')
        if not local and not _https_origin(profile['domain']):
            raise ValueError('cloud domain must be a valid HTTPS origin')
        if provider == 'qiniu':
            if not _https_origin(profile['upload_host'], qiniu_upload=True):
                raise ValueError('invalid Qiniu upload domain')
        elif not local and (not _https_origin(profile.get('endpoint'))
              or not isinstance(profile.get('region'), str)
              or not re.fullmatch('[a-z0-9-]{1,63}', profile['region'])
              or profile.get('addressing_style', 'path') not in ('path', 'virtual')):
            raise ValueError('invalid S3 endpoint, region or addressing style')
        if profile.get('quota_unit', 'GiB') not in ('GB', 'GiB'):
            raise ValueError('invalid cloud capacity unit')
        for field, default, low, high in [('monthly_egress_gib', 0, 0, 1000000), ('egress_threshold', 90, 10, 100)]:
            value = profile.get(field, default)
            if type(value) is not int or not low <= value <= high:
                raise ValueError('invalid cloud ' + field)
        if not isinstance(profile['prefix'], str) or not re.fullmatch('[a-z0-9][a-z0-9/_-]{0,79}', profile['prefix']) or '//' in profile['prefix'] or profile['prefix'].endswith('/'):
            raise ValueError('invalid qiniu prefix')
        if type(profile['private']) is not bool or any(type(profile[name]) is not int or not 1 <= profile[name] <= 1000000 for name in ('quota_gib', 'daily_upload_gib')):
            raise ValueError('invalid qiniu profile policy')
    routes = settings['qiniu_routes']
    if not isinstance(routes, dict) or set(routes) != set(ROUTE_NAMES):
        raise ValueError('invalid qiniu routes')
    profile_by_id = {profile['id']: profile for profile in profiles}
    for name, route in routes.items():
        label = dict(core='游戏核心', loaders='加载器', mods='模组', java='Java 运行时',
                     libraries='依赖库', assets='原版资源', room_mods='客户端模组',
                     room_resourcepacks='客户端资源包', room_shaderpacks='客户端光影')[name]
        if not isinstance(route, dict) or set(route) != {'mode', 'profile', 'path'}:
            raise ValueError('invalid qiniu route')
        if route['mode'] not in ('local', 'copy', 'cloud', 'auto') or not isinstance(route['profile'], str):
            raise ValueError('invalid qiniu route mode')
        if route['mode'] == 'auto':
            if route['profile'] or not profiles or name.startswith('room_') and not any(p['private'] for p in profiles):
                raise ValueError(f'{label}：自动存储需要至少一个可用空间，房间文件需要私有空间。')
        elif route['mode'] != 'local' and route['profile'] not in ids:
            raise ValueError(f'{label}：请选择已添加的存储空间，或改为本地存储。')
        if name.startswith('room_') and route['mode'] not in ('local', 'auto') and not profile_by_id[route['profile']]['private']:
            raise ValueError(f'{label}：房间临时文件需要私有空间，请开启目标空间的私有下载。')
        if not isinstance(route['path'], str) or not re.fullmatch('[a-z0-9][a-z0-9/_-]{0,79}', route['path']) or '//' in route['path'] or route['path'].endswith('/'):
            raise ValueError(f'{label}：对象路径须为小写字母、数字、/、_、-，最长 80 字符，不能含连续 / 或以 / 结尾。')
    if type(settings['qiniu_paused']) is not bool:
        raise ValueError('invalid qiniu pause state')
    if type(settings['qiniu_signed_url_minutes']) is not int or not 30 <= settings['qiniu_signed_url_minutes'] <= 1440:
        raise ValueError('invalid qiniu signed URL lifetime')


def credential_entry(profile_id):
    for prefix in ('TRANSLATION_CLOUD', 'TRANSLATION_QINIU'):
        path = os.environ.get(prefix + '_CREDENTIALS_FILE', '')
        try:
            data = (json.loads(Path(path).read_text(encoding='utf-8-sig')) if path else
                    json.loads(os.environ.get(prefix + '_CREDENTIALS', '{}')))
            entry = data.get(profile_id, {})
            if isinstance(entry, dict) and entry:
                return entry
        except (OSError, ValueError, AttributeError, TypeError):
            pass
    return {}


def credentials(profile_id):
    entry = credential_entry(profile_id)
    access, secret = entry.get('access_key'), entry.get('secret_key')
    return (access, secret) if isinstance(access, str) and isinstance(secret, str) and access and secret else None


def sha256_file(path):
    digest, size = hashlib.sha256(), 0
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def multipart_etag(path, part_size=1024 * 1024):
    pieces = []
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(part_size), b''):
            pieces.append(hashlib.sha1(chunk).digest())
    if not pieces:
        pieces.append(hashlib.sha1(b'').digest())
    return base64.urlsafe_b64encode(b'\x9e' + hashlib.sha1(b''.join(pieces)).digest()).decode('ascii')


class QiniuStorage:
    def __init__(self, profile, *, signed_url_minutes=240):
        pair = credentials(profile['id'])
        if not pair:
            raise StorageError('Qiniu credentials are not configured for this profile')
        try:
            import qiniu
        except ImportError as exc:
            raise StorageError('Install the qiniu optional dependency') from exc
        self.sdk, self.profile = qiniu, profile
        self.auth = qiniu.Auth(*pair)
        self.manager = qiniu.BucketManager(self.auth)
        self.signed_url_minutes = signed_url_minutes

    def key(self, route_path, digest):
        if not re.fullmatch('[a-z0-9][a-z0-9/_-]{0,79}', route_path) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('invalid cloud object identity')
        return f"{self.profile['prefix']}/{route_path}/sha256/{digest[:2]}/{digest}"

    async def stat(self, key):
        data, info = await asyncio.to_thread(self.manager.stat, self.profile['bucket'], key)
        if info.status_code == 612:
            return None
        if info.status_code != 200 or not isinstance(data, dict):
            raise StorageError(f'Kodo stat returned HTTP {info.status_code}')
        return data

    def _expected_etag(self, path):
        if path.stat().st_size > self.sdk.config.get_default('default_upload_threshold'):
            return multipart_etag(path)
        return self.sdk.etag(str(path))

    def _upload(self, path, key, digest, progress=None):
        sdk = self.sdk
        region = sdk.region.Region(up_host=self.profile['upload_host'], scheme='https')
        with _SDK_LOCK:
            sdk.config.set_default(default_zone=region, connection_timeout=120)
            size = path.stat().st_size
            token = self.auth.upload_token(self.profile['bucket'], key, 3600,
                                           {'insertOnly': 1, 'fsizeLimit': max(size, 1)})
            result, info = sdk.put_file_v2(token, key, str(path), check_crc=True,
                                            version='v2', bucket_name=self.profile['bucket'],
                                            metadata={'x-qn-meta-sha256': digest},
                                            regions=[region], part_size=1024 * 1024,
                                            progress_handler=progress)
            expected = self._expected_etag(path)
        if info.status_code == 614:
            return expected
        if info.status_code not in (200, 298) or not isinstance(result, dict) or result.get('key') != key or result.get('hash') != expected:
            raise StorageError(f'Kodo upload or ETag validation failed (HTTP {info.status_code})')
        return expected

    async def put_verified(self, path, key, digest, size, *, progress=None):
        path = Path(path)
        actual, actual_size = await asyncio.to_thread(sha256_file, path)
        if actual != digest or actual_size != size:
            raise StorageError('Local SHA-256 or size changed before cloud upload')
        existing = await self.stat(key)
        if existing:
            etag = await asyncio.to_thread(self._expected_etag, path)
            if existing.get('fsize') == size and existing.get('hash') == etag:
                return
            raise StorageError('Cloud object key already contains different bytes')
        etag = await asyncio.to_thread(self._upload, path, key, digest, progress)
        remote = await self.stat(key)
        if not remote or remote.get('fsize') != size or remote.get('hash') != etag:
            raise StorageError('Cloud object did not pass size and ETag verification')

    def download_url(self, key, *, expires_seconds=None, method='GET'):
        url = self.profile['domain'].rstrip('/') + '/' + quote(key, safe='/')
        ttl = min(self.signed_url_minutes * 60, max(1, int(expires_seconds))) if expires_seconds is not None else self.signed_url_minutes * 60
        return self.auth.private_download_url(url, expires=ttl) if self.profile['private'] else url

    async def delete(self, key):
        _, info = await asyncio.to_thread(self.manager.delete, self.profile['bucket'], key)
        if info.status_code not in (200, 612):
            raise StorageError(f'Kodo delete returned HTTP {info.status_code}')

    async def probe(self):
        key = self.profile['prefix'] + '/_connection_check_' + os.urandom(16).hex()
        return await self.stat(key) is None

    async def test(self):
        """Explicit 32-byte write, download and deletion check; never bulk-migrates."""
        payload = os.urandom(32)
        digest = hashlib.sha256(payload).hexdigest()
        key = self.profile['prefix'] + '/_connection_check_' + digest
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / digest
            path.write_bytes(payload)
            try:
                await self.put_verified(path, key, digest, len(payload))
                url = self.download_url(key)
                def fetch():
                    with urlopen(Request(url, headers={'Range': 'bytes=0-31'}), timeout=12) as response:
                        return response.read(64)
                if await asyncio.to_thread(fetch) != payload:
                    raise StorageError('Cloud test download returned different bytes')
                return True
            finally:
                await self.delete(key)


class LocalStorage(QiniuStorage):
    """One existing mounted directory; use the mirror queue, ledger and signed download route."""

    def __init__(self, profile, *, signed_url_minutes=240, signing_key, public_url):
        self.profile = profile
        try:
            self.root = Path(profile['root']).resolve(strict=True)
        except OSError:
            raise StorageError('本地存储目录不存在，请先挂载并配置目录') from None
        if not self.root.is_dir():
            raise StorageError('本地存储目录尚未挂载或不是目录')
        self.signed_url_minutes = signed_url_minutes
        self.signing_key = signing_key.encode('utf-8')
        self.public_url = public_url.rstrip('/')
        self.write_lock = threading.Lock()

    def file_path(self, key):
        if not re.fullmatch('[a-z0-9][a-z0-9/_-]{0,200}', key) or not key.startswith(self.profile['prefix'] + '/'):
            raise StorageError('Invalid local object identity')
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise StorageError('Local object leaves storage root')
        return path

    async def stat(self, key):
        def inspect():
            path = self.file_path(key)
            return {'fsize': path.stat().st_size} if path.is_file() else None
        return await asyncio.to_thread(inspect)

    async def put_verified(self, path, key, digest, size, *, progress=None):
        def copy():
            destination = self.file_path(key)
            if destination.is_file():
                if sha256_file(destination) != (digest, size):
                    raise StorageError('Existing local object has different bytes')
                return
            if shutil.disk_usage(self.root).free - size < self.profile.get('reserve_gib', 5) * 1024 ** 3:
                raise StorageError('本地空间剩余容量不足，已保留系统安全空间')
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix='.part-', dir=destination.parent)
            try:
                actual, count = hashlib.sha256(), 0
                with os.fdopen(descriptor, 'wb') as target, Path(path).open('rb') as source:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
                        actual.update(chunk)
                        count += len(chunk)
                        if count > size:
                            raise StorageError('Local source size changed during copy')
                        if progress:
                            progress(count, size)
                    target.flush()
                    os.fsync(target.fileno())
                if count != size or actual.hexdigest() != digest:
                    raise StorageError('Local object SHA-256 or size verification failed')
                with self.write_lock:
                    try:
                        os.link(temporary, destination)
                    except FileExistsError:
                        if sha256_file(destination) != (digest, size):
                            raise StorageError('Existing local object has different bytes') from None
                    except OSError as exc:
                        if exc.errno not in (errno.EPERM,errno.EXDEV,errno.ENOSYS,errno.EOPNOTSUPP):
                            raise
                        # COS/FUSE mounts may write normally but provide no hard links.
                        # One mirror owns this directory. Publish a verified temp file
                        # under the same lock, then verify the final key before indexing.
                        if destination.exists():
                            if sha256_file(destination)!=(digest,size):
                                raise StorageError('Existing local object has different bytes') from None
                        else:
                            os.rename(temporary,destination)
                            if sha256_file(destination)!=(digest,size):
                                raise StorageError('Mounted object verification failed after rename')
            except OSError as exc:
                raise StorageError('挂载目录写入失败：'+str(exc.strerror or exc)) from exc
            finally:
                Path(temporary).unlink(missing_ok=True)
        try:
            await asyncio.to_thread(copy)
        except OSError as exc:
            raise StorageError('挂载目录写入失败：'+str(exc.strerror or exc)) from exc

    def signature(self, key, expires):
        payload = f'bugg-local-download-v1\n{self.profile["id"]}\n{key}\n{expires}'
        return hmac.new(self.signing_key, payload.encode('utf-8'), hashlib.sha256).hexdigest()

    def download_url(self, key, *, expires_seconds=None, method='GET'):
        ttl = self.signed_url_minutes * 60
        if expires_seconds is not None:
            ttl = min(ttl, max(1, int(expires_seconds)))
        expires = int(time.time()) + ttl
        return (f'{self.public_url}/_storage/{self.profile["id"]}/{quote(key, safe="/")}'
                f'?expires={expires}&signature={self.signature(key, expires)}')

    def authorized_path(self, key, query):
        try:
            expires = int(query.get('expires', ''))
        except ValueError:
            raise StorageError('Invalid local download authorization') from None
        if expires < time.time() or not hmac.compare_digest(query.get('signature', ''), self.signature(key, expires)):
            raise StorageError('Local download authorization expired or invalid')
        return self.file_path(key)

    async def delete(self, key):
        try:
            await asyncio.to_thread(self.file_path(key).unlink, missing_ok=True)
        except OSError as exc:
            raise StorageError('挂载目录删除失败：'+str(exc.strerror or exc)) from exc


class S3Storage(QiniuStorage):
    """S3-compatible private buckets, including Rainyun and bucket-bound domains."""

    def __init__(self, profile, *, signed_url_minutes=240):
        pair = credentials(profile['id'])
        if not pair:
            raise StorageError('S3 credentials are not configured for this profile')
        try:
            import boto3
            from botocore.config import Config
            from boto3.s3.transfer import TransferConfig
        except ImportError as exc:
            raise StorageError('Install the s3 optional dependency') from exc
        self.profile, self.pair, self.signed_url_minutes = profile, pair, signed_url_minutes
        self.client = boto3.client('s3', endpoint_url=profile['endpoint'],
            aws_access_key_id=pair[0], aws_secret_access_key=pair[1],
            region_name=profile['region'], config=Config(signature_version='s3v4',
                s3={'addressing_style': profile.get('addressing_style', 'path')},
                connect_timeout=5, read_timeout=30, retries={'max_attempts': 3, 'mode': 'standard'},
                max_pool_connections=20,
                request_checksum_calculation='when_required', response_checksum_validation='when_required'))
        self.transfer = TransferConfig(multipart_threshold=16 * 1024 ** 2,
            multipart_chunksize=8 * 1024 ** 2, max_concurrency=2)

    @staticmethod
    def _error(action, exc):
        response = getattr(exc, 'response', None) or {}
        status = (response.get('ResponseMetadata') or {}).get('HTTPStatusCode', 'network')
        code = (response.get('Error') or {}).get('Code', type(exc).__name__)
        error = StorageError(f'S3 {action} failed (HTTP {status}, {code})')
        error.retryable = status == 'network' or status in (408, 429, 500, 502, 503, 504)
        return error

    async def stat(self, key):
        try:
            data = await asyncio.to_thread(self.client.head_object, Bucket=self.profile['bucket'], Key=key)
            return dict(size=data['ContentLength'], etag=data.get('ETag', '').strip('"'),
                        sha256=data.get('Metadata', {}).get('sha256'))
        except Exception as exc:
            response = getattr(exc, 'response', None) or {}
            if (response.get('ResponseMetadata') or {}).get('HTTPStatusCode') == 404:
                return None
            raise self._error('stat', exc) from None

    def direct_upload(self, key, digest, size, *, expires=600):
        """Sign one PUT's key, length and actual SHA-256; never issue bucket credentials."""
        checksum = base64.b64encode(bytes.fromhex(digest)).decode('ascii')
        params = dict(Bucket=self.profile['bucket'], Key=key, ContentLength=size,
                      ContentType='application/octet-stream', ChecksumSHA256=checksum)
        return dict(method='PUT', url=self.client.generate_presigned_url('put_object',
                    Params=params, ExpiresIn=expires),
                    headers={'Content-Type': 'application/octet-stream',
                             'Content-Length': str(size), 'x-amz-checksum-sha256': checksum})

    async def verify_direct(self, key, digest, size):
        """Only the storage service's checksum counts; user metadata is not evidence."""
        try:
            remote = await asyncio.to_thread(self.client.head_object, Bucket=self.profile['bucket'],
                                            Key=key, ChecksumMode='ENABLED')
        except Exception as exc:
            raise self._error('verify direct upload', exc) from None
        checksum = base64.b64encode(bytes.fromhex(digest)).decode('ascii')
        if remote.get('ContentLength') != size or remote.get('ChecksumSHA256') != checksum:
            raise StorageError('云端文件大小或服务端 SHA-256 校验未通过；文件不会开放下载')

    def _etag(self, path):
        if path.stat().st_size < self.transfer.multipart_threshold:
            with path.open('rb') as stream:
                return hashlib.file_digest(stream, 'md5').hexdigest()
        parts = []
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(self.transfer.multipart_chunksize), b''):
                parts.append(hashlib.md5(chunk).digest())
        return hashlib.md5(b''.join(parts)).hexdigest() + '-' + str(len(parts))

    def _fingerprint(self, path):
        """Compute SHA-256 and the S3 ETag in one disk pass."""
        before = path.stat()
        sha256, md5 = hashlib.sha256(), hashlib.md5()
        pieces, size = [], 0
        multipart = before.st_size >= self.transfer.multipart_threshold
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(self.transfer.multipart_chunksize), b''):
                sha256.update(chunk)
                size += len(chunk)
                if multipart:
                    pieces.append(hashlib.md5(chunk).digest())
                else:
                    md5.update(chunk)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise StorageError('Local file changed during S3 verification')
        etag = hashlib.md5(b''.join(pieces)).hexdigest()+'-'+str(len(pieces)) if multipart else md5.hexdigest()
        return sha256.hexdigest(), size, etag

    async def put_verified(self, path, key, digest, size, *, progress=None):
        path = Path(path)
        actual, actual_size, etag = await asyncio.to_thread(self._fingerprint, path)
        if (actual, actual_size) != (digest, size):
            raise StorageError('Local SHA-256 or size changed before S3 upload')
        def matches(remote):
            return remote and remote['size'] == size and remote['sha256'] == digest and remote['etag'] == etag
        remote = await self.stat(key)
        if remote:
            if matches(remote):
                return
            raise StorageError('S3 object key already contains different bytes')
        uploaded, lock = 0, threading.Lock()
        def callback(amount):
            nonlocal uploaded
            with lock:
                uploaded += amount
                if progress:
                    progress(uploaded, size)
        try:
            await asyncio.to_thread(self.client.upload_file, str(path), self.profile['bucket'], key,
                ExtraArgs={'Metadata': {'sha256': digest}, 'ContentType': 'application/octet-stream'},
                Config=self.transfer, Callback=callback)
        except Exception as exc:
            raise self._error('upload', exc) from None
        if not matches(await self.stat(key)):
            raise StorageError('S3 object did not pass SHA-256 metadata, size and ETag verification')

    def download_url(self, key, *, expires_seconds=None, method='GET'):
        ttl = min(self.signed_url_minutes * 60, max(1, int(expires_seconds))) if expires_seconds is not None else self.signed_url_minutes * 60
        if self.profile['domain'].rstrip('/') == self.profile['endpoint'].rstrip('/'):
            return self.client.generate_presigned_url('get_object',
                Params={'Bucket': self.profile['bucket'], 'Key': key}, ExpiresIn=ttl, HttpMethod=method)
        # This domain must be bound to the bucket. Sign its real host and path;
        # replacing the API endpoint after signing would invalidate SigV4.
        url = self.profile['domain'].rstrip('/') + '/' + quote(key, safe='/')
        if not self.profile['private']:
            return url
        from botocore.auth import S3SigV4QueryAuth
        from botocore.awsrequest import AWSRequest
        from botocore.credentials import Credentials
        request = AWSRequest(method=method, url=url)
        S3SigV4QueryAuth(Credentials(*self.pair), 's3', self.profile['region'], expires=ttl).add_auth(request)
        return request.url

    def endpoint_download_url(self, key, *, expires_seconds=None, method='GET'):
        """Sign the API endpoint itself; never rewrite the host of a signed URL."""
        ttl = min(self.signed_url_minutes * 60, max(1, int(expires_seconds))) if expires_seconds is not None else self.signed_url_minutes * 60
        return self.client.generate_presigned_url('get_object',
            Params={'Bucket': self.profile['bucket'], 'Key': key}, ExpiresIn=ttl, HttpMethod=method)

    async def delete(self, key):
        try:
            await asyncio.to_thread(self.client.delete_object, Bucket=self.profile['bucket'], Key=key)
        except Exception as exc:
            raise self._error('delete', exc) from None
