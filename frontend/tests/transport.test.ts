import {afterAll,beforeAll,expect,it} from 'vitest'
import {createServer,type Server} from 'node:http'
import {platformFetch,platformHttp} from '../src/transport'

let server:Server,base:string
beforeAll(async()=>{
  server=createServer((request,response)=>{
    if(request.url==='/slow')return
    if(request.url==='/empty'){response.writeHead(204);response.end();return}
    const chunks:Buffer[]=[]
    request.on('data',chunk=>chunks.push(chunk))
    request.on('end',()=>{response.writeHead(422,{'Content-Type':'application/json'});response.end(JSON.stringify({body:Buffer.concat(chunks).toString(),custom:request.headers['x-custom']}))})
  })
  await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve))
  const address=server.address() as {port:number}
  base=`http://127.0.0.1:${address.port}`
})
afterAll(async()=>{server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()))})
it('preserves HTTP error status, JSON body and headers through the real Axios adapter',async()=>{
  const response=await platformFetch(base+'/echo',{method:'POST',body:'{"name":"模组"}',headers:{'X-Custom':'present'}})
  expect(response.ok).toBe(false)
  expect(response.status).toBe(422)
  expect(await response.json()).toEqual({body:'{"name":"模组"}',custom:'present'})
})
it('keeps bodyless responses valid',async()=>{
  const response=await platformFetch(base+'/empty')
  expect(response.status).toBe(204)
  expect(await response.text()).toBe('')
})
it('uses the caller deadline and preserves the abort reason',async()=>{
  const previous=platformHttp.defaults.timeout
  platformHttp.defaults.timeout=1
  try{
    const controller=new AbortController()
    const pending=platformFetch(base+'/slow',{signal:controller.signal})
    const reason=new DOMException('caller timeout','TimeoutError')
    setTimeout(()=>controller.abort(reason),80)
    await expect(pending).rejects.toBe(reason)
  }finally{platformHttp.defaults.timeout=previous}
})
