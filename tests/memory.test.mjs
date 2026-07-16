import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdirSync, readFileSync } from 'node:fs'
import { buildEnv, esbuildPath } from './test-deps.mjs'

mkdirSync(new URL('./.build/', import.meta.url), { recursive: true })
execFileSync(esbuildPath, [
  '--bundle',
  '--format=esm',
  '--jsx=automatic',
  '--platform=node',
  'index.jsx',
  '--outfile=tests/.build/index.mjs',
], {
  cwd: new URL('..', import.meta.url),
  env: buildEnv(),
  stdio: 'pipe',
})

const {
  buildLocalGraphData,
  computeRendererFitTransform,
  normalizeRendererGraphData,
  shouldShowScreenLabel,
  renderWikiLinks,
  nodeRadius,
  shouldShowNodeLabel,
  safeMemoryPath,
  neutralizeMemoryMarkdown,
  parseDailyCronTime,
  timeToDailyCron,
  MEMORY_SANITIZE_OPTIONS,
  makeSharedMemoryStore,
} = await import('./.build/index.mjs')

test('shouldShowNodeLabel hides ordinary nodes below every threshold except close zoom', () => {
  const node = { id: 'plain', importance: 6, mocs: [] }
  assert.equal(shouldShowNodeLabel(0.9499, node, null), false)
  assert.equal(shouldShowNodeLabel(0.95, node, null), true)
})

test('shouldShowNodeLabel always shows small-graph labels when marked', () => {
  assert.equal(shouldShowNodeLabel(0.001, { id: 'plain', showLabelAlways: true }, null), true)
  assert.equal(shouldShowNodeLabel(undefined, { id: 'plain', showLabelAlways: true }, null), true)
})

test('shouldShowNodeLabel shows MOC-linked nodes at 0.24 and above', () => {
  const node = { id: 'linked', importance: 1, mocs: ['projects'] }
  assert.equal(shouldShowNodeLabel(0.2399, node, null), false)
  assert.equal(shouldShowNodeLabel(0.24, node, null), true)
})

test('shouldShowNodeLabel always shows hovered nodes', () => {
  const node = { id: 'hovered', importance: 1, mocs: [] }
  assert.equal(shouldShowNodeLabel(0.001, node, 'hovered'), true)
})

test('shouldShowNodeLabel always shows MOC and local-center nodes', () => {
  assert.equal(shouldShowNodeLabel(0.001, { id: 'hub', type: 'moc' }, null), true)
  assert.equal(shouldShowNodeLabel(0.001, { id: 'center', localDepth: 0 }, null), true)
})

test('shouldShowNodeLabel shows important nodes at 0.18', () => {
  const important = { id: 'important', importance: 7, mocs: [] }
  const almostImportant = { id: 'almost', importance: 6.99, mocs: [] }
  assert.equal(shouldShowNodeLabel(0.1799, important, null), false)
  assert.equal(shouldShowNodeLabel(0.18, important, null), true)
  assert.equal(shouldShowNodeLabel(0.18, almostImportant, null), false)
})

test('shouldShowNodeLabel rejects malformed scales for threshold labels', () => {
  assert.equal(shouldShowNodeLabel(Number.NaN, { id: 'x', mocs: ['m'] }, null), false)
  assert.equal(shouldShowNodeLabel(Infinity, { id: 'x' }, null), false)
})

test('nodeRadius uses importance and access count for ordinary nodes', () => {
  assert.equal(nodeRadius({ importance: 1, access_count: 0 }), 4.55)
  assert.equal(nodeRadius({ importance: 5, access_count: 0 }), 10.75)
  assert.equal(nodeRadius({ importance: 1, access_count: 7 }), 9.2)
})

test('nodeRadius applies the MOC multiplier', () => {
  assert.equal(nodeRadius({ type: 'moc', importance: 5, access_count: 0 }), 15.049999999999999)
})

test('nodeRadius guards sparse and malformed node data', () => {
  assert.equal(nodeRadius(), 4.55)
  assert.equal(nodeRadius({ importance: -5, access_count: -2 }), 4.55)
  assert.equal(nodeRadius({ importance: Number.NaN, access_count: Infinity }), 4.55)
})

test('daily cron helpers round-trip Memory schedule times', () => {
  assert.equal(parseDailyCronTime('30 5 * * *'), '05:30')
  assert.equal(parseDailyCronTime('0 23 * * *'), '23:00')
  assert.equal(timeToDailyCron('05:30'), '30 5 * * *')
  assert.equal(timeToDailyCron('23:00'), '0 23 * * *')
})

test('daily cron helpers reject custom or malformed schedules', () => {
  assert.equal(parseDailyCronTime('*/15 * * * *'), null)
  assert.equal(parseDailyCronTime('30 5 * * 1'), null)
  assert.equal(parseDailyCronTime('60 5 * * *'), null)
  assert.equal(timeToDailyCron('5:30'), null)
  assert.equal(timeToDailyCron('25:00'), null)
})

test('fetch wrapper passes app id into the Memory runner gate', () => {
  const wrapper = readFileSync(new URL('../fetch.sh', import.meta.url), 'utf8')
  assert.match(wrapper, /MEMORY_APP_ID="\$APP_ID"/)
  assert.match(wrapper, /python3 "\$RUNNER" "\$APP_ID"/)
  assert.match(wrapper, /APP_TOKEN/)
  assert.doesNotMatch(wrapper, /service-token|SERVICE_TOKEN|AGENT_TOKEN/)
})

test('manifest activates Memory only through a system prompt contribution', () => {
  const manifest = JSON.parse(readFileSync(new URL('../mobius.json', import.meta.url), 'utf8'))
  const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'))
  assert.equal(pkg.version, manifest.version)
  assert.equal(manifest.system_app, true)
  assert.equal(manifest.system_prompt, 'memory-core.md')
  assert.deepEqual(manifest.skills, ['memory.md'])
  assert.equal('extensions' in manifest, false)
  assert.equal(manifest.permissions.shared_memory, 'write')
  assert.equal(manifest.permissions.chat_log_access, 'summary')
  assert.equal(manifest.permissions.background_agent, true)
  assert.equal(manifest.schedule.initialize_on_install, true)
  assert.equal(manifest.embeds_agent, false)
  for (const file of [
    'memory-core.md', 'memory.md', 'memory_search.py', 'memory_runner.py',
    'memory_store.py', 'memory_graph.py',
  ]) {
    assert.ok(manifest.source_files.includes(file), file)
  }
})

test('reader returns verified graph-relative file pointers', () => {
  const reader = readFileSync(new URL('../memory_search.py', import.meta.url), 'utf8')
  assert.match(reader, /FILES:/)
  assert.match(reader, /ready_pointer\(\)/)
  assert.match(reader, /read_revision_file\(commit, rel\)/)
  assert.match(reader, /retrieval subagent/)
  assert.match(reader, /"--tools", ""/)
  assert.match(reader, /path in allowed/)
  assert.doesNotMatch(reader, /codex|Glob|Grep/)
  const prompt = readFileSync(new URL('../memory-core.md', import.meta.url), 'utf8')
  assert.match(prompt, /focused retrieval prompt/)
  assert.match(prompt, /source_dir/)
  assert.doesNotMatch(prompt, /\/data\/apps\/memory\/memory_search/)
  assert.match(prompt, /never\s+injected/i)
})

test('viewer pins graph and notes to the validated ready commit', () => {
  const source = readFileSync(new URL('../index.jsx', import.meta.url), 'utf8')
  assert.match(source, /store\.subscribe\('\.ready'/)
  assert.match(source, /COMMIT_RE\.test/)
  assert.match(source, /store\.subscribe\('graph\.json'/)
  assert.match(source, /revision/)
  assert.doesNotMatch(source, /generations\/\$\{/)
  assert.match(source, /status === 'initializing'/)
})

test('runner liveness is tied to the live app row, not a generic extension', () => {
  const runner = readFileSync(new URL('../memory_runner.py', import.meta.url), 'utf8')
  assert.match(runner, /\/api\/apps\/\{app_id\}/)
  assert.match(runner, /\/api\/apps\/\{app_id\}\/job-context/)
  assert.match(runner, /\/api\/chat-logs/)
  assert.doesNotMatch(runner, /app_extensions|extensions\.memory_graph/)
  assert.doesNotMatch(runner, /app\.background_agents|codex_sdk_runner|service-token|SERVICE_TOKEN|AGENT_TOKEN/)
  assert.match(runner, /"--tools", ""/)
})

test('settings expose app-level background agent overrides', () => {
  const source = readFileSync(new URL('../index.jsx', import.meta.url), 'utf8')
  const runner = readFileSync(new URL('../memory_runner.py', import.meta.url), 'utf8')
  assert.match(source, /Background primary/)
  assert.match(source, /Background secondary/)
  assert.match(source, /primary_agent_mode/)
  assert.match(source, /secondary_agent_mode/)
  assert.match(source, /primaryAgentMode === 'app' \? 'custom' : 'system'/)
  assert.match(source, /secondaryAgentMode === 'app' \? 'custom' : 'system'/)
  assert.match(runner, /primary_agent_mode.*in \("custom", "app"\)/)
  assert.match(runner, /secondary_agent_mode.*in \("custom", "app"\)/)
  assert.match(runner, /def _settings/)
  assert.match(runner, /def _agent_choices/)
  assert.match(runner, /job-context/)
})

test('renderWikiLinks replaces slugs with note titles and keeps aliases', () => {
  const md = 'See [[abc]] and [[def|custom label]] and [[missing]].'
  const out = renderWikiLinks(md, [
    { id: 'abc', title: 'Alpha Beta' },
    { id: 'def', title: 'Delta Echo' },
  ])
  assert.equal(
    out,
    'See [Alpha Beta](#memory-node-abc) and [custom label](#memory-node-def) and [missing](#memory-node-missing).',
  )
})

test('buildLocalGraphData returns a depth-limited neighborhood', () => {
  const graph = {
    nodes: [
      { id: 'a', title: 'A' },
      { id: 'b', title: 'B' },
      { id: 'c', title: 'C' },
      { id: 'd', title: 'D' },
      { id: 'e', title: 'E' },
      { id: 'f', title: 'F' },
    ],
    edges: [
      { source: 'a', target: 'b', kind: 'link' },
      { source: 'b', target: 'c', kind: 'link' },
      { source: 'c', target: 'd', kind: 'link' },
      { source: 'd', target: 'e', kind: 'link' },
      { source: 'e', target: 'f', kind: 'link' },
    ],
  }
  const oneHop = buildLocalGraphData(graph, 'a', 1)
  assert.deepEqual(oneHop.nodes.map((n) => n.id).sort(), ['a', 'b'])
  assert.equal(oneHop.nodes.find((n) => n.id === 'a').localDepth, 0)
  assert.equal(oneHop.nodes.find((n) => n.id === 'b').localDepth, 1)
  assert.equal(oneHop.nodes.every((n) => n.showLabelAlways), true)
  assert.deepEqual(oneHop.links.map((e) => `${e.source}-${e.target}`), ['a-b'])

  const capped = buildLocalGraphData(graph, 'a', 99)
  assert.deepEqual(capped.nodes.map((n) => n.id).sort(), ['a', 'b', 'c', 'd', 'e'])
  assert.equal(capped.links.length, 4)
})

test('screen labels keep global graph selective at low zoom', () => {
  assert.equal(shouldShowScreenLabel({ id: 'hub', type: 'moc' }, 0.2, 99, { mode: 'global' }), true)
  assert.equal(shouldShowScreenLabel({ id: 'plain' }, 0.89, 0, { mode: 'global' }), false)
  assert.equal(shouldShowScreenLabel({ id: 'plain' }, 1.1, 5, { mode: 'global' }), true)
  assert.equal(shouldShowScreenLabel({ id: 'plain' }, 1.1, 6, { mode: 'global' }), false)
})

test('screen labels show local center and nearby nodes before distant nodes', () => {
  assert.equal(shouldShowScreenLabel({ id: 'center', localDepth: 0 }, 0.1, 99, { mode: 'local' }), true)
  assert.equal(shouldShowScreenLabel({ id: 'near', localDepth: 1 }, 0.72, 99, { mode: 'local' }), true)
  assert.equal(shouldShowScreenLabel({ id: 'far', localDepth: 2 }, 1.14, 0, { mode: 'local' }), false)
  assert.equal(shouldShowScreenLabel({ id: 'far', localDepth: 2 }, 1.15, 0, { mode: 'local' }), true)
})

test('normalizeRendererGraphData clones nodes and drops dangling links', () => {
  const out = normalizeRendererGraphData({
    nodes: [
      { id: 'a', title: 'A' },
      { id: 'b', title: 'B', x: 12, y: -3 },
    ],
    links: [
      { source: 'a', target: 'b', kind: 'link' },
      { source: 'a', target: 'missing', kind: 'link' },
    ],
  }, 400, 300)

  assert.equal(out.nodes.length, 2)
  assert.equal(out.links.length, 1)
  assert.equal(out.links[0].source.id, 'a')
  assert.equal(out.links[0].target.id, 'b')
  assert.equal(out.nodes.find((n) => n.id === 'b').x, 12)
  assert.equal(Number.isFinite(out.nodes.find((n) => n.id === 'a').x), true)
})

test('computeRendererFitTransform centers finite graph bounds within limits', () => {
  const fit = computeRendererFitTransform([
    { id: 'a', x: -100, y: -50 },
    { id: 'b', x: 100, y: 50 },
  ], 400, 300, { padding: 40, minScale: 0.5, maxScale: 1.2 })

  assert.equal(fit.k <= 1.2, true)
  assert.equal(fit.k >= 0.5, true)
  assert.equal(Math.round(fit.x), 200)
  assert.equal(Math.round(fit.y), 150)
})

test('safeMemoryPath accepts normal markdown note paths and encodes segments', () => {
  assert.equal(safeMemoryPath('notes/about me.md'), 'notes/about%20me.md')
  assert.equal(safeMemoryPath('mocs/platform.md'), 'mocs/platform.md')
})

test('safeMemoryPath rejects traversal, absolute, empty, and non-markdown paths', () => {
  const bad = [
    null,
    undefined,
    '',
    '   ',
    '/etc/passwd',
    '..\\notes\\x.md',
    'notes/../../service-token.txt',
    'notes/./x.md',
    'notes//x.md',
    'notes/x.md?inline=1',
    'notes/x.md#frag',
    'notes/x.txt',
  ]
  for (const path of bad) {
    assert.equal(safeMemoryPath(path), null, String(path))
  }
})

test('neutralizeMemoryMarkdown keeps labels but removes urls before rendering', () => {
  const md = [
    '![remote pixel](https://example.test/track.png)',
    '[source](https://example.test/page)',
    '[local](notes/idea.md)',
  ].join('\n')
  const out = neutralizeMemoryMarkdown(md)

  assert.ok(out.includes('remote pixel'))
  assert.ok(out.includes('source'))
  assert.ok(out.includes('local'))
  assert.ok(!out.includes('https://'))
  assert.ok(!out.includes('notes/idea.md'))
})

test('neutralizeMemoryMarkdown leaves wikilink syntax for renderWikiLinks', () => {
  const md = 'See [[some-note]] and [[other|alias]] but not [ext](https://evil.test/x).'
  const out = renderWikiLinks(neutralizeMemoryMarkdown(md), [
    { id: 'some-note', title: 'Some Note' },
  ])
  assert.ok(out.includes('[Some Note](#memory-node-some-note)'))
  assert.ok(out.includes('[alias](#memory-node-other)'))
  assert.ok(!out.includes('https://evil.test'))
})

test('memory sanitizer forbids network-bearing tags and attributes', () => {
  assert.ok(MEMORY_SANITIZE_OPTIONS.FORBID_TAGS.includes('img'))
  assert.ok(MEMORY_SANITIZE_OPTIONS.FORBID_TAGS.includes('iframe'))
  assert.ok(MEMORY_SANITIZE_OPTIONS.FORBID_TAGS.includes('form'))
  assert.ok(MEMORY_SANITIZE_OPTIONS.FORBID_ATTR.includes('src'))
  assert.ok(MEMORY_SANITIZE_OPTIONS.FORBID_ATTR.includes('srcset'))
  // href is deliberately NOT forbidden — wikilink anchors need it; the
  // restrictNoteHtml pass strips every non-#memory-node- href instead.
  assert.ok(!MEMORY_SANITIZE_OPTIONS.FORBID_ATTR.includes('href'))
})

// ── makeSharedMemoryStore: offline read-through + subscribe repaint ──────────
// A fake cache (plain Map) and a controllable fetch let these run with no
// network and no browser caches — the same shape the offline harness drives.
function makeFakeCache() {
  const m = new Map()
  return {
    map: m,
    read: async (k) => (m.has(k) ? m.get(k) : null),
    write: async (k, e) => { m.set(k, e) },
  }
}

test('store getJSON caches the graph then serves it offline', async () => {
  const cacheStore = makeFakeCache()
  let online = true
  const graph = JSON.stringify({ nodes: [{ id: 'a' }], edges: [], problems: [] })
  const fetchImpl = async () => {
    if (!online) throw new TypeError('Failed to fetch')
    return { ok: true, status: 200, text: async () => graph }
  }
  const store = makeSharedMemoryStore({ getToken: () => 't', fetchImpl, cacheStore, pollMs: 0 })

  const first = await store.getJSON('graph.json')
  assert.equal(first.present, true)
  assert.deepEqual(first.value.nodes, [{ id: 'a' }])

  // Network goes down; the read-through cache still answers from the mirror.
  online = false
  const offlineRead = await store.getJSON('graph.json')
  assert.equal(offlineRead.present, true, 'cached graph served offline')
  assert.deepEqual(offlineRead.value.nodes, [{ id: 'a' }])
  assert.equal(offlineRead.error, null)
})

test('store reads graph blobs from an exact Git revision', async () => {
  const seen = []
  const commit = '0123456789abcdef0123456789abcdef01234567'
  const fetchImpl = async (url) => {
    seen.push(url)
    return { ok: true, status: 200, text: async () => '{"nodes":[]}' }
  }
  const store = makeSharedMemoryStore({
    getToken: () => 't', fetchImpl, cacheStore: makeFakeCache(), pollMs: 0,
  })

  await store.getJSON('graph.json', { revision: commit })

  const parsed = new URL(seen[0], 'https://mobius.local')
  assert.equal(parsed.pathname, '/api/storage/shared-git/memory/repository')
  assert.equal(parsed.searchParams.get('revision'), commit)
  assert.equal(parsed.searchParams.get('file'), 'graph.json')
})

test('store getText with no cache and offline reports an error, not a crash', async () => {
  const cacheStore = makeFakeCache()
  const fetchImpl = async () => { throw new TypeError('Failed to fetch') }
  const store = makeSharedMemoryStore({ getToken: () => 't', fetchImpl, cacheStore, pollMs: 0 })
  const r = await store.getText('notes/x.md')
  assert.equal(r.present, false)
  assert.equal(r.value, null)
  assert.ok(r.error, 'no cache + offline surfaces an error to render the error state')
})

test('store falls back when a sandbox throws on Cache Storage access', async () => {
  const prior = Object.getOwnPropertyDescriptor(globalThis, 'caches')
  Object.defineProperty(globalThis, 'caches', {
    configurable: true,
    get() { throw new DOMException('sandboxed', 'SecurityError') },
  })
  try {
    const fetchImpl = async () => ({
      ok: true,
      status: 200,
      text: async () => '{"schema":2,"commit":"0123456789abcdef0123456789abcdef01234567"}',
    })
    const store = makeSharedMemoryStore({
      getToken: () => 't', fetchImpl, pollMs: 0,
    })
    const result = await store.getText('.ready')
    assert.equal(result.present, true)
    assert.match(result.value, /0123456789abcdef0123456789abcdef01234567/)
    assert.equal(result.error, null)
  } finally {
    if (prior) Object.defineProperty(globalThis, 'caches', prior)
    else delete globalThis.caches
  }
})

test('store getText caches a note then serves it offline', async () => {
  const cacheStore = makeFakeCache()
  let online = true
  let body = '# hello\nworld'
  const fetchImpl = async () => {
    if (!online) throw new TypeError('offline')
    return { ok: true, status: 200, text: async () => body }
  }
  const store = makeSharedMemoryStore({ getToken: () => 't', fetchImpl, cacheStore, pollMs: 0 })
  const first = await store.getText('notes/n.md')
  assert.equal(first.value, '# hello\nworld')
  online = false
  const offline = await store.getText('notes/n.md')
  assert.equal(offline.value, '# hello\nworld', 'cached note served offline')
})

test('store subscribe repaints when an external write changes the body, and brackets revalidation', async () => {
  const cacheStore = makeFakeCache()
  let body = 'v1'
  let visible = true
  const fetchImpl = async () => ({ ok: true, status: 200, text: async () => body })
  const store = makeSharedMemoryStore({
    getToken: () => 't', fetchImpl, cacheStore, pollMs: 5,
    isVisible: () => visible,
  })

  const bodies = []
  const reval = []
  const unsub = store.subscribe(
    'notes/n.md',
    ({ body }) => bodies.push(body),
    { onRevalidate: (b) => reval.push(b) },
  )

  // Let the initial read + first revalidation settle.
  await new Promise((r) => setTimeout(r, 30))
  assert.ok(bodies.includes('v1'), 'initial body delivered')
  assert.ok(reval.includes(true) && reval.includes(false), 'revalidation brackets fired (true then false)')

  // Simulate an external (agent/cron) write to this path, then a poll tick.
  body = 'v2-agent-wrote-this'
  await new Promise((r) => setTimeout(r, 40))
  assert.equal(bodies[bodies.length - 1], 'v2-agent-wrote-this', 'view repainted on external write')

  // No change => no extra repaint (dedupe).
  const countBefore = bodies.length
  await new Promise((r) => setTimeout(r, 30))
  assert.equal(bodies.length, countBefore, 'no repaint when body is unchanged')

  unsub()
})

test('store subscribe paints cached value first when offline, before any network', async () => {
  const cacheStore = makeFakeCache()
  // Pre-seed the cache as if a previous online session had stored the graph.
  const graphUrl = '/api/storage/shared/memory/graph.json'
  cacheStore.map.set(graphUrl, { body: JSON.stringify({ nodes: [{ id: 'seed' }] }), present: true })
  const fetchImpl = async () => { throw new TypeError('offline') }
  const store = makeSharedMemoryStore({ getToken: () => 't', fetchImpl, cacheStore, pollMs: 0 })

  const seen = []
  const unsub = store.subscribe('graph.json', ({ body }) => seen.push(body))
  await new Promise((r) => setTimeout(r, 20))
  assert.ok(seen.length >= 1, 'cached value painted even though network is down')
  assert.match(seen[0], /seed/)
  unsub()
})
