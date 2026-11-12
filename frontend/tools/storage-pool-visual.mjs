/** Compiled source admin: local labelled fixtures or authenticated production read-only review. */
import {chromium,expect} from '@playwright/test';
import {createServer} from 'vite';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../../',import.meta.url));
const production=Boolean(process.env.BUGG_RESOURCE_VISUAL_URL);
const base=process.env.BUGG_RESOURCE_VISUAL_URL||'http://127.0.0.1:15175';
const output=root+'data/visual-storage-pool'+(production?'-production':'-local');
const token=process.env.BUGG_VISUAL_TOKEN||(await readFile(root+'data/resource-review/visual-admin-token','utf8')).trim();
await mkdir(output,{recursive:true});
const server=production?null:await createServer({configFile:false,root,server:{host:'127.0.0.1',port:15175,strictPort:true,proxy:{'/api/v1':'http://127.0.0.1:18649','^/':'http://127.0.0.1:18648'}}});
await server?.listen();
const browser=await chromium.launch({channel:'msedge'}),errors=[],captures=[];
try{
 for(const [mode,width,height] of [['desktop',1440,1000],['mobile',390,844]]){
  const context=await browser.newContext({viewport:{width,height}});
  expect((await context.request.post(base+'/v1/admin/session',{headers:{Authorization:'Bearer '+token}})).status()).toBe(200);
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  const capture=async(name,locator)=>{
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
   const filename=mode+'-'+name+'.png';
   if(locator)await locator.screenshot({path:output+'/'+filename});else await page.screenshot({path:output+'/'+filename,fullPage:false});
   captures.push({filename,width,height,source:production?'production HTTPS':'isolated local API / compiled pages'});
  };
  if(process.env.BUGG_POOL_CAPACITY_ONLY){
   if(!production)await page.route('**/v1/admin/mirror',async route=>{
    const response=await route.fetch(),data=await response.json();
    data.cloud_storage.profiles=[{id:'fixture-only',name:'测试夹具 · OSS 月预算用完',provider:'s3',configured:true,capacity:{used_bytes:1024**3,limit_bytes:10*1024**3,automatic_available_bytes:8*1024**3},egress:{bytes:90*1024**3,limit_bytes:100*1024**3,stop_bytes:90*1024**3,warning:'blocked'}}];
    await route.fulfill({response,json:data});
   });
   await page.goto(base+'/admin/mirror/settings/cloud',{waitUntil:'networkidle'});
   await expect(page.getByText('存储容量',{exact:true})).toBeVisible();
   if(!production)await expect(page.getByRole('alert').filter({hasText:'已达流量分流线'})).toBeVisible();
   await capture(production?'capacity-final':'capacity-warning-fixture');
   await context.close();continue;
  }
  await page.goto(base+'/admin/mirror/settings/downloads?view=prefetch',{waitUntil:'networkidle'});
  await expect(page.getByRole('heading',{name:'主动缓存',exact:true})).toBeVisible();
  await expect(page.getByText('缓存准备情况',{exact:true})).toHaveCount(0);
  await capture('settings');
  const popular=page.locator('.workspace-card').filter({has:page.getByRole('heading',{name:'热门排行与固定项目'})});
  await capture('popular-settings',popular);
  await page.goto(base+'/admin/mirror/settings/downloads',{waitUntil:'networkidle'});
  await page.getByRole('button',{name:'编辑',exact:true}).first().click();
  const target=page.locator('.workspace-drawer select').nth(1);
  await expect(target.locator('option[value="@auto"]')).toHaveCount(1);
  await target.selectOption('@auto');
  await capture('automatic-route');
  await page.goto(base+'/admin/mirror/settings/cloud',{waitUntil:'networkidle'});
  await expect(page.getByText('存储容量',{exact:true})).toBeVisible();await capture('capacity');
  // Room settings must expose auto placement too; inspecting does not save production configuration.
  await page.goto(base+'/admin/mirror/settings/room-cache',{waitUntil:'networkidle'});
  const options=page.locator('option[value="@auto"]');await expect.poll(()=>options.count()).toBeGreaterThan(0);
  const roomStorage=page.locator('.workspace-card').filter({has:page.getByText('客户端临时文件存储',{exact:true})});
  await capture('room-storage',roomStorage);
  await page.goto(base+'/admin/mirror/settings/storage?view=sync',{waitUntil:'networkidle'});
  await expect(page.getByRole('link',{name:/配置主动缓存/})).toBeVisible();
  await expect(page.getByText('优先准备的热门文件数')).toHaveCount(0);
  await capture('sync-settings');
  await page.goto(base+'/admin/mirror/tasks',{waitUntil:'networkidle'});
  await expect(page.getByText('缓存准备情况',{exact:true})).toBeVisible();
  await capture('tasks');
  const preparation=page.locator('.workspace-card').filter({has:page.getByText('缓存准备情况',{exact:true})});
  await capture('preparation',preparation);
  if(!production){
   await page.route('**/v1/admin/mirror',route=>route.request().method()==='POST'?route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({message:'本地故障注入：节点暂时离线，任务未改变'})}):route.continue());
   const button=page.getByRole('button',{name:/准备排行与固定项|继续准备/});
   if(await button.isEnabled()){await button.click();await expect(page.getByRole('alert').filter({hasText:'本地故障注入'})).toBeVisible();await capture('request-error',preparation);}
  }
  await context.close();
 }
 expect(errors).toEqual([]);await writeFile(output+(process.env.BUGG_POOL_CAPACITY_ONLY?'/capacity-manifest.json':'/manifest.json'),JSON.stringify({base,production,captures,errors},null,2));console.log(JSON.stringify({output,captures:captures.map(c=>c.filename),errors}));
}finally{await browser.close();await server?.close();}
