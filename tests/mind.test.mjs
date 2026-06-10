import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdirSync } from 'node:fs'

const esbuild = '/home/hmzmrzx/projects/mobius/frontend/node_modules/.bin/esbuild'
const nodePath = '/home/hmzmrzx/projects/mobius/frontend/node_modules'
mkdirSync(new URL('./.build/', import.meta.url), { recursive: true })
execFileSync(esbuild, [
  '--bundle',
  '--format=esm',
  '--jsx=automatic',
  '--platform=node',
  'index.jsx',
  '--external:https://esm.sh/*',
  '--outfile=tests/.build/index.mjs',
], {
  cwd: new URL('..', import.meta.url),
  env: { ...process.env, NODE_PATH: nodePath },
  stdio: 'pipe',
})

const {
  RUNTIME_MODULE_CANDIDATES,
  MEMORY_SANITIZE_OPTIONS,
  deriveLocalGraph,
  filterNodes,
  importFirstAvailable,
  neutralizeMemoryMarkdown,
  nodeRadius,
  safeMemoryPath,
  shouldShowNodeLabel,
} = await import('./.build/index.mjs')

test('safeMemoryPath accepts normal markdown note paths and encodes segments', () => {
  assert.equal(safeMemoryPath('notes/about me.md'), 'notes/about%20me.md')
  assert.equal(safeMemoryPath('mocs/platform.md'), 'mocs/platform.md')
})

test('safeMemoryPath rejects traversal, absolute, empty, and non-markdown paths', () => {
  for (const path of [
    '../secret.md',
    'notes/../secret.md',
    '/notes/a.md',
    'notes//a.md',
    'notes\\a.md',
    'notes/a.txt',
    'notes/a.md?download=1',
    '',
    null,
  ]) {
    assert.equal(safeMemoryPath(path), null, String(path))
  }
})

test('nodeRadius and labels stay guarded on malformed input', () => {
  assert.ok(nodeRadius({ importance: 'bad', access_count: -1 }) > 0)
  assert.equal(shouldShowNodeLabel(Number.NaN, { id: 'x' }, null), false)
  assert.equal(shouldShowNodeLabel(1.5, { id: 'x' }, null), true)
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

test('memory sanitizer forbids network-bearing tags and attributes', () => {
  assert.ok(MEMORY_SANITIZE_OPTIONS.FORBID_TAGS.includes('img'))
  assert.ok(MEMORY_SANITIZE_OPTIONS.FORBID_TAGS.includes('iframe'))
  assert.ok(MEMORY_SANITIZE_OPTIONS.FORBID_ATTR.includes('href'))
  assert.ok(MEMORY_SANITIZE_OPTIONS.FORBID_ATTR.includes('src'))
  assert.ok(MEMORY_SANITIZE_OPTIONS.FORBID_ATTR.includes('srcset'))
})

test('deriveLocalGraph returns the selected neighborhood by depth', () => {
  const graph = {
    nodes: ['a', 'b', 'c', 'd', 'x'].map((id) => ({ id })),
    edges: [
      { source: 'a', target: 'b', kind: 'link' },
      { source: { id: 'b' }, target: { id: 'c' }, kind: 'link' },
      { source: 'c', target: 'd', kind: 'link' },
      { source: 'x', target: 'd', kind: 'link' },
    ],
  }

  assert.deepEqual(
    deriveLocalGraph(graph, 'a', 1).nodes.map((n) => n.id),
    ['a', 'b'],
  )
  assert.deepEqual(
    deriveLocalGraph(graph, 'a', 2).nodes.map((n) => n.id),
    ['a', 'b', 'c'],
  )
  assert.deepEqual(
    deriveLocalGraph(graph, 'a', 2).edges.map((e) => [e.source.id || e.source, e.target.id || e.target]),
    [['a', 'b'], ['b', 'c']],
  )
})

test('deriveLocalGraph falls back to the full graph without a valid center', () => {
  const graph = {
    nodes: [{ id: 'a' }, { id: 'b' }],
    edges: [{ source: 'a', target: 'b', kind: 'link' }],
  }

  assert.equal(deriveLocalGraph(graph, null, 1).nodes.length, 2)
  assert.equal(deriveLocalGraph(graph, 'missing', 1).edges.length, 1)
})

test('filterNodes returns null when no filter is active', () => {
  const nodes = [{ id: 'a', title: 'Alpha', mocs: [], tags: [] }]
  assert.equal(filterNodes(nodes, {}), null)
  assert.equal(filterNodes(nodes, { query: '', mocSlug: null }), null)
})

test('filterNodes matches by title substring (case-insensitive)', () => {
  const nodes = [
    { id: 'note-alpha', title: 'Alpha note', mocs: [], tags: [] },
    { id: 'note-beta', title: 'Beta study', mocs: [], tags: [] },
    { id: 'note-gamma', title: 'Gamma', mocs: [], tags: ['learning'] },
  ]
  const result = filterNodes(nodes, { query: 'alpha' })
  assert.equal(result.length, 1)
  assert.equal(result[0].id, 'note-alpha')
})

test('filterNodes matches by tag', () => {
  const nodes = [
    { id: 'a', title: 'A', mocs: [], tags: ['machine-learning'] },
    { id: 'b', title: 'B', mocs: [], tags: ['cooking'] },
  ]
  const result = filterNodes(nodes, { query: 'learning' })
  assert.equal(result.length, 1)
  assert.equal(result[0].id, 'a')
})

test('filterNodes matches by id', () => {
  const nodes = [
    { id: 'special-topic', title: 'Special', mocs: [], tags: [] },
    { id: 'other', title: 'Other', mocs: [], tags: [] },
  ]
  const result = filterNodes(nodes, { query: 'special-topic' })
  assert.equal(result.length, 1)
  assert.equal(result[0].id, 'special-topic')
})

test('filterNodes filters by MOC membership', () => {
  const nodes = [
    { id: 'hub', title: 'Hub', type: 'moc', mocs: [], tags: [] },
    { id: 'member', title: 'Member', mocs: ['hub'], tags: [] },
    { id: 'outsider', title: 'Outsider', mocs: ['other'], tags: [] },
  ]
  const result = filterNodes(nodes, { mocSlug: 'hub' })
  assert.equal(result.length, 2)
  assert.ok(result.some((n) => n.id === 'hub'))
  assert.ok(result.some((n) => n.id === 'member'))
  assert.ok(!result.some((n) => n.id === 'outsider'))
})

test('filterNodes combines MOC and search filters', () => {
  const nodes = [
    { id: 'hub', title: 'Hub', type: 'moc', mocs: [], tags: [] },
    { id: 'member-a', title: 'Alpha note', mocs: ['hub'], tags: [] },
    { id: 'member-b', title: 'Beta note', mocs: ['hub'], tags: [] },
  ]
  const result = filterNodes(nodes, { query: 'alpha', mocSlug: 'hub' })
  assert.equal(result.length, 1)
  assert.equal(result[0].id, 'member-a')
})

test('importFirstAvailable tries importmap, vendor, then CDN candidates in order', async () => {
  const seen = []
  const mod = await importFirstAvailable(['bare', '/vendor/lib.mjs', 'https://esm.sh/lib'], async (spec) => {
    seen.push(spec)
    if (spec !== 'https://esm.sh/lib') throw new Error('nope')
    return { ok: true }
  })

  assert.deepEqual(seen, ['bare', '/vendor/lib.mjs', 'https://esm.sh/lib'])
  assert.equal(mod.ok, true)
  assert.equal(RUNTIME_MODULE_CANDIDATES.forceGraph2d[0], 'react-force-graph-2d')
  assert.ok(RUNTIME_MODULE_CANDIDATES.marked[1].startsWith('/vendor/'))
})
