/** Real admin/catalog flow; failure injection is explicitly labelled and local only. */
import {chromium,expect} from '@playwright/test';
import {createServer} from 'vite';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../../',import.meta.url));
const production=Boolean(process.env.BUGG_RESOURCE_VISUAL_URL);
const base=process.env.BUGG_RESOURCE_VISUAL_URL||'http://127.0.0.1:15175';
const token=process.env.BUGG_VISUAL_TOKEN||(await readFile(root+'data/resource-review/visual-admin-token','utf8')).trim();
const output=root+'data/visual-resource-pins'+(production?'-production':'');
await mkdir(output,{recursive:true});
const server=production?null:await createServer({configFile:false,root,server:{host:'127.0.0.1',port:15175,strictPort:true,
  proxy:{'/api/v1':'http://127.0.0.1:18649','^/':'http://127.0.0.1:18648'}}});
await server?.listen();
const browser=await chromium.launch({channel:'msedge'});
const captures=[],errors=[],checks=[];
try{
 for(const [name,width] of [['desktop',1360],['panel',410]]){
  const context=await browser.newContext({viewport:{width,height:950}});
  expect((await context.request.post(base+'/v1/admin/session',{headers:{Authorization:'Bearer '+token}})).status()).toBe(200);
  const original=await (await context.request.get(base+'/v1/admin/mirror')).json();
  const initial=original.settings.resource_prefetch;
  let saved=false;
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  const capture=async(suffix,locator)=>{
    await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
    const file=name+'-'+suffix+'.png';
    if(locator)await locator.screenshot({path:output+'/'+file});
    else await page.screenshot({path:output+'/'+file,fullPage:true});
    captures.push(file);
  };
  try{
   await page.goto(base+'/admin/mirror/settings/downloads?view=prefetch',{waitUntil:'networkidle'});
   await expect(page.getByText('主动准备资源',{exact:true})).toBeVisible();
   await expect(page.getByText('正在读取资源名称…',{exact:true})).toHaveCount(0,{timeout:60000});
   await expect(page.getByText('资源信息暂时无法读取',{exact:true})).toHaveCount(0);
   const card=page.locator('.workspace-card').filter({has:page.getByText('主动准备资源',{exact:true})});
   await capture('settings',card);
   await expect(card.getByText('项目 ID / 短名',{exact:true})).toHaveCount(0);
   await page.getByRole('button',{name:'添加固定资源',exact:true}).click();
   const drawer=page.getByRole('dialog');
   await drawer.getByPlaceholder('输入资源名称，例如 Iris、Sodium').fill('Iris');
   // Search is intentional; no per-keystroke request flood.
   await drawer.getByRole('button',{name:'搜索',exact:true}).click();
   const iris=drawer.locator('.resource-pin-result').filter({has:page.getByText('Iris Shaders',{exact:true})});
   await expect(iris).toBeVisible({timeout:45000});
   await expect.poll(()=>iris.evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
   await capture('search',drawer);
   await iris.click();
   await expect(drawer.getByLabel('缓存版本',{exact:false})).toBeVisible({timeout:45000});
   const select=drawer.locator('select');
   const choice=await select.locator('option').evaluateAll(options=>options.find(option=>option.value&&!option.textContent.includes('测试版'))?.value);
   expect(choice).toBeTruthy();await select.selectOption(choice);
   const title=await select.locator('option:checked').textContent();
   expect(title).toMatch(/\d.*(?:fabric|neoforge|forge)/i);
   await capture('version',drawer);
   await drawer.getByRole('button',{name:'添加到固定缓存',exact:true}).click();
   await expect(drawer).toHaveCount(0);
   const duplicate=await page.getByText('此资源版本已经在固定缓存中。',{exact:true}).count();
   if(!duplicate)await expect(page.getByRole('button',{name:'立即准备',exact:true})).toBeDisabled();
   await capture('selection',card);
   checks.push({viewport:name,realCatalog:true,selectedVersion:title,duplicate:Boolean(duplicate)});
   if(!production&&!duplicate){
    await page.getByRole('button',{name:'保存本页设置',exact:true}).click();
    await expect.poll(async()=>{
      const data=await(await context.request.get(base+'/v1/admin/mirror')).json();
      return data.settings.resource_prefetch.pins.some(pin=>pin.version===choice);
    }).toBe(true);
    saved=true;await page.reload({waitUntil:'networkidle'});
    await expect(page.getByText('正在读取资源名称…',{exact:true})).toHaveCount(0,{timeout:60000});
    checks.push({viewport:name,persistedAndReloaded:true});
    // Explicit test response; no production faults or configuration writes.
    await page.route('**/v1/catalog/modrinth/search?**',route=>route.fulfill({status:503,contentType:'application/json',body:'{"code":"visual_test_offline"}'}));
    await page.getByRole('button',{name:'添加固定资源',exact:true}).click();
    await expect(drawer.getByRole('alert')).toContainText('资源目录暂时无法连接');
    await page.evaluate(()=>{const note=document.createElement('p');note.textContent='测试场景：模拟资源目录暂时离线，现有固定配置保持不变。';note.style.cssText='padding:12px;background:#fff3cd;color:#674900';document.querySelector('.workspace-drawer-content').prepend(note);});
    await capture('catalog-error',drawer);
    await drawer.getByRole('button',{name:'取消',exact:true}).click();
   }
  }finally{
   if(saved){const restored=await context.request.post(base+'/v1/admin/mirror',{data:{resource_prefetch:initial}});expect(restored.status()).toBe(200);}
   await context.close();
  }
 }
 expect(errors).toEqual([]);
 await writeFile(output+'/manifest.json',JSON.stringify({base,production,captures,errors,checks},null,2));
 console.log(JSON.stringify({base,captures,errors,checks}));
}finally{await browser.close();await server?.close();}
