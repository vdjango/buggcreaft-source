"""Focused real HTTP quarantine/restore and existing-worker prefetch contracts."""
import hashlib
import errno
import json
import unittest
from unittest.mock import patch

from aiohttp import web
from mc_translate.catalog_gateway import CatalogGateway
from mc_translate.runtime_catalog import RuntimeCatalog, LOADERS
from mc_translate.mirror_resources import compatible_releases, validate_compatibility
from mc_translate.mirror_storage import LocalStorage, StorageError
from pathlib import Path
from mc_translate.sources import Sources
from . import test_mirror


class RuntimeCatalogCase(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_mirror.MirrorCase.asyncSetUp
    asyncTearDown=test_mirror.MirrorCase.asyncTearDown
    async def test_mark_blocks_cached_direct_peer_and_fallback_then_restore(self):
        url=LOADERS['neoforge'][1]+'26.3.0.101/neoforge-26.3.0.101-installer.jar'
        digest=hashlib.sha256(self.payload).hexdigest()
        path=self.mirror.root/'objects'/digest[:2]/digest
        path.parent.mkdir();path.write_bytes(self.payload)
        await self.mirror.db.execute('INSERT INTO objects(digest,size) VALUES(?,?)',(digest,len(self.payload)))
        await self.mirror.db.execute('INSERT INTO cache VALUES(?,?,?,?,?,?,?)',(url,digest,len(self.payload),hashlib.sha1(self.payload).hexdigest(),1,0,'application/java-archive'))
        await self.mirror.db.commit()
        headers={'X-Mirror-Control':self.mirror.secret}
        body=dict(action='runtime_mark',family='neoforge',version='26.3.0.101',blocked=True,reason='fixture incompatibility')
        self.assertEqual((await self.client.post('/_internal/control',json=body)).status,403)
        self.assertEqual((await self.client.post('/_internal/control',json=body,headers=headers)).status,200)
        route='/neoforge-maven/releases/net/neoforged/neoforge/26.3.0.101/neoforge-26.3.0.101-installer.jar'
        for prefix in ('','/_mirror_node'):
            response=await self.client.get(prefix+route,allow_redirects=False)
            self.assertEqual(response.status,403)
            self.assertEqual(response.headers['X-Bugg-Error'],'runtime_blocked')
            self.assertNotIn('Location',response.headers)
        with self.assertRaises(web.HTTPForbidden):await self.mirror.ensure(url)
        self.assertEqual(self.calls,0)
        await self.mirror.runtimes.cleanup()
        self.assertFalse(path.exists())
        with self.assertRaises(web.HTTPForbidden):await self.mirror.runtimes.assert_allowed(digest=digest)
        fresh=RuntimeCatalog(self.mirror);await fresh.open()
        with self.assertRaises(web.HTTPForbidden):await fresh.assert_allowed(url)
        body['blocked']=False
        self.assertEqual((await self.client.post('/_internal/control',json=body,headers=headers)).status,200)
        # Restoring clears the rule; removed bytes are fetched again, never reported as cached.
        await self.mirror.runtimes.assert_allowed(url)
        self.assertIsNone(await self.mirror.one('SELECT 1 FROM cache WHERE url=?',(url,)))

    async def test_core_candidates_observe_cached_metadata_and_exact_dependency_block(self):
        sodium=dict(id='sodium-bad',project_id='AANobbMI',version_number='0.9.2+mc26.3',game_versions=['26.3'],loaders=['neoforge'],
            files=[dict(url='https://cdn.modrinth.com/data/AANobbMI/versions/sodium-bad/core.jar',size=5,hashes={'sha1':'a'*40},primary=True)])
        iris=dict(id='iris-pin',project_id='YL57xq9U',version_number='1.11.7',files=[],dependencies=[dict(version_id='sodium-bad',dependency_type='required')])
        class Control:
            async def call(_,body):
                if body['action']=='runtime_observe':await self.mirror.runtimes.observe(body['releases'])
                return self.mirror.runtimes.policy
        gateway=CatalogGateway({},Sources(self.config,self.http,None),Control())
        self.assertEqual(json.loads((await gateway.response([sodium], 'modrinth',observe=True)).body),[sodium])
        await self.mirror.runtimes.mark(dict(family='sodium',version=sodium['version_number'],blocked=True,reason='bad fixture'))
        response=await gateway.response([sodium,iris], 'modrinth')
        self.assertEqual(json.loads(response.body),[])
        self.assertEqual((await gateway.response(sodium,'modrinth')).status,403)
        cf_alias=dict(id=123,gameVersions=['26.3'],hashes=[dict(algo=1,value='a'*40)])
        self.assertEqual(json.loads((await gateway.response(dict(data=[cf_alias]),'curseforge')).body)['data'],[])
        with self.assertRaises(web.HTTPForbidden):await self.mirror.runtimes.assert_allowed(sodium['files'][0]['url'])
        # A different filename in the same official release cannot bypass the rule.
        with self.assertRaises(web.HTTPForbidden):await self.mirror.runtimes.assert_allowed(sodium['files'][0]['url'].replace('core.jar','alias.jar'))
        await self.mirror.runtimes.mark(dict(family='sodium',version=sodium['version_number'],blocked=False,reason=''))
        self.assertEqual(json.loads((await gateway.response([sodium,iris],'modrinth')).body),[sodium,iris])

    async def test_prefetch_uses_existing_worker_inputs_and_filters_mutable_loader_list(self):
        self.payload=b'<metadata><versioning><latest>0.19.0</latest><release>0.19.0</release><versions><version>0.18.0</version><version>0.19.0</version></versions></versioning></metadata>'
        await self.mirror.runtimes.mark(dict(family='fabric',version='0.19.0',blocked=True,reason='bad fixture'))
        response=await self.client.get('/fabric-maven/net/fabricmc/fabric-loader/maven-metadata.xml',allow_redirects=False)
        self.assertEqual(response.status,200)
        text=await response.text();self.assertNotIn('0.19.0',text);self.assertIn('0.18.0',text)
        self.assertNotIn('Location',response.headers)
        settings=__import__('mc_translate.mirror',fromlist=['read_settings']).read_settings(self.config)
        settings['resource_prefetch']['compatibility']=dict(mode='selected',games=['1.21.1'],recent=3,per_game=1,overrides={})
        settings['runtime_prefetch']['cores']=[]
        async def meta(_inventory,*args,**kwargs):
            if args[1].endswith('/versions/game'):return [dict(version='1.21.1')]
            if '/versions/loader/' in args[1]:return [dict(loader=dict(version='0.19.0')),dict(loader=dict(version='0.18.0'))]
            payload=self.payload if 'fabric' in args[1] or 'quilt' in args[1] else b'<metadata><versioning><versions><version>1.21.1-52.0.1</version></versions></versioning></metadata>' if 'minecraftforge' in args[1] else b'<metadata><versioning><versions><version>21.1.100</version></versions></versioning></metadata>'
            return __import__('xml.etree.ElementTree',fromlist=['fromstring']).fromstring(payload)
        with patch('mc_translate.runtime_catalog.Inventory.fetch_json',new=meta):
            files=await self.mirror.runtimes.discover(settings)
        self.assertEqual(len(files),4)  # Blocked newest does not consume Fabric's slot: 0.18.0 fills it.
        self.assertTrue(any('0.18.0' in file['url'] for file in files))
        self.assertTrue(all(file['category']=='loaders' for file in files))
        await self.mirror.runtimes.request()
        self.assertTrue(RuntimeCatalog(self.mirror).state['requested'])

    def test_compatibility_union_slots_exclude_bad_then_refill(self):
        games=['1.21.1','1.21.2','1.21.3']
        rules=validate_compatibility(dict(mode='selected',games=games,per_game=5))
        releases=[dict(id=str(i),date_published=f'2026-11-{i:02}',game_versions=games,files=[dict(hashes=dict(sha1=str(i)))]) for i in range(1,8)]
        selected=compatible_releases(releases,games,rules,blocked=lambda r:r['id']=='7')
        self.assertEqual([r['id'] for r in selected],['6','5','4','3','2'])
        # Explicit support has a gap; min/max must never silently include the middle game.
        releases[0]['game_versions']=['1.21.1','1.21.3']
        self.assertEqual(compatible_releases([releases[0]],['1.21.2'],rules),[])
        rules['overrides']={'1.21.3':6}
        self.assertEqual(len(compatible_releases(releases,games,rules,blocked=lambda r:r['id']=='7')),6)
        duplicate=dict(releases[-1],id='other-platform-same-bytes')
        self.assertEqual(len(compatible_releases([releases[-1],duplicate],games,rules)),1)

    async def test_mounted_storage_without_hard_links_commits_verified_bytes(self):
        root=Path(self.temp.name)/'cosfs';root.mkdir()
        storage=LocalStorage(dict(id='cos',provider='local',prefix='cache',root=str(root),reserve_gib=0),signing_key='fixture',public_url='https://example.org')
        source=Path(self.temp.name)/'cos-input';source.write_bytes(b'mount fixture')
        digest=hashlib.sha256(source.read_bytes()).hexdigest();key=storage.key('mods',digest)
        with patch('mc_translate.mirror_storage.os.link',side_effect=OSError(errno.EPERM,'Operation not permitted')):
            await storage.put_verified(source,key,digest,source.stat().st_size)
        self.assertEqual(storage.file_path(key).read_bytes(),source.read_bytes())
        self.assertEqual(list(root.rglob('.part-*')),[])
        storage.file_path(key).write_bytes(b'other bytes')
        with self.assertRaises(StorageError):await storage.put_verified(source,key,digest,source.stat().st_size)
        self.assertEqual(storage.file_path(key).read_bytes(),b'other bytes')


if __name__=='__main__':unittest.main()
