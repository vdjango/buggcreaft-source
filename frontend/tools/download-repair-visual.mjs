import {chromium,expect} from '@playwright/test';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
const token=(await readFile('../data/download-repair-admin-token','utf8')).trim();
const root='../data/visual-download-repair';await mkdir(root,{recursive:true});
const browser=await chromium.launch({channel:'msedge'});
const evidence=[];
try{
 const context=await browser.newContext({viewport:{width:1360,height:1000}});
 expect((await context.request.post('https://mirror.bugg.icu/v1/admin/session',{headers:{Authorization:'Bearer '+token}})).status()).toBe(200);
 const page=await context.newPage();
 for(const [section,text] of [['quality','这是慢速判断线'],['storage','优先准备的热门文件数'],['downloads','缓存策略优先使用已有副本']]){
  await page.goto('https://mirror.bugg.icu/admin/mirror/settings/'+section,{waitUntil:'networkidle'});
  await expect(page.getByText(text,{exact:false})).toBeVisible({timeout:20000});
  if(section==='quality')await expect(page.getByLabel('慢速阈值 / KiB/s')).toHaveValue('128');
  if(section==='storage')await expect(page.getByLabel('优先准备的热门文件数')).toHaveValue('200');
  const path=root+'/'+section+'.png';await page.screenshot({path,fullPage:true});evidence.push({section,path});
 }
 await context.close();
}finally{await browser.close();}
await writeFile(root+'/result.json',JSON.stringify(evidence,null,2));
console.log('three actual production settings pages captured; no settings changed');
