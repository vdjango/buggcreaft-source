/** Actual Vue + local API; only the isolated fixture is mutated. Production mode reads only. */
import {chromium,expect} from '@playwright/test';
import {createServer} from 'vite';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
const production=process.env.BUGG_RUNTIME_PRODUCTION==='1';
const drawerOnly=process.env.BUGG_RUNTIME_DRAWER_ONLY==='1';
const root='E:/code/bugg-source',output=root+'/data/runtime-catalog-'+(production?'production':'review');
const base=production?'https://mirror.bugg.icu':'http://127.0.0.1:15175';
const token=production?process.env.BUGG_VISUAL_TOKEN:(await readFile(output+'/token','utf8')).trim();
const server=production?undefined:await createServer({configFile:false,root,server:{host:'127.0.0.1',port:15175,strictPort:true,proxy:{'^/':'http://127.0.0.1:18648'}}});
await server?.listen();await mkdir(output,{recursive:true});
const browser=await chromium.launch({channel:'msedge'}),errors=[],captures=[];
try{
 for(const [mode,width,height] of [['desktop',1440,1000],['mobile',390,844]]){
  const context=await browser.newContext({viewport:{width,height}}),page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));
  expect((await context.request.post(base+'/v1/admin/session',{headers:{Authorization:'Bearer '+token}})).status()).toBe(200);
  await page.goto(base+'/admin/mirror/runtimes',{waitUntil:'networkidle'});
  await expect(page.getByRole('heading',{name:'加载器与核心',exact:true})).toBeVisible({timeout:15000});
  await expect(page.locator('.workspace-skeleton')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'查询',exact:true})).toBeEnabled();
  async function shot(name){await page.screenshot({path:output+'/'+mode+'-'+name+'.png',fullPage:true});captures.push(mode+'-'+name+'.png');}
  if(!drawerOnly)await shot('versions');
  if(!production){
   await page.getByRole('button',{name:'标记问题',exact:true}).first().click();
   await page.locator('textarea').fill('本地测试数据：需要暂停该版本');
   await shot('mark');
   if(drawerOnly){await page.getByRole('button',{name:'取消',exact:true}).click();await context.close();continue;}
   await page.getByRole('button',{name:'停止分发',exact:true}).click();
   await expect(page.getByText('已停止分发；问题文件清理与可用版本补位已排队。',{exact:true})).toBeVisible();
  }
  await page.getByRole('tab',{name:'缓存规则',exact:true}).click();
  if(!production){await page.getByLabel('游戏范围',{exact:true}).selectOption('selected');await page.getByLabel('指定的游戏版本',{exact:true}).fill('1.21.1\n1.21.2\n1.21.3');}
  await shot('cache-rules');
  await expect(page.getByLabel('每个游戏版本、每个项目的发布版本数',{exact:true})).toBeVisible();
  await page.getByRole('tab',{name:'停供记录',exact:true}).click();await shot('blocked');
  if(!production){
   const restore=page.locator('tr').filter({hasText:'1.11.7'}).getByRole('button',{name:'恢复 / 详情'});
   await restore.click();await page.getByRole('button',{name:'恢复分发',exact:true}).click();
   await expect(page.getByText('已恢复分发；已清理的文件会重新缓存。',{exact:true})).toBeVisible();
   await page.getByRole('tab',{name:'版本目录'}).click();
   await page.getByPlaceholder('搜索版本、游戏版本').fill('no-such-fixture');await page.getByRole('button',{name:'查询',exact:true}).click();
   await expect(page.getByText('暂无匹配版本，可更新目录或调整筛选。',{exact:true})).toBeVisible();await shot('empty');
  }
  await page.goto(base+'/admin/mirror/settings/downloads?view=prefetch',{waitUntil:'networkidle'});
  await expect(page.getByText('加载器与光影核心的版本缓存、问题标记统一在')).toBeVisible();
  expect(await page.getByLabel('加载器 / 文件数',{exact:true}).count()).toBe(0);
  await page.getByText('游戏版本范围、每个游戏版本准备多少个发布版本，统一在').scrollIntoViewIfNeeded();
  await page.screenshot({path:output+'/'+mode+'-shared-settings.png',fullPage:false});captures.push(mode+'-shared-settings.png');
  await page.goto(base+'/admin/mirror/tasks',{waitUntil:'networkidle'});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await context.close();
 }
 expect(errors).toEqual([]);
 await writeFile(output+'/'+(drawerOnly?'drawer-visual.json':'visual.json'),JSON.stringify({base,production,readOnly:production,source:production?'actual production data':'actual compiled Vue + real isolated API, labelled release fixture',captures,errors},null,2));
 console.log(JSON.stringify({captures,errors,production}));
}finally{await browser.close();await server?.close();}
