import asyncio
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestClient, TestServer

from mc_translate.api import create_app
from mc_translate.config import Config
from mc_translate.mirror import create_mirror, upstreams, validate_settings, read_settings, settings_path
from mc_translate.mirror_catalog import Inventory, allowed_url, write_json, prefetch_matches_platform
from mc_translate.mirror_policy import POLICIES, category_for
from mc_translate.mirror_routing import source_target


class MirrorCase(unittest.IsolatedAsyncioTestCase):
    async def test_public_cache_counts_content_across_spaces_not_aliases_or_replicas(self):
        # Two URLs and three verified copies of A are still one resource.
        a, b, c = 'a'*64, 'b'*64, 'c'*64
        await self.mirror.db.executemany('INSERT INTO objects(digest,size,local) VALUES(?,?,?)',
                                        [(a, 100, 1), (b, 200, 0)])
        await self.mirror.db.executemany('INSERT INTO cache(url,object,size,checked) VALUES(?,?,?,1)',
                                        [('https://a', a, 100), ('https://alias', a, 100)])
        await self.mirror.db.executemany('''INSERT INTO object_locations
            (scope,digest,profile,route,object_key,size,state) VALUES(?,?,?,?,?,?,?)''', [
                ('mirror', a, 'local-volume', 'mods', a, 100, 'ready'),
                ('mirror', a, 'oss', 'mods', a, 100, 'ready'),
                ('mirror', b, 'oss', 'mods', b, 200, 'ready'),
                ('mirror', c, 'oss', 'mods', c, 300, 'failed'),
                ('room', c, 'oss', 'room_mods', c, 300, 'ready')])
        await self.mirror.db.execute('UPDATE totals SET bytes=100,files=2 WHERE id=1')
        await self.mirror.db.commit()
        response = await self.client.get('/mirror-api/status')
        self.assertEqual(response.status, 200)
        value = await response.json()
        self.assertEqual(value['content_cache'], {'files': 2, 'bytes': 300})
        self.assertEqual(value['cached_files'], 2)  # Old management fields retain their contract.
        self.assertEqual(value['cached_bytes'], 100)

    async def test_resource_start_queues_once_and_pause_survives_restart(self):
        from mc_translate.mirror_resources import ResourcePrefetch
        gate=asyncio.Event()
        self.mirror.job=asyncio.create_task(gate.wait())
        self.mirror.job_state=dict(state='running',action='inventory')
        headers={'X-Mirror-Control':self.mirror.secret}
        for _ in range(2):
            response=await self.client.post('/_internal/control',headers=headers,json=dict(action='resource_prefetch_start'))
            self.assertEqual(response.status,200,await response.text())
        self.assertTrue(ResourcePrefetch(self.mirror).state['requested'])
        self.assertFalse(self.mirror.job.done())
        response=await self.client.post('/_internal/control',headers=headers,json=dict(action='resource_prefetch_pause'))
        self.assertEqual(response.status,200,await response.text())
        self.assertTrue(ResourcePrefetch(self.mirror).state['paused'])
        self.assertFalse(ResourcePrefetch(self.mirror).state['requested'])
        self.assertFalse(self.mirror.job.done())  # Never cancel unrelated mirror work.
        self.mirror.job_state['action']='resource_prefetch'
        response=await self.client.post('/_internal/control',headers=headers,json=dict(action='resource_prefetch_pause'))
        self.assertEqual(response.status,200,await response.text())
        self.assertTrue(self.mirror.job.done())
        await self.mirror.resources.finished(dict(state='cancelled'),dict(refresh_hours=24))
        self.assertTrue(ResourcePrefetch(self.mirror).state['paused'])

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = Config(database=str(self.root/'translations.sqlite'), mirror_root=str(self.root/'mirror'))
        write_json(settings_path(self.config), validate_settings({
            'quota_gib': 2, 'reserve_gib': 10, 'auto_sources': False,
            'auto_refresh': False, 'quality_enabled': False, 'policies': {
            **POLICIES, 'libraries': 'cache', 'assets': 'cache', 'java': 'cache'}}))
        self.payload = b'validated game resource\n' * 100
        self.calls = 0
        self.response_status = 200
        self.gate = None

        async def origin(request):
            self.calls += 1
            if self.gate:
                await self.gate.wait()
            return web.Response(body=self.payload, status=self.response_status)

        app = web.Application()
        app.router.add_get('/data', origin)
        self.origin = TestServer(app)
        await self.origin.start_server()
        self.client = TestClient(TestServer(await create_mirror(self.config)))
        await self.client.start_server()
        self.mirror = self.client.app['mirror']
        self.assertEqual(self.mirror.http.headers['Accept-Encoding'], 'identity')
        await self.mirror.http.close()
        self.http = ClientSession()
        self.mirror.http = SimpleNamespace(get=lambda *a, **kw: self.http.get(self.origin.make_url('/data')), close=self.http.close)

    async def asyncTearDown(self):
        await self.client.close()
        await self.origin.close()
        self.temp.cleanup()

    def asset(self):
        sha = hashlib.sha1(self.payload).hexdigest()
        return '/assets/' + sha[:2] + '/' + sha

    async def test_hot_requested_files_precede_newer_inventory(self):
        old = 'https://libraries.minecraft.net/org/example/old.jar'
        new = 'https://libraries.minecraft.net/org/example/new.jar'
        await self.mirror.db.execute('INSERT INTO resource_demand VALUES(?,?,20,?)', (old,int(time.time()//86400),time.time()))
        await self.mirror.db.commit()
        settings = read_settings(self.config)
        settings['prefetch_hot_limit'] = 1
        selected = await self.mirror.automation.batch([
            dict(url=new,category='libraries',priority=200),
            dict(url=old,category='libraries',priority=1)], settings, limit=1)
        self.assertEqual([row['url'] for row in selected], [old])

    async def test_proactive_categories_do_not_require_demand_or_expand_each_round(self):
        settings = read_settings(self.config)
        settings['prefetch_category_limits'] = {key: 0 for key in settings['policies']}
        settings['prefetch_category_limits']['libraries'] = 1
        files = [dict(url='https://libraries.minecraft.net/org/example/new.jar',
                      category='libraries',priority=20),
                 dict(url='https://libraries.minecraft.net/org/example/old.jar',
                      category='libraries',priority=10)]
        first = await self.mirror.automation.batch(files, settings)
        self.assertEqual([row['url'] for row in first], [files[0]['url']])
        self.mirror.automation.state['next_at'] = 0
        second = await self.mirror.automation.batch(files, settings)
        self.assertEqual([row['url'] for row in second], [files[0]['url']])
        settings['prefetch_category_limits']['libraries'] = 0
        self.assertEqual(await self.mirror.automation.batch(files, settings), [])

    async def test_slow_cold_burst_shares_one_preparation_then_serves_cache(self):
        self.gate = asyncio.Event()
        with patch.object(self.mirror.quality, 'content_metric', return_value={'health': 'slow'}):
            requests = [asyncio.create_task(self.client.get(self.asset(), allow_redirects=False))
                        for _ in range(2)]
            for _ in range(50):
                if self.calls:
                    break
                await asyncio.sleep(.01)
            self.assertEqual(self.calls, 1)
            self.gate.set()
            responses = await asyncio.gather(*requests)
            for response in responses:
                self.assertEqual(response.status, 200)
                self.assertEqual(await response.read(), self.payload)
            self.assertEqual(self.calls, 1)
            self.assertEqual(self.mirror.cold_waiters, 0)

    async def test_two_platform_rankings_share_one_verified_download(self):
        self.policies(mods='cache')
        sha = hashlib.sha1(self.payload).hexdigest()
        seen = []
        async def official(provider, route, params=None):
            seen.append((provider, route, params))
            if provider == 'curseforge':
                return dict(data=[dict(id=42,name='Same mod',allowModDistribution=True,
                    latestFiles=[dict(id=901,modId=42,fileName='renamed.jar',fileDate='2026-11-11',
                        fileLength=len(self.payload),hashes=[dict(algo=1,value=sha)],
                        downloadUrl='https://edge.forgecdn.net/files/0/901/renamed.jar')])])
            if route == '/search':
                return dict(hits=[dict(project_id='Same',latest_version='Release',title='Same mod')])
            if route == '/versions':
                return [dict(id='Release',project_id='Same',files=[dict(primary=True,
                    url='https://cdn.modrinth.com/data/Same/versions/Release/original.jar',
                    size=len(self.payload),hashes=dict(sha1=sha))])]
            raise AssertionError(route)
        settings = read_settings(self.config)
        settings['resource_prefetch'] = dict(limit=1,types=['mod'],providers=['modrinth','curseforge'],pins=[])
        write_json(settings_path(self.config),validate_settings(settings))
        with patch.object(self.mirror.resources,'get',side_effect=official):
            await self.mirror.run_job('resource_prefetch')
        self.assertEqual(self.mirror.job_state['state'],'finished',self.mirror.job_state)
        self.assertEqual(self.calls,1)
        self.assertEqual((await self.mirror.one('SELECT COUNT(*) n FROM cache'))['n'],2)
        self.assertEqual((await self.mirror.one('SELECT COUNT(*) n FROM objects'))['n'],1)
        self.assertEqual((await self.mirror.resources.snapshot())['ready'],2)
        rankings=[item for item in seen if item[1] in ('/search','/mods/search')]
        self.assertEqual({item[0] for item in rankings},{'modrinth','curseforge'})
        self.assertEqual(rankings[1][2]['sortField'],6)

    async def test_catalog_key_control_is_private_and_persists(self):
        headers={'X-Mirror-Control':self.mirror.secret}
        body=dict(action='catalog_credentials',curseforge_key='private-catalog-test')
        self.assertEqual((await self.client.post('/_internal/control',json=body)).status,403)
        response=await self.client.post('/_internal/control',json=body,headers=headers)
        self.assertEqual(response.status,200)
        value=await response.json()
        self.assertTrue(value['configured'])
        self.assertNotIn('private-catalog-test',json.dumps(value))
        stored=json.loads((self.mirror.root/'catalog-credentials.json').read_text())
        self.assertEqual(stored['curseforge_key'],'private-catalog-test')
        self.assertNotIn('private-catalog-test',json.dumps(await self.mirror.status()))
        self.assertEqual((await self.client.post('/_internal/control',json=dict(body,curseforge_key='bad\nkey'),headers=headers)).status,400)

    async def test_popular_and_pinned_resources_are_downloaded_and_served_without_demand(self):
        self.policies(mods='cache')
        sha = hashlib.sha1(self.payload).hexdigest()
        def release(project, version, extension='jar'):
            return dict(id=version,project_id=project,version_type='release',files=[dict(primary=True,
                url=f'https://cdn.modrinth.com/data/{project}/versions/{version}/core%2B1.{extension}',
                size=len(self.payload),hashes=dict(sha1=sha))])
        async def official(provider, route, params=None):
            if route == '/project/iris':
                return dict(id='IrisCore',title='Iris',project_type='mod')
            if route == '/project/iris/version':
                return [release('IrisCore','Pinned')]
            if route == '/search':
                return dict(hits=[dict(project_id='ShaderPack',latest_version='ShaderV',title='Shader')])
            if route == '/versions':
                return [release('ShaderPack','ShaderV','zip')]
            raise AssertionError(route)
        settings = read_settings(self.config)
        settings['resource_prefetch'] = dict(limit=1,types=['shader'],providers=['modrinth'],
            pins=[dict(provider='modrinth',project='iris',version='')])
        write_json(settings_path(self.config),validate_settings(settings))
        with patch.object(self.mirror.resources,'get',side_effect=official):
            await self.mirror.run_job('resource_prefetch')
        self.assertEqual(self.mirror.job_state['state'],'finished',self.mirror.job_state)
        state = await self.mirror.resources.snapshot()
        self.assertEqual((state['total'],state['ready'],state['pinned']),(2,2,1))
        calls = self.calls
        self.assertEqual(calls, 1)  # Same verified bytes are fetched once, even under different URLs.
        for file in state['files']:
            path = file['url'].replace('https://cdn.modrinth.com','/mods/modrinth')
            response = await self.client.get(path,headers={'X-Buggcraft-Pack-Archive':'1'},allow_redirects=False)
            self.assertEqual(response.status,200)
            self.assertEqual(await response.read(),self.payload)
        self.assertEqual(self.calls,calls) # No caller was needed to create either cache entry.
        # Two URL aliases with one content digest consume one object allocation.
        self.assertEqual((await self.mirror.status())['cached_bytes'],len(self.payload))
        digest = (await self.mirror.one('SELECT digest FROM objects'))['digest']
        self.assertTrue(await self.mirror.resources.protected(digest))
        settings.update(eviction='lru',quota_gib=1,protect_latest=0)
        self.mirror.reserved=1024**3
        async with self.mirror.space_lock:
            await self.mirror.make_space(0,settings)
        self.mirror.reserved=0
        self.assertIsNotNone(await self.mirror.cached(state['files'][0]['url']))
        # A failed catalogue refresh keeps the old protection and ready cache.
        with patch.object(self.mirror.resources,'get',side_effect=ValueError('offline')):
            await self.mirror.run_job('resource_prefetch')
        self.assertEqual(self.mirror.job_state['state'],'failed')
        self.assertTrue(await self.mirror.resources.protected(digest))
        with patch.object(self.mirror.resources,'get',side_effect=official):
            await self.mirror.run_job('resource_prefetch')
        self.assertEqual(self.mirror.job_state['reused'],2)
        self.assertEqual(self.calls,calls)

    async def test_follow_latest_pinned_project_uses_per_game_compatibility_quota(self):
        self.policies(mods='cache')
        releases = [
            dict(id='v6', project_id='IrisCore', version_type='release', date_published='2026-11-06',
                 game_versions=['1.21.1'], files=[dict(primary=True,
                     url='https://cdn.modrinth.com/data/IrisCore/versions/v6/iris.jar',
                     size=len(self.payload), hashes=dict(sha1=hashlib.sha1(b'v6').hexdigest()))]),
            dict(id='v5', project_id='IrisCore', version_type='release', date_published='2026-11-05',
                 game_versions=['1.21.1', '1.21.2'], files=[dict(primary=True,
                     url='https://cdn.modrinth.com/data/IrisCore/versions/v5/iris.jar',
                     size=len(self.payload), hashes=dict(sha1=hashlib.sha1(b'v5').hexdigest()))]),
            dict(id='v4', project_id='IrisCore', version_type='release', date_published='2026-11-04',
                 game_versions=['1.21.1', '1.21.2', '1.21.3'], files=[dict(primary=True,
                     url='https://cdn.modrinth.com/data/IrisCore/versions/v4/iris.jar',
                     size=len(self.payload), hashes=dict(sha1=hashlib.sha1(b'v4').hexdigest()))]),
            dict(id='v3', project_id='IrisCore', version_type='release', date_published='2026-11-03',
                 game_versions=['1.21.3'], files=[dict(primary=True,
                     url='https://cdn.modrinth.com/data/IrisCore/versions/v3/iris.jar',
                     size=len(self.payload), hashes=dict(sha1=hashlib.sha1(b'v3').hexdigest()))]),
        ]

        async def official(provider, route, params=None):
            if route == '/project/iris':
                return dict(id='IrisCore', title='Iris', project_type='mod')
            if route == '/project/IrisCore/version':
                return releases
            raise AssertionError(route)

        settings = read_settings(self.config)
        settings['resource_prefetch'] = dict(
            enabled=False, limit=0, types=['mod'], providers=['modrinth'], refresh_hours=24,
            pins=[dict(provider='modrinth', project='iris', version='')],
            compatibility=dict(mode='selected', recent=3,
                games=['1.21.1', '1.21.2', '1.21.3'], per_game=1, overrides={}))
        write_json(settings_path(self.config), validate_settings(settings))
        with patch.object(self.mirror.resources, 'get', side_effect=official):
            await self.mirror.resources.discover(settings['resource_prefetch'])

        files = (await self.mirror.resources.snapshot())['files']
        self.assertEqual({row['version'] for row in files}, {'v6', 'v5', 'v4'})
        self.assertTrue(all(row['pinned'] == 1 for row in files))
        # v4 spans all configured game versions but remains one cached file.
        self.assertEqual(len({row['url'] for row in files}), 3)

    async def test_follow_latest_shared_release_is_cached_once_for_all_game_versions(self):
        self.policies(mods='cache')
        digest = hashlib.sha1(b'iris-shared').hexdigest()
        release = dict(id='shared-v1', project_id='IrisCore', version_type='release',
                       date_published='2026-11-06',
                       game_versions=['1.21.1', '1.21.2', '1.21.3'],
                       files=[dict(primary=True,
                           url='https://cdn.modrinth.com/data/IrisCore/versions/shared/iris.jar',
                           size=len(self.payload), hashes=dict(sha1=digest))])

        async def official(provider, route, params=None):
            if route == '/project/iris':
                return dict(id='IrisCore', title='Iris', project_type='shader')
            if route == '/project/IrisCore/version':
                return [release]
            raise AssertionError(route)

        settings = read_settings(self.config)
        settings['resource_prefetch'] = dict(
            enabled=False, limit=0, types=['shader'], providers=['modrinth'], refresh_hours=24,
            pins=[dict(provider='modrinth', project='iris', version='')],
            compatibility=dict(mode='selected', recent=3,
                games=['1.21.1', '1.21.2', '1.21.3'], per_game=1, overrides={}))
        write_json(settings_path(self.config), validate_settings(settings))
        with patch.object(self.mirror.resources, 'get', side_effect=official):
            await self.mirror.resources.discover(settings['resource_prefetch'])

        files = (await self.mirror.resources.snapshot())['files']
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['version'], 'shared-v1')
        self.assertEqual(files[0]['pinned'], 1)

    async def test_resource_ranking_pagination_validation_and_failed_discovery(self):
        from mc_translate.mirror_resources import validate_resource_prefetch
        for value in (dict(limit=10001),dict(limit=True),dict(providers=['unknown']),
                      dict(pins=[dict(project='../escape')])):
            with self.subTest(value=value),self.assertRaises(ValueError):
                validate_resource_prefetch(value)
        self.policies(mods='cache')
        pages=[]
        async def official(provider,route,params=None):
            if route=='/search':
                pages.append((params['offset'],params['limit']))
                return dict(hits=[dict(project_id=f'P{i}',title=f'Mod {i}',latest_version=f'V{i}')
                    for i in range(params['offset'],params['offset']+params['limit'])])
            if route=='/versions':
                return []
            raise AssertionError(route)
        with patch.object(self.mirror.resources,'get',side_effect=official):
            files=await self.mirror.resources.discover(dict(limit=101))
        self.assertEqual(pages,[(0,100),(100,1)])
        self.assertEqual(files,[])
        self.assertEqual(self.mirror.job_state['projects'],101)
        self.assertFalse(allowed_url('https://cdn.modrinth.com/data/%2e%2e/escape.jar'))
        self.assertTrue(allowed_url('https://cdn.modrinth.com/data/P/versions/V/iris%2B1.jar'))

    async def test_prefetch_setting_preserves_equal_existing_quality_thresholds(self):
        settings=read_settings(self.config)
        settings.update(quality_slow_kibps=512,room_cache_slow_kibps=512,quality_fast_kibps=512)
        write_json(settings_path(self.config),settings)
        await self.mirror.save_settings(dict(resource_prefetch=dict(limit=0,pins=[])))
        saved=read_settings(self.config)
        self.assertEqual(saved['quality_slow_kibps'],512)
        self.assertEqual(saved['quality_fast_kibps'],512)
        self.assertEqual(saved['resource_prefetch']['limit'],0)
        with self.assertRaises(ValueError):
            validate_settings(dict(quality_slow_kibps=513,quality_fast_kibps=512))

    async def test_resource_discovery_resumes_after_restart_without_new_catalogue(self):
        from mc_translate.mirror_resources import ResourcePrefetch
        settings = dict(limit=1)
        async def official(provider, route, params=None):
            if route == '/search':
                return dict(hits=[dict(project_id='P', title='Mod', latest_version='V')])
            return [dict(id='V', project_id='P', files=[dict(primary=True,
                url='https://cdn.modrinth.com/data/P/versions/V/mod.jar',
                size=len(self.payload), hashes=dict(sha1=hashlib.sha1(self.payload).hexdigest()))])]
        with patch.object(self.mirror.resources, 'get', side_effect=official):
            files = await self.mirror.resources.discover(settings)
        restarted = ResourcePrefetch(self.mirror)
        with patch.object(restarted, 'get', side_effect=AssertionError('must reuse durable catalogue')):
            self.assertEqual(await restarted.discover(settings), files)
        self.assertTrue(self.mirror.job_state['resumed'])
        await restarted.finished(dict(state='cancelled'), dict(refresh_hours=24))
        self.assertTrue(ResourcePrefetch(self.mirror).state['pending'])
        await restarted.finished(dict(state='finished'), dict(refresh_hours=24))
        self.assertFalse(ResourcePrefetch(self.mirror).state['pending'])

    async def test_curseforge_resources_use_official_classes_and_distribution_permission(self):
        digest = hashlib.sha1(self.payload).hexdigest()
        release = dict(id=12, modId=7, downloadUrl='https://edge.forgecdn.net/files/1/2/core.jar',
            fileLength=len(self.payload), hashes=[dict(algo=1, value=digest)], fileDate='2026-01-01')
        project = dict(id=7, name='Core', classId=6552, latestFiles=[release])
        requested = []
        async def official(provider, route, params=None):
            self.assertEqual(provider, 'curseforge')
            if route == '/mods/7':
                return dict(data=project)
            if route == '/mods/7/files/12':
                return dict(data=release)
            self.assertEqual(route, '/mods/search')
            requested.append(params)
            return dict(data=[project, dict(project, id=9, allowModDistribution=False)])
        settings = dict(limit=2, providers=['curseforge'], types=['shader'],
            pins=[dict(provider='curseforge', project='7', version='12')])
        with patch.object(self.mirror.resources, 'get', side_effect=official):
            files = await self.mirror.resources.discover(settings)
            self.assertEqual(len(files), 1)
            self.assertEqual((files[0]['pinned'], files[0]['kind']), (1, 'shader'))
            self.assertEqual(requested[0], dict(gameId=432, classId=6552,
                sortField=6, sortOrder='desc', pageSize=2, index=0))
            await self.mirror.resources.finished(dict(state='finished'), dict(refresh_hours=24))
            project['allowModDistribution'] = False
            with self.assertRaisesRegex(ValueError, '不允许'):
                await self.mirror.resources.discover(settings)

    async def test_failed_prefetch_continues_and_batch_retry_only_retries_missing_file(self):
        paths = ['/maven/com/example/one/1.0/one-1.0.jar',
                 '/maven/com/example/two/1.0/two-1.0.jar']
        files = [dict(path=path, url=upstreams(path)[0], category='libraries',
                      sha1=hashlib.sha1(self.payload).hexdigest(), size=len(self.payload), priority=0)
                 for path in paths]
        (self.mirror.root / 'inventory.json').write_text(
            json.dumps(dict(report=dict(complete=True), files=files)), encoding='utf-8')
        self.mirror.http.get = lambda url, **kw: self.http.get(
            self.origin.make_url('/missing' if '/one-1.0.jar' in str(url) else '/data'), **kw)
        self.mirror.job_state = dict(state='running', action='prefetch')
        await self.mirror._run_job('prefetch')
        self.assertEqual(self.mirror.job_state['state'], 'incomplete')
        self.assertEqual(self.mirror.job_state['done'], 2)
        self.assertEqual(self.mirror.job_state['cached'], 1)
        self.assertEqual((await self.mirror.observation.snapshot())['failed_queue']['count'], 1)
        self.mirror.http.get = lambda url, **kw: self.http.get(self.origin.make_url('/data'), **kw)
        await self.mirror.db.execute('UPDATE mirror_failed_files SET next_retry=0')
        await self.mirror.db.commit()
        self.mirror.job_state = dict(state='running', action='retry_failures')
        await self.mirror._run_job('retry_failures')
        self.assertEqual(self.mirror.job_state['state'], 'finished')
        self.assertEqual(self.mirror.job_state['total'], 1)
        self.assertEqual((await self.mirror.observation.snapshot())['failed_queue']['count'], 0)

    async def test_primary_failure_uses_verified_alternate_source(self):
        path = self.asset()
        url = upstreams(path)[0]
        settings = read_settings(self.config)
        settings['sources'] = [dict(prefix=path, target='https://verified.example' + path,
                                    mode='redirect', enabled=True)]
        write_json(settings_path(self.config), validate_settings(settings))
        seen = []
        def get(target, **kw):
            seen.append(str(target))
            return self.http.get(self.origin.make_url(
                '/missing' if str(target) == url else '/data'), **kw)
        self.mirror.http.get = get
        item = await self.mirror.download(url)
        self.assertEqual(item['size'], len(self.payload))
        self.assertEqual(len(seen), 2)
        self.assertTrue(seen[1].startswith('https://verified.example/'))

    async def test_curseforge_cdn_failure_uses_same_publisher_file_and_server_key(self):
        self.config.curseforge_key = 'private-cdn-key'
        url = 'https://edge.forgecdn.net/files/9543/699/pack.zip'
        seen = []
        def get(target, **kwargs):
            seen.append((str(target), kwargs.get('headers')))
            return self.http.get(self.origin.make_url('/missing' if 'edge.forgecdn.net' in str(target) else '/data'))
        self.mirror.http.get = get
        item = await self.mirror.ensure(url)
        self.assertEqual(item['sha1'], hashlib.sha1(self.payload).hexdigest())
        self.assertEqual([value[0] for value in seen], [url, url.replace('edge.forgecdn.net','mediafilez.forgecdn.net')])
        self.assertTrue(all(value[1] == {'x-api-key':'private-cdn-key'} for value in seen))
        self.assertEqual(item['url'],url)

    async def test_primary_failure_uses_healthy_peer_when_no_mapped_source(self):
        path = self.asset()
        url = upstreams(path)[0]
        seen = []
        def get(target, **kw):
            seen.append(str(target))
            return self.http.get(self.origin.make_url(
                '/missing' if str(target) == url else '/data'), **kw)
        self.mirror.http.get = get
        with patch.object(self.mirror, 'select_node', return_value='https://mirror2.bugg.icu'):
            item = await self.mirror.download(url)
        self.assertEqual(item['size'], len(self.payload))
        self.assertEqual(len(seen), 2)
        self.assertIn('/_mirror_node' + path, seen[1])

    async def test_peer_request_never_hops_to_another_source(self):
        path = self.asset()
        url = upstreams(path)[0]
        settings = read_settings(self.config)
        settings['sources'] = [dict(prefix=path, target='https://verified.example' + path,
                                    mode='redirect', enabled=True)]
        write_json(settings_path(self.config), validate_settings(settings))
        seen = []
        def get(target, **kw):
            seen.append(str(target))
            return self.http.get(self.origin.make_url(
                '/missing' if str(target) == url else '/data'), **kw)
        self.mirror.http.get = get
        with patch.object(self.mirror, 'select_node', return_value='https://other-peer.example'):
            response = await self.client.get('/_mirror_node' + path, allow_redirects=False)
        self.assertEqual(response.status, 307)
        self.assertEqual(response.headers['Location'], url)
        self.assertTrue(all(target == url for target in seen))

    async def test_pack_archives_redirect_and_resource_zips_follow_policy(self):
        routes = {
            '/mods/modrinth/data/project/versions/release/pack.mrpack':
                'https://cdn.modrinth.com/data/project/versions/release/pack.mrpack',
            '/mods/modrinth/data/project/versions/release/resources.zip':
                'https://cdn.modrinth.com/data/project/versions/release/resources.zip',
            '/mods/curseforge/files/123/456/resources.zip':
                'https://edge.forgecdn.net/files/123/456/resources.zip',
            '/mods/curseforge-mediafilez/files/123/456/resources.zip':
                'https://mediafilez.forgecdn.net/files/123/456/resources.zip',
        }
        for path, upstream in routes.items():
            with self.subTest(path=path):
                self.assertEqual(upstreams(path), [upstream])
                headers = {'X-Buggcraft-Pack-Archive': '1'} if path.endswith('pack.mrpack') else {}
                result = await self.client.get(path, headers=headers,
                                               allow_redirects=False)
                self.assertEqual(result.status, 307)
                self.assertEqual(result.headers['Location'], upstream)
        self.policies(mods='cache')
        resource_zip = '/mods/curseforge/files/123/456/resources.zip'
        response = await self.client.get(resource_zip, headers={'X-Buggcraft-Origin-Only': '1'})
        self.assertEqual((response.status, await response.read()), (200, self.payload))
        pack_zip = await self.client.get(resource_zip, headers={'X-Buggcraft-Pack-Archive': '1'},
                                         allow_redirects=False)
        self.assertEqual(pack_zip.status, 307)
        settings = read_settings(self.config)
        alternate = 'https://pack.example.net/resources.zip'
        settings['sources'] = [dict(prefix=resource_zip, target=alternate,
                                    mode='redirect', enabled=True)]
        write_json(settings_path(self.config), validate_settings(settings))
        mapped_pack = await self.client.get(resource_zip,
                                            headers={'X-Buggcraft-Pack-Archive': '1'},
                                            allow_redirects=False)
        self.assertEqual((mapped_pack.status, mapped_pack.headers['Location']),
                         (307, alternate))
        self.assertEqual(upstreams('/mods/modrinth/data/x/versions/y/../../private.zip'), [])

    def policies(self, **values):
        settings = read_settings(self.config)
        settings['policies'].update(values)
        write_json(settings_path(self.config), validate_settings(settings))

    async def test_redirect_and_disabled_do_not_contact_origin_even_with_cache(self):
        path = self.asset()
        await self.mirror.ensure(upstreams(path)[0])
        self.assertEqual(self.calls, 1)
        self.policies(assets='redirect')
        for method in ('GET', 'HEAD'):
            response = await self.client.request(method, path, allow_redirects=False, headers={'Range': 'bytes=0-3'})
            self.assertEqual(response.status, 307)
            self.assertEqual(response.headers['Location'], 'https://resources.download.minecraft.net'+path[7:])
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.calls, 1)
        self.policies(assets='disabled')
        self.assertEqual((await self.client.get(path, allow_redirects=False)).status, 307)
        self.policies(assets='cache')
        self.assertEqual((await self.client.get(path)).status, 200)
        self.assertEqual(self.calls, 1)

    async def test_shared_object_host_uses_metadata_category_and_explicit_java_route(self):
        digest = hashlib.sha1(self.payload).hexdigest()
        path = '/v1/objects/'+digest+'/java.exe'
        url = upstreams(path)[0]
        await self.mirror.db.execute('INSERT INTO expected VALUES(?,?,?,?)', (url, digest, len(self.payload), 'java-raw'))
        await self.mirror.db.commit()
        self.policies(java='redirect')
        for resource in (path, '/java'+path):
            response = await self.client.get(resource, allow_redirects=False)
            self.assertEqual(response.status, 307)
            self.assertEqual(response.headers['X-Mirror-Category'], 'java')
            self.assertEqual(response.headers['Location'], url)
        self.assertEqual(self.calls, 0)

    async def test_mods_default_off_and_allow_only_fixed_download_paths(self):
        path = '/mods/modrinth/data/abc123/versions/def456/example.jar'
        self.assertEqual((await self.client.get(path, allow_redirects=False)).status, 307)
        self.policies(mods='redirect')
        response = await self.client.get(path, allow_redirects=False)
        self.assertEqual(response.status, 307)
        self.assertEqual(response.headers['Location'], 'https://cdn.modrinth.com/data/abc123/versions/def456/example.jar')
        self.assertEqual(self.calls, 0)
        self.policies(mods='cache')
        await self.mirror.ensure(upstreams(path)[0])
        response = await self.client.get(path)
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), self.payload)
        for bad in ('/mods/https://evil.com/file.jar', '/mods/modrinth/data/a/versions/b/file.txt',
                    '/mods/curseforge/files/1/2/file.jar?url=https://evil.com'):
            self.assertIn((await self.client.get(bad)).status, (400, 404))

    async def test_mod_catalog_fast_file_redirects_without_origin_download(self):
        path = '/mods/curseforge/files/123/456/test.jar'
        url = upstreams(path)[0]
        self.policies(mods='cache')
        item = dict(url=url, sha1=hashlib.sha1(self.payload).hexdigest(),
                    size=len(self.payload), state='available', speed_kibps=800)
        await self.mirror.room_cache.import_catalog(dict(files=[item]))
        response = await self.client.get(path, allow_redirects=False)
        self.assertEqual((response.status, response.headers['Location']), (307, url))
        self.assertEqual(response.headers['X-Mirror-Reason'], 'catalog_fast_source')
        self.assertEqual(self.calls, 0)

        item.update(state='slow', speed_kibps=20)
        await self.mirror.room_cache.import_catalog(dict(files=[item]))
        response = await self.client.get(path, allow_redirects=False)
        self.assertEqual(response.status, 307)
        await asyncio.gather(*self.mirror.backups.values())
        response = await self.client.get(path, allow_redirects=False)
        self.assertEqual((response.status, await response.read()), (200, self.payload))
        self.assertEqual(self.calls, 1)
        alternate = 'https://mirror.minguncle.cn/files/123/456/test.jar'
        await self.mirror.room_cache.import_catalog(dict(files=[dict(
            item, url=alternate, state='available', speed_kibps=900)]))
        redirected = await self.client.get(path, allow_redirects=False)
        self.assertEqual((redirected.status, redirected.headers['Location']), (307, alternate))
        self.assertEqual(self.calls, 1)
        await self.mirror.room_cache.import_catalog(dict(files=[
            dict(item, url=alternate, state='unavailable', speed_kibps=0),
            dict(item, url='https://mod.mcimirror.top/files/123/456/test.jar',
                 state='available', speed_kibps=900)]))
        no_mcim = await self.client.get(path, allow_redirects=False)
        self.assertEqual((no_mcim.status, await no_mcim.read()), (200, self.payload))
        self.assertEqual(self.calls, 1)

    async def test_mod_catalog_uses_verified_room_copy_when_public_file_unavailable(self):
        path = '/mods/curseforge/files/123/456/test.jar'
        url = upstreams(path)[0]
        sha1 = hashlib.sha1(self.payload).hexdigest()
        digest = hashlib.sha256(self.payload).hexdigest()
        self.policies(mods='cache')
        await self.mirror.room_cache.import_catalog(dict(files=[dict(
            url=url, sha256=digest, sha1=sha1, size=len(self.payload),
            state='unavailable', speed_kibps=0)]))
        room = self.mirror.room_cache
        room.path(digest).write_bytes(self.payload)
        await room.db.execute('INSERT INTO objects(digest,size,created) VALUES(?,?,?)',
                              (digest, len(self.payload), time.time()))
        await room.db.execute('''INSERT INTO files
            (scope,digest,path,size,sha1,urls,touched,lease_until)
            VALUES(?,?,?,?,?,?,?,?)''',
            ('test-room', digest, 'mods/test.jar', len(self.payload), sha1,
             json.dumps([url]), time.time(), time.time() + 3600))
        await room.db.commit()
        response = await self.client.get(path, allow_redirects=False)
        self.assertEqual(response.status, 307)
        self.assertEqual(response.headers['X-Mirror-Reason'], 'verified_room_file')
        self.assertEqual(response.headers['Location'],
                         'https://mirror.bugg.icu/room-cache/files/' + digest)
        self.assertEqual(self.calls, 0)
        file_response = await self.client.get('/room-cache/files/' + digest)
        self.assertEqual((file_response.status, await file_response.read()), (200, self.payload))
        self.policies(mods='redirect')
        direct = await self.client.get(path, allow_redirects=False)
        self.assertEqual((direct.status, direct.headers['Location']), (307, url))
        self.policies(mods='disabled')
        disabled = await self.client.get(path, allow_redirects=False)
        self.assertEqual((disabled.status, disabled.headers['Location']), (307, url))

    async def test_old_launcher_header_respects_live_category_policy(self):
        path = '/mods/modrinth/data/abc123/versions/def456/example.jar'
        headers = {'X-Buggcraft-Origin-Only': '1'}
        default = await self.client.get(path, headers=headers, allow_redirects=False)
        self.assertEqual(default.status, 307)
        self.assertEqual(self.calls, 0)
        self.policies(mods='cache')
        response = await self.client.get(path, headers=headers, allow_redirects=False)
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), self.payload)
        self.assertNotIn('Location', response.headers)
        self.response_status = 404
        missing = await self.client.get('/mods/modrinth/data/abc123/versions/def456/missing.jar',
                                        headers=headers, allow_redirects=False)
        self.assertEqual(missing.status, 503)
        self.assertNotIn('Location', missing.headers)
        self.policies(mods='redirect')
        direct = await self.client.get(path, headers=headers, allow_redirects=False)
        self.assertEqual(direct.status, 307)

    async def test_prefetch_skips_redirected_categories_and_honors_live_change(self):
        sha = hashlib.sha1(self.payload).hexdigest()
        files = [
            dict(url='https://resources.download.minecraft.net/'+sha[:2]+'/'+sha, category='assets', size=len(self.payload), sha1=sha),
            dict(url='https://libraries.minecraft.net/example.jar', category='libraries', size=len(self.payload), sha1=sha),
            dict(url='https://piston-data.mojang.com/v1/objects/'+sha+'/java.exe', category='java-raw', size=len(self.payload), sha1=sha),
            dict(url='https://piston-data.mojang.com/v1/objects/'+sha+'/client.jar', category='game-client', size=len(self.payload), sha1=sha),
        ]
        write_json(self.mirror.root/'inventory.json', dict(report={'complete': True}, files=files))
        self.policies(assets='redirect', libraries='disabled', java='redirect')
        await self.mirror.run_job('prefetch')
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.mirror.job_state['skipped'], 3)
        self.assertEqual(self.mirror.job_state['cached'], 1)
        self.assertEqual(self.mirror.job_state['state'], 'finished')

    async def test_policy_validation_and_legacy_settings_defaults(self):
        write_json(settings_path(self.config), {'concurrency': 2})
        settings = read_settings(self.config)
        self.assertEqual(settings['policies'], POLICIES)
        for invalid in ({'assets': 'stream'}, {'unknown': 'cache'}, [], {'java': 1}):
            with self.assertRaises(ValueError):
                validate_settings({'policies': invalid})
        self.assertEqual(category_for('https://libraries.minecraft.net/x.jar'), 'libraries')
        self.assertEqual(category_for('https://maven.minecraftforge.net/x.jar'), 'loaders')

    async def test_backup_redirects_immediately_then_falls_back_and_recovers(self):
        path = self.asset()
        self.policies(assets='backup')
        self.gate = asyncio.Event()
        response = await self.client.get(path, allow_redirects=False)
        self.assertEqual(response.status, 307)
        self.assertEqual(response.headers['X-Mirror-Backup'], 'pending')
        self.gate.set()
        await asyncio.gather(*list(self.mirror.backups.values()))
        url = upstreams(path)[0]
        self.mirror.origin_health[url] = (time.monotonic(), False)
        self.response_status = 503
        local = await self.client.get(path, headers={'Range':'bytes=0-9'}, allow_redirects=False)
        self.assertEqual(local.status, 206)
        self.assertEqual(await local.read(), self.payload[:10])
        self.assertEqual(local.headers['X-Mirror-Mode'], 'fallback')
        self.assertEqual(local.headers['Cache-Control'], 'no-store')
        self.mirror.origin_health[url] = (time.monotonic(), True)
        self.assertEqual((await self.client.head(path, allow_redirects=False)).status, 307)
        self.policies(assets='cache')
        self.assertEqual((await self.client.get(path, allow_redirects=False)).status, 200)
        self.assertEqual(self.calls, 1)

    async def test_node_selection_excludes_dead_nodes_and_direct_route_never_loops(self):
        settings = read_settings(self.config)
        settings.update(node_weight=0, nodes=[dict(url='https://node.example.com',weight=3,enabled=True)])
        write_json(settings_path(self.config), validate_settings(settings))
        self.assertIsNone(self.mirror.select_node(settings))
        self.mirror.node_health['https://node.example.com'] = dict(healthy=True,at=time.time(),policies={'assets':'redirect'})
        self.assertIsNone(self.mirror.select_node(settings, 'assets'))
        self.mirror.node_health['https://node.example.com']['policies']['assets'] = 'cache'
        response = await self.client.get(self.asset(), allow_redirects=False)
        self.assertEqual(response.status, 307)
        self.assertEqual(response.headers['Location'], 'https://node.example.com/_mirror_node'+self.asset())
        self.assertEqual(self.calls, 0)
        await self.mirror.ensure(upstreams(self.asset())[0])
        local = await self.client.get('/_mirror_node'+self.asset(), allow_redirects=False)
        self.assertEqual(local.status, 200)
        self.assertEqual(await local.read(), self.payload)
        self.mirror.node_health['https://node.example.com']['healthy'] = False
        self.assertEqual((await self.client.get(self.asset(),allow_redirects=False)).status, 200)

    async def test_lru_eviction_reclaims_object_and_all_aliases_with_hard_limit(self):
        settings = read_settings(self.config)
        settings.update(quota_gib=1,eviction='lru',protect_latest=0)
        write_json(settings_path(self.config), settings)
        await self.mirror.ensure(upstreams(self.asset())[0])
        await self.mirror.ensure(upstreams('/maven/alias.jar')[0])
        # Simulate an almost-full GiB without creating a GiB fixture file.
        await self.mirror.db.execute('UPDATE objects SET size=?', (1024**3-8,))
        await self.mirror.db.execute('UPDATE totals SET bytes=?', (1024**3-8,))
        await self.mirror.db.commit()
        self.payload=b'new bytes'
        await self.mirror.ensure(upstreams('/maven/new.jar')[0])
        response = await self.client.get('/maven/new.jar')
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), self.payload)
        status = await self.mirror.status()
        self.assertEqual(status['cached_bytes'], len(self.payload))
        self.assertEqual(status['cached_files'], 1)
        self.assertIsNone(await self.mirror.cached('https://libraries.minecraft.net/alias.jar'))

    async def test_protected_new_version_cannot_be_evicted_by_old_download(self):
        settings = read_settings(self.config)
        settings.update(quota_gib=1,eviction='oldest_version',protect_latest=1)
        write_json(settings_path(self.config), settings)
        await self.mirror.ensure(upstreams(self.asset())[0])
        await self.mirror.db.execute('INSERT INTO priorities VALUES(?,?)', (upstreams(self.asset())[0], 100))
        await self.mirror.db.execute('UPDATE objects SET size=?,priority=100', (1024**3-8,))
        await self.mirror.db.execute('UPDATE totals SET bytes=?', (1024**3-8,))
        await self.mirror.db.commit()
        response = await self.client.get('/maven/old.jar', allow_redirects=False)
        self.assertEqual(response.status, 307)
        self.assertEqual(response.headers['X-Mirror-Reason'], 'cache_miss')
        await asyncio.gather(*self.mirror.backups.values())
        self.assertEqual((await self.client.get(self.asset())).status, 200)
        self.assertEqual(self.mirror.reserved, 0)

    async def test_quota_reduction_evicts_more_than_one_batch_and_skips_pinned_objects(self):
        settings = read_settings(self.config)
        settings.update(quota_gib=1, eviction='lru', protect_latest=0)
        rows = [(f'{i:064x}', 4*1024**2, i, 0) for i in range(300)]
        await self.mirror.db.executemany('INSERT INTO objects(digest,size,accessed,priority) VALUES(?,?,?,?)', rows)
        await self.mirror.db.execute('UPDATE totals SET bytes=?', (300*4*1024**2,))
        await self.mirror.db.commit()
        self.mirror.serving[rows[0][0]] = 1
        # Reserve almost the entire budget, forcing over 256 victims.
        self.mirror.reserved = 1024**3-8*1024**2
        async with self.mirror.space_lock:
            await self.mirror.make_space(0, settings)
        self.mirror.reserved = 0
        self.assertEqual((await self.mirror.status())['cached_bytes'], 8*1024**2)
        self.assertIsNotNone(await self.mirror.one('SELECT digest FROM objects WHERE digest=?', (rows[0][0],)))
        self.mirror.serving.clear()

    async def test_incremental_inventory_skips_unchanged_version_and_asset_indices(self):
        inventory = Inventory(self.mirror.root)
        manifest=dict(versions=[dict(id='1.0',url='https://piston-meta.mojang.com/v1/packages/'+'a'*40+'/1.0.json',sha1='a'*40,releaseTime='2026-01-01T00:00:00Z')])
        version=dict(downloads={},libraries=[],assetIndex=dict(url='https://piston-meta.mojang.com/v1/packages/'+'b'*40+'/assets.json',sha1='b'*40))
        async def fetch(session,url,*args,**kwargs):
            if 'version_manifest' in url:return manifest
            if url.endswith('1.0.json'):return version
            return {'objects':{}}
        with patch.object(inventory, 'fetch_json', new=AsyncMock(side_effect=fetch)) as requests:
            await inventory.scan(include_java=False)
            self.assertEqual(requests.await_count,3)
        newer=Inventory(self.mirror.root)
        with patch.object(newer, 'fetch_json', new=AsyncMock(side_effect=fetch)) as requests:
            report=await newer.scan(include_java=False,incremental=True)
            self.assertEqual(requests.await_count,1)
            self.assertEqual(report['changed_versions'],0)
            self.assertTrue(report['incremental'])

    async def test_exact_source_mapping_preserves_cache_and_prefetch_policy(self):
        item = dict(url='https://libraries.minecraft.net/org/example/library.jar', path='/maven/org/example/library.jar',
                    category='libraries',size=len(self.payload),sha1=hashlib.sha1(self.payload).hexdigest())
        settings = read_settings(self.config)
        settings['sources'] = [dict(prefix=item['path'], target='https://repo.maven.apache.org/maven2/org/example/library.jar',mode='redirect',enabled=True)]
        write_json(settings_path(self.config),validate_settings(settings))
        with patch.object(self.mirror, 'upstream_available', new=AsyncMock(return_value=True)):
            response = await self.client.get(item['path'], allow_redirects=False)
        self.assertEqual(response.status,307)
        self.assertEqual(response.headers['X-Mirror-Reason'],'cache_miss')
        self.assertEqual(response.headers['Location'],settings['sources'][0]['target'])
        await asyncio.gather(*list(self.mirror.backups.values()))
        self.assertEqual(self.calls,1)
        write_json(self.mirror.root/'inventory.json',dict(report={'complete':True},files=[item]))
        await self.mirror.run_job('prefetch')
        self.assertEqual(self.mirror.job_state['skipped'],0)
        self.assertEqual(self.calls,1)

    async def test_quality_cache_decision_preserves_category_and_storage_limits(self):
        path = self.asset()
        target = 'https://source.example/slow-file.jar'
        with patch('mc_translate.mirror.source_targets', return_value=[target]), \
                patch.object(self.mirror.quality, 'needs_cache', return_value=True), \
                patch.object(self.mirror, 'upstream_available', new=AsyncMock(return_value=True)):
            # A slow-source hint does not turn an explicitly redirected class into storage.
            self.policies(assets='redirect')
            response = await self.client.get(path, allow_redirects=False)
            self.assertEqual((response.status, response.headers['Location']), (307, target))
            self.assertEqual(self.calls, 0)
            self.policies(assets='cache')
            response = await self.client.get(path, allow_redirects=False)
            self.assertEqual((response.status, response.headers['Location']), (307, target))
            await asyncio.gather(*list(self.mirror.backups.values()))
            response = await self.client.get(path, allow_redirects=False)
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.read(), self.payload)
            self.assertEqual(self.calls, 1)
            # A quality decision still cannot bypass the normal capacity check.
            with patch.object(self.mirror, 'ensure', new=AsyncMock(side_effect=web.HTTPInsufficientStorage())), \
                    patch.object(self.mirror, 'cached', new=AsyncMock(return_value=None)):
                response = await self.client.get(path, allow_redirects=False)
                self.assertEqual(response.status, 307)
                self.assertEqual(response.headers['Location'], target)
                await asyncio.gather(*list(self.mirror.backups.values()))

    async def test_prefetch_windows_filters_linux_runtime_and_reports_selection(self):
        sha1=hashlib.sha1(self.payload).hexdigest()
        files=[dict(url='https://piston-data.mojang.com/v1/objects/'+sha1+'/'+name,
                    path='/v1/objects/'+sha1+'/'+name,category='java',
                    sha1=hashlib.sha1(self.payload).hexdigest(),size=len(self.payload),
                    platforms=platforms)
               for n,name,platforms in [(1,'libfreetype.so',['linux']),
                                        (2,'java.dll',['windows-x64']),
                                        (3,'libjvm.dylib',['macos'])]]
        write_json(self.mirror.root/'inventory.json',dict(report={'complete':True},files=files))
        await self.mirror.run_job('prefetch')
        self.assertEqual(self.mirror.job_state['state'],'finished')
        self.assertEqual(self.mirror.job_state['platform_skipped'],2)
        self.assertEqual(self.mirror.job_state['plan']['java']['files'],1)
        self.assertEqual(self.mirror.job_state['cached'],1)
        self.assertEqual(self.calls,1)
        self.assertFalse(prefetch_matches_platform({'url':files[0]['url']},'windows'))
        self.assertTrue(prefetch_matches_platform(files[0],'all'))

    async def test_prefetch_platform_combinations_keep_common_files_once_and_can_disable(self):
        files = [dict(url='https://libraries.minecraft.net/example/'+name,
                      path='/maven/example/'+name, category='libraries',
                      sha1=hashlib.sha1(self.payload).hexdigest(), size=len(self.payload),
                      platforms=platforms)
                 for name, platforms in [('windows.dll', ['windows']), ('linux.so', ['linux']),
                                         ('mac.dylib', ['macos']), ('common.jar', [])]]
        write_json(self.mirror.root/'inventory.json', dict(report={'complete': True}, files=files))
        settings = read_settings(self.config)
        settings['prefetch_platform'] = ['linux', 'macos']
        write_json(settings_path(self.config), validate_settings(settings))
        await self.mirror.run_job('prefetch')
        # Progress includes scanned files that were excluded by platform.
        self.assertEqual(self.mirror.job_state['total'], 4)
        self.assertEqual(self.mirror.job_state['done'], 4)
        self.assertEqual(self.mirror.job_state['skipped'], 1)
        self.assertEqual(self.mirror.job_state['plan']['libraries']['files'], 3)
        self.assertEqual(self.mirror.job_state['platform_skipped'], 1)
        self.assertEqual(self.mirror.job_state['cached'], 3)
        self.assertIsNone(await self.mirror.cached(files[0]['url']))
        settings['prefetch_platform'] = []
        write_json(settings_path(self.config), validate_settings(settings))
        calls = self.calls
        await self.mirror.run_job('prefetch')
        self.assertEqual(self.mirror.job_state['total'], 4)
        self.assertEqual(self.mirror.job_state['done'], 4)
        self.assertEqual(self.mirror.job_state['skipped'], 4)
        self.assertEqual(self.mirror.job_state['cached'], 0)
        self.assertEqual(self.mirror.job_state['plan']['libraries']['files'], 0)
        self.assertEqual(self.calls, calls)
        self.assertEqual(validate_settings({'prefetch_platform': 'windows'})['prefetch_platform'], ['windows'])
        self.assertEqual(validate_settings({'prefetch_platform': 'all'})['prefetch_platform'], ['windows', 'macos', 'linux'])
        self.assertEqual(validate_settings({'prefetch_platform': ['windows', 'windows', 'macos', 'macos']})['prefetch_platform'], ['windows', 'macos'])
        for invalid in (None, 'android', ['windows', 'android'], {'windows': True}):
            with self.assertRaises(ValueError):
                validate_settings({'prefetch_platform': invalid})
        self.assertFalse(prefetch_matches_platform({'url':'https://example.com/natives-macos-arm64.jar'}, ['linux']))

    async def test_source_validation_exact_and_longest_prefix_mapping(self):
        rows=[dict(prefix='/maven/org/',target='https://repo.example.com/org/',mode='redirect',enabled=True),
              dict(prefix='/maven/org/ex/',target='https://repo2.example.com/ex/',mode='redirect',enabled=True),
              dict(prefix='/maven/org/ex/file.jar',target='https://repo3.example.com/file.jar',mode='redirect',enabled=True)]
        settings=validate_settings({'sources':rows})
        self.assertEqual(source_target(settings,'/maven/org/ex/file.jar','redirect'),'https://repo3.example.com/file.jar')
        self.assertEqual(source_target(settings,'/maven/org/ex/other.jar','redirect'),'https://repo2.example.com/ex/other.jar')
        self.assertEqual(source_target(settings,'/maven/org/else.jar','redirect'),'https://repo.example.com/org/else.jar')
        self.assertIsNone(source_target(settings,'/maven/org/ex/file.jar/evil','fallback'))
        for target in ('http://repo.example.com/org/','https://127.0.0.1/org/','https://mirror.bugg.icu/org/',
                       'https://repo.example.com/../org/','https://repo.example.com/org/?x=1'):
            with self.assertRaises(ValueError):
                validate_settings({'sources':[dict(rows[0],target=target)]})

    async def test_monthly_budget_persists_and_redirects_without_origin_work(self):
        settings=read_settings(self.config)
        settings.update(monthly_egress_gib=1,egress_threshold=90)
        write_json(settings_path(self.config),settings)
        state=await self.mirror.egress.snapshot(settings)
        await self.mirror.db.execute('INSERT INTO egress VALUES(?,?)',(state['period'],1024**3))
        await self.mirror.db.commit()
        response=await self.client.get(self.asset(),allow_redirects=False)
        self.assertEqual(response.status,307)
        self.assertEqual(response.headers['X-Mirror-Reason'],'monthly_budget')
        self.assertEqual(self.calls,0)
        self.assertFalse((await (await self.client.get('/mirror-api/status')).json())['accepting'])

    async def test_range_egress_count_and_bandwidth_offload(self):
        await self.mirror.ensure(upstreams(self.asset())[0])
        response=await self.client.get(self.asset(),headers={'Range':'bytes=0-9'})
        self.assertEqual(await response.read(),self.payload[:10])
        settings=read_settings(self.config)
        self.assertEqual((await self.mirror.egress.snapshot(settings))['bytes'],10)
        await self.client.head(self.asset())
        self.assertEqual((await self.mirror.egress.snapshot(settings))['bytes'],10)
        settings['bandwidth_mbps']=1
        write_json(settings_path(self.config),settings)
        self.mirror.egress.recent.clear()
        self.mirror.egress.recent.append((int(time.monotonic()),8_000_000))
        self.mirror.egress.window_bytes=8_000_000
        response=await self.client.get(self.asset(),allow_redirects=False)
        self.assertEqual(response.status,307)
        self.assertEqual(response.headers['X-Mirror-Reason'],'bandwidth_budget')
        self.assertEqual(self.calls,1)

    async def test_saturated_node_does_not_hop_again_and_external_failure_uses_official(self):
        settings=read_settings(self.config)
        settings.update(max_serving=1,nodes=[dict(url='https://node.example.com',weight=1,enabled=True)],
                        sources=[dict(prefix='/assets/',target='https://fallback.example.com/assets/',mode='fallback',enabled=True)])
        write_json(settings_path(self.config),validate_settings(settings))
        self.mirror.node_health['https://node.example.com']=dict(healthy=True,at=time.time(),policies={'assets':'cache'})
        self.mirror.serving['pinned']=1
        first=await self.client.get(self.asset(),allow_redirects=False)
        self.assertEqual(first.headers['X-Mirror-Mode'],'node')
        with patch.object(self.mirror,'upstream_available',new=AsyncMock(return_value=True)):
            direct=await self.client.get('/_mirror_node'+self.asset(),allow_redirects=False)
        self.assertEqual(direct.headers['X-Mirror-Mode'],'external')
        with patch.object(self.mirror,'upstream_available',new=AsyncMock(return_value=False)):
            direct=await self.client.get('/_mirror_node'+self.asset(),allow_redirects=False)
        self.assertEqual(direct.headers['Location'],upstreams(self.asset())[0])
        self.mirror.serving.clear()

    async def test_checksum_dedup_range_head_and_cache_hits_when_busy(self):
        path = self.asset()
        await self.mirror.ensure(upstreams(path)[0])
        first = await self.client.get(path)
        self.assertEqual(await first.read(), self.payload)
        self.assertEqual(first.headers['X-Mirror-Verified'], 'upstream-sha1')
        self.assertIn('immutable', first.headers['Cache-Control'])
        self.assertEqual(self.calls, 1)
        await self.mirror.ensure(upstreams('/maven/org/example/example.jar')[0])
        other = await self.client.get('/maven/org/example/example.jar')
        self.assertEqual(other.status, 200)
        await other.read()
        status = await self.mirror.status()
        self.assertEqual(status['cached_bytes'], len(self.payload))
        self.assertEqual(status['cached_files'], 2)
        # Existing cache must stay available even while every download slot is occupied.
        self.mirror.inflight.update({str(n): asyncio.create_task(asyncio.sleep(10)) for n in range(4)})
        ranged = await self.client.get(path, headers={'Range': 'bytes=5-15'})
        self.assertEqual(ranged.status, 206)
        self.assertEqual(await ranged.read(), self.payload[5:16])
        head = await self.client.head(path)
        self.assertEqual(head.status, 200)
        self.assertEqual(int(head.headers['Content-Length']), len(self.payload))
        self.assertEqual(await head.read(), b'')
        self.assertEqual(self.calls, 2)

    async def test_bad_checksum_does_not_publish_or_leave_partial(self):
        path = '/assets/aa/' + 'a'*40
        response = await self.client.get(path, allow_redirects=False)
        self.assertEqual(response.status, 307)
        self.assertEqual(response.headers['X-Mirror-Reason'], 'cache_miss')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        await asyncio.gather(*self.mirror.backups.values(), return_exceptions=True)
        self.assertEqual((await self.mirror.status())['cached_files'], 0)
        self.assertFalse(list((self.mirror.root/'tmp').iterdir()))
        self.assertEqual(self.mirror.reserved, 0)

    async def test_disk_reserve_denies_new_file(self):
        with patch('mc_translate.mirror.shutil.disk_usage', return_value=SimpleNamespace(free=0)):
            response = await self.client.get(self.asset(), allow_redirects=False)
            await asyncio.gather(*self.mirror.backups.values(), return_exceptions=True)
        self.assertEqual(response.status, 307)
        self.assertEqual(response.headers['X-Mirror-Reason'], 'cache_miss')
        self.assertEqual((await self.mirror.status())['cached_files'], 0)
        self.assertEqual(self.mirror.reserved, 0)

    async def test_simultaneous_miss_is_downloaded_once(self):
        self.gate = asyncio.Event()
        tasks = [asyncio.create_task(self.client.get(self.asset(), allow_redirects=False)) for _ in range(3)]
        # The origin is deliberately blocked: player responses must finish first.
        responses = await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
        for response in responses:
            self.assertEqual(response.status, 307)
            self.assertEqual(response.headers['X-Mirror-Reason'], 'cache_miss')
            self.assertEqual(response.headers['Location'], upstreams(self.asset())[0])
        self.assertFalse(self.gate.is_set())
        for _ in range(100):
            if self.calls:
                break
            await asyncio.sleep(.01)
        self.gate.set()
        await asyncio.gather(*self.mirror.backups.values())
        response = await self.client.get(self.asset(), allow_redirects=False)
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), self.payload)
        self.assertEqual(self.calls, 1)

    async def test_neoforge_miss_uses_enabled_domestic_repository_without_waiting(self):
        path = '/neoforge-maven/releases/net/neoforged/neoforge/21.1.265/neoforge-21.1.265-installer.jar'
        settings = read_settings(self.config)
        settings.update(auto_sources=True, source_registries=[dict(
            id='bmclapi', name='BMCLAPI', url='https://bmclapi2.bangbang93.com/',
            kind='minecraft', region='cn', enabled=True)])
        write_json(settings_path(self.config), validate_settings(settings))
        self.gate = asyncio.Event()
        response = await asyncio.wait_for(self.client.get(path, allow_redirects=False), 2)
        self.assertEqual((response.status, response.headers['Location']), (307,
            'https://bmclapi2.bangbang93.com/maven/net/neoforged/neoforge/21.1.265/neoforge-21.1.265-installer.jar'))
        self.assertFalse(self.gate.is_set())
        settings['source_registries'][0]['enabled'] = False
        write_json(settings_path(self.config), validate_settings(settings))
        response = await self.client.get(path, allow_redirects=False)
        self.assertEqual(response.headers['Location'], upstreams(path)[0])
        self.gate.set()
        await asyncio.gather(*self.mirror.backups.values(), return_exceptions=True)

    async def test_neoforge_universal_uses_repository_alias_and_existing_cache_first(self):
        path = '/neoforge-maven/releases/net/neoforged/neoforge/21.1.209/neoforge-21.1.209-universal.jar'
        settings = read_settings(self.config)
        settings.update(auto_sources=True, source_registries=[dict(
            id='nju', name='NJU', url='https://mirror.nju.edu.cn/bmclapi/',
            kind='maven', region='cn', enabled=True)])
        write_json(settings_path(self.config), validate_settings(settings))
        with patch.object(self.mirror, 'backup'):
            response = await self.client.get(path, allow_redirects=False)
        self.assertEqual(response.headers['Location'],
            'https://mirror.nju.edu.cn/bmclapi/net/neoforged/neoforge/21.1.209/neoforge-21.1.209-universal.jar')
        await self.mirror.ensure(upstreams(path)[0])
        settings['sources'] = [dict(prefix=path, target='https://example.com/elsewhere.jar',
                                    mode='redirect', enabled=True)]
        write_json(settings_path(self.config), validate_settings(settings))
        with patch.object(self.mirror, 'upstream_available', side_effect=AssertionError('cached copy must win')):
            response = await self.client.get(path, allow_redirects=False)
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.read(), self.payload)
        self.assertEqual(self.calls, 1)

    def test_repository_aliases_preserve_coordinates(self):
        from mc_translate.mirror_sources import repository_target
        suffix = 'net/neoforged/neoforge/21.1.209/neoforge-21.1.209-universal.jar'
        for prefix in ('/neoforge-maven/releases/', '/maven/'):
            self.assertEqual(repository_target(dict(kind='maven', url='https://maven.aliyun.com/repository/public/'), prefix + suffix),
                             'https://maven.aliyun.com/repository/public/' + suffix)
            self.assertEqual(repository_target(dict(kind='minecraft', url='https://bmclapi2.bangbang93.com/'), prefix + suffix),
                             'https://bmclapi2.bangbang93.com/maven/' + suffix)

    async def test_fixed_origins_control_auth_and_errors(self):
        for path in ('/https://example.com/a', '/fabric-maven/net/fabricmc/fabric-api/foo.jar', '/assets/ab/'+'a'*40, '/maven/test.jar?url=https://example.com'):
            response = await self.client.get(path)
            self.assertIn(response.status, (400, 404))
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual((await self.client.get('/_internal/control')).status, 403)
        headers={'X-Mirror-Control': self.mirror.secret}
        self.assertEqual((await self.client.get('/_internal/control', headers=headers)).status, 200)
        self.assertEqual((await self.client.post('/_internal/control', headers=headers, json=[])).status, 400)
        self.response_status = 302
        self.assertEqual((await self.client.get(self.asset(), allow_redirects=False)).status, 307)
        self.assertTrue(upstreams('/v1/packages/'+'a'*40+'/1.14 Pre-Release.json')[0].endswith('1.14%20Pre-Release.json'))
        self.assertTrue(allowed_url('https://piston-meta.mojang.com/v1/packages/'+'a'*40+'/a%20b.json'))
        for url in ('https://evil.com/a', 'https://libraries.minecraft.net/%2e%2e/a', 'https://libraries.minecraft.net/a?url=x'):
            self.assertFalse(allowed_url(url))

    async def test_metadata_reads_all_streamed_chunks(self):
        value = {'objects': {str(n): {'size': n} for n in range(3000)}}
        raw = json.dumps(value).encode()
        async def streamed(request):
            response = web.StreamResponse()
            await response.prepare(request)
            for start in range(0, len(raw), 1024):
                await response.write(raw[start:start+1024])
                await asyncio.sleep(.001)
            await response.write_eof()
            return response
        app = web.Application()
        app.router.add_get('/data', streamed)
        server = TestServer(app)
        await server.start_server()
        try:
            session = SimpleNamespace(get=lambda *a, **kw: self.http.get(server.make_url('/data')))
            result = await Inventory(self.root).fetch_json(session, 'https://piston-meta.mojang.com/mc/game/version_manifest_v2.json', hashlib.sha1(raw).hexdigest())
            self.assertEqual(result, value)
        finally:
            await server.close()

    async def test_translation_survives_offline_mirror_and_admin_updates_live_owner(self):
        self.config.admin_token = 'test-admin'
        self.config.mirror_port = 1
        client = TestClient(TestServer(await create_app(self.config, workers=False)))
        await client.start_server()
        try:
            self.assertEqual((await client.get('/health')).status, 200)
            self.assertEqual((await client.get('/v1/catalog/projects')).status, 200)
            for path in ('/mirror', '/admin/mirror'):
                response = await client.get(path)
                self.assertEqual(response.status, 200)
                self.assertIn('no-store', response.headers['Cache-Control'])
            self.assertEqual((await client.get('/v1/admin/mirror')).status, 401)
            headers={'Authorization': 'Bearer test-admin'}
            response = await client.post('/v1/admin/mirror', json={'quota_gib': 200}, headers=headers)
            self.assertEqual(response.status, 503)
            self.config.mirror_port = self.client.server.port
            response = await client.post('/v1/admin/mirror', json={'quota_gib': 200}, headers=headers)
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())['settings']['quota_gib'], 200)
            response = await client.post('/v1/admin/mirror', json={'policies': {'assets':'redirect'}}, headers=headers)
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())['settings']['policies']['libraries'], 'cache')
            self.assertEqual((await client.post('/v1/admin/mirror', json={'policies': {'assets':'invalid'}}, headers=headers)).status, 400)
            self.assertEqual((await client.post('/v1/admin/mirror', json={'concurrency': 100}, headers=headers)).status, 400)
            self.assertEqual((await client.post('/v1/admin/mirror', json=[], headers=headers)).status, 400)
            self.assertEqual((await client.post('/v1/admin/mirror', json={'public_url': 123}, headers=headers)).status, 400)
            sources=[dict(prefix='/maven/'+('a'*160)+str(n)+'.jar',target='https://repo.example.com/'+('a'*160)+str(n)+'.jar',mode='redirect',enabled=True) for n in range(200)]
            self.assertGreater(len(json.dumps(sources)),65536)
            self.assertEqual((await client.post('/v1/admin/mirror',json={'sources':sources},headers=headers)).status,200)
            # Other translation APIs retain the smaller request limit.
            self.assertEqual((await client.post('/v1/admin/mirror',json={'sources':sources})).status,401)
            self.config.mirror_port = 1
            self.assertEqual((await client.post('/v1/admin/mirror', json={'action':'prefetch'}, headers=headers)).status, 503)
            self.assertEqual((await client.get('/health')).status, 200)
        finally:
            await client.close()

    async def test_mutable_manifest_refresh_ignores_old_inventory_digest(self):
        url = 'https://piston-meta.mojang.com/mc/game/version_manifest_v2.json'
        await self.mirror.db.execute('INSERT INTO expected VALUES(?,?,?,?)',
                                     (url, '0'*40, 1, 'metadata'))
        await self.mirror.db.commit()
        await self.mirror.ensure(url)
        response = await self.client.get('/mc/game/version_manifest_v2.json')
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), self.payload)
        self.assertEqual(response.headers['X-Mirror-Verified'], 'local-sha256')

    async def test_extensionless_loader_metadata_refreshes_after_ttl(self):
        path = '/fabric-meta/v2/versions/loader'
        await self.mirror.ensure(upstreams(path)[0])
        first = await self.client.get(path)
        self.assertEqual(first.status, 200)
        await first.read()
        self.assertEqual(self.calls, 1)
        await self.mirror.db.execute('UPDATE cache SET fetched=0')
        await self.mirror.db.commit()
        second = await self.client.get(path, allow_redirects=False)
        self.assertEqual(second.status, 307)
        self.assertEqual(second.headers['X-Mirror-Reason'], 'cache_refresh_pending')
        await asyncio.gather(*self.mirror.backups.values())
        refreshed = await self.client.get(path, allow_redirects=False)
        self.assertEqual(refreshed.status, 200)
        await refreshed.read()
        self.assertEqual(self.calls, 2)


if __name__ == '__main__':
    unittest.main()
