import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { request, type Job, type Scan } from '../api'
import { useCollections } from './collections'

vi.mock('../api', () => ({ request: vi.fn(), isCancelled: (value: unknown) => value === 'cancelled' }))
const mocked = vi.mocked(request)
const job = (id: string): Job => ({ id, state: 'succeeded', attempts: 1, error: null,
  artifact_sha256: id.repeat(64), cancel_requested: false, verification: 'unverified',
  parser_version: '1.0.0', priority: 100, cache_hit: false })
const scan = (id: string): Scan => ({ artifact_sha256: id.repeat(64), parser_version: '1.0.0',
  status: 'complete_declared', mods: [], evidence: [], issues: [] })

describe('collections lifecycle', () => {
  beforeEach(() => { setActivePinia(createPinia()); mocked.mockReset(); vi.useFakeTimers() })
  afterEach(() => { useCollections().disconnect(); vi.useRealTimers() })
  it('ignores old scan response after selecting another job', async () => {
    const store = useCollections(); store.connected = true
    let resolveA!: (value: Scan) => void
    mocked.mockImplementation((_token, path) => path.includes('/artifacts/a')
      ? new Promise(resolve => { resolveA = resolve as (value: Scan) => void })
      : Promise.resolve(scan('b')) as never)
    const pending = store.openArtifact('a'.repeat(64), '1.0.0')
    await store.openArtifact('b'.repeat(64), '1.0.0')
    resolveA(scan('a')); await pending
    expect(store.scan?.artifact_sha256).toBe('b'.repeat(64))
  })
  it('ignores old session responses and clears session data', async () => {
    const store = useCollections()
    let resolveOld!: (value: Job[]) => void
    mocked.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve as (value: Job[]) => void }))
    const first = store.connect('old')
    mocked.mockResolvedValueOnce([])
    await store.connect('new')
    resolveOld([job('a')]); await first
    expect(store.jobs).toEqual([]); expect(store.connected).toBe(true)
    store.disconnect()
    expect(store.scan).toBeNull(); expect(store.storage).toBeNull(); expect(vi.getTimerCount()).toBe(0)
  })
  it('polls only while collections page is mounted', async () => {
    const store = useCollections()
    store.startPolling(); mocked.mockResolvedValue([])
    await store.connect('token')
    expect(vi.getTimerCount()).toBe(1)
    store.stopPolling()
    expect(vi.getTimerCount()).toBe(0)
    await store.connect('replacement')
    expect(vi.getTimerCount()).toBe(0)
  })
  it('shows auth errors and does not poll on failed connection', async () => {
    const store = useCollections()
    store.startPolling(); mocked.mockRejectedValueOnce(new Error('unauthorized'))
    await store.connect('wrong')
    expect(store.connected).toBe(false); expect(store.error).toBe('unauthorized')
    expect(vi.getTimerCount()).toBe(0)
  })
  it('ignores a late detail response after leaving the page', async () => {
    const store = useCollections(); store.connected = true
    let resolve!: (value: Scan) => void
    mocked.mockImplementationOnce(() => new Promise(done => { resolve = done as typeof resolve }))
    const pending = store.openArtifact('a'.repeat(64), '1.0.0')
    store.stopScan(); resolve(scan('a')); await pending
    expect(store.scan).toBeNull(); expect(store.scanLoading).toBe(false)
  })
  it('ignores a previous directory page and aborted errors', async () => {
    const store = useCollections(); store.connected = true
    let reject!: (cause: Error) => void
    mocked.mockImplementationOnce(() => new Promise((_done, fail) => { reject = fail }))
    const previous = store.loadArtifacts()
    mocked.mockResolvedValueOnce({ items: [], next_cursor: null })
    await store.loadArtifacts('next')
    reject(new Error('old page failed')); await previous
    expect(store.artifacts).toEqual({ items: [], next_cursor: null })
    expect(store.error).toBe(''); expect(store.artifactsLoading).toBe(false)
  })
  it('leaving tasks during authentication does not authenticate an aborted response', async () => {
    const store = useCollections(); store.startPolling()
    let resolve!: (value: Job[]) => void
    mocked.mockImplementationOnce(() => new Promise(done => { resolve = done as typeof resolve }))
    const pending = store.connect('token')
    store.stopPolling()
    resolve([]); await pending
    expect(store.connected).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })
})
