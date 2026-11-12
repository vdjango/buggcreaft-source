"""Small management bridge. Bulk bytes never pass through the translation app."""
import asyncio
import re
from pathlib import Path

from aiohttp import ClientError, ClientSession, ClientTimeout, TCPConnector, web

from .mirror import read_settings
from .mirror_storage import LOCAL_ROUTES
from .mirror_sources import source_status


class MirrorControl:
    def __init__(self, api):
        self.api = api
        self.config = api.config
        self.http = None
        self.commands = None

    async def start(self):
        self.http = ClientSession(timeout=ClientTimeout(total=3), connector=TCPConnector(limit=2))
        self.commands = ClientSession(timeout=ClientTimeout(total=30), connector=TCPConnector(limit=2))

    async def close(self):
        if self.http:
            await self.http.close()
        if self.commands:
            await self.commands.close()

    async def call(self, body=None, *, binary=False):
        token_path = Path(self.config.mirror_root) / 'control-token'
        if not token_path.exists() or not self.http:
            return None
        try:
            token = await asyncio.to_thread(token_path.read_text, encoding='ascii')
            timeout = (80 if body and body.get('action') == 'cloud_test' else
                       8 if body and body.get('action') == 'inventory_files' else
                       30 if body and body.get('action') in ('settings', 'cloud_migrate', 'cloud_probe', 'room_catalog_import', 'cloud_cleanup_preview', 'cloud_cleanup_local') else 3)
            async with (self.commands if body else self.http).request('POST' if body else 'GET',
                                         f'http://127.0.0.1:{self.config.mirror_control_port or self.config.mirror_port}/_internal/control',
                                         headers={'X-Mirror-Control': token.strip()}, json=body,
                                         timeout=ClientTimeout(total=timeout)) as response:
                if response.status != 200:
                    if response.status == 409:
                        raise web.HTTPConflict(text='mirror_job_running')
                    if response.status == 507:
                        raise ValueError('cache quota is below active downloads or protected versions')
                    if response.status in (500,502,503,504):
                        raise ValueError('源站未能完成操作（HTTP '+str(response.status)+'），请查看存储诊断或稍后重试；已保存的配置保留。')
                    if response.status == 400:
                        raise ValueError((await response.text())[:200])
                    if response.status == 404:
                        raise web.HTTPNotFound(text=(await response.text())[:200])
                    return None
                return await response.read() if binary else await response.json()
        except (ClientError, asyncio.TimeoutError, OSError):
            return None

    async def admin(self, request):
        self.api.require_admin(request)
        if request.method == 'POST':
            # Exact verified file mappings can exceed the normal small API body.
            data = await request.clone(client_max_size=2*1024*1024).json()
            if not isinstance(data, dict):
                raise ValueError('expected a JSON object')
            if 'action' in data:
                if data['action'] in ('dependency_status', 'dependency_retry'):
                    if set(data) != {'action'}:
                        raise ValueError('invalid dependency operation')
                    result = await self.call(data)
                    return web.json_response(result or {'configured': False, 'states': {}, 'items': []})
                if data['action'] in ('runtime_files', 'runtime_mark', 'runtime_prepare'):
                    allowed = {'action','offset','family','query'} if data['action']=='runtime_files' else {'action','family','version','blocked','reason'} if data['action']=='runtime_mark' else {'action'}
                    if set(data)-allowed:
                        raise ValueError('invalid runtime operation')
                    result = await self.call(data)
                    return web.json_response(result if result is not None else dict(code='mirror_offline'),
                                             status=200 if result is not None else 503, headers={'Cache-Control':'no-store'})
                if data['action'] == 'catalog_credentials':
                    result = await self.call(dict(action='catalog_credentials', curseforge_key=self.config.curseforge_key))
                    if result is None:
                        return web.json_response(dict(code='mirror_offline'), status=503)
                    return web.json_response(result, headers={'Cache-Control': 'no-store'})
                if data['action'] not in ('inventory', 'inventory_files', 'prefetch', 'resource_prefetch', 'resource_prefetch_start', 'resource_prefetch_pause', 'resource_files', 'retry_failures', 'cancel', 'refresh', 'source_scan', 'source_files', 'source_preview', 'source_cleanup', 'quality_probe', 'quality_history', 'content_files', 'cache_files', 'room_files', 'room_catalog_import', 'cloud_migrate', 'cloud_jobs', 'cloud_retry', 'cloud_probe', 'cloud_test', 'cloud_tls_issue', 'cloud_cleanup_preview', 'cloud_cleanup_local'):
                    raise ValueError('invalid mirror action')
                arguments = dict(action=data['action'])
                if data['action'] == 'resource_files':
                    arguments['offset'] = data.get('offset', 0)
                if data['action'] == 'inventory_files':
                    arguments.update({key: data[key] for key in ('query', 'category', 'handling', 'offset', 'summary_only') if key in data})
                if data['action'] == 'content_files':
                    arguments.update({key:data[key] for key in ('query','provider','state','cursor') if key in data})
                if data['action'] == 'cloud_cleanup_preview':
                    arguments['after'] = data.get('after', '')
                if data['action'] == 'cloud_cleanup_local':
                    arguments['digests'] = data.get('digests')
                if data['action'] == 'cloud_jobs':
                    arguments.update({key:data[key] for key in ('route', 'state', 'offset') if key in data})
                if data['action'] == 'room_catalog_import':
                    if data.get('mode', 'merge') not in ('merge', 'replace') or not isinstance(data.get('files'), list) or len(data['files']) > 3000:
                        raise ValueError('文件目录无效，单次最多导入 3000 项。')
                    arguments.update(mode=data.get('mode', 'merge'), files=data['files'])
                if data['action'] in ('cache_files', 'room_files'):
                    from .mirror_observation import page_arguments
                    page_arguments(data)
                    arguments.update({key: data[key] for key in ('query', 'category', 'cursor', 'storage') if key in data})
                if data['action'] in ('quality_probe', 'quality_history'):
                    source = str(data.get('source', ''))
                    if len(source) > 64:
                        raise ValueError('invalid source')
                    arguments['source'] = source or None
                if data['action']=='source_files':
                    source,offset = str(data.get('source','')),int(data.get('offset',0))
                    if len(source)>64 or not 0<=offset<=100000:
                        raise ValueError('invalid source pagination')
                    arguments.update(source=source,offset=offset)
                if data['action'] == 'cloud_migrate':
                    route, limit = str(data.get('route', '')), data.get('limit', 20)
                    if route not in LOCAL_ROUTES or type(limit) is not int or not 1 <= limit <= 200:
                        raise ValueError('invalid cloud migration selection')
                    arguments.update(route=route, limit=limit)
                if data['action'] == 'cloud_retry':
                    job_id = data.get('job_id')
                    if type(job_id) is not int or job_id <= 0:
                        raise ValueError('invalid cloud job')
                    arguments['job_id'] = job_id
                if data['action'] in ('cloud_probe', 'cloud_test', 'cloud_tls_issue'):
                    profile = str(data.get('profile', ''))
                    if not re.fullmatch('[a-z][a-z0-9_-]{0,31}', profile):
                        raise ValueError('invalid cloud profile')
                    arguments['profile'] = profile
                result = await self.call(arguments)
                if result is None:
                    return web.json_response(dict(code='mirror_offline'), status=503)
                return web.json_response(result)
            try:
                status = await self.call(dict(action='settings', settings=data))
            except ValueError as exc:
                return web.json_response(dict(code='invalid_mirror_settings', message=str(exc)), status=400)
            if status is None:
                return web.json_response(dict(code='mirror_offline'), status=503)
            return web.json_response(status)
        status = await self.call()
        if status is None:
            if self.config.mirror_control_port:
                return web.json_response(dict(code='mirror_offline'), status=503)
            settings = read_settings(self.config)
            status = dict(running=False, settings={**settings,'sources':[]}, root=str(Path(self.config.mirror_root).resolve()),
                          source_mappings=sum(row['enabled'] and row['mode']=='redirect' for row in settings['sources']))
            status['automatic_sources'] = source_status(self.config.mirror_root)
        return web.json_response(status)

    async def room_nodes(self, request):
        self.api.require_admin(request)
        data = None
        if request.method == 'POST':
            data = await request.json()
            if not isinstance(data, dict):
                raise web.HTTPBadRequest(text='Invalid node operation')
        result = await self.call(dict(action='room_nodes', operation=data))
        if result is None:
            raise web.HTTPServiceUnavailable(text='mirror_offline')
        return web.json_response(result, headers={'Cache-Control': 'no-store'})

    async def certificate(self, request):
        self.api.require_admin(request)
        identifier = request.query.get('profile', '')
        if not re.fullmatch('[a-z][a-z0-9_-]{0,31}', identifier):
            raise web.HTTPBadRequest(text='Invalid cloud profile')
        body = await self.call(dict(action='cloud_certificate', profile=identifier), binary=True)
        if body is None:
            raise web.HTTPServiceUnavailable(text='mirror_offline')
        return web.Response(body=body, content_type='application/zip', headers={
            'Content-Disposition': f'attachment; filename="{identifier}-https.zip"',
            'Cache-Control': 'no-store', 'Cloudflare-CDN-Cache-Control': 'no-store',
            'X-Content-Type-Options': 'nosniff'})
    async def status(self, request):
        result = await self.call()
        fields = ('running', 'cached_bytes', 'cached_files', 'active_downloads', 'exclusions', 'policies')
        public = {**{k: result[k] for k in fields}, 'enabled': result['settings']['enabled'],
                  'content_cache': result.get('content_cache'),
                  'protocol': 'buggcraft-resources-v1', 'accepting': result.get('accepting', False),
                  'source_mappings': result.get('source_mappings',sum(n['enabled'] and n['mode']=='redirect' for n in result['settings']['sources'])),
                  'nodes': [dict(url=n['url'], weight=n['weight']) for n in result.get('nodes', []) if n['healthy'] and n['enabled']]} if result else dict(running=False)
        return web.json_response(public,
                                 headers={'Cache-Control': 'no-store'})
