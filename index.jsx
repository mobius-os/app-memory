// Memory — thin app shell. The module tree is declared in mobius.json's
// source_files; the multi-file installer fetches each path and esbuild bundles
// from this entry, resolving the relative imports below at compile time.
//
//   constants.js  — shared storage URLs, graph-runtime URLs, palette, and style table
//   theme.js      — the single app stylesheet (CSS)
//   domain.js     — pure + DOM-level graph, markdown, sanitization, and formatting helpers; no React/network
//   storage.js    — shared-memory read-through cache and subscribe store
//   graph/render.jsx — d3/Pixi runtime loader, renderer component, and renderer math
//   ui/*.jsx      — one React component per file
//
// Only App lives here: it owns top-level graph/note state, persistence wiring,
// shell navigation state, and mounts the graph, list, and note-panel UI.
import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { D3_URL, EFFORT_LEVELS, PALETTE, PIXI_URL, S, defaultEffort } from './constants.js'
import { CSS } from './theme.js'
import { makeSharedMemoryStore } from './storage.js'
import {
  MEMORY_SANITIZE_OPTIONS,
  buildLocalGraphData,
  cssVar,
  escapeHtml,
  fmtBytes,
  hashStr,
  neutralizeMemoryMarkdown,
  nodeRadius,
  parseDailyCronTime,
  parseFrontmatter,
  relDate,
  renderWikiLinks,
  restrictNoteHtml,
  safeMemoryPath,
  stripFrontmatter,
  timeToDailyCron,
} from './domain.js'
import { MemoryGraphRenderer, loadScriptOnce } from './graph/render.jsx'
import { Th } from './ui/Th.jsx'
import { ImportanceDots } from './ui/ImportanceDots.jsx'
import { EmptyConstellation } from './ui/EmptyConstellation.jsx'
import { GraphGlyph } from './ui/GraphGlyph.jsx'
import { ListGlyph } from './ui/ListGlyph.jsx'
import { ChatGlyph } from './ui/ChatGlyph.jsx'
import { TextGlyph } from './ui/TextGlyph.jsx'
import { NetworkGlyph } from './ui/NetworkGlyph.jsx'
import { ModelPicker } from './ui/ModelPicker.jsx'
import { EffortStepper } from './ui/EffortStepper.jsx'
import { BackgroundAgentList } from './ui/BackgroundAgentList.jsx'

export { makeSharedMemoryStore } from './storage.js'
export {
  MEMORY_SANITIZE_OPTIONS,
  buildLocalGraphData,
  neutralizeMemoryMarkdown,
  nodeRadius,
  parseDailyCronTime,
  renderWikiLinks,
  safeMemoryPath,
  shouldShowScreenLabel,
  shouldShowNodeLabel,
  timeToDailyCron,
} from './domain.js'
export {
  computeRendererFitTransform,
  normalizeRendererGraphData,
  updateRendererSelectionPin,
} from './graph/render.jsx'

const AGENT_PROVIDER_META = [
  { key: 'claude', label: 'Claude Code' },
  { key: 'codex', label: 'OpenAI Codex' },
];

const FALLBACK_AGENT_GROUPS = [
  {
    key: 'claude',
    label: 'Claude Code',
    models: [
      { id: 'claude-opus-4-8', name: 'Opus 4.8' },
      { id: 'claude-opus-4-7', name: 'Opus 4.7' },
      { id: 'claude-opus-4-6', name: 'Opus 4.6' },
      { id: 'claude-sonnet-4-6', name: 'Sonnet 4.6' },
      { id: 'claude-sonnet-4-5-20251001', name: 'Sonnet 4.5' },
      { id: 'claude-haiku-4-5-20251001', name: 'Haiku 4.5' },
    ],
  },
  {
    key: 'codex',
    label: 'OpenAI Codex',
    models: [
      { id: 'gpt-5.5', name: 'gpt-5.5' },
      { id: 'gpt-5.4', name: 'gpt-5.4' },
    ],
  },
];

const COMMIT_RE = /^[0-9a-f]{40}$/;

function buildAgentGroups(payload) {
  if (!payload || typeof payload !== 'object') return FALLBACK_AGENT_GROUPS;
  const groups = [];
  for (const meta of AGENT_PROVIDER_META) {
    const rows = Array.isArray(payload[meta.key]) ? payload[meta.key] : null;
    if (!rows || rows.length === 0) continue;
    groups.push({
      key: meta.key,
      label: meta.label,
      models: rows
        .filter((row) => row && typeof row.id === 'string')
        .map((row) => ({ id: row.id, name: row.name || row.id })),
    });
  }
  return groups.length ? groups : FALLBACK_AGENT_GROUPS;
}

function isKnownAgentProvider(provider) {
  return AGENT_PROVIDER_META.some((meta) => meta.key === provider);
}

function effortForProvider(provider, value) {
  const levels = EFFORT_LEVELS[provider] || [];
  return levels.some((level) => level.value === value)
    ? value
    : defaultEffort(provider);
}

function effortLabel(provider, value) {
  return (EFFORT_LEVELS[provider] || []).find((level) => level.value === value)?.label || value;
}

export default function App({ appId, token }) {
  const [graph, setGraph] = useState(null);
  const [revision, setRevision] = useState(null);
  const [status, setStatus] = useState('loading'); // loading | initializing | ready | empty | error
  const [errMsg, setErrMsg] = useState('');
  const [view, setView] = useState('graph'); // graph | list
  const [selected, setSelected] = useState(null); // node object
  const [noteState, setNoteState] = useState({ status: 'idle', md: '', fm: {}, revalidating: false });
  const [hoverId, setHoverId] = useState(null);
  const [sortKey, setSortKey] = useState('access_count');
  const [sortDir, setSortDir] = useState('desc');
  const [showHealth, setShowHealth] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [scheduleStatus, setScheduleStatus] = useState('idle'); // idle | loading | ready | error
  const [scheduleCron, setScheduleCron] = useState('30 5 * * *');
  const [scheduleTime, setScheduleTime] = useState('05:30');
  const [scheduleCustom, setScheduleCustom] = useState(false);
  const [scheduleSaving, setScheduleSaving] = useState(false);
  const [scheduleMessage, setScheduleMessage] = useState('');
  const [agentStatus, setAgentStatus] = useState('idle'); // idle | loading | ready | error
  const [agentGroups, setAgentGroups] = useState(null);
  const [connectedProviders, setConnectedProviders] = useState(null);
  const [agentSettingsExtra, setAgentSettingsExtra] = useState({});
  const [primaryAgentMode, setPrimaryAgentMode] = useState('system');
  const [agentProvider, setAgentProvider] = useState('claude');
  const [agentModel, setAgentModel] = useState('');
  const [agentEffort, setAgentEffort] = useState(defaultEffort('claude'));
  const [secondaryAgentMode, setSecondaryAgentMode] = useState('system');
  const [secondaryAgentProvider, setSecondaryAgentProvider] = useState('');
  const [secondaryAgentModel, setSecondaryAgentModel] = useState('');
  const [secondaryAgentEffort, setSecondaryAgentEffort] = useState('');
  const [agentSaving, setAgentSaving] = useState(false);
  const [agentMessage, setAgentMessage] = useState('');
  const [localDepth, setLocalDepth] = useState(1);
  // Node-detail tab: 'text' shows the note, 'graph' shows the local graph.
  // Defaults to 'text' — the user arrives here from the global graph, so they
  // already have spatial context; the note body is what they came to read.
  // Only the active tab's pane mounts, so the Pixi local-graph renderer is
  // never resized (the old draggable split rebuilt it on every drag tick and
  // crashed). See the graph/text tab panes below.
  const [detailTab, setDetailTab] = useState('text');
  const detailTabRefs = useRef([]);
  const [graphRuntime, setGraphRuntime] = useState(undefined); // undefined loading | null failed | { d3, PIXI }
  const [marked, setMarked] = useState(null);
  const [purify, setPurify] = useState(null); // DOMPurify — audited HTML sanitizer
  const selectDetailTab = (next) => {
    if (next === 'graph' && detailTab !== 'graph') window.mobius.signal('memory_local_graph_viewed');
    setDetailTab(next);
  };
  const onDetailTabKeyDown = (event, index) => {
    const order = ['text', 'graph'];
    let nextIndex = index;
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % order.length;
    else if (event.key === 'ArrowLeft') nextIndex = (index - 1 + order.length) % order.length;
    else if (event.key === 'Home') nextIndex = 0;
    else if (event.key === 'End') nextIndex = order.length - 1;
    else return;
    event.preventDefault();
    selectDetailTab(order[nextIndex]);
    window.requestAnimationFrame(() => detailTabRefs.current[nextIndex]?.focus());
  };
  const panelNavRef = useRef(null);
  const panelRef = useRef(null);
  const panelCloseRef = useRef(null);
  const panelOpenerRef = useRef(null);
  // One-shot guards for the open-outcome analytics signals: the graph.json
  // subscribe callback re-fires on every revalidation and maintenance rebuild,
  // so without these the open/empty signals would inflate on a single session.
  const openedSignaledRef = useRef(false);
  const emptySignaledRef = useRef(false);

  const wrapRef = useRef(null);
  const localWrapRef = useRef(null);
  const [dims, setDims] = useState({ w: 0, h: 0 });
  const [localDims, setLocalDims] = useState({ w: 0, h: 0 });

  // Read-through, offline-capable, subscribe-driven store over the SHARED
  // /api/storage/shared/memory/ route (see makeSharedMemoryStore). One per
  // token so a token refresh rebuilds it; the cache mirror is keyed by URL and
  // shared across instances, so the offline value survives the rebuild.
  const store = useMemo(
    () => makeSharedMemoryStore({ getToken: () => token }),
    [token],
  );

  // --- Load the Quartz-style graph renderer runtime. ---
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        await Promise.all([
          loadScriptOnce(D3_URL),
          loadScriptOnce(PIXI_URL),
        ]);
        const d3 = window.d3;
        const PIXI = window.PIXI;
        if (!d3 || !PIXI) throw new Error('Graph runtime scripts loaded without d3/PIXI globals.');
        if (alive) setGraphRuntime({ d3, PIXI });
      } catch (e) {
        // Graph view degrades to the list view if the runtime can't load.
        if (alive) setGraphRuntime(null);
      }
    })();
    return () => { alive = false; };
  }, []);

  // Pin every render to the immutable Git commit selected by the atomic
  // pointer. A missing pointer means first-install initialization is still in
  // progress; malformed pointer data is never interpolated into a path.
  useEffect(() => {
    const unsub = store.subscribe('.ready', ({ body, present, error }) => {
      if (error && body == null) {
        setErrMsg(String(error.message || error));
        setStatus('error');
        return;
      }
      if (!present || body == null) {
        setRevision(null);
        setGraph(null);
        setStatus('initializing');
        return;
      }
      let pointer;
      try { pointer = JSON.parse(body); } catch {
        setErrMsg('The Memory commit pointer is not valid JSON.');
        setStatus('error');
        return;
      }
      const next = pointer?.schema === 2 ? pointer.commit : null;
      if (!COMMIT_RE.test(String(next || ''))) {
        setErrMsg('The Memory commit pointer is invalid.');
        setStatus('error');
        return;
      }
      setRevision(next);
    });
    return unsub;
  }, [store]);

  // Read graph.json from the pinned commit. Publication changes .ready and
  // switches the whole view to the next complete tree at once.
  useEffect(() => {
    if (!revision) return undefined;
    setStatus('loading');
    // Fire-and-forget open-outcome signals, each once per session (see the refs
    // above). memory_opened reports that the app reached a real graph;
    // memory_empty_shown flags cold-start installs that never got data.
    const signalReady = (nodeCount, linkCount) => {
      if (openedSignaledRef.current) return;
      openedSignaledRef.current = true;
      window.mobius.signal('memory_opened', { node_count: nodeCount, link_count: linkCount });
    };
    const signalEmpty = () => {
      if (emptySignaledRef.current) return;
      emptySignaledRef.current = true;
      window.mobius.signal('memory_empty_shown');
    };
    const unsub = store.subscribe('graph.json', ({ body, present, error }) => {
      if (error && body == null) {
        setErrMsg(String(error.message || error));
        setStatus('error');
        return;
      }
      if (!present || body == null) {
        setGraph({ nodes: [], edges: [], problems: [] });
        setSelected(null);
        setStatus('empty');
        signalEmpty();
        return;
      }
      let data;
      try { data = JSON.parse(body); } catch {
        setErrMsg('The published graph is not valid JSON.');
        setStatus('error');
        return;
      }
      const nodes = Array.isArray(data.nodes) ? data.nodes : [];
      const edges = Array.isArray(data.edges) ? data.edges : [];
      setGraph({
        nodes,
        edges,
        problems: Array.isArray(data.problems) ? data.problems : [],
      });
      setSelected((current) => current
        ? (nodes.find((node) => node.id === current.id) || null)
        : null);
      if (nodes.length === 0) {
        setStatus('empty');
        signalEmpty();
      } else {
        setStatus('ready');
        signalReady(nodes.length, edges.length);
      }
    }, { revision });
    return unsub;
  }, [revision, store]);

  // --- Measure graph containers in CSS pixels; Pixi handles the DPR backing store. ---
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0].contentRect;
      setDims({ w: Math.round(r.width), h: Math.round(r.height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [view, status]);

  // The local-graph host only exists in the DOM while the graph tab is active,
  // so re-run the measurement when the tab flips — not just when the node
  // changes. Without the detailTab dep the observer would attach to a stale
  // (or absent) element and the graph would never get non-zero dimensions.
  useEffect(() => {
    const el = localWrapRef.current;
    if (!el || !selected || detailTab !== 'graph') return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0].contentRect;
      setLocalDims({ w: Math.round(r.width), h: Math.round(r.height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [selected, detailTab]);

  // --- Build a color map: stable moc-slug -> palette color. ---
  const mocColors = useMemo(() => {
    const map = {};
    if (!graph) return map;
    const mocSlugs = new Set();
    for (const n of graph.nodes) {
      if (n.type === 'moc') mocSlugs.add(n.id);
      if (Array.isArray(n.mocs)) for (const m of n.mocs) mocSlugs.add(m);
    }
    // Sort for determinism so colors don't reshuffle between loads.
    const sorted = [...mocSlugs].sort();
    sorted.forEach((slug, i) => {
      map[slug] = PALETTE[hashStr(slug) % PALETTE.length] || PALETTE[i % PALETTE.length];
    });
    return map;
  }, [graph]);

  const colorForNode = useCallback((n) => {
    if (n.type === 'moc') return cssVar('--accent', '#a78bfa');
    const primary = Array.isArray(n.mocs) && n.mocs.length ? n.mocs[0] : null;
    if (primary && mocColors[primary]) return mocColors[primary];
    return cssVar('--muted', '#8a8a93');
  }, [mocColors]);

  // --- Node radius from importance + usage. ---
  const radiusForNode = useCallback((n) => nodeRadius(n), []);

  // --- D3 mutates node objects (x/y/vx/vy) in place, so the renderer gets
  //     its own object references. Build once per graph. ---
  const fgData = useMemo(() => {
    if (!graph) return { nodes: [], links: [] };
    const showLabelAlways = graph.nodes.length <= 120;
    return {
      nodes: graph.nodes.map((n) => ({ ...n, showLabelAlways })),
      links: graph.edges.map((e) => ({
        source: typeof e.source === 'object' ? e.source.id : e.source,
        target: typeof e.target === 'object' ? e.target.id : e.target,
        kind: e.kind,
      })),
    };
  }, [graph]);

  const nodesById = useMemo(() => {
    const map = new Map();
    if (graph) for (const n of graph.nodes) map.set(n.id, n);
    return map;
  }, [graph]);

  const localGraphData = useMemo(
    () => buildLocalGraphData(graph, selected?.id, localDepth),
    [graph, selected, localDepth],
  );

  // --- Subscribe to the selected note body. ---
  // Notes are immutable within a commit. Subscribe so the offline cache can
  // paint instantly and a revision switch can replace the entire view.
  useEffect(() => {
    if (!selected) return;
    // node.path comes from agent-written graph.json — refuse traversal,
    // absolute paths, and query/fragment smuggling before fetching.
    const rel = safeMemoryPath(selected.path || ('notes/' + selected.id + '.md'));
    if (!rel || !revision) {
      setNoteState({ status: 'missing', md: '', fm: {}, revalidating: false });
      return;
    }
    setNoteState({ status: 'loading', md: '', fm: {}, revalidating: false });
    const unsub = store.subscribe(
      rel,
      ({ body, present, error }) => {
        if (error && body == null) {
          setNoteState({ status: 'error', md: String(error.message || error), fm: {}, revalidating: false });
          return;
        }
        if (!present || body == null) {
          setNoteState((s) => ({ status: 'missing', md: '', fm: {}, revalidating: s.revalidating }));
          return;
        }
        setNoteState((s) => ({
          status: 'ready',
          md: stripFrontmatter(body),
          fm: parseFrontmatter(body),
          revalidating: s.revalidating,
        }));
      },
      {
        revision,
        onRevalidate: (busy) => setNoteState((s) => ({ ...s, revalidating: busy })),
      },
    );
    return unsub;
  }, [revision, selected, store]);

  // --- Lazy-load the markdown renderer the first time we need it. ---
  useEffect(() => {
    if ((marked && purify) || !selected) return;
    let alive = true;
    (async () => {
      try {
        const [mk, dp] = await Promise.all([
          import('marked'),
          import('dompurify'),
        ]);
        if (alive) {
          setMarked(() => mk.marked || mk.default);
          setPurify(() => dp.default || dp);
        }
      } catch (e) {
        if (alive) { setMarked(null); setPurify(null); }
      }
    })();
    return () => { alive = false; };
  }, [selected, marked, purify]);

  const noteHtml = useMemo(() => {
    if (noteState.status !== 'ready') return '';
    // Plain markdown links/images are neutralized BEFORE wikilink expansion:
    // notes can carry agent-researched content, so remote URLs are
    // dropped (their label text survives). Wikilinks expand after, so the only
    // live anchors are the #memory-node- fragments this app generates itself.
    const linkedMd = renderWikiLinks(neutralizeMemoryMarkdown(noteState.md), graph?.nodes || []);
    // Require BOTH the renderer AND the sanitizer before producing HTML — never
    // render un-sanitized markup. Notes can contain agent-researched
    // content, so DOMPurify (a real HTML-parser sanitizer) is the right tool;
    // a regex net is routinely bypassed.
    if (marked && purify) {
      try {
        const raw = marked(linkedMd, { breaks: true, gfm: true });
        return restrictNoteHtml(purify.sanitize(raw, MEMORY_SANITIZE_OPTIONS));
      } catch { return escapeHtml(noteState.md); }
    }
    return null; // renderer not ready yet -> fall back to plain text below
  }, [noteState, marked, purify, graph]);

  const onNoteClick = useCallback((e) => {
    const a = e.target?.closest?.('a[href^="#memory-node-"]');
    if (!a) return;
    e.preventDefault();
    // Note bodies are agent-written and can carry malformed percent-encoding
    // (e.g. a stray `[x](#memory-node-%)`); decodeURIComponent throws URIError on
    // those. A bad fragment must dead-end as a no-op, never break the handler.
    const raw = a.getAttribute('href').replace('#memory-node-', '');
    let slug;
    try {
      slug = decodeURIComponent(raw);
    } catch {
      return;
    }
    const node = nodesById.get(slug);
    if (node) {
      setSelected(node);
      setHoverId(slug);
      window.mobius.signal('memory_node_opened', { node_type: node.type === 'moc' ? 'moc' : 'note' });
    }
  }, [nodesById]);

  const closePanel = useCallback(() => {
    setSelected(null);
    setHoverId(null);
  }, []);

  const openPanel = useCallback(async (node, opts = {}) => {
    if (!node) return;
    if (!selected) panelOpenerRef.current = document.activeElement;
    if (!selected && window.mobius?.nav?.open) {
      try { panelNavRef.current?.close?.(); } catch {}
      const handle = window.mobius.nav.open('memory-note', () => {
        panelNavRef.current = null;
        setSelected(null);
        setHoverId(null);
      });
      panelNavRef.current = handle;
      const ready = await handle.ready?.catch(() => false);
      if (panelNavRef.current !== handle) return;
      if (!ready) panelNavRef.current = null;
    }
    setSelected(node);
    setHoverId(opts.hoverId ?? null);
    setDetailTab('text'); // every node opens on its note, not the graph
    if (opts.resetLocalDepth) setLocalDepth(1);
    // Core-engagement signal, fired here (after the panel commits to opening,
    // past the superseded-open early return above) so every open from the
    // graph, list, or legend is counted with the node's kind.
    window.mobius.signal('memory_node_opened', { node_type: node.type === 'moc' ? 'moc' : 'note' });
  }, [selected]);

  const discuss = useCallback((node) => {
    const title = node.title || node.id;
    const draft = "Let's talk about what you know: " + title;
    window.parent.postMessage(
      { type: 'moebius:new-chat', draft },
      window.location.origin,
    );
    // Funnel exit: the owner is moving from browsing memory into acting on it.
    window.mobius.signal('memory_discuss_started', { node_type: node.type === 'moc' ? 'moc' : 'note' });
  }, []);

  const authHeaders = useMemo(() => (
    token ? { Authorization: `Bearer ${token}` } : {}
  ), [token]);

  const chooseAgentGroup = useCallback((avoidProvider = '') => {
    const groups = agentGroups || FALLBACK_AGENT_GROUPS;
    const connected = (group) => !connectedProviders || connectedProviders.has(group.key);
    return (
      groups.find((group) => group.key !== avoidProvider && connected(group) && group.models?.length) ||
      groups.find((group) => connected(group) && group.models?.length) ||
      groups.find((group) => group.models?.length) ||
      null
    );
  }, [agentGroups, connectedProviders]);

  const loadAgentSettings = useCallback(async () => {
    setAgentStatus('loading');
    setAgentMessage('');
    try {
      const headers = authHeaders;
      const [settingsRes, statusRes, modelsRes] = await Promise.all([
        fetch(`/api/storage/apps/${encodeURIComponent(appId)}/settings.json`, { headers }),
        fetch('/api/auth/providers/status', { headers }).catch(() => null),
        fetch('/api/auth/providers/models', { headers }).catch(() => null),
      ]);
      if (!settingsRes.ok && settingsRes.status !== 404) {
        throw new Error('Could not load agent settings.');
      }
      const settings = settingsRes.ok ? await settingsRes.json() : {};
      const safeSettings = settings && typeof settings === 'object' && !Array.isArray(settings)
        ? settings
        : {};
      setAgentSettingsExtra(safeSettings);

      let connected = null;
      if (statusRes?.ok) {
        const data = await statusRes.json();
        connected = new Set(
          Object.entries(data || {})
            .filter(([, value]) => value && value.authenticated)
            .map(([key]) => key),
        );
        setConnectedProviders(connected);
      }

      const groups = modelsRes?.ok
        ? buildAgentGroups(await modelsRes.json())
        : FALLBACK_AGENT_GROUPS;
      setAgentGroups(groups);

      const providerValue = typeof safeSettings.provider === 'string'
        ? safeSettings.provider.trim()
        : '';
      const modelValue = typeof safeSettings.model === 'string'
        ? safeSettings.model.trim()
        : '';
      const effortValue = typeof safeSettings.effort === 'string'
        ? safeSettings.effort.trim()
        : '';
      const fallbackProviderValue = typeof safeSettings.fallback_provider === 'string'
        ? safeSettings.fallback_provider.trim()
        : '';
      const fallbackModelValue = typeof safeSettings.fallback_model === 'string'
        ? safeSettings.fallback_model.trim()
        : '';
      const fallbackEffortValue = typeof safeSettings.fallback_effort === 'string'
        ? safeSettings.fallback_effort.trim()
        : '';

      const primaryMode = safeSettings.primary_agent_mode === 'custom'
        || safeSettings.primary_agent_mode === 'app'
        || (safeSettings.primary_agent_mode !== 'system' && Boolean(providerValue || modelValue || effortValue))
        ? 'app'
        : 'system';
      const secondaryMode = safeSettings.secondary_agent_mode === 'custom'
        || safeSettings.secondary_agent_mode === 'app'
        || (safeSettings.secondary_agent_mode !== 'system' && Boolean(fallbackProviderValue || fallbackModelValue || fallbackEffortValue))
        ? 'app'
        : 'system';
      setPrimaryAgentMode(primaryMode);
      setSecondaryAgentMode(secondaryMode);

      if (isKnownAgentProvider(providerValue)) {
        setAgentProvider(providerValue);
        setAgentModel(modelValue);
        setAgentEffort(effortForProvider(providerValue, effortValue));
      } else {
        const chosen = groups.find((group) => (!connected || connected.has(group.key)) && group.models?.length)
          || groups.find((group) => group.models?.length)
          || null;
        if (chosen) {
          setAgentProvider(chosen.key);
          setAgentModel(chosen.models?.[0]?.id || '');
          setAgentEffort(defaultEffort(chosen.key));
        }
      }
      if (isKnownAgentProvider(fallbackProviderValue)) {
        setSecondaryAgentProvider(fallbackProviderValue);
        setSecondaryAgentModel(fallbackModelValue);
        setSecondaryAgentEffort(effortForProvider(fallbackProviderValue, fallbackEffortValue));
      }
      setAgentStatus('ready');
    } catch (err) {
      setAgentStatus('error');
      setAgentMessage(err.message || 'Could not load agent settings.');
    }
  }, [appId, authHeaders]);

  const loadSchedule = useCallback(async () => {
    setScheduleStatus('loading');
    setScheduleMessage('');
    try {
      const res = await fetch('/api/apps/schedules', {
        headers: authHeaders,
      });
      if (!res.ok) throw new Error('Could not load schedule.');
      const rows = await res.json();
      const row = Array.isArray(rows)
        ? rows.find((item) => Number(item.id) === Number(appId))
        : null;
      const cron = typeof row?.cron === 'string' && row.cron.trim()
        ? row.cron.trim()
        : '30 5 * * *';
      const parsed = parseDailyCronTime(cron);
      setScheduleCron(cron);
      setScheduleCustom(!parsed);
      if (parsed) setScheduleTime(parsed);
      setScheduleStatus('ready');
    } catch (err) {
      setScheduleStatus('error');
      setScheduleMessage(err.message || 'Could not load schedule.');
    }
  }, [appId, authHeaders]);

  useEffect(() => {
    if (!settingsOpen || scheduleStatus !== 'idle') return;
    loadSchedule();
  }, [settingsOpen, scheduleStatus, loadSchedule]);

  useEffect(() => {
    if (!settingsOpen || agentStatus !== 'idle') return;
    loadAgentSettings();
  }, [settingsOpen, agentStatus, loadAgentSettings]);

  const saveSchedule = useCallback(async () => {
    if (scheduleSaving) return;
    const cron = timeToDailyCron(scheduleTime);
    if (!cron) {
      setScheduleMessage('Pick a valid time.');
      return;
    }
    setScheduleSaving(true);
    setScheduleMessage('');
    try {
      const res = await fetch(`/api/apps/${encodeURIComponent(appId)}/schedule`, {
        method: 'POST',
        headers: {
          ...authHeaders,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ cron, job: 'fetch.sh' }),
      });
      if (!res.ok) {
        let detail = '';
        try { detail = (await res.json())?.detail || ''; } catch {}
        throw new Error(detail || 'Could not save schedule.');
      }
      setScheduleCron(cron);
      setScheduleCustom(false);
      setScheduleStatus('ready');
      setScheduleMessage('Saved');
      setTimeout(() => setScheduleMessage(''), 2200);
    } catch (err) {
      setScheduleMessage(err.message || 'Could not save schedule.');
    } finally {
      setScheduleSaving(false);
    }
  }, [appId, authHeaders, scheduleSaving, scheduleTime]);

  const setPrimaryAgentModeChoice = useCallback((mode) => {
    setPrimaryAgentMode(mode);
    setAgentMessage('');
    if (mode !== 'app') return;
    const currentGroup = (agentGroups || FALLBACK_AGENT_GROUPS)
      .find((group) => group.key === agentProvider);
    if (currentGroup?.models?.some((item) => item.id === agentModel)) return;
    const chosen = chooseAgentGroup();
    if (chosen) {
      setAgentProvider(chosen.key);
      setAgentModel(chosen.models?.[0]?.id || '');
      setAgentEffort(defaultEffort(chosen.key));
    }
  }, [agentGroups, agentProvider, agentModel, chooseAgentGroup]);

  const setSecondaryAgentModeChoice = useCallback((mode) => {
    setSecondaryAgentMode(mode);
    setAgentMessage('');
    if (mode !== 'app') return;
    const currentGroup = (agentGroups || FALLBACK_AGENT_GROUPS)
      .find((group) => group.key === secondaryAgentProvider);
    if (currentGroup?.models?.some((item) => item.id === secondaryAgentModel)) return;
    const chosen = chooseAgentGroup(agentProvider);
    if (chosen) {
      setSecondaryAgentProvider(chosen.key);
      setSecondaryAgentModel(chosen.models?.[0]?.id || '');
      setSecondaryAgentEffort(defaultEffort(chosen.key));
    }
  }, [agentGroups, agentProvider, secondaryAgentProvider, secondaryAgentModel, chooseAgentGroup]);

  const reorderAgents = useCallback((fromIndex, toIndex) => {
    if (fromIndex === toIndex) return;
    const primary = {
      mode: primaryAgentMode,
      provider: agentProvider,
      model: agentModel,
      effort: agentEffort,
    };
    const secondary = {
      mode: secondaryAgentMode,
      provider: secondaryAgentProvider,
      model: secondaryAgentModel,
      effort: secondaryAgentEffort,
    };
    setPrimaryAgentMode(secondary.mode);
    setAgentProvider(secondary.provider);
    setAgentModel(secondary.model);
    setAgentEffort(secondary.effort);
    setSecondaryAgentMode(primary.mode);
    setSecondaryAgentProvider(primary.provider);
    setSecondaryAgentModel(primary.model);
    setSecondaryAgentEffort(primary.effort);
    setAgentMessage('');
  }, [
    primaryAgentMode,
    agentProvider,
    agentModel,
    agentEffort,
    secondaryAgentMode,
    secondaryAgentProvider,
    secondaryAgentModel,
    secondaryAgentEffort,
  ]);

  const saveAgentSettings = useCallback(async () => {
    if (agentSaving) return;
    setAgentSaving(true);
    setAgentMessage('');
    const payload = {
      ...agentSettingsExtra,
      primary_agent_mode: primaryAgentMode === 'app' ? 'app' : 'system',
      provider: primaryAgentMode === 'app' ? (agentProvider || 'claude') : null,
      model: primaryAgentMode === 'app' ? (agentModel || null) : null,
      effort: primaryAgentMode === 'app' ? effortForProvider(agentProvider, agentEffort) : null,
      secondary_agent_mode: secondaryAgentMode === 'app' ? 'app' : 'system',
      fallback_provider: secondaryAgentMode === 'app' ? (secondaryAgentProvider || null) : null,
      fallback_model: secondaryAgentMode === 'app' && secondaryAgentProvider
        ? (secondaryAgentModel || null)
        : null,
      fallback_effort: secondaryAgentMode === 'app' && secondaryAgentProvider
        ? effortForProvider(secondaryAgentProvider, secondaryAgentEffort)
        : null,
    };
    try {
      const res = await fetch(`/api/storage/apps/${encodeURIComponent(appId)}/settings.json`, {
        method: 'PUT',
        headers: {
          ...authHeaders,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        let detail = '';
        try { detail = (await res.json())?.detail || ''; } catch {}
        throw new Error(detail || 'Could not save agent settings.');
      }
      setAgentSettingsExtra(payload);
      setAgentMessage('Agents saved');
      setTimeout(() => setAgentMessage(''), 2200);
    } catch (err) {
      setAgentMessage(err.message || 'Could not save agent settings.');
    } finally {
      setAgentSaving(false);
    }
  }, [
    appId,
    authHeaders,
    agentSaving,
    agentSettingsExtra,
    primaryAgentMode,
    agentProvider,
    agentModel,
    agentEffort,
    secondaryAgentMode,
    secondaryAgentProvider,
    secondaryAgentModel,
    secondaryAgentEffort,
  ]);

  // The detail drawer is modal on phone and desktop (it owns a scrim), so it
  // must also own focus: enter on Close, trap Tab, close on Escape, then return
  // to the row/control that opened it. Depend on the open BOOLEAN so switching
  // between nodes inside the drawer does not tear down and recapture focus.
  useEffect(() => {
    if (!selected) return;
    panelCloseRef.current?.focus();
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        closePanel();
        return;
      }
      if (e.key !== 'Tab') return;
      const focusable = panelRef.current?.querySelectorAll(
        'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable?.length) {
        e.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      const opener = panelOpenerRef.current;
      if (opener && typeof opener.focus === 'function' && document.contains(opener)) opener.focus();
      panelOpenerRef.current = null;
    };
  }, [Boolean(selected), closePanel]);

  useEffect(() => {
    if (selected) return;
    try { panelNavRef.current?.close?.(); } catch {}
    panelNavRef.current = null;
  }, [!!selected]);

  useEffect(() => () => {
    try { panelNavRef.current?.close?.(); } catch {}
  }, []);

  // --- List view: sorted rows with plain usage/size metadata. ---
  const sortedNodes = useMemo(() => {
    if (!graph) return [];
    const rows = [...graph.nodes];
    rows.sort((a, b) => {
      let av, bv;
      if (sortKey === 'title') { av = (a.title || a.id).toLowerCase(); bv = (b.title || b.id).toLowerCase(); }
      else { av = a[sortKey] || 0; bv = b[sortKey] || 0; }
      if (av < bv) return sortDir === 'asc' ? -1 : 1;
      if (av > bv) return sortDir === 'asc' ? 1 : -1;
      return 0;
    });
    return rows;
  }, [graph, sortKey, sortDir]);

  const toggleSort = (key) => {
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortKey(key); setSortDir(key === 'title' ? 'asc' : 'desc'); }
  };

  // Switch the Graph/List segmented control, signalling only a real change so
  // Emit only real view changes, not idempotent re-taps.
  const selectView = useCallback((next) => {
    const changed = next !== view;
    setView(next);
    if (changed) window.mobius.signal('memory_view_toggled', { view: next });
  }, [view]);

  const legendItems = useMemo(() => {
    if (!graph) return [];
    const items = [];
    const byId = {};
    for (const n of graph.nodes) byId[n.id] = n;
    for (const slug of Object.keys(mocColors).sort()) {
      const node = byId[slug];
      items.push({ slug, label: node ? (node.title || slug) : slug, color: mocColors[slug] });
    }
    return items;
  }, [graph, mocColors]);

  const problems = graph?.problems || [];
  const errCount = problems.filter((p) => p.severity === 'error').length;
  const counts = useMemo(() => {
    const c = { note: 0, moc: 0 };
    if (graph) for (const n of graph.nodes) c[n.type === 'moc' ? 'moc' : 'note']++;
    return c;
  }, [graph]);
  const selectedUpdated = relDate(noteState.fm.updated);
  const visibleAgentGroups = agentGroups || FALLBACK_AGENT_GROUPS;

  // ---------------------------------------------------------------- render ---
  return (
    <div className="mg-root" style={S.root}>
      <style>{CSS}</style>
      <h1 className="mg-sr-only">Memory</h1>

      <header style={S.header}>
        <div style={S.brand}>
          {/* The app's own glossy icon as the brand mark; falls back to the
              accent dot if this install has no custom icon (the route 404s). */}
          <img
            src={`/api/apps/${appId}/icon?size=64`}
            alt=""
            width={34}
            height={34}
            style={S.brandIcon}
            onError={(e) => {
              e.currentTarget.style.display = 'none'
              const dot = e.currentTarget.nextElementSibling
              if (dot) dot.style.display = 'flex'
            }}
          />
          <span style={{ ...S.brandDot, display: 'none' }}><span style={S.brandDotCore} /></span>
          <div style={{ minWidth: 0 }}>
            <div style={S.subtitle}>
              {status === 'ready'
                ? `${counts.note + counts.moc} notes · ${graph.edges.length} links`
                : 'What the agent knows'}
            </div>
          </div>
        </div>

        <div style={S.headerRight}>
          <button
            style={{
              ...S.settingsBtn,
              ...(settingsOpen ? S.settingsBtnActive : {}),
            }}
            className="mg-settings-btn"
            type="button"
            onClick={() => setSettingsOpen((v) => !v)}
            aria-expanded={settingsOpen}
          >
            Settings
          </button>
          {problems.length > 0 && (
            <button
              style={{ ...S.healthBadge, ...(errCount ? S.healthErr : S.healthWarn) }}
              onClick={() => setShowHealth((v) => !v)}
              title="Graph health"
            >
              <span style={{ ...S.healthDot, background: errCount ? 'var(--danger)' : 'var(--accent-hover, #f0c674)' }} />
              {problems.length}
            </button>
          )}
          <div style={S.toggle}>
            <button
              className="mg-tgl"
              style={{ ...S.toggleBtn, ...(view === 'graph' ? S.toggleActive : {}) }}
              onClick={() => selectView('graph')}
            >
              <GraphGlyph /> Graph
            </button>
            <button
              className="mg-tgl"
              style={{ ...S.toggleBtn, ...(view === 'list' ? S.toggleActive : {}) }}
              onClick={() => selectView('list')}
            >
              <ListGlyph /> List
            </button>
          </div>
        </div>
      </header>

      {settingsOpen && (
        <section style={S.settingsPanel}>
          <div style={S.settingsCopy}>
            <div style={S.settingsTitle}>Maintenance</div>
            <div style={S.settingsSub}>Schedule and Background agents</div>
          </div>

          {scheduleStatus === 'idle' || scheduleStatus === 'loading' ? (
            <div style={S.settingsMeta}>Loading schedule...</div>
          ) : scheduleStatus === 'error' ? (
            <div style={S.settingsActions}>
              <span style={S.settingsError}>{scheduleMessage}</span>
              <button
                style={S.settingsGhostBtn}
                type="button"
                onClick={loadSchedule}
              >
                Retry
              </button>
            </div>
          ) : (
            <>
              <label style={S.settingsField}>
                <span style={S.settingsLabel}>Run time</span>
                <input
                  style={S.timeInput}
                  type="time"
                  step="60"
                  value={scheduleTime}
                  onChange={(e) => {
                    setScheduleTime(e.target.value);
                    setScheduleCustom(false);
                    setScheduleMessage('');
                  }}
                />
              </label>
              {scheduleCustom && (
                <div style={S.settingsMeta}>Current cron: {scheduleCron}</div>
              )}
              {scheduleMessage && (
                <div
                  style={
                    scheduleMessage === 'Saved'
                      ? S.settingsOk
                      : S.settingsError
                  }
                >
                  {scheduleMessage}
                </div>
              )}
              <div style={S.settingsActions}>
                <button
                  style={S.settingsGhostBtn}
                  type="button"
                  onClick={() => setSettingsOpen(false)}
                >
                  Close
                </button>
                <button
                  style={{
                    ...S.settingsSaveBtn,
                    ...(scheduleSaving ? S.settingsSaveBtnDisabled : {}),
                  }}
                  type="button"
                  onClick={saveSchedule}
                  disabled={scheduleSaving}
                >
                  {scheduleSaving ? 'Saving...' : 'Save'}
                </button>
              </div>
            </>
          )}
          <div className="mg-agent-settings">
            <div className="mg-agent-settings-head">
              <div>
                <div className="mg-agent-settings-title">Background agents</div>
                <div className="mg-agent-settings-sub">
                  Tried in order. Drag to change priority. Each row follows Möbius Settings by default, or can use its own model for Memory.
                </div>
              </div>
              {agentStatus === 'error' && (
                <button
                  style={S.settingsGhostBtn}
                  type="button"
                  onClick={loadAgentSettings}
                >
                  Retry
                </button>
              )}
            </div>
            {agentStatus === 'idle' || agentStatus === 'loading' ? (
              <div style={S.settingsMeta}>Loading agent settings...</div>
            ) : agentStatus === 'error' ? (
              <div style={S.settingsError}>{agentMessage}</div>
            ) : (
              <>
                <BackgroundAgentList onMove={reorderAgents}>
                  <div key="primary">
                    <ModelPicker
                      provider={primaryAgentMode === 'system' ? '' : agentProvider}
                      model={primaryAgentMode === 'system' ? '' : agentModel}
                      groups={visibleAgentGroups}
                      connectedProviders={connectedProviders}
                      title="Memory primary model"
                      navKey="memory-primary-model"
                      useSettingsDefault={primaryAgentMode === 'system'}
                      onSettingsDefault={() => setPrimaryAgentModeChoice('system')}
                      effortLabel={primaryAgentMode === 'system' ? '' : effortLabel(agentProvider, agentEffort)}
                      efforts={EFFORT_LEVELS[agentProvider] || []}
                      effort={agentEffort}
                      effortControl={primaryAgentMode === 'system' ? null : (
                        <EffortStepper provider={agentProvider} value={agentEffort} onChange={setAgentEffort} />
                      )}
                      onChange={(nextProvider, nextModel) => {
                        setPrimaryAgentModeChoice('app');
                        setAgentProvider(nextProvider);
                        setAgentModel(nextModel);
                        setAgentEffort(effortForProvider(nextProvider, agentEffort));
                        setAgentMessage('');
                      }}
                    />
                  </div>
                  <div key="secondary">
                    <ModelPicker
                      provider={secondaryAgentMode === 'system' ? '' : secondaryAgentProvider}
                      model={secondaryAgentMode === 'system' ? '' : secondaryAgentModel}
                      groups={visibleAgentGroups}
                      connectedProviders={connectedProviders}
                      title="Memory secondary model"
                      navKey="memory-secondary-model"
                      useSettingsDefault={secondaryAgentMode === 'system'}
                      onSettingsDefault={() => setSecondaryAgentModeChoice('system')}
                      effortLabel={secondaryAgentMode === 'system' ? '' : effortLabel(secondaryAgentProvider, secondaryAgentEffort)}
                      efforts={EFFORT_LEVELS[secondaryAgentProvider] || []}
                      effort={secondaryAgentEffort}
                      effortControl={secondaryAgentMode === 'system' ? null : (
                        <EffortStepper
                          provider={secondaryAgentProvider}
                          value={secondaryAgentEffort}
                          onChange={setSecondaryAgentEffort}
                        />
                      )}
                      onChange={(nextProvider, nextModel) => {
                        setSecondaryAgentModeChoice('app');
                        setSecondaryAgentProvider(nextProvider);
                        setSecondaryAgentModel(nextModel);
                        setSecondaryAgentEffort(effortForProvider(nextProvider, secondaryAgentEffort));
                        setAgentMessage('');
                      }}
                    />
                  </div>
                </BackgroundAgentList>
                <div style={S.settingsActions}>
                  {agentMessage && (
                    <span style={agentMessage === 'Agents saved' ? S.settingsOk : S.settingsError}>
                      {agentMessage}
                    </span>
                  )}
                  <button
                    style={{
                      ...S.settingsSaveBtn,
                      ...(agentSaving ? S.settingsSaveBtnDisabled : {}),
                    }}
                    type="button"
                    onClick={saveAgentSettings}
                    disabled={agentSaving}
                  >
                    {agentSaving ? 'Saving...' : 'Save agents'}
                  </button>
                </div>
              </>
            )}
          </div>
        </section>
      )}

      {showHealth && problems.length > 0 && (
        <div style={S.healthPanel} className="mg-scroll">
          <div style={S.healthHead}>
            {errCount > 0
              ? `${errCount} error${errCount === 1 ? '' : 's'} block the graph from rebuilding`
              : 'A few loose threads — nothing broken'}
          </div>
          {problems.map((p, i) => (
            <div key={i} style={S.healthRow}>
              <span style={{ ...S.sevTag, ...(p.severity === 'error' ? S.sevErr : S.sevWarn) }}>
                {p.severity}
              </span>
              <span style={S.healthKind}>{String(p.kind || '').replace(/_/g, ' ')}</span>
              <span style={S.healthDetail}>{p.detail}</span>
            </div>
          ))}
        </div>
      )}

      <main style={S.main}>
        {status === 'loading' && (
          <div style={S.center}>
            <div className="mg-orbit"><span /><span /><span /></div>
            <div style={S.centerText}>Opening Memory…</div>
          </div>
        )}

        {status === 'initializing' && (
          <div style={S.center}>
            <div className="mg-orbit"><span /><span /><span /></div>
            <div style={S.centerTitle}>Preparing your first memory graph</div>
            <div style={S.centerText}>
              Memory is reviewing the available chat summaries. This view will
              appear when the first complete graph commit is published.
            </div>
          </div>
        )}

        {status === 'error' && (
          <div style={S.center}>
            <div style={S.errIcon}>!</div>
            <div style={S.centerTitle}>Couldn't load the graph</div>
            <div style={S.centerText}>{errMsg}</div>
          </div>
        )}

        {status === 'empty' && (
          <div style={S.center}>
            <EmptyConstellation />
            <div style={S.centerTitle}>Memory is just getting to know you</div>
            <div style={S.centerText}>
              Its scheduled review promotes durable facts from your chats only
              when they are useful enough to keep. Come back after more conversation.
            </div>
          </div>
        )}

        {status === 'ready' && view === 'graph' && (
          <div ref={wrapRef} style={S.graphWrap} className="mg-graph">
            {graphRuntime && dims.w > 0 && dims.h > 0 ? (
              <MemoryGraphRenderer
                runtime={graphRuntime}
                graphData={fgData}
                width={dims.w}
                height={dims.h}
                mode="global"
                selectedId={selected?.id}
                hoverId={hoverId}
                colorForNode={colorForNode}
                radiusForNode={radiusForNode}
                onNodeClick={(n) => openPanel(n, { resetLocalDepth: true })}
                onNodeHover={(n) => setHoverId(n ? n.id : null)}
                onBackgroundClick={closePanel}
              />
            ) : (
              <div style={S.center}>
                <div className="mg-orbit"><span /><span /><span /></div>
                <div style={S.centerText}>
                  {graphRuntime === null ? 'Graph view is offline — try List.' : 'Laying out the graph…'}
                </div>
              </div>
            )}

            <div style={S.graphHint}>Drag to pan · scroll to zoom · tap a node to read it</div>

            {legendItems.length > 0 && (
              <div style={S.legend} className="mg-scroll">
                <div style={S.legendTitle}>Maps of Content</div>
                <div style={S.legendRow}>
                  <span style={{ ...S.legendSwatch, background: cssVar('--accent', '#a78bfa') }} />
                  <span style={S.legendLabel}>Hub (MOC)</span>
                </div>
                {legendItems.slice(0, 12).map((it) => (
                  <button
                    key={it.slug}
                    style={S.legendRow}
                    className="mg-legend-row"
                    onMouseEnter={() => setHoverId(it.slug)}
                    onMouseLeave={() => setHoverId(null)}
                    onClick={() => {
                      const n = graph.nodes.find((x) => x.id === it.slug);
                      if (n) openPanel(n);
                    }}
                  >
                    <span style={{ ...S.legendSwatch, background: it.color }} />
                    <span style={S.legendLabel}>{it.label}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {status === 'ready' && view === 'list' && (
          <div style={S.listWrap} className="mg-scroll">
            <table style={S.table}>
              <thead>
                <tr>
                  <Th label="Note" active={sortKey === 'title'} dir={sortDir} onSort={() => toggleSort('title')} align="left" />
                  <Th label="Type" />
                  <Th label="Weight" active={sortKey === 'importance'} dir={sortDir} onSort={() => toggleSort('importance')} />
                  <Th label="Reads" active={sortKey === 'access_count'} dir={sortDir} onSort={() => toggleSort('access_count')} />
                  <Th label="Size" active={sortKey === 'size_bytes'} dir={sortDir} onSort={() => toggleSort('size_bytes')} />
                </tr>
              </thead>
              <tbody>
                {sortedNodes.map((n) => (
                  <tr
                    key={n.id}
                    style={S.tr}
                    onClick={() => openPanel(n)}
                    className="mg-row"
                    role="button"
                    tabIndex={0}
                    aria-label={`Open ${n.title || n.id}`}
                    onKeyDown={(e) => {
                      // Enter/Space activate the row like a button; preventDefault
                      // on Space stops the list pane scrolling instead of opening.
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        openPanel(n);
                      }
                    }}
                  >
                    <td style={S.tdTitle}>
                      <span style={{ ...S.rowDot, background: colorForNode(n) }} />
                      <span style={S.rowTitleText}>{n.title || n.id}</span>
                    </td>
                    <td style={S.td}>
                      <span style={{ ...S.typeTag, ...(n.type === 'moc' ? S.typeMoc : {}) }}>
                        {n.type === 'moc' ? 'hub' : 'note'}
                      </span>
                    </td>
                    <td style={{ ...S.td, ...S.tdNum }}>
                      <ImportanceDots value={n.importance || 1} />
                    </td>
                    <td style={{ ...S.td, ...S.tdMeta }}>{n.access_count || 0}</td>
                    <td style={{ ...S.td, ...S.tdMeta }}>{fmtBytes(n.size_bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </main>

      {/* ----------------------------------------------------- note panel --- */}
      {selected && (
        <>
          <div style={S.scrim} className="mg-scrim" onClick={closePanel} role="presentation" aria-hidden="true" />
          <aside
            ref={panelRef}
            style={S.panel}
            className="mg-panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby="mg-panel-title"
          >
            <div style={{ ...S.panelAccent, background: colorForNode(selected) }} />
            <div style={S.panelHead} className="mg-panel-head">
              <div style={S.panelHeadMain}>
                <span style={{ ...S.rowDot, background: colorForNode(selected), width: 11, height: 11, marginTop: 5 }} />
                <div style={{ minWidth: 0 }}>
                  <div style={S.panelTitle} id="mg-panel-title">{selected.title || selected.id}</div>
                  <div style={S.panelMetaLine}>
                    <span>{selected.type === 'moc' ? 'Hub' : 'Note'}</span>
                    <span>Weight <ImportanceDots value={selected.importance || 1} /></span>
                    <span>{selected.access_count || 0} reads</span>
                    <span>{fmtBytes(selected.size_bytes)}</span>
                    {selectedUpdated && <span>{selectedUpdated}</span>}
                  </div>
                </div>
              </div>
              <button ref={panelCloseRef} type="button" style={S.closeBtn} className="mg-close" onClick={closePanel} aria-label="Close">×</button>
            </div>

            {Array.isArray(selected.tags) && selected.tags.length > 0 && (
              <div style={S.tagRow} className="mg-tag-row">
                {selected.tags.map((t) => <span key={t} style={S.tag}>#{t}</span>)}
              </div>
            )}

            {/* Tab toggle replaces the old resizable note/graph split. Only the
                active tab's pane mounts: keeping the local graph unmounted while
                reading text means the Pixi renderer is never resized, which was
                the entire crash class the draggable divider produced. */}
            <div style={S.detailBar}>
              <div style={S.detailContext}>
                {detailTab === 'graph' ? (
                  <>
                    <span style={S.paneHead}>Local graph</span>
                    <span style={S.localCount}>
                      {localGraphData.nodes.length} nodes · {localGraphData.links.length} links
                    </span>
                  </>
                ) : (
                  <span style={S.paneHead}>Note</span>
                )}
              </div>

              {detailTab === 'graph' && (
                <div style={S.depthToggle} aria-label="Local graph depth">
                  {[1, 2, 3, 4].map((d) => (
                    <button
                      key={d}
                      type="button"
                      style={{ ...S.depthBtn, ...(localDepth === d ? S.depthBtnActive : {}) }}
                      onClick={() => setLocalDepth(d)}
                      title={`${d} hop${d === 1 ? '' : 's'}`}
                    >
                      {d}
                    </button>
                  ))}
                </div>
              )}

              <div style={S.tabToggle} role="tablist" aria-label="Note or local graph">
                <button
                  id="mg-tab-text"
                  type="button"
                  ref={(node) => { detailTabRefs.current[0] = node; }}
                  className="mg-tab"
                  style={{ ...S.tabBtn, ...(detailTab === 'text' ? S.tabBtnActive : {}) }}
                  onClick={() => selectDetailTab('text')}
                  onKeyDown={(event) => onDetailTabKeyDown(event, 0)}
                  role="tab"
                  aria-selected={detailTab === 'text'}
                  aria-controls="mg-detail-panel"
                  tabIndex={detailTab === 'text' ? 0 : -1}
                  aria-label="Show note text"
                  title="Note text"
                >
                  <TextGlyph />
                </button>
                <button
                  id="mg-tab-graph"
                  type="button"
                  ref={(node) => { detailTabRefs.current[1] = node; }}
                  className="mg-tab"
                  style={{ ...S.tabBtn, ...(detailTab === 'graph' ? S.tabBtnActive : {}) }}
                  onClick={() => selectDetailTab('graph')}
                  onKeyDown={(event) => onDetailTabKeyDown(event, 1)}
                  role="tab"
                  aria-selected={detailTab === 'graph'}
                  aria-controls="mg-detail-panel"
                  tabIndex={detailTab === 'graph' ? 0 : -1}
                  aria-label="Show local graph"
                  title="Local graph"
                >
                  <NetworkGlyph />
                </button>
              </div>
            </div>

            <div id="mg-detail-panel" role="tabpanel" aria-labelledby={detailTab === 'text' ? 'mg-tab-text' : 'mg-tab-graph'} style={S.detailBody}>
              {detailTab === 'text' ? (
                <div style={S.panelBody} className="mg-md mg-scroll" onClick={onNoteClick}>
                  {noteState.status === 'loading' && (
                    <div style={S.notePlaceholder}>
                      <div className="mg-skel" style={{ width: '70%' }} />
                      <div className="mg-skel" style={{ width: '95%' }} />
                      <div className="mg-skel" style={{ width: '88%' }} />
                      <div className="mg-skel" style={{ width: '60%' }} />
                    </div>
                  )}
                  {noteState.status === 'missing' && <div style={S.centerText}>No note body on disk for this entry.</div>}
                  {noteState.status === 'error' && <div style={S.centerText}>Couldn't load: {noteState.md}</div>}
                  {noteState.status === 'ready' && noteState.revalidating && (
                    <div style={S.mergePill} role="status" aria-live="polite">
                      <span style={S.mergeDot} aria-hidden="true" />
                      Merging latest…
                    </div>
                  )}
                  {noteState.status === 'ready' && (
                    noteHtml != null
                      ? <div dangerouslySetInnerHTML={{ __html: noteHtml }} />
                      : <pre style={S.pre}>{noteState.md}</pre>
                  )}
                </div>
              ) : (
                <div ref={localWrapRef} style={S.localGraphWrap} className="mg-local-graph">
                  {graphRuntime && localDims.w > 0 && localDims.h > 0 && localGraphData.nodes.length > 0 ? (
                    <MemoryGraphRenderer
                      runtime={graphRuntime}
                      graphData={localGraphData}
                      width={localDims.w}
                      height={localDims.h}
                      mode="local"
                      selectedId={selected?.id}
                      hoverId={hoverId}
                      colorForNode={colorForNode}
                      radiusForNode={radiusForNode}
                      onNodeClick={(n) => openPanel(nodesById.get(n.id) || n)}
                      onNodeHover={(n) => setHoverId(n ? n.id : null)}
                    />
                  ) : (
                    <div style={S.localEmpty}>
                      {graphRuntime === null ? 'Graph view is offline.' : 'Laying out local graph…'}
                    </div>
                  )}
                </div>
              )}
            </div>

            <div style={S.panelFoot}>
              <button type="button" style={S.discussBtn} className="mg-discuss" onClick={() => discuss(selected)}>
                <ChatGlyph />
                Discuss in a new chat
              </button>
            </div>
          </aside>
        </>
      )}
    </div>
  );
}
