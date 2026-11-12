/** Actual compiled Vue pages with a local API and clearly labelled node fixture. */
import {chromium,expect} from '@playwright/test';
import {createServer} from 'vite';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
const root='E:/code/bugg-source';
const output=root+'/data/window-cache-review';
const base='http://127.0.0.1:15175';
const token=(await readFile(output+'/token','utf8')).trim();
const server=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:15175,strictPort:true,proxy:{'^/':'http://127.0.0.1:18648'}}});
await server.listen();await mkdir(output,{recursive:true});
const browser=await chromium.launch({channel:'msedge'}),errors=[],captures=[];
try{
 for(const [mode,width,height] of [['desktop',1440,1000],['mobile',390,844]]){
  const context=await browser.newContext({viewport:{width,height}});
  expect((await context.request.post(base+'/v1/admin/session',{headers:{Authorization:'Bearer '+token}})).status()).toBe(200);
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/v1/admin/nodes',r=>r.fulfill({json:{nodes:[{id:'fixture',name:'本地测试数据 · 分发节点',public_url:'https://fixture.invalid',region:'测试',state:'active',online:true,last_seen:Date.now()/1000}]}}));
  await page.route('**/v1/admin/room-cache/nodes',r=>r.fulfill({json:{nodes:[]}}));
  await page.goto(base+'/mirror',{waitUntil:'networkidle'});
  await expect(page.locator('.source-status')).toContainText('3.00 GiB');
  await page.locator('.source-status').scrollIntoViewIfNeeded();
  await page.screenshot({path:output+'/'+mode+'-public-cache.png'});captures.push(mode+'-public-cache.png');
  // A normal SPA navigation from another authenticated management page.
  await page.goto(base+'/admin',{waitUntil:'networkidle'});
  await page.evaluate(()=>{const a=document.createElement('a');a.href='/admin/nodes';a.textContent='测试导航';document.body.append(a);a.click();});
  await expect(page.getByText('本地测试数据 · 分发节点',{exact:true})).toBeVisible();
  await page.screenshot({path:output+'/'+mode+'-nodes.png'});captures.push(mode+'-nodes.png');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await context.close();
 }
 expect(errors).toEqual([]);await writeFile(output+'/visual.json',JSON.stringify({base,source:'local compiled Vue + real local cache ledger, labelled node fixture',captures,errors},null,2));
 console.log(JSON.stringify({captures,errors}));
}finally{await browser.close();await server.close();}
