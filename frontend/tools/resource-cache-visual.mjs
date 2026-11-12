/** Actual admin + API, with isolated local state or explicit production URL. */
import {chromium,expect} from '@playwright/test';
import {createServer} from 'vite';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../../',import.meta.url));
const production=Boolean(process.env.BUGG_RESOURCE_VISUAL_URL);
const base=process.env.BUGG_RESOURCE_VISUAL_URL||'http://127.0.0.1:15175';
const token=process.env.BUGG_VISUAL_TOKEN||(await readFile(root+'data/resource-review/visual-admin-token','utf8')).trim();
const output=root+'data/visual-resource-cache'+(production?'-production':'');
await mkdir(output,{recursive:true});
const server=production?null:await createServer({configFile:false,root,server:{host:'127.0.0.1',port:15175,strictPort:true,
  proxy:{'/api/v1':'http://127.0.0.1:18649','^/':'http://127.0.0.1:18648'}}});
await server?.listen();
const browser=await chromium.launch({channel:'msedge'});
const captures=[],errors=[];
try{
 for(const [name,width] of [['desktop',1360],['panel',410]]){
  const context=await browser.newContext({viewport:{width,height:950}});
  expect((await context.request.post(base+'/v1/admin/session',{headers:{Authorization:'Bearer '+token}})).status()).toBe(200);
  const page=await context.newPage();
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base+'/admin/mirror/settings/downloads?view=prefetch',{waitUntil:'networkidle'});
  await expect(page.getByText('主动准备资源',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'立即准备',exact:true})).toBeVisible();
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:output+'/'+name+'-settings.png',fullPage:true});captures.push(name+'-settings.png');
  if(production){
   await page.goto(base+'/admin/mirror/tasks',{waitUntil:'networkidle'});
   await expect(page.getByText('实时回源',{exact:true})).toBeVisible();
   await page.evaluate(()=>window.scrollTo(0,0));
   await page.screenshot({path:output+'/'+name+'-tasks.png',fullPage:true});captures.push(name+'-tasks.png');
  }else{
   await page.getByRole('button',{name:'添加固定资源',exact:true}).click();
   await page.getByPlaceholder('例如 iris').last().fill('iris');
   await expect(page.getByRole('button',{name:'立即准备',exact:true})).toBeDisabled();
   await page.evaluate(()=>window.scrollTo(0,0));
   await page.screenshot({path:output+'/'+name+'-editing.png',fullPage:true});captures.push(name+'-editing.png');
   // A network error must not replace the configuration being edited.
   await page.route('**/v1/admin/mirror',route=>route.request().method()==='POST'
      ?route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({message:'测试：镜像节点暂时离线'})}):route.continue());
   const save=page.getByRole('button',{name:'保存本页设置',exact:true});
   if(await save.count()){
    await save.click();await expect(page.getByPlaceholder('例如 iris').last()).toHaveValue('iris');
    await page.evaluate(()=>window.scrollTo(0,0));
    await page.screenshot({path:output+'/'+name+'-save-error.png',fullPage:true});captures.push(name+'-save-error.png');
   }
  }
  await context.close();
 }
 expect(errors).toEqual([]);
 await writeFile(output+'/manifest.json',JSON.stringify({base,production,captures,errors},null,2));
 console.log(JSON.stringify({base,captures,errors}));
}finally{await browser.close();await server?.close();}
