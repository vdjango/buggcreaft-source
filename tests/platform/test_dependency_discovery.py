"""One persisted discovery graph; publisher hints never become package facts."""
import asyncio
import json
import unittest

import test_mirror as mirror_fixture

from mc_translate.cache_facts import CacheFacts


class DiscoveryCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await mirror_fixture.MirrorCase.asyncSetUp(self)
        self.mirror.scheduler.cancel()
        await asyncio.gather(self.mirror.scheduler, return_exceptions=True)
        self.discovery = self.mirror.facts.discovery

    async def asyncTearDown(self):
        await mirror_fixture.MirrorCase.asyncTearDown(self)

    def release(self, project, version, dependencies=()):
        return dict(id=version, project_id=project, game_versions=['26.3'], loaders=['neoforge'],
                    dependencies=list(dependencies), files=[dict(primary=True,
                        url=f'https://cdn.modrinth.com/data/{project}/{version}.jar',size=100,
                        hashes=dict(sha256=(project[0]*64)))])

    def facts(self, digest, mod, targets=()):
        return dict(artifact_sha256=digest,status='complete_declared',mods=[dict(mod_id=mod,
            loader='neoforge',container_path='root',dependencies=[dict(target_id=t,relation='required') for t in targets])])

    async def row(self, key):
        return await self.mirror.one('SELECT * FROM dependency_releases WHERE key=?',(key,))

    async def test_cycle_deduplicates_and_real_identity_closes_unresolved_edge_after_restart(self):
        a=self.release('alpha','a1',[dict(dependency_type='required',project_id='beta',version_id='b1')])
        b=self.release('beta','b1',[dict(dependency_type='required',project_id='alpha',version_id='a1')])
        async def get(provider,route,params=None,**kwargs):
            return a if route.endswith('/a1') else b if route.endswith('/b1') else [b]
        self.mirror.resources.get=get
        ka=(await self.discovery.observe('modrinth',[a],'request'))[0]
        await self.discovery.expand(await self.row(ka),self.facts('a'*64,'actual_alpha',['actual_beta']))
        unresolved=await self.mirror.one("SELECT COUNT(*) n FROM dependency_edges WHERE child='' AND target='mod:actual_beta'")
        self.assertEqual(unresolved['n'],1)
        kb=(await self.discovery.observe('modrinth',[b],'hotspot'))[0]
        await self.discovery.expand(await self.row(kb),self.facts('b'*64,'actual_beta',['actual_alpha']))
        self.mirror.facts=CacheFacts(self.mirror)
        await self.mirror.facts.open()
        status=await self.mirror.facts.discovery.status()
        self.assertEqual(status['unresolved_bindings'],0)
        count=await self.mirror.one('SELECT COUNT(*) n FROM dependency_releases')
        self.assertEqual(count['n'],2)
        # A parser-backed relation is retained per actual file, not replaced by
        # the publisher's project name or by the last observed release.
        binding=await self.mirror.one("SELECT digest FROM dependency_bindings WHERE mod_id='actual_beta'")
        self.assertEqual(binding['digest'],'b'*64)

    async def test_depth_and_distribution_stop_remain_explicit(self):
        a=self.release('alpha','a1',[dict(dependency_type='required',project_id='beta',version_id='b1')])
        key=(await self.discovery.observe('modrinth',[a],'hotspot',8))[0]
        await self.discovery.expand(await self.row(key),self.facts('a'*64,'actual_alpha',['actual_beta']))
        edge=await self.mirror.one("SELECT reason FROM dependency_edges WHERE target='b1'")
        self.assertEqual(edge['reason'],'depth_budget')
        self.mirror.runtimes.policy['version_ids']=['b1']
        self.assertEqual(await self.discovery.observe('modrinth',[self.release('beta','b1')]),[])
        # Unsupported metadata is kept partial by the runner, never zero edges.
        self.assertGreater((await self.discovery.status())['unresolved_bindings'],0)
