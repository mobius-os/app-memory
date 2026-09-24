import { NOTE_BASE, NOTE_GIT_BASE } from './constants.js'

// Every store created in one app surface shares commit ownership for the same
// runtime-storage bridge. The bridge's versioned operations provide the same
// guarantee across separate frames/tabs, where JavaScript objects are not
// shared. A WeakMap keeps this coordination lifecycle-bound to the bridge.
const CACHE_COORDINATORS = new WeakMap()
function cacheCoordinator(bridge) {
  let coordinator = CACHE_COORDINATORS.get(bridge)
  if (!coordinator) {
    coordinator = { claims: new Map(), nextClaim: 0 }
    CACHE_COORDINATORS.set(bridge, coordinator)
  }
  return coordinator
}

function ensureCacheConflictHandler(bridge, coordinator) {
  if (coordinator.conflictHandlerInstalled || typeof bridge.onConflict !== 'function') return
  coordinator.conflictHandlerInstalled = true
  bridge.onConflict(async (conflict) => {
    if (typeof conflict?.path !== 'string' || !conflict.path.startsWith('offline-cache/')) {
      return false
    }
    const candidate = conflict.refusedValue
    if (!candidate || typeof candidate !== 'object'
        || !candidate.requestKey || !candidate.entry
        || typeof bridge.getWithVersion !== 'function'
        || typeof bridge.durableWrite !== 'function') return false

    // A cache write can be queued during a brief disconnect after the shared
    // read succeeds. If its delayed CAS later conflicts, compare the refused
    // candidate with the current slot and commit the freshest one before
    // acknowledging the platform outcome. This is cache reconciliation only;
    // shared Memory source data is never modified here.
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const current = await bridge.getWithVersion(conflict.path)
      const value = current?.value ?? null
      if (Number.isFinite(value?.cacheStartedAt)
          && value.cacheStartedAt > candidate.cacheStartedAt) return true
      if (value?.requestKey === candidate.requestKey
          && value?.entry?.body === candidate.entry?.body
          && value?.entry?.present === candidate.entry?.present) return true
      try {
        const result = await bridge.durableWrite(conflict.path, candidate, current?.version
          ? { ifMatch: current.version }
          : { ifNoneMatch: true })
        return result?.durability === 'synced'
      } catch (error) {
        if (error?.code !== 'conflict') return false
        // This immediate loser is redundant with the still-unacknowledged
        // original candidate that owns the retry loop.
        if (error.writeId && typeof bridge._ackConflict === 'function') {
          await bridge._ackConflict(error.writeId).catch(() => {})
        }
      }
    }
    return false
  })
}

// ── Shared-memory read-through store ──────────────────────────────────────
// The graph + notes live in SHARED storage (/api/storage/shared/memory/),
// which `window.mobius.storage` cannot reach — that runtime hard-scopes every
// read to /api/storage/apps/${appId}/ — and the shell service worker sends all
// other /api/* straight to network, so a raw shared GET is blank offline and
// load-once (stale after an agent rewrite). This factory is the shared-scope
// twin of window.mobius.storage.get/getText/subscribe: read-through cache
// (last-known value served instantly, offline-capable), background revalidate,
// and a visibility-aware poller so subscribed views repaint when Memory's
// maintenance job advances `.ready`. Pure factory (deps injected) so the offline
// harness can drive it with a mocked cache + fetch and no network.
export function makeSharedMemoryStore({
  baseUrl = NOTE_BASE,
  gitBaseUrl = NOTE_GIT_BASE,
  getToken,
  fetchImpl,
  cacheStore,
  runtimeStorage,
  cacheName = 'mobius-memory-shared-v1',
  pollMs = 4000,
  // How long a background revalidation must stay in flight before the
  // "merging…" indicator is surfaced. A read against local shared storage
  // normally resolves in a few ms, so raising the indicator for that split
  // second — every pollMs, while the owner is just reading a note — is pure
  // visual noise (a pill blinks on and off). We only raise it if the pull is
  // still running after this delay: a fast routine poll never shows it; a
  // genuinely slow pull (the case actually worth signalling) still does.
  indicatorDelayMs = 500,
  isVisible = () => (typeof document === 'undefined'
    ? true
    : document.visibilityState !== 'hidden'),
} = {}) {
  const doFetch = fetchImpl
    || (typeof fetch === 'function' ? (...a) => fetch(...a) : null);

  // The cache is a thin key->{ body, present } map. In production, prefer the
  // app-scoped Mobius storage bridge: opaque sandboxed frames cannot use
  // CacheStorage, while the bridge owns a persistent reload-safe offline
  // mirror and keeps this private Memory copy inside the app's storage scope.
  // Cache Storage remains a compatibility path for non-opaque surfaces; the
  // in-memory fallback is intentionally online-session-only.
  function memoryCache() {
    const m = new Map();
    return {
      async read(key) { return m.has(key) ? m.get(key) : null; },
      async write(key, entry) { m.set(key, entry); },
    };
  }

  function logicalCacheKey(key) {
    // Immutable Git URLs change revision on every Memory publish. Cache one
    // exact request per logical file instead of accumulating one record per
    // revision forever. The stored requestKey below prevents an older revision
    // from being returned for a different explicit revision.
    let logical = String(key);
    try {
      const queryAt = logical.indexOf('?');
      const pathname = queryAt < 0 ? logical : logical.slice(0, queryAt);
      const params = new URLSearchParams(queryAt < 0 ? '' : logical.slice(queryAt + 1));
      const gitPath = String(gitBaseUrl).split('?', 1)[0];
      logical = pathname === gitPath
        ? `git:${params.get('file') || ''}`
        : `shared:${pathname}`;
    } catch { /* the encoded raw key remains a safe deterministic fallback */ }
    return logical;
  }

  async function appCachePath(key) {
    const bytes = new TextEncoder().encode(logicalCacheKey(key));
    let digest;
    try {
      digest = await globalThis.crypto?.subtle?.digest('SHA-256', bytes);
    } catch { digest = null; }
    if (digest) {
      const hex = [...new Uint8Array(digest)]
        .map((byte) => byte.toString(16).padStart(2, '0')).join('');
      return `offline-cache/${hex}.json`;
    }
    // Very old test/compatibility realms may not expose SubtleCrypto. The
    // requestKey stored inside every record still prevents a collision from
    // returning the wrong body; this deterministic suffix only selects a slot.
    let hash = 2166136261;
    for (const byte of bytes) hash = Math.imul(hash ^ byte, 16777619) >>> 0;
    return `offline-cache/compat-${hash.toString(16).padStart(8, '0')}.json`;
  }

  const localCoordinator = { claims: new Map(), nextClaim: 0 }
  let activeCoordinator = localCoordinator

  function requestStartedAt() {
    try {
      if (Number.isFinite(globalThis.performance?.timeOrigin)
          && typeof globalThis.performance?.now === 'function') {
        return globalThis.performance.timeOrigin + globalThis.performance.now()
      }
    } catch { /* use the wall clock compatibility fallback */ }
    return Date.now()
  }

  function mobiusCache(bridge) {
    const coordinator = cacheCoordinator(bridge)
    activeCoordinator = coordinator
    ensureCacheConflictHandler(bridge, coordinator)
    const known = new Map();
    return {
      async read(key) {
        try {
          const path = await appCachePath(key);
          const record = await bridge.get(path);
          known.set(path, record);
          if (!record || record.requestKey !== key || !record.entry
              || typeof record.entry.present !== 'boolean') return null;
          const body = record.entry.body;
          if (body != null && typeof body !== 'string') return null;
          return { body: body ?? null, present: record.entry.present };
        } catch { return null; }
      },
      async write(key, entry, owner = null) {
        try {
          const path = await appCachePath(key);
          const next = {
            requestKey: key,
            entry: { body: entry.body ?? null, present: entry.present === true },
            // The request-start time, rather than response-completion time,
            // expresses which revision the view most recently asked for. A
            // slow retired revision may finish last, but it must never evict a
            // newer revision that already became current in another mount.
            cacheStartedAt: owner?.startedAt ?? Date.now(),
          };
          if (owner && !ownsCacheWrite(owner)) return;
          let current = known.get(path);
          if (current?.requestKey === next.requestKey
              && current?.entry?.body === next.entry.body
              && current?.entry?.present === next.entry.present) return;

          // Across distinct app frames, only storage CAS can make the final
          // validation and write indivisible. A newer candidate retries a
          // lost CAS against the winner; an older or retired candidate stops.
          // Three total attempts bound contention without silently discarding
          // the current graph merely because an obsolete response committed
          // first.
          if (typeof bridge.getWithVersion === 'function'
              && typeof bridge.durableWrite === 'function') {
            for (let attempt = 0; attempt < 3; attempt += 1) {
              const versioned = await bridge.getWithVersion(path)
              current = versioned?.value ?? null
              known.set(path, current)
              if (owner && !ownsCacheWrite(owner)) return;
              if (Number.isFinite(current?.cacheStartedAt)
                  && current.cacheStartedAt > next.cacheStartedAt) return;
              if (current?.requestKey === next.requestKey
                  && current?.entry?.body === next.entry.body
                  && current?.entry?.present === next.entry.present) return;
              try {
                await bridge.durableWrite(path, next, versioned?.version
                  ? { ifMatch: versioned.version }
                  : { ifNoneMatch: true })
                known.set(path, next)
                return
              } catch (error) {
                // Immediate cache-only CAS losers are acknowledged. Retry
                // only conflicts; transient/fatal failures remain best-effort
                // and the next normal Memory poll can refresh the mirror.
                if (error?.code === 'conflict' && error?.writeId
                    && typeof bridge._ackConflict === 'function') {
                  await bridge._ackConflict(error.writeId).catch(() => {})
                }
                if (error?.code !== 'conflict') return
              }
            }
            return
          }

          // Lightweight injected/test bridges have no CAS. They can only be
          // shared inside one JavaScript realm, where the coordinator above
          // makes ownership visible across store instances. Re-check after
          // every asynchronous read so a delayed snapshot cannot authorize an
          // obsolete set.
          current = await bridge.get(path)
          known.set(path, current)
          if (owner && !ownsCacheWrite(owner)) return;
          if (Number.isFinite(current?.cacheStartedAt)
              && current.cacheStartedAt > next.cacheStartedAt) return;
          if (current?.requestKey === next.requestKey
              && current?.entry?.body === next.entry.body
              && current?.entry?.present === next.entry.present) return;
          await bridge.set(path, next)
          known.set(path, next)
        } catch { /* cache persistence must never turn a live graph read into failure */ }
      },
    };
  }

  async function openCacheStore() {
    if (cacheStore) return cacheStore;
    let bridge = runtimeStorage;
    if (!bridge) {
      try { bridge = globalThis.window?.mobius?.storage; } catch { bridge = null; }
    }
    if (bridge && typeof bridge.get === 'function' && typeof bridge.set === 'function') {
      return mobiusCache(bridge);
    }
    // Sandboxed app frames intentionally omit `allow-same-origin`. Chromium
    // exposes the Cache Storage name there, but READING `window.caches` throws
    // a SecurityError. Guard the property access itself — `typeof caches` is
    // not sufficient in that context — and degrade to the online-only memory
    // mirror so a missing browser cache can never prevent the graph opening.
    let cacheApi;
    try { cacheApi = globalThis.caches; } catch { return memoryCache(); }
    if (!cacheApi || typeof cacheApi.open !== 'function') return memoryCache();
    let c;
    try { c = await cacheApi.open(cacheName); } catch { return memoryCache(); }
    return {
      async read(key) {
        const res = await c.match(key);
        if (!res) return null;
        const present = res.headers.get('x-memory-present') !== '0';
        const body = present ? await res.text() : null;
        return { body, present };
      },
      async write(key, entry) {
        const headers = { 'x-memory-present': entry.present ? '1' : '0' };
        try { await c.put(key, new Response(entry.body ?? '', { headers })); }
        catch { /* cache write is best-effort; reads still hit network */ }
      },
    };
  }
  let cacheReady = null;
  function cache() { return (cacheReady ||= openCacheStore()); }

  // A logical file has one bounded persistent slot. Network responses can
  // complete out of order when `.ready` advances and React replaces an old
  // graph/note subscription. Ownership is claimed when a request STARTS; only
  // the newest claim may write that logical slot. Retiring a subscription also
  // invalidates its in-flight claim, even before its replacement starts.
  function claimCacheWrite(key) {
    const slot = logicalCacheKey(key);
    const coordinator = activeCoordinator
    const owner = {
      slot,
      coordinator,
      id: ++coordinator.nextClaim,
      startedAt: requestStartedAt(),
    };
    coordinator.claims.set(slot, owner.id);
    return owner;
  }
  function ownsCacheWrite(owner) {
    return owner && owner.coordinator.claims.get(owner.slot) === owner.id;
  }
  function retireCacheWrite(owner) {
    if (ownsCacheWrite(owner)) {
      owner.coordinator.claims.set(owner.slot, ++owner.coordinator.nextClaim)
    }
  }

  function url(path, opts = {}) {
    if (opts.revision) {
      const query = new URLSearchParams({ revision: opts.revision, file: path });
      return `${gitBaseUrl}?${query}`;
    }
    return baseUrl + path;
  }

  // One network read. Returns { present, body } on a definitive answer (200 or
  // 404) and writes it through to the cache; throws on transient failure
  // (offline / 5xx) so the caller can fall back to the cached value.
  async function fetchThrough(path, opts = {}, claimedOwner = null) {
    if (!doFetch) throw new Error('no fetch');
    const token = typeof getToken === 'function' ? await getToken() : null;
    const headers = token ? { Authorization: 'Bearer ' + token } : {};
    const key = url(path, opts);
    const owner = claimedOwner || claimCacheWrite(key);
    const res = await doFetch(key, { headers });
    if (res.status === 404) {
      const entry = { body: null, present: false };
      if (ownsCacheWrite(owner)) await (await cache()).write(key, entry, owner);
      return entry;
    }
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const body = await res.text();
    const entry = { body, present: true };
    if (ownsCacheWrite(owner)) await (await cache()).write(key, entry, owner);
    return entry;
  }

  // Read-through: cached value first (instant, offline), revalidated in the
  // background. Returns { body, present, fromCache, error }. `error` is set only
  // when there is NO cached value AND the network failed — the genuine
  // can't-render state; a background-revalidate failure is swallowed (the
  // cached value already answered).
  async function read(path, opts = {}) {
    const key = url(path, opts);
    const cached = await (await cache()).read(key);
    if (cached) {
      fetchThrough(path, opts).catch(() => {}); // revalidate; poller delivers fresh data
      return { ...cached, fromCache: true, error: null };
    }
    try {
      const fresh = await fetchThrough(path, opts);
      return { ...fresh, fromCache: false, error: null };
    } catch (e) {
      return { body: null, present: false, fromCache: false, error: e };
    }
  }

  function parseJSON(body) {
    if (body == null) return null;
    try { return JSON.parse(body); } catch { return null; }
  }

  async function getJSON(path, opts = {}) {
    const r = await read(path, opts);
    return { value: r.present ? parseJSON(r.body) : null, present: r.present, error: r.error };
  }
  async function getText(path, opts = {}) {
    const r = await read(path, opts);
    return { value: r.present ? (r.body ?? '') : null, present: r.present, error: r.error };
  }

  // Subscribe a path: fire `cb` immediately with the cached/first value, then on
  // every poll where the raw body changed (an agent write). The poller only
  // ticks while the tab is visible, so a backgrounded app costs nothing. `cb`
  // receives { body, present, error } so callers parse for their own kind.
  // `opts.onRevalidate(bool)` brackets a background revalidation so a view can
  // show a "merging…" indicator while fresh shared data is being pulled in and
  // clear it once the new content (or a no-change verdict) has landed. The
  // bracket fires ONLY for revalidations that outlast indicatorDelayMs — fast
  // routine polls resolve first and never flip it, so the indicator doesn't
  // flash on and off every pollMs while the owner is just reading a note.
  function subscribe(path, cb, opts = {}) {
    const onRevalidate = typeof opts.onRevalidate === 'function' ? opts.onRevalidate : () => {};
    let alive = true;
    let last; // last raw body delivered — repaint only on a real change
    let timer = null;
    let activeCacheOwner = null;

    function deliver(body, present, error) {
      last = body;
      try { cb({ body, present, error: error || null }); }
      catch { /* a subscriber throwing must not kill the poller */ }
    }

    async function revalidate() {
      // Only raise the "merging…" indicator if this revalidation is STILL in
      // flight after indicatorDelayMs — a fast poll (the common case) resolves
      // first and never flashes the pill; `raised` guards the paired clear so a
      // skipped raise leaves no stray onRevalidate(false).
      let settled = false;
      let raised = false;
      const raise = () => { if (!raised && alive) { raised = true; onRevalidate(true); } };
      const timer = indicatorDelayMs > 0
        ? setTimeout(() => { if (!settled) raise(); }, indicatorDelayMs)
        : (raise(), null);
      try {
        const owner = claimCacheWrite(url(path, opts));
        activeCacheOwner = owner;
        const e = await fetchThrough(path, opts, owner);
        if (activeCacheOwner === owner) activeCacheOwner = null;
        if (alive && e.body !== last) deliver(e.body, e.present, null);
      } catch { /* transient: keep the last value, just clear the indicator */ }
      finally {
        settled = true;
        if (timer) clearTimeout(timer);
        if (alive && raised) onRevalidate(false);
      }
    }

    async function init() {
      const cached = await (await cache()).read(url(path, opts));
      if (!alive) return;
      if (cached) {
        // Cached value paints instantly (offline-capable); then revalidate so an
        // agent write since last open is merged in.
        deliver(cached.body, cached.present, null);
        revalidate();
      } else {
        // Nothing cached: the first read IS the revalidation.
        onRevalidate(true);
        try {
          const owner = claimCacheWrite(url(path, opts));
          activeCacheOwner = owner;
          const e = await fetchThrough(path, opts, owner);
          if (activeCacheOwner === owner) activeCacheOwner = null;
          if (alive) deliver(e.body, e.present, null);
        } catch (e) {
          if (alive) deliver(null, false, e);
        } finally { if (alive) onRevalidate(false); }
      }
    }

    function schedule() {
      // Commit-addressed blobs are immutable and never poll. Mutable paths such
      // as `.ready` and the usage ledger poll while visible; a pointer change
      // remounts graph/note subscriptions with a different revision URL.
      if (!alive || pollMs <= 0 || opts.revision) return;
      timer = setTimeout(async () => {
        if (isVisible()) await revalidate();
        schedule();
      }, pollMs);
    }

    init().finally(schedule);
    return () => {
      alive = false;
      if (activeCacheOwner) retireCacheWrite(activeCacheOwner);
      activeCacheOwner = null;
      if (timer) clearTimeout(timer);
    };
  }

  return { read, getJSON, getText, subscribe, _url: url };
}
