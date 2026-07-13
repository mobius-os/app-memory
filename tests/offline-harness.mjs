// OFFLINE RENDER HARNESS for app-memory.
// Drives the REAL compiled makeSharedMemoryStore (from the esbuild bundle of
// index.jsx) through the exact two effects the App runs — the graph subscribe
// and the open-note subscribe — with:
//   * a mocked Cache-Storage-style mirror (read-through cache), and
//   * the network forced DOWN after the first prime, and
//   * a controllable per-request latency (state.latencyMs) so we can exercise
//     both fast polls and genuinely slow pulls.
// It reproduces the component's render-state reducer (status + revalidating)
// so the assertions are on the SAME state the UI paints. No DOM needed; the
// data path here is byte-identical to what the iframe runs.
//
// Phases P3–P5 are the "Merging latest…" flash guard: a routine background
// revalidation resolves in a few ms, and the indicator must NOT flash on and
// off for every one of those (the owner-visible bug). It is raised ONLY for a
// revalidation that outlasts indicatorDelayMs.
import assert from 'node:assert/strict'
import { makeSharedMemoryStore } from './.build/index.mjs'

const GRAPH = JSON.stringify({
  nodes: [
    { id: 'index', title: 'Memory — Home', type: 'moc', path: 'index.md' },
    { id: 'about-user', title: 'About the user', type: 'note', path: 'notes/about-user.md' },
  ],
  edges: [{ source: 'index', target: 'about-user', kind: 'moc' }],
  problems: [],
})
const NOTE_V1 = '---\ntitle: About the user\n---\n# About the user\nLikes terse prompts.'
const NOTE_V2 = NOTE_V1 + '\n\nUPDATE: an agent appended this line.'

// Mocked Cache Storage mirror (what survives offline).
function makeCacheMirror() {
  const m = new Map()
  return { map: m, read: async (k) => (m.has(k) ? m.get(k) : null), write: async (k, e) => { m.set(k, e) } }
}

// Controllable network. `online=false` => every request throws like a real
// fetch outage. `latencyMs` delays each response (to drive the slow-pull path).
// `files` is the server-side truth (an agent write mutates it).
function makeNet(files, state) {
  return async (url) => {
    if (state.latencyMs) await wait(state.latencyMs)
    if (!state.online) throw new TypeError('Failed to fetch (offline)')
    const rel = url.replace('/api/storage/shared/memory/', '')
    if (!(rel in files)) return { ok: false, status: 404, text: async () => '' }
    return { ok: true, status: 200, text: async () => files[rel] }
  }
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms))

// Poll fast (pollMs:8) and raise the merging indicator only past 40ms in flight,
// so a fast poll (latency 0) never flips it but a slow pull (latency 80) does.
const INDICATOR_DELAY = 40

async function run() {
  const files = { 'graph.json': GRAPH, 'notes/about-user.md': NOTE_V1 }
  const state = { online: true, latencyMs: 0 }
  const cacheStore = makeCacheMirror()
  const net = makeNet(files, state)
  const store = makeSharedMemoryStore({
    getToken: () => 'tok', fetchImpl: net, cacheStore,
    pollMs: 8, indicatorDelayMs: INDICATOR_DELAY, isVisible: () => true,
  })

  // ── Phase 1: ONLINE prime — the App opens once with a network so the cache
  // mirror fills (mirrors the owner's last online session). ──
  // Graph effect render-state reducer (mirror of the App's setGraph/setStatus).
  let graphView = { status: 'loading', nodes: null }
  const unsubGraph = store.subscribe('graph.json', ({ body, present, error }) => {
    if (error && body == null) { graphView = { status: 'error', nodes: null }; return }
    if (!present || body == null) { graphView = { status: 'empty', nodes: [] }; return }
    const data = JSON.parse(body)
    graphView = { status: data.nodes.length ? 'ready' : 'empty', nodes: data.nodes }
  })
  await wait(30)
  assert.equal(graphView.status, 'ready', 'P1: graph rendered online')
  assert.equal(graphView.nodes.length, 2)
  console.log('P1 OK  graph primed online: status=ready nodes=2')

  // Open the "about-user" note (note effect reducer: status + revalidating).
  let noteView = { status: 'loading', md: '', revalidating: false }
  const notePath = 'notes/about-user.md'
  const unsubNote = store.subscribe(
    notePath,
    ({ body, present, error }) => {
      if (error && body == null) { noteView = { ...noteView, status: 'error' }; return }
      if (!present || body == null) { noteView = { ...noteView, status: 'missing' }; return }
      noteView = { ...noteView, status: 'ready', md: body }
    },
    { onRevalidate: (busy) => { noteView = { ...noteView, revalidating: busy } } },
  )
  await wait(30)
  assert.equal(noteView.status, 'ready', 'P1: note rendered online')
  assert.ok(noteView.md.includes('Likes terse prompts'))
  assert.equal(noteView.revalidating, false, 'P1: merging indicator cleared after revalidation')
  console.log('P1 OK  note primed online: status=ready, revalidating cleared')

  unsubGraph(); unsubNote()

  // ── Phase 2: OFFLINE — fresh open of the app with NO network. The store must
  // serve graph + note from the cache mirror. ──
  state.online = false
  let g2 = { status: 'loading', nodes: null }
  const u2g = store.subscribe('graph.json', ({ body, present }) => {
    if (!present || body == null) { g2 = { status: 'empty', nodes: [] }; return }
    const d = JSON.parse(body); g2 = { status: d.nodes.length ? 'ready' : 'empty', nodes: d.nodes }
  })
  let n2 = { status: 'loading', md: '', revalidating: false }
  const revLog = []          // every onRevalidate(bool) — deterministic bracket record
  const mdLog = []           // every body delivered to the note view
  const u2n = store.subscribe(
    notePath,
    ({ body, present }) => {
      if (!present || body == null) { n2 = { ...n2, status: 'missing' }; return }
      n2 = { ...n2, status: 'ready', md: body }; mdLog.push(body)
    },
    { onRevalidate: (b) => { n2 = { ...n2, revalidating: b }; revLog.push(b) } },
  )
  await wait(40)
  assert.equal(g2.status, 'ready', 'P2: GRAPH renders OFFLINE from cache')
  assert.equal(g2.nodes.length, 2)
  assert.equal(n2.status, 'ready', 'P2: NOTE renders OFFLINE from cache')
  assert.ok(n2.md.includes('Likes terse prompts'))
  assert.equal(n2.revalidating, false, 'P2: indicator not stuck on while offline')
  assert.ok(!revLog.includes(true), 'P2: indicator never flashed on failed (offline) polls')
  console.log('P2 OK  OFFLINE: graph + note render from cache; no indicator flash on failed polls')

  // ── Phase 3: external write to the OPEN note lands via a FAST poll. The note
  // must REPAINT, but the "merging…" indicator must NOT flash — a revalidation
  // that resolves before INDICATOR_DELAY never raises the pill. ──
  state.online = true
  state.latencyMs = 0
  files['notes/about-user.md'] = NOTE_V2  // an agent rewrote the note on the server
  const revStart = revLog.length
  const deadline = Date.now() + 500
  while (Date.now() < deadline) {
    if (n2.md.includes('an agent appended this line')) break
    await wait(5)
  }
  await wait(60)   // let several more fast polls run past the change
  assert.ok(n2.md.includes('an agent appended this line'), 'P3: open note REPAINTED after external agent write')
  const revDuringWrite = revLog.slice(revStart)
  assert.ok(!revDuringWrite.includes(true), 'P3: indicator did NOT flash on a fast change/revalidation')
  assert.equal(n2.revalidating, false, 'P3: final revalidating state is cleared')
  console.log('P3 OK  fast change repainted with NO indicator flash; revLog window=' + JSON.stringify(revDuringWrite))

  // ── Phase 4: THE REPORTED BUG — idle polls with no server-side change and
  // fast reads must leave the indicator completely quiet across many cycles
  // (pre-fix it flashed the pill on and off every pollMs). ──
  const revP4Start = revLog.length
  await wait(90)   // ~11 poll cycles at pollMs=8
  const revP4 = revLog.slice(revP4Start)
  assert.ok(!revP4.includes(true), 'P4: no indicator flash on routine no-change polls')
  assert.equal(revP4.length, 0, 'P4: the pill was never raised across idle polls')
  assert.equal(n2.revalidating, false, 'P4: indicator stays cleared while idle')
  console.log('P4 OK  idle polls emitted no bracket events — pill never appeared')

  u2g(); u2n()

  // ── Phase 5: a genuinely SLOW pull (latency > INDICATOR_DELAY) MUST still
  // surface the indicator — the feature is preserved, only the flash is gone.
  // A dedicated no-poll store makes the bracket a single deterministic event. ──
  state.latencyMs = 80
  const slowStore = makeSharedMemoryStore({
    getToken: () => 'tok', fetchImpl: net, cacheStore,
    pollMs: 0, indicatorDelayMs: INDICATOR_DELAY, isVisible: () => true,
  })
  const revS = []
  let n5 = { status: 'loading', md: '', revalidating: false }
  const u5 = slowStore.subscribe(
    notePath,
    ({ body, present }) => {
      if (!present || body == null) { n5 = { ...n5, status: 'missing' }; return }
      n5 = { ...n5, status: 'ready', md: body }
    },
    { onRevalidate: (b) => { n5 = { ...n5, revalidating: b }; revS.push(b) } },
  )
  await wait(180)   // > INDICATOR_DELAY(40) + latency(80): the pill must have shown
  assert.equal(n5.status, 'ready', 'P5: slow store still paints the cached note instantly')
  assert.ok(revS.includes(true), 'P5: indicator STILL raised for a genuinely slow pull')
  assert.equal(revS[revS.length - 1], false, 'P5: indicator cleared after the slow pull landed')
  assert.equal(n5.revalidating, false, 'P5: final revalidating state cleared')
  u5()
  console.log('P5 OK  slow pull raised then cleared the pill: ' + JSON.stringify(revS))

  console.log('\nALL OFFLINE-HARNESS PHASES PASSED')
}

run().then(() => process.exit(0)).catch((e) => { console.error('HARNESS FAIL:', e.message); process.exit(1) })
