import axios from 'axios'

/** Axios owns normal HTTP; Response preserves the migrated callers' contract. */
export const platformHttp = axios.create({ adapter: 'fetch', timeout: 25000,
  validateStatus: () => true, responseType: 'stream' })

export async function platformFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const original = input instanceof Request ? input : undefined
  const url = original?.url ?? String(input)
  const signal = init.signal ?? original?.signal
  try {
    const reply = await platformHttp.request<ReadableStream>({ url,
      method: init.method ?? original?.method ?? 'GET',
      headers: Object.fromEntries(new Headers(init.headers ?? original?.headers).entries()),
      data: init.body ?? (original?.body ? await original.clone().arrayBuffer() : undefined),
      transformRequest: data => data,
      signal: signal ?? undefined,
      timeout: signal ? 0 : 25000,
      // The adapter captures fetch per request, keeping tests and hosts deterministic.
      env: { fetch: globalThis.fetch },
      fetchOptions: { credentials: init.credentials ?? original?.credentials ?? 'same-origin',
        cache: init.cache ?? original?.cache, redirect: init.redirect ?? original?.redirect },
    })
    const headers = new Headers()
    for (const [key, value] of Object.entries(reply.headers)) if (value != null) headers.set(key, String(value))
    const body = [101, 204, 205, 304].includes(reply.status) || (init.method ?? original?.method) === 'HEAD' ? null : reply.data
    const response = new Response(body, { status: reply.status, statusText: reply.statusText, headers })
    Object.defineProperty(response, 'url', { value: reply.request?.url ?? url })
    return response
  } catch (cause) {
    if (signal?.aborted) throw signal.reason ?? new DOMException('Aborted', 'AbortError')
    throw cause
  }
}
