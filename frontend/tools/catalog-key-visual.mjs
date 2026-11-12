/** Real admin API; read-only, never writes production credentials. */
import {chromium,expect} from '@playwright/test';
import {createServer} from 'vite';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../../',import.meta.url));
const production=Boolean(process.env.BUGG_RESOURCE_VISUAL_URL);
const base=process.env.BUGG_RESOURCE_VISUAL_URL||'http://127.0.0.1:15175';
const token=process.env.BUGG_VISUAL_TOKEN||(await readFile(root+'data/resource-review/visual-admin-token','utf8')).trim();
const output=root+'data/visual-catalog-key'+(production?'-production':'');
await mkdir(output,{recursive:true});
const server=production?null:await createServer({configFile:false,root,server:{host:'127.0.0.1',port:15175,strictPort:true,proxy:{'/api/v1':'http://127.0.0.1:18649','^/':'http://127.0.0.1:18648'}}});
await server?.listen();
const browser=await chromium.launch({channel:'msedge'}),captures=[],errors=[];
try{
 for(const [name,width] of [['desktop',1360],['panel',410]]){
  const context=await browser.newContext({viewport:{width,height:950}});
  expect((await context.request.post(base+'/v1/admin/session',{headers:{Authorization:'Bearer '+token}})).status()).toBe(200);
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/admin/mirror/settings/downloads?view=prefetch',{waitUntil:'networkidle'});
  const section=page.locator('details').filter({has:page.locator('summary').filter({hasText:'CurseForge 接入'})});
  await section.locator('summary').click();
  const field=section.getByLabel('API 密钥',{exact:true});
  await expect(field).toHaveAttribute('type','password');await expect(field).toHaveValue('');
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await section.scrollIntoViewIfNeeded();
  const path=name+'-catalog-key.png';await section.screenshot({path:output+'/'+path});captures.push(path);
  expect(await section.evaluate(n=>n.scrollWidth<=n.clientWidth+1)).toBe(true);
  await context.close();
 }
 expect(errors).toEqual([]);
 await writeFile(output+'/manifest.json',JSON.stringify({production,readOnly:true,realAPI:true,captures,errors},null,2));
}finally{await browser.close();await server?.close();}
