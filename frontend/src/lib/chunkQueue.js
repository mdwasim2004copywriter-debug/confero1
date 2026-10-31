// Persistent client-side queue for Phase B streaming chunks, backed by
// IndexedDB (never localStorage -- localStorage is string-only and size-
// limited, unsuitable for audio Blobs; IndexedDB stores Blobs natively).
//
// No new npm dependency: the native indexedDB API is small enough to
// wrap by hand for what this needs, rather than adding a convenience
// library (e.g. `idb`) for a handful of operations.
//
// Row shape: { id (auto), streamId, sequenceNumber, startTime, endTime,
//   blob, status ('pending' | 'failed'), retryCount, createdAt }
// 'pending' covers "not yet successfully uploaded" -- there's no separate
// 'uploading' status persisted to the store, since a page reload mid-
// upload should simply see it as pending again and retry, not get stuck.

const DB_NAME = 'confero-stream-queue'
const DB_VERSION = 1
const STORE = 'chunks'

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: 'id', autoIncrement: true })
        store.createIndex('byStream', 'streamId', { unique: false })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

function promisify(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

export async function enqueueChunk({ streamId, sequenceNumber, startTime, endTime, blob }) {
  const db = await openDb()
  const tx = db.transaction(STORE, 'readwrite')
  const id = await promisify(
    tx.objectStore(STORE).add({
      streamId,
      sequenceNumber,
      startTime,
      endTime,
      blob,
      status: 'pending',
      retryCount: 0,
      createdAt: Date.now(),
    })
  )
  return id
}

// Oldest-sequence-first, pending chunks plus failed ones still under the
// retry cap -- a permanently-failed chunk (retryCount >= maxRetry) is
// left in the store (so it's still visible/countable) but never
// returned here, so the drain loop stops retrying it forever.
export async function getPendingChunks(streamId, maxRetry) {
  const db = await openDb()
  const tx = db.transaction(STORE, 'readonly')
  const all = await promisify(tx.objectStore(STORE).index('byStream').getAll(streamId))
  return all
    .filter((c) => c.status === 'pending' || (c.status === 'failed' && c.retryCount < maxRetry))
    .sort((a, b) => a.sequenceNumber - b.sequenceNumber)
}

export async function markChunkFailed(id) {
  const db = await openDb()
  const tx = db.transaction(STORE, 'readwrite')
  const store = tx.objectStore(STORE)
  const chunk = await promisify(store.get(id))
  if (!chunk) return
  chunk.status = 'failed'
  chunk.retryCount += 1
  await promisify(store.put(chunk))
}

// Only ever called after the server has acknowledged the chunk (success
// or an idempotent replay of an already-known success) -- never on a
// network failure, so a chunk is never lost before it's confirmed.
export async function deleteChunk(id) {
  const db = await openDb()
  const tx = db.transaction(STORE, 'readwrite')
  await promisify(tx.objectStore(STORE).delete(id))
}

export async function countByStatus(streamId) {
  const db = await openDb()
  const tx = db.transaction(STORE, 'readonly')
  const all = await promisify(tx.objectStore(STORE).index('byStream').getAll(streamId))
  const counts = { pending: 0, failed: 0 }
  for (const c of all) counts[c.status] = (counts[c.status] || 0) + 1
  return counts
}

export async function clearStream(streamId) {
  const db = await openDb()
  const tx = db.transaction(STORE, 'readwrite')
  const store = tx.objectStore(STORE)
  const all = await promisify(store.index('byStream').getAll(streamId))
  await Promise.all(all.map((c) => promisify(store.delete(c.id))))
}
