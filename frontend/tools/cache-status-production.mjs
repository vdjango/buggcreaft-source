/** Read-only production review; credentials enter via the parent process environment. */
import {chromium,expect} from '@playwright/test';
import {mkdir,writeFile} from 'node:fs/promises';
const base='https://mirror.bugg.icu',out='E:/code/bugg-source/data/window-cache-production';
await mkdir(out,{recursive:true});
const browser=await chromium.launch({channel:'msedge'}),errors=[],captures=[];
try{
 for(const [mode,width,height] of [['desktop',1440,1000],['mobile',390,844]]){
  const context=await browser.newContext({viewport:{width,height}}),page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{if(m.type()==='error')console.log('browser error:',m.text());});
  page.on('requestfailed',r=>console.log('request failed:',r.url(),r.failure()?.errorText));
  await page.goto(base+'/',{waitUntil:'networkidle'});
  await expect(page.getByText('资源缓存总量',{exact:true})).toBeVisible();
  await expect(page.locator('.source-status')).not.toContainText('—');
  const status=await (await context.request.get(base+'/mirror-api/status')).json();
  expect(status.content_cache.files).toBeGreaterThan(0);expect(status.content_cache.bytes).toBeGreaterThan(1024**3);
  await page.locator('.source-status').scrollIntoViewIfNeeded();
  await page.screenshot({path:out+'/'+mode+'-cache.png'});captures.push(mode+'-cache.png');
  if(process.env.BUGG_VISUAL_TOKEN){
   expect((await context.request.post(base+'/v1/admin/session',{headers:{Authorization:'Bearer '+process.env.BUGG_VISUAL_TOKEN}})).status()).toBe(200);
   await page.goto(base+'/admin',{waitUntil:'networkidle'});
   await page.evaluate(()=>{const a=document.createElement('a');a.href='/admin/nodes';a.textContent='导航验收';document.body.append(a);a.click();});
   try{await expect(page.getByText('已接入节点',{exact:true})).toBeVisible({timeout:10000});}
   catch(e){await page.screenshot({path:out+'/'+mode+'-nodes-failure.png'});console.log('page errors',errors);throw e;}
   await expect(page.locator('.workspace-skeleton')).toHaveCount(0);
   await page.screenshot({path:out+'/'+mode+'-nodes.png'});captures.push(mode+'-nodes.png');
  }
  await context.close();
 }
 expect(errors).toEqual([]);
 await writeFile(out+'/visual.json',JSON.stringify({base,captures,errors,authenticated:!!process.env.BUGG_VISUAL_TOKEN,readOnly:true},null,2));
 console.log(JSON.stringify({captures,errors,authenticated:!!process.env.BUGG_VISUAL_TOKEN}));
}finally{await browser.close();}
