import { describe, expect, it } from 'vitest'
import { AxiosError, CanceledError } from 'axios'
import { http, request, isCancelled } from './api'

describe('Axios transport', () => {
  it('sends auth per request and accepts AbortSignal', async () => {
    const controller = new AbortController()
    http.defaults.adapter = async config => {
      expect(config.headers.get('Authorization')).toBe('Bearer private-test')
      expect(config.signal).toBe(controller.signal)
      expect(config.baseURL).toBe('/api/v1')
      return { data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config }
    }
    expect(await request('private-test', '/collections', undefined, controller.signal)).toEqual({ ok: true })
    expect(http.defaults.headers.common.Authorization).toBeUndefined()
  })
  it('preserves cancellation without leaking transport config', async () => {
    http.defaults.adapter = async () => { throw new CanceledError() }
    await request('token', '/collections').catch(cause => expect(isCancelled(cause)).toBe(true))
    http.defaults.adapter = async config => { throw new AxiosError('secret url and token', 'ERR_NETWORK', config) }
    await expect(request('token', '/collections')).rejects.toThrow('无法连接服务，请检查网络后重试')
  })
})
