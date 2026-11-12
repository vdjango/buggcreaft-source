import { defineStore } from 'pinia'
import { ref } from 'vue'
import { request, isCancelled, type Job, type Scan, type CollectRequest, type StorageStatus, type ArtifactPage } from '../api'
import {type SearchStatus} from '../search'
import {useMirrorWorkspaceStore} from '../platform/stores/mirrorWorkspace'

export const useCollections = defineStore('collections', () => {
  const jobs = ref<Job[]>([]), scan = ref<Scan | null>(null), storage = ref<StorageStatus | null>(null)
  const selected = ref<string | null>(null), error = ref(''), busy = ref(false), connected = ref(false)
  const artifacts = ref<ArtifactPage | null>(null), artifactsLoading = ref(false), scanLoading = ref(false)
  const collectionControl = ref<{paused:boolean; states:Record<string,number>; parser_version:string}|null>(null)
  const discovery = ref<any>(null)
  let token = '', generation = 0
  let pollingWanted = false
  let listController: AbortController | undefined, scanController: AbortController | undefined
  let storageController: AbortController | undefined, timer: ReturnType<typeof setInterval> | undefined
  let artifactsController: AbortController | undefined
  let connectionController: AbortController | undefined

  function fail(cause: unknown, revision: number) {
    if (revision === generation && !isCancelled(cause)) error.value = cause instanceof Error ? cause.message : '请求失败'
  }
  async function loadScan(digest: string, parserVersion: string, key: string) {
    scanController?.abort()
    const controller = scanController = new AbortController(), revision = generation
    scanLoading.value = true
    try {
      const value = await request<Scan>(token, `/artifacts/${digest}/dependencies?parser_version=${encodeURIComponent(parserVersion)}`, undefined, controller.signal)
      if (!controller.signal.aborted && revision === generation && selected.value === key) { scan.value = value; error.value = '' }
    } catch (cause) { if (!controller.signal.aborted) fail(cause, revision) }
    finally { if (scanController === controller) { scanController = undefined; scanLoading.value = false } }
  }
  async function refresh() {
    if (!connected.value || listController) return
    const controller = listController = new AbortController(), revision = generation
    try {
      const rows = await request<Job[]>(token, '/collections', undefined, controller.signal)
      if (controller.signal.aborted || revision !== generation) return
      jobs.value = rows; error.value = ''
      const control = await request<typeof collectionControl.value>(token, '/dependency-collection/control', undefined, controller.signal)
      if (!controller.signal.aborted && revision === generation) collectionControl.value = control
      try {
        const progress = await useMirrorWorkspaceStore().request({action:'dependency_status'},controller.signal)
        if(!controller.signal.aborted && revision===generation) discovery.value=progress
      } catch { /* Facts management remains usable when an independent mirror is offline. */ }
    } catch (cause) { if (!controller.signal.aborted) fail(cause, revision) }
    finally { if (listController === controller) listController = undefined }
  }
  function stopPolling() {
    pollingWanted = false
    clearInterval(timer); timer = undefined
    listController?.abort(); listController = undefined
  }
  function startPolling() {
    stopPolling()
    pollingWanted = true
    if (connected.value) { void refresh(); timer = setInterval(refresh, 2500) }
  }
  function stopStorage() { storageController?.abort(); storageController = undefined }
  function stopScan() { scanController?.abort(); scanController = undefined; scanLoading.value = false; selected.value = null; scan.value = null }
  function stopArtifacts() { artifactsController?.abort(); artifactsController = undefined; artifactsLoading.value = false }
  function disconnect() {
    generation++; stopPolling(); stopStorage(); stopScan(); stopArtifacts(); token = ''; connected.value = false
    connectionController?.abort(); connectionController = undefined
    jobs.value = []; scan.value = null; selected.value = null; storage.value = null; error.value = ''; busy.value = false
    artifacts.value = null
    collectionControl.value = null
    discovery.value = null
  }
  async function connect(value: string) {
    const resume = pollingWanted
    disconnect(); pollingWanted = resume; token = value.trim()
    const revision = generation, controller = connectionController = new AbortController()
    try {
      await request<Job[]>(token, '/collections?limit=1', undefined, controller.signal)
      if (!controller.signal.aborted && revision === generation) connected.value = true
    } catch (cause) { if (!controller.signal.aborted) fail(cause, revision) }
    finally { if (connectionController === controller) connectionController = undefined }
    if (revision === generation && connected.value && pollingWanted) startPolling()
  }
  async function openArtifact(digest: string, parserVersion: string) {
    stopScan(); selected.value = `${digest}:${parserVersion}`
    if (connected.value) await loadScan(digest, parserVersion, selected.value)
  }
  async function loadArtifacts(cursor: string | null = null) {
    if (!connected.value) return
    stopArtifacts()
    const controller = artifactsController = new AbortController(), revision = generation
    artifactsLoading.value = true
    try {
      const value = await request<ArtifactPage>(token, `/artifacts?limit=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`, undefined, controller.signal)
      if (!controller.signal.aborted && revision === generation) { artifacts.value = value; error.value = '' }
    } catch (cause) { if (!controller.signal.aborted) fail(cause, revision) }
    finally { if (artifactsController === controller) { artifactsController = undefined; artifactsLoading.value = false } }
  }
  async function enqueue(body: CollectRequest) {
    const revision = generation; busy.value = true; error.value = ''
    try {
      const job = await request<Job>(token, '/collections', body)
      if (revision !== generation) return
      return job
    } catch (cause) { fail(cause, revision) }
    finally { if (revision === generation) busy.value = false }
  }
  async function act(job: Job, action: 'cancel' | 'retry') {
    const revision = generation
    try {
      const updated = await request<Job>(token, `/collections/${job.id}/${action}`, {})
      if (revision !== generation) return
      if (pollingWanted) await refresh()
      return updated
    } catch (cause) { if (pollingWanted) fail(cause, revision) }
  }
  async function pauseCollection(paused:boolean) {
    if (!connected.value || busy.value) return
    const revision=generation
    busy.value=true
    try {
      const control=await request<typeof collectionControl.value>(token,'/dependency-collection/control',{paused})
      if(revision===generation) collectionControl.value=control
    } catch(cause) { fail(cause,revision) }
    finally { if(revision===generation) busy.value=false }
  }
  async function retryDiscovery() {
    if (!connected.value || busy.value) return
    const revision=generation
    busy.value=true
    try {
      const progress=await useMirrorWorkspaceStore().request({action:'dependency_retry'})
      if(revision===generation) { discovery.value=progress; error.value='' }
    } catch(cause) { fail(cause,revision) }
    finally { if(revision===generation) busy.value=false }
  }
  async function loadStorage() {
    if (!connected.value) return
    stopStorage()
    const controller = storageController = new AbortController(), revision = generation
    try {
      const value = await request<StorageStatus>(token, '/storage/status', undefined, controller.signal)
      if (!controller.signal.aborted && revision === generation) { storage.value = value; error.value = '' }
    } catch (cause) { if (!controller.signal.aborted) fail(cause, revision) }
    finally { if (storageController === controller) storageController = undefined }
  }
  function searchStatus(signal:AbortSignal){return request<SearchStatus>(token,'/admin/search/status',undefined,signal)}
  function rebuildSearch(signal:AbortSignal){return request<{state:string}>(token,'/admin/search/rebuilds',{reset_source:false},signal)}
  return { jobs, scan, storage, artifacts, artifactsLoading, scanLoading, selected, error, busy, connected, collectionControl, discovery, pauseCollection, retryDiscovery, connect, disconnect, refresh, searchStatus,rebuildSearch,
    openArtifact, stopScan, loadArtifacts, stopArtifacts, enqueue, act, startPolling, stopPolling, loadStorage, stopStorage }
})
