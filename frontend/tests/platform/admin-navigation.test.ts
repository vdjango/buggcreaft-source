// @vitest-environment jsdom
import {describe,expect,it} from 'vitest';
import {createSSRApp,h} from 'vue';
import {renderToString} from 'vue/server-renderer';
import {adminItemActive,adminSectionFor,adminSections,isAdminPath} from '../../src/navigation';
import {platformRoute} from '../../src/platform/platformRoutes';
import SiteNav from '../../src/platform/SiteNav.vue';

describe('one management navigation',()=>{
  it('keeps public catalogue and search out of every admin menu',()=>{
    for(const section of adminSections)for(const item of section.items){
      expect(isAdminPath(item.href)).toBe(true);
      expect(platformRoute(item.href),item.href).toBeDefined();
    }
    expect(isAdminPath('/')).toBe(false);
    expect(isAdminPath('/search')).toBe(false);
  });
  it('renders the same management navigation on legacy workspace URLs',async()=>{
    for(const path of ['/admin','/operations/storage','/operations/search/status','/content/artifacts','/settings']){
      history.replaceState(null,'',path);
      const html=await renderToString(createSSRApp({render:()=>h(SiteNav,{authenticated:true,initialPath:path})}));
      expect(html).toContain('site-nav-admin');
      expect(html).not.toContain('资源搜索');
      expect(html).not.toContain('译文库');
      expect(html).toContain('运维监控');
      expect(html).not.toContain('theme-toggle');
    }
    history.replaceState(null,'','/');
  });
  it('resolves detail and index routes without falling back to the homepage',()=>{
    const detail='/content/artifacts/'+'a'.repeat(64);
    expect(platformRoute(detail)?.entry).toBe('admin-workbench');
    expect(adminItemActive(detail,'/content/artifacts')).toBe(true);
    expect(adminSectionFor('/operations/search/status')).toBe('monitor');
    expect(platformRoute('/operations/storage')?.entry).toBe('admin-workbench');
    expect(platformRoute('/search')?.entry).toBe('search');
  });
});
