/** Real compiled pages + real local services; Vite supplies only a local HTTP proxy. */
import {createServer} from 'vite'
import {chromium,expect} from '@playwright/test'
import {mkdir,readFile,writeFile} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'

const root=fileURLToPath(new URL('../../',import.meta.url))
const production=Boolean(process.env.BUGG_VISUAL_BASE_URL)
const publicCopy=process.env.BUGG_VISUAL_PUBLIC_COPY==='1'
const output=root+(publicCopy?'data/visual-public-copy'+(production?'-production/':'-local/'):(production?'data/visual-production/':'data/visual-migration/'))
const token=process.env.BUGG_VISUAL_TOKEN
const cookie=process.env.BUGG_VISUAL_COOKIE
if(!token&&!cookie&&!publicCopy)throw Error('BUGG_VISUAL_TOKEN or BUGG_VISUAL_COOKIE required')
const server=production?null:await createServer({configFile:false,root,server:{host:'127.0.0.1',port:15174,strictPort:true,
  proxy:{'/api/v1':process.env.BUGG_VISUAL_API_URL||'http://127.0.0.1:18449','^/':process.env.BUGG_VISUAL_HUB_URL||'http://127.0.0.1:18448'}}})
await server?.listen()
const browser=await chromium.launch(process.env.BUGG_VISUAL_EDGE==='1'?{channel:'msedge'}:{})
const context=await browser.newContext({viewport:{width:1440,height:1000}})
const base=process.env.BUGG_VISUAL_BASE_URL||'http://127.0.0.1:15174'
const captures=[],errors=[]
const manifest=process.env.BUGG_VISUAL_ONLY?'manifest-recheck.json':'manifest.json'
const digest=production||publicCopy?(process.env.BUGG_VISUAL_ARTIFACT_DIGEST||''):(await readFile(root+'data/migration-preview/digest.txt','utf8')).trim()
const routes=[
  ['catalog','/'],['mirror','/mirror'],['docs','/docs'],['developers','/developers'],
  ['project',process.env.BUGG_VISUAL_PROJECT_PATH||'/projects/modrinth/Visual01'],['overview','/admin'],
  ['feedback','/admin/feedback'],['clients','/admin/clients'],['nodes','/admin/nodes'],
  ['translation-tasks','/admin/tasks'],['translation-settings','/admin/settings'],['mail','/admin/mail'],
  ['cache','/admin/mirror/resources'],['temporary','/admin/mirror/temporary'],
  ['sources','/admin/mirror/sources'],['quality','/admin/mirror/quality'],
  ['sync','/admin/mirror/tasks'],['transfers','/admin/mirror/transfers'],
  ...['downloads','sources','quality','cloud','storage','room-cache','cloudflare'].map(key=>['settings-'+key,'/admin/mirror/settings/'+key]),
  ['artifacts','/content/artifacts'],...(digest?[['artifact','/content/artifacts/'+digest]]:[]),
  ['collections','/operations/collections'],['new-collection','/operations/collections/new'],
  ['archive-status','/operations/storage'],['dependency-settings','/settings'],
  ['search','/search'],['search-jobs','/operations/search/jobs'],['search-status','/operations/search/status'],
]
await mkdir(output,{recursive:true})
async function extraCapture(page,name,path,bottom=false,anchor=''){
  for(const [mode,width,height] of [['desktop',1440,1000],['mobile',390,844]]){
    await page.setViewportSize({width,height})
    await page.evaluate(({scroll,anchor,production})=>{
      if(!production&&!document.getElementById('migration-fixture')){
        const mark=document.createElement('div');mark.id='migration-fixture';mark.textContent='本地迁移验收 · 测试资源 / 测试 JAR · 非生产数据';document.body.prepend(mark)
      }
      const target=anchor?document.querySelector(anchor):null
      if(target)window.scrollTo({top:window.scrollY+target.getBoundingClientRect().top-80,behavior:'instant'})
      else window.scrollTo({top:scroll?document.body.scrollHeight:0,behavior:'instant'})
    },{scroll:bottom,anchor,production})
    const filename=`extra-${name}-${mode}.png`
    await page.screenshot({path:output+filename,fullPage:false})
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)
    captures.push({filename,path,mode,width,height,overflow,title:await page.locator('h1').first().innerText(),source:production?'production HTTPS':'real local HTTP / isolated fixture'})
    if(overflow>1)errors.push({path,mode,error:'horizontal overflow '+overflow})
  }
}
try{
  if(process.env.BUGG_VISUAL_EXTRA==='1'&&!production){
    const anonymous=await browser.newContext()
    const page=await anonymous.newPage()
    await page.goto(base+'/admin',{waitUntil:'networkidle'})
    await extraCapture(page,'login','/admin')
    await page.goto('http://127.0.0.1:18451/setup',{waitUntil:'networkidle'})
    await extraCapture(page,'setup','/setup')
    await anonymous.close()
  }
  if(cookie)await context.addCookies([{name:'translation_admin',value:cookie,url:base,httpOnly:true,secure:base.startsWith('https:') ,sameSite:'Strict'}])
  else if(token){
    const login=await context.request.post(base+'/v1/admin/session',{headers:{Authorization:'Bearer '+token}})
    expect(login.status()).toBe(200)
  }
  const navigation=[];
  if(process.env.BUGG_VISUAL_NAVIGATION==='1'){
    const page=await context.newPage();
    page.on('pageerror',error=>errors.push({path:page.url(),error:error.message}));
    await page.goto(base+'/admin',{waitUntil:'networkidle'});
    const sentinel='admin-navigation-'+Date.now();
    await page.evaluate(value=>{window.__navigationSentinel=value;},sentinel);
    const groups=await page.locator('.site-desktop-links a').evaluateAll(items=>items.map(item=>({href:item.getAttribute('href'),label:item.textContent})));
    for(const group of groups){
      await page.locator('.site-desktop-links a').filter({hasText:group.label}).click();
      await expect(page).toHaveURL(base+group.href);
      const links=await page.locator('.admin-sidebar a').evaluateAll(items=>items.map(item=>({href:item.getAttribute('href'),label:item.textContent})));
      for(const link of links){
        expect(link.href).not.toBe('/');expect(link.href).not.toBe('/search');
        await page.locator('.admin-sidebar a').filter({hasText:link.label}).click();
        await expect(page).toHaveURL(base+link.href);
        await expect(page.locator('.site-nav-admin')).toBeVisible();
        await expect(page.locator('.admin-sidebar a[aria-current="page"]')).toHaveCount(1);
        await expect(page.locator('h1').first()).toBeVisible();
        expect(await page.evaluate(()=>window.__navigationSentinel)).toBe(sentinel);
        expect(await page.locator('.theme-toggle,.search-public-nav,.sidebar').count()).toBe(0);
        navigation.push(link);
      }
    }
    await page.setViewportSize({width:390,height:844});
    await page.locator('.site-mobile-menu summary').click();
    await page.locator('.site-mobile-menu a').filter({hasText:'运维监控'}).click();
    await expect(page).toHaveURL(base+'/admin');
    await expect(page.locator('.site-mobile-menu')).not.toHaveAttribute('open','');
    await page.locator('.admin-sidebar a').filter({hasText:'依赖存储与额度'}).click();
    await expect(page).toHaveURL(base+'/operations/storage');
    await expect(page.locator('.dependency-workspace h1')).toHaveText('存储与额度');
    await page.close();
    await writeFile(output+'navigation.json',JSON.stringify({navigation,errors,mobile_group_and_storage:true,one_application:true},null,2));
    console.log('Clicked '+navigation.length+' admin menu destinations in one application');
  }
  for(const [name,path] of routes.filter(([name])=>!process.env.BUGG_VISUAL_ONLY||process.env.BUGG_VISUAL_ONLY.split(',').includes(name))){
    const page=await context.newPage()
    page.on('pageerror',error=>errors.push({path,error:error.message}))
    await page.goto(base+path,{waitUntil:'networkidle'})
    if(process.env.BUGG_VISUAL_THEME==='light'){
      if(await page.getByRole('button',{name:'切换到浅色主题',exact:true}).count())await page.getByRole('button',{name:'切换到浅色主题',exact:true}).click()
      await expect(page.locator('html')).toHaveAttribute('data-theme','light')
    }
    await expect(page.locator('h1').first()).toBeVisible()
    if(path.startsWith('/content/')||path.startsWith('/operations/')||path==='/settings'){
      await expect(page.locator('.site-nav-admin')).toBeVisible()
      await expect(page.locator('.dependency-workspace')).toBeVisible()
    }
    if(path!=='/search'&&(path.startsWith('/admin')||path.startsWith('/operations/')||path.startsWith('/content/')||path==='/settings')){
      await expect(page.locator('.admin-sidebar a[href="/"]')).toHaveCount(0)
      await expect(page.locator('.admin-sidebar a[href="/search"]')).toHaveCount(0)
    }
    if(name==='artifact'&&!production)await expect(page.getByRole('heading',{level:3}).first()).toContainText('视觉验收模组（测试包内名称）')
    if(path.startsWith('/admin/mirror/'))await expect(page.locator('.workspace-stack').first()).toBeVisible()
    for(const [mode,width,height] of [['desktop',1440,1000],['mobile',390,844]]){
      await page.setViewportSize({width,height})
      await page.evaluate((production)=>{
        if(production)return
        if(document.getElementById('migration-fixture'))return
        const mark=document.createElement('div');mark.id='migration-fixture';mark.textContent='本地迁移验收 · 测试资源 / 测试 JAR · 非生产数据';mark.setAttribute('role','note');document.body.prepend(mark)
      },production)
      const overflow=await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)
      if(publicCopy){
        expect(await page.locator('body').innerText()).not.toMatch(/BMCLAPI|bangbang93|阿里云|华为云|南京大学/i)
        for(const anchor of name==='mirror'?['mirror-connect']:['downloads','source-discovery']){
          const section=page.locator('#'+anchor)
          await expect(section).toBeVisible()
          const filename=name+'-'+anchor+'-'+mode+'.png'
          await section.screenshot({path:output+filename})
          captures.push({filename,path,mode,width,height,overflow,text:await section.innerText(),source:base})
        }
        if(overflow>1)errors.push({path,mode,error:'horizontal overflow '+overflow})
        continue
      }
      const number=routes.findIndex(([key])=>key===name)*2+(mode==='desktop'?1:2)
      const filename=`${String(number).padStart(2,'0')}-${name}-${mode}.png`
      await page.screenshot({path:output+filename,fullPage:false})
      captures.push({filename,path,mode,width,height,overflow,title:await page.locator('h1').first().innerText(),
        text:await page.locator('main').first().innerText(),source:production?'production HTTPS':'real local HTTP services / isolated labelled fixture database'})
      if(overflow>1)errors.push({path,mode,error:'horizontal overflow '+overflow})
    }
    if(name==='settings-storage')await extraCapture(page,'storage-categories',path,false,'.workspace-card h3')
    if(process.env.BUGG_VISUAL_EXTRA==='1'&&['catalog','mirror','project','settings-room-cache','docs','artifact','new-collection'].includes(name))await extraCapture(page,name+'-bottom',path,true)
    if(process.env.BUGG_VISUAL_EXTRA==='1'&&name==='mirror')await extraCapture(page,'mirror-connect',path,false,'#mirror-connect')
    if(process.env.BUGG_VISUAL_EXTRA==='1'&&name==='catalog')await extraCapture(page,'catalog-card',path,false,'#projects')
    if(process.env.BUGG_VISUAL_EXTRA==='1'&&name==='project'){
      const versions=page.locator('details').filter({has:page.locator('summary').filter({hasText:'上游版本信息'})}).first()
      if(await versions.count()){
        await versions.evaluate(element=>{element.open=true})
        await extraCapture(page,'project-versions',path,false,'details[open]')
      }
    }
    if(process.env.BUGG_VISUAL_EXTRA==='1'&&name==='artifacts'){
      await page.setViewportSize({width:390,height:844})
      await page.locator('.site-mobile-menu summary').click()
      await expect(page.locator('.site-mobile-menu[open]')).toBeVisible()
      const filename='extra-workbench-menu-mobile.png'
      await page.screenshot({path:output+filename,fullPage:false})
      captures.push({filename,path,mode:'mobile',width:390,height:844,source:'real local HTTP / expanded menu'})
    }
    if(process.env.BUGG_VISUAL_EXTRA==='1'&&name==='overview'){
      for(const [view,label] of [['traffic','翻译接口访问'],['usage','翻译用量与费用']]){
        await page.getByRole('tab',{name:label,exact:true}).click()
        await page.waitForLoadState('networkidle')
        await expect(page.locator('.workspace-metrics').first()).toBeVisible()
        await extraCapture(page,'overview-'+view,'/admin?view='+view)
      }
    }
    if(process.env.BUGG_VISUAL_STORAGE==='1'&&production&&name==='settings-cloud'){
      const row=page.locator('tbody tr').filter({hasText:'成都本地磁盘'})
      await expect(row).toBeVisible()
      await row.getByRole('button',{name:'管理',exact:true}).click()
      await expect(page.getByRole('dialog').getByLabel('已挂载的目录')).toHaveValue('/var/lib/bugg-source/storage')
      await extraCapture(page,'local-storage-production-editor',path)
      await page.getByRole('dialog').getByRole('button',{name:'关闭详情',exact:true}).click()
    }
    if(process.env.BUGG_VISUAL_STORAGE==='1'&&name==='settings-downloads'){
      await page.locator('tbody tr').first().getByRole('button',{name:'编辑',exact:true}).click()
      await expect(page.getByRole('dialog')).toBeVisible()
      await extraCapture(page,'storage-routing-editor',path)
      await page.getByRole('dialog').getByRole('button',{name:'完成编辑',exact:true}).click()
    }
    if(process.env.BUGG_VISUAL_STORAGE==='1'&&!production&&name==='settings-cloud'){
      await page.getByLabel('新增存储类型').selectOption('local')
      await page.getByRole('button',{name:'添加空间',exact:true}).click()
      await expect(page.getByRole('dialog')).toBeVisible()
      await page.getByRole('dialog').getByLabel('名称',{exact:true}).fill('本地磁盘 · 未保存测试配置')
      await page.getByRole('dialog').getByLabel('已挂载的目录').fill('/var/lib/bugg-source/storage')
      const storagePath=fileURLToPath(new URL('../../data/storage-review/mounted/',import.meta.url))
      if(process.env.BUGG_VISUAL_STORAGE_WRITE==='1'){
        await mkdir(storagePath,{recursive:true})
        await page.getByRole('dialog').getByLabel('已挂载的目录').fill(storagePath)
      }
      await extraCapture(page,'local-storage-editor',path)
      if(process.env.BUGG_VISUAL_STORAGE_WRITE==='1'){
        await page.getByRole('dialog').getByRole('button',{name:'保存设置',exact:true}).click()
        try{await expect(page.getByRole('dialog')).toHaveCount(0)}catch(error){
          await extraCapture(page,'local-storage-save-error',path)
          throw Error((await page.getByRole('dialog').getByRole('alert').allTextContents()).join('; ')||String(error))
        }
        const row=page.locator('tbody tr').filter({hasText:'本地磁盘 · 未保存测试配置'})
        await expect(row).toContainText(storagePath)
        await page.reload({waitUntil:'networkidle'})
        await expect(row).toContainText(storagePath)
        await extraCapture(page,'local-storage-saved',path)
        await row.getByRole('button',{name:'管理',exact:true}).click()
        await page.getByRole('dialog').getByLabel('已挂载的目录').fill(storagePath+'not-mounted')
        await page.getByRole('dialog').getByRole('button',{name:'保存设置',exact:true}).click()
        await expect(page.getByRole('dialog').getByRole('alert')).toContainText('不存在')
        await extraCapture(page,'local-storage-missing-mount',path)
        await page.getByRole('dialog').getByRole('button',{name:'移除配置',exact:true}).click()
        await page.getByRole('button',{name:'保存本页设置',exact:true}).click()
        await expect(row).toHaveCount(0)
      }else{
      await page.getByRole('button',{name:'关闭详情',exact:true}).click()
      }
      await page.getByRole('tab',{name:'默认缓存',exact:true}).click()
      await extraCapture(page,'local-storage-budget',path+'?view=local')
    }
    if(process.env.BUGG_VISUAL_STORAGE==='1'&&!production&&name==='settings-room-cache'){
      await page.getByLabel('缓存方式',{exact:false}).selectOption('full')
      await extraCapture(page,'room-cache-full',path)
      await extraCapture(page,'room-storage-bottom',path,true)
    }
    if(process.env.BUGG_VISUAL_INVITATION==='1'&&!production&&name==='settings-room-cache'){
      await page.getByRole('button',{name:'接入联机节点',exact:true}).click()
      const dialog=page.getByRole('dialog')
      await dialog.getByLabel('节点名称').fill('本地视觉测试节点')
      await dialog.getByRole('button',{name:'生成一次性接入邀请',exact:true}).click()
      await expect(dialog.locator('.workspace-invite code')).toContainText('buggcache1.')
      await expect(dialog).not.toContainText('联机节点后台 →')
      await dialog.locator('.workspace-invite code').evaluate(element=>{element.textContent='本地测试邀请已生成 · 凭证遮盖'})
      await extraCapture(page,'room-node-invitation',path)
      await dialog.getByRole('button',{name:'关闭详情',exact:true}).click()
    }
    await page.close()
    console.log('Captured '+name)
    if(production)await new Promise(resolve=>setTimeout(resolve,3000))
  }
  await writeFile(output+manifest,JSON.stringify({captures,errors},null,2))
  console.log(JSON.stringify({screenshots:captures.length,errors}))
  expect(errors).toEqual([])
}finally{
  await writeFile(output+manifest,JSON.stringify({captures,errors},null,2))
  await context.close();await browser.close();await server?.close()
}
