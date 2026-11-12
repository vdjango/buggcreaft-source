/** Actual compiled pages, isolated PostgreSQL corpus, real model; no API interception. */
import {createServer} from 'vite'
import {chromium,expect} from '@playwright/test'
import {mkdir,writeFile} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
const root=fileURLToPath(new URL('../../',import.meta.url)),output=root+'data/visual-search/'
const server=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:15175,strictPort:true,proxy:{
  '^/api/v1/search(?:\\?|/|$)':{target:'http://127.0.0.1:18448',rewrite:path=>path.replace('/api/v1/search','/v1/search')},
  '^/api/v1/projects/[^/]+/similar':{target:'http://127.0.0.1:18448',rewrite:path=>path.replace('/api/v1/projects/','/v1/search/projects/')},
  '/api/v1':'http://127.0.0.1:18449','^/':'http://127.0.0.1:18448'}}})
await server.listen()
const browser=await chromium.launch({channel:'msedge'})
const context=await browser.newContext(),errors=[],captures=[]
const page=await context.newPage()
page.on('pageerror',error=>errors.push(error.message))
page.on('request',request=>{if(page.url().includes('/search?')&&request.url().includes('/api/v1/collections'))errors.push('Public search requested admin collections')})
const base='http://127.0.0.1:15175'
async function capture(name,path,anchor=''){
  if(process.env.BUGG_VISUAL_ONLY&&!process.env.BUGG_VISUAL_ONLY.split(',').includes(name))return
  for(const [mode,width,height] of [['desktop',1440,1000],['mobile',390,844]]){
    await page.setViewportSize({width,height})
    await page.evaluate(({anchor,tableEnd})=>{
      if(!document.getElementById('search-test-fixture')){const label=document.createElement('p');label.id='search-test-fixture';label.textContent='本地搜索验收 · 隔离 PostgreSQL / 真实 ONNX 模型 / 非生产资源';label.style.cssText='font:12px sans-serif;margin:0;padding:8px 14px;background:#fff5df;color:#5c4200';document.body.prepend(label)}
      if(anchor)document.querySelector(anchor)?.scrollIntoView({block:'start'});else window.scrollTo(0,0)
      if(tableEnd){const table=document.querySelector('.table-scroll');if(table)table.scrollLeft=table.scrollWidth}
    },{anchor,tableEnd:name==='jobs-table-end'})
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)
    if(overflow>1)errors.push(`${name}/${mode} overflow ${overflow}`)
    const filename=`${name}-${mode}.png`
    await page.screenshot({path:output+filename,fullPage:false})
    captures.push({filename,path,mode,width,height,overflow,text:await page.locator('main').innerText()})
  }
}
try{
  await mkdir(output,{recursive:true})
  if(process.env.BUGG_VISUAL_FAILURES==='1'){
    await page.goto(base+'/search?q='+encodeURIComponent('铜制漏斗')+'&mode=hybrid',{waitUntil:'networkidle'})
    await expect(page.locator('.search-notice')).toContainText('已显示文字匹配结果')
    await expect(page.locator('.search-result')).toHaveCount(1)
    await capture('degraded',page.url(),'.search-notice')
    await page.goto(base+'/search?q='+encodeURIComponent('铜制漏斗')+'&mode=semantic',{waitUntil:'networkidle'})
    await expect(page.locator('[role="alert"]')).toBeVisible()
    await capture('semantic-unavailable',page.url(),'[role="alert"]')
  }else{
  await page.goto(base+'/',{waitUntil:'networkidle'})
  await capture('home-navigation','/')
  await page.setViewportSize({width:390,height:844})
  await page.locator('.site-mobile-menu summary').click()
  await expect(page.locator('.site-mobile-menu').getByRole('link',{name:'资源搜索',exact:true})).toBeVisible()
  await capture('home-menu','/')
  await page.goto(base+'/search?q='+encodeURIComponent('铜制漏斗')+'&mode=lexical',{waitUntil:'networkidle'})
  await expect(page.locator('.search-result')).toHaveCount(1)
  await expect(page.locator('.search-snippet')).toContainText('正文')
  await expect(page.locator('.search-snippet mark').first()).toBeVisible()
  await capture('fulltext',page.url())
  await capture('fulltext-result',page.url(),'.search-result')
  await page.goto(base+'/search?q='+encodeURIComponent('出门冒险时记住回家的路线')+'&mode=semantic',{waitUntil:'networkidle'})
  await expect(page.locator('.search-result').first()).toContainText('远行手册')
  await capture('semantic',page.url())
  await capture('semantic-result',page.url(),'.search-result')
  await page.locator('.search-result').first().getByRole('button',{name:'功能相近'}).click()
  await expect(page.locator('.similar-section .search-result').first()).toBeVisible()
  await capture('similar',page.url(),'.similar-section')
  await page.goto(base+'/search?q=zzznosuchresourcetest9988&mode=lexical',{waitUntil:'networkidle'})
  await expect(page.getByText('没有找到符合条件的已收录资源。试试其他关键词或放宽筛选。')).toBeVisible()
  await capture('empty',page.url())
  await capture('empty-message',page.url(),'.section-heading')
  await page.goto(base+'/search?q='+encodeURIComponent('铜制漏斗')+'&mode=lexical&source=curseforge',{waitUntil:'networkidle'})
  await expect(page.locator('.search-result')).toHaveCount(0)
  await capture('filtered-empty',page.url())
  await capture('filtered-empty-message',page.url(),'.section-heading')
  await page.goto(base+'/operations/search/jobs',{waitUntil:'networkidle'})
    await expect(page.locator('.admin-access')).toBeVisible()
  await capture('jobs-disconnected',page.url())
  const token=process.env.BUGG_VISUAL_TOKEN
  if(!token)throw Error('BUGG_VISUAL_TOKEN required')
  const login=await context.request.post(base+'/v1/admin/session',{headers:{Authorization:'Bearer '+token}})
  expect(login.status()).toBe(200)
  for(const [name,path] of [['jobs','/operations/search/jobs'],['status','/operations/search/status']]){
    await page.goto(base+path,{waitUntil:'networkidle'})
    await expect(page.locator('.site-nav-admin')).toBeVisible()
    await expect(page.locator('h1')).toContainText(name==='jobs'?'搜索索引作业':'搜索索引状态')
    await capture(name,path)
    if(name==='jobs'){
      await page.locator('.table-scroll').evaluate(element=>{element.scrollLeft=element.scrollWidth})
      await capture('jobs-table-end',path,'.table-scroll')
    }else await capture('status-diagnostics',path,'.search-diagnostics')
  }
  }
  await writeFile(output+(process.env.BUGG_VISUAL_FAILURES==='1'?'failures':process.env.BUGG_VISUAL_ONLY?'selected':'manifest')+'.json',JSON.stringify({captures,errors},null,2))
  if(errors.length)throw Error(JSON.stringify(errors))
  console.log(JSON.stringify({screenshots:captures.length,errors}))
}finally{await browser.close();await server.close()}
