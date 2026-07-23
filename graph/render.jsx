import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { S } from '../constants.js'
import {
  clamp,
  cssVar,
  hashStr,
  labelScore,
  nodeRadius,
  shouldShowScreenLabel,
} from '../domain.js'

const MIN_ZOOM = 0.22;
const MAX_ZOOM = 4.6;
const EXACT_REPULSION_NODE_LIMIT = 220;
const SAMPLED_REPULSION_SPAN = 48;

export function MemoryGraphRenderer({
  graphData,
  width,
  height,
  mode,
  selectedId,
  hoverId,
  colorForNode,
  radiusForNode,
  onNodeClick,
  onNodeHover,
  onBackgroundClick,
}) {
  const svgRef = useRef(null);
  const nodeRefs = useRef(new Map());
  const interactionRef = useRef(null);
  const transformRef = useRef({ x: width / 2, y: height / 2, k: 1 });
  const pendingFrameRef = useRef({ transform: null, position: null });
  const renderFrameRef = useRef(0);
  const latestRef = useRef({});
  const [retryKey, setRetryKey] = useState(0);
  const [positionOverrides, setPositionOverrides] = useState(() => new Map());
  const [keyboardFocusId, setKeyboardFocusId] = useState(null);

  latestRef.current = {
    selectedId,
    hoverId,
    onNodeClick,
    onNodeHover,
    onBackgroundClick,
  };

  const scheduleRenderFrame = useCallback(() => {
    if (renderFrameRef.current) return;
    renderFrameRef.current = requestAnimationFrame(() => {
      renderFrameRef.current = 0;
      const pending = pendingFrameRef.current;
      pendingFrameRef.current = { transform: null, position: null };
      if (pending.transform) setTransform(pending.transform);
      if (pending.position) {
        setPositionOverrides((current) => {
          const next = new Map(current);
          next.set(pending.position.id, pending.position.point);
          return next;
        });
      }
    });
  }, []);

  useEffect(() => () => {
    if (renderFrameRef.current) cancelAnimationFrame(renderFrameRef.current);
    renderFrameRef.current = 0;
  }, []);

  const sceneResult = useMemo(() => {
    try {
      return {
        graph: layoutRendererGraphData(graphData, width, height, {
          mode,
          radiusForNode,
        }),
        error: null,
      };
    } catch (error) {
      return { graph: { nodes: [], links: [] }, error };
    }
  }, [graphData, width, height, mode, radiusForNode, retryKey]);

  const initialTransform = useMemo(() => computeRendererFitTransform(
    sceneResult.graph.nodes,
    width,
    height,
    {
      padding: mode === 'local' ? 34 : 72,
      minScale: mode === 'local' ? 0.72 : 0.42,
      maxScale: mode === 'local' ? 1.45 : 1.08,
    },
  ), [sceneResult.graph, width, height, mode]);
  const [transform, setTransform] = useState(initialTransform);
  transformRef.current = transform;

  useEffect(() => {
    if (renderFrameRef.current) cancelAnimationFrame(renderFrameRef.current);
    renderFrameRef.current = 0;
    pendingFrameRef.current = { transform: null, position: null };
    setTransform(initialTransform);
    setPositionOverrides(new Map());
    interactionRef.current = null;
  }, [initialTransform]);

  useEffect(() => {
    const nodes = sceneResult.graph.nodes;
    const preferred = nodes.find((node) => node.id === selectedId)?.id
      || nodes[0]?.id
      || null;
    setKeyboardFocusId((current) => (
      nodes.some((node) => node.id === current) ? current : preferred
    ));
  }, [sceneResult.graph, selectedId]);

  useEffect(() => {
    if (!sceneResult.error) return;
    console.error('[Memory] SVG graph layout failed', sceneResult.error);
    try {
      window.mobius?.signal?.('error', {
        phase: 'graph_layout',
        renderer: 'svg',
        mode,
        node_count: Array.isArray(graphData?.nodes) ? graphData.nodes.length : 0,
        message: String(sceneResult.error?.message || sceneResult.error).slice(0, 300),
      });
    } catch {}
  }, [sceneResult.error, graphData, mode]);

  // React delegates wheel handlers as passive in some browser/runtime
  // combinations. Register this one directly so custom graph zoom can prevent
  // the page from scrolling at the same time.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || sceneResult.error) return undefined;
    const handleWheel = (event) => {
      event.preventDefault();
      const rect = svg.getBoundingClientRect();
      const point = {
        x: (event.clientX - rect.left) * (width / Math.max(1, rect.width)),
        y: (event.clientY - rect.top) * (height / Math.max(1, rect.height)),
      };
      const current = transformRef.current;
      const nextScale = clamp(
        current.k * Math.exp(-event.deltaY * 0.0015),
        MIN_ZOOM,
        MAX_ZOOM,
      );
      if (nextScale === current.k) return;
      const graphX = (point.x - current.x) / current.k;
      const graphY = (point.y - current.y) / current.k;
      const next = {
        x: point.x - graphX * nextScale,
        y: point.y - graphY * nextScale,
        k: nextScale,
      };
      transformRef.current = next;
      pendingFrameRef.current.transform = next;
      scheduleRenderFrame();
    };
    svg.addEventListener('wheel', handleWheel, { passive: false });
    return () => svg.removeEventListener('wheel', handleWheel);
  }, [width, height, sceneResult.error, scheduleRenderFrame]);

  const positionedGraph = useMemo(() => {
    const nodes = sceneResult.graph.nodes.map((node) => {
      const override = positionOverrides.get(node.id);
      return override ? { ...node, ...override } : node;
    });
    const nodesById = new Map(nodes.map((node) => [node.id, node]));
    const links = sceneResult.graph.links.map((link) => ({
      ...link,
      source: nodesById.get(link.sourceId),
      target: nodesById.get(link.targetId),
    }));
    return { nodes, links, nodesById };
  }, [sceneResult.graph, positionOverrides]);
  const neighbors = useMemo(
    () => buildRendererNeighborMap(sceneResult.graph.links),
    [sceneResult.graph.links],
  );
  const labelRanks = useMemo(
    () => buildLabelRankMap(sceneResult.graph.nodes),
    [sceneResult.graph.nodes],
  );

  if (sceneResult.error) {
    return (
      <div style={S.graphError} role="alert" aria-live="polite">
        <div style={S.graphErrorTitle}>Graph unavailable</div>
        <div style={S.graphErrorText}>
          Switch to {mode === 'local' ? 'Text' : 'List'} to keep browsing, or
          try laying out the graph again.
        </div>
        <button
          type="button"
          style={S.graphRetryBtn}
          onClick={() => setRetryKey((value) => value + 1)}
        >
          Retry graph
        </button>
      </div>
    );
  }

  const positionedNodes = positionedGraph.nodes;
  const positionedLinks = positionedGraph.links;
  const nodesById = positionedGraph.nodesById;
  const scale = transform.k || 1;
  const textColor = cssVar('--text', '#e5e5e5');
  const backgroundColor = cssVar('--bg', '#0d0d0d');
  const borderColor = cssVar('--text', '#e5e5e5');
  const accentColor = cssVar('--accent', '#a78bfa');
  const hovered = hoverId || null;

  const isFocused = (id) => (
    !hovered || id === hovered || neighbors.get(hovered)?.has(id)
  );

  const pointFromEvent = (event) => {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const rect = svg.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) * (width / Math.max(1, rect.width)),
      y: (event.clientY - rect.top) * (height / Math.max(1, rect.height)),
    };
  };

  const releasePointer = (pointerId) => {
    const svg = svgRef.current;
    if (!svg?.hasPointerCapture?.(pointerId)) return;
    try { svg.releasePointerCapture(pointerId); } catch {}
  };

  const beginPan = (event) => {
    if (event.button !== 0 || !svgRef.current) return;
    const point = pointFromEvent(event);
    svgRef.current.setPointerCapture?.(event.pointerId);
    interactionRef.current = {
      kind: 'pan',
      pointerId: event.pointerId,
      startPoint: point,
      startTransform: transformRef.current,
      moved: false,
    };
  };

  const beginNodeInteraction = (event, node) => {
    if (event.button !== 0 || !svgRef.current) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.focus?.();
    setKeyboardFocusId(node.id);
    latestRef.current.onNodeHover?.(node);
    const point = pointFromEvent(event);
    svgRef.current.setPointerCapture?.(event.pointerId);
    interactionRef.current = {
      kind: 'node',
      pointerId: event.pointerId,
      nodeId: node.id,
      startPoint: point,
      startedAt: Date.now(),
      moved: false,
    };
  };

  const handlePointerMove = (event) => {
    const interaction = interactionRef.current;
    if (!interaction || interaction.pointerId !== event.pointerId) return;
    const point = pointFromEvent(event);
    const distance = Math.hypot(
      point.x - interaction.startPoint.x,
      point.y - interaction.startPoint.y,
    );
    if (distance >= 2) interaction.moved = true;

    if (interaction.kind === 'pan') {
      const next = {
        ...interaction.startTransform,
        x: interaction.startTransform.x + point.x - interaction.startPoint.x,
        y: interaction.startTransform.y + point.y - interaction.startPoint.y,
      };
      transformRef.current = next;
      pendingFrameRef.current.transform = next;
      scheduleRenderFrame();
      return;
    }

    const currentTransform = transformRef.current;
    const nextPosition = {
      x: (point.x - currentTransform.x) / currentTransform.k,
      y: (point.y - currentTransform.y) / currentTransform.k,
    };
    pendingFrameRef.current.position = {
      id: interaction.nodeId,
      point: nextPosition,
    };
    scheduleRenderFrame();
  };

  const finishPointerInteraction = (event, cancelled = false) => {
    const interaction = interactionRef.current;
    if (!interaction || interaction.pointerId !== event.pointerId) return;
    interactionRef.current = null;
    releasePointer(event.pointerId);
    if (cancelled) return;

    if (interaction.kind === 'pan') {
      if (!interaction.moved) latestRef.current.onBackgroundClick?.();
      return;
    }

    const node = nodesById.get(interaction.nodeId);
    const isClick = !interaction.moved && Date.now() - interaction.startedAt < 520;
    if (node && isClick) latestRef.current.onNodeClick?.(node);
  };

  const zoomAtPoint = (point, factor) => {
    const current = transformRef.current;
    const nextScale = clamp(current.k * factor, MIN_ZOOM, MAX_ZOOM);
    if (nextScale === current.k) return;
    const graphX = (point.x - current.x) / current.k;
    const graphY = (point.y - current.y) / current.k;
    const next = {
      x: point.x - graphX * nextScale,
      y: point.y - graphY * nextScale,
      k: nextScale,
    };
    transformRef.current = next;
    pendingFrameRef.current.transform = next;
    scheduleRenderFrame();
  };

  const focusNodeAt = (index) => {
    const count = positionedNodes.length;
    if (!count) return;
    const bounded = (index + count) % count;
    const node = positionedNodes[bounded];
    setKeyboardFocusId(node.id);
    requestAnimationFrame(() => nodeRefs.current.get(node.id)?.focus?.());
  };

  const handleNodeKeyDown = (event, node, index) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      latestRef.current.onNodeClick?.(node);
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      latestRef.current.onBackgroundClick?.();
      return;
    }
    let nextIndex = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') nextIndex = index + 1;
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') nextIndex = index - 1;
    else if (event.key === 'Home') nextIndex = 0;
    else if (event.key === 'End') nextIndex = positionedNodes.length - 1;
    if (nextIndex == null) return;
    event.preventDefault();
    focusNodeAt(nextIndex);
  };

  return (
    <svg
      ref={svgRef}
      style={S.svgGraph}
      className="mg-svg-graph"
      viewBox={`0 0 ${Math.max(1, width)} ${Math.max(1, height)}`}
      preserveAspectRatio="none"
      role="group"
      aria-label={mode === 'local' ? 'Local note graph' : 'Memory graph'}
      onPointerDown={beginPan}
      onPointerMove={handlePointerMove}
      onPointerUp={(event) => finishPointerInteraction(event)}
      onPointerCancel={(event) => finishPointerInteraction(event, true)}
      onDoubleClick={(event) => zoomAtPoint(pointFromEvent(event), 1.4)}
    >
      <title>{mode === 'local' ? 'Local note graph' : 'Memory graph'}</title>
      <desc>
        Use arrow keys to move between notes, Enter to open one, and Escape to close the current note.
      </desc>
      <g transform={`translate(${roundSvg(transform.x)} ${roundSvg(transform.y)}) scale(${roundSvg(scale)})`}>
        <g aria-hidden="true" pointerEvents="none">
          {positionedLinks.map((link, index) => {
            const source = link.source;
            const target = link.target;
            if (!source || !target) return null;
            const focused = isFocused(source.id) && isFocused(target.id);
            const isMoc = link.kind === 'moc';
            return (
              <line
                key={`${link.sourceId}:${link.targetId}:${index}`}
                x1={roundSvg(source.x)}
                y1={roundSvg(source.y)}
                x2={roundSvg(target.x)}
                y2={roundSvg(target.y)}
                stroke={isMoc ? accentColor : textColor}
                strokeWidth={isMoc ? 1.55 : 0.8}
                strokeOpacity={(isMoc ? 0.48 : 0.28) * (focused ? 1 : 0.18)}
                vectorEffect="non-scaling-stroke"
              />
            );
          })}
        </g>

        {positionedNodes.map((node, index) => {
          const radius = safeRendererRadius(radiusForNode, node);
          const color = safeRendererColor(colorForNode, node);
          const focused = isFocused(node.id);
          const isHover = hovered === node.id;
          const isSelected = selectedId === node.id;
          const isHub = node.type === 'moc';
          const rank = labelRanks.get(node.id) ?? 9999;
          const showLabel = shouldShowScreenLabel(node, scale, rank, {
            mode,
            hoverId: hovered,
            selectedId,
          });
          const strongLabel = isHub || isHover || isSelected || node.localDepth === 0;
          const label = truncateGraphLabel(node.title || node.id);
          const labelWidth = Math.min(230, Math.max(44, label.length * 6.4 + 12));
          const labelHeight = strongLabel ? 22 : 21;

          return (
            <g
              key={node.id}
              ref={(element) => {
                if (element) nodeRefs.current.set(node.id, element);
                else nodeRefs.current.delete(node.id);
              }}
              className="mg-svg-node"
              role="button"
              tabIndex={keyboardFocusId === node.id ? 0 : -1}
              aria-label={graphNodeAriaLabel(node)}
              aria-pressed={isSelected}
              aria-posinset={index + 1}
              aria-setsize={positionedNodes.length}
              transform={`translate(${roundSvg(node.x)} ${roundSvg(node.y)})`}
              opacity={focused ? 1 : 0.18}
              onPointerDown={(event) => beginNodeInteraction(event, node)}
              onPointerEnter={() => latestRef.current.onNodeHover?.(node)}
              onPointerLeave={() => {
                if (interactionRef.current?.kind !== 'node') {
                  latestRef.current.onNodeHover?.(null);
                }
              }}
              onFocus={() => latestRef.current.onNodeHover?.(node)}
              onBlur={() => latestRef.current.onNodeHover?.(null)}
              onKeyDown={(event) => handleNodeKeyDown(event, node, index)}
            >
              <title>{node.title || node.id}</title>
              <circle r={roundSvg(radius + 22 / scale)} fill="transparent" />
              <circle
                r={roundSvg(radius)}
                fill={color}
                fillOpacity="1"
                stroke={isHover || isSelected ? accentColor : borderColor}
                strokeWidth={isHover || isSelected || isHub ? 1.4 : 0.85}
                strokeOpacity={isHover || isSelected || isHub ? 0.62 : 0.24}
                vectorEffect="non-scaling-stroke"
              />
              <circle
                className="mg-svg-node-focus"
                r={roundSvg(radius + 4 / scale)}
                fill="none"
                stroke={accentColor}
                strokeWidth="2"
                vectorEffect="non-scaling-stroke"
              />
              {showLabel ? (
                <g
                  className="mg-svg-label"
                  transform={`translate(0 ${roundSvg(radius + 5 / scale)}) scale(${roundSvg(1 / scale)})`}
                  opacity={strongLabel ? 1 : 0.7}
                  pointerEvents="none"
                  aria-hidden="true"
                >
                  <rect
                    x={roundSvg(-labelWidth / 2)}
                    y="0"
                    width={roundSvg(labelWidth)}
                    height={labelHeight}
                    rx="7"
                    fill={backgroundColor}
                    fillOpacity={strongLabel ? 0.88 : 0.74}
                    stroke={borderColor}
                    strokeOpacity={strongLabel ? 0.26 : 0.16}
                  />
                  <text
                    x="0"
                    y={strongLabel ? 15 : 14.5}
                    fill={textColor}
                    fontFamily="var(--font)"
                    fontSize={strongLabel ? 12 : 11}
                    fontWeight={strongLabel ? 700 : 650}
                    textAnchor="middle"
                  >
                    {label}
                  </text>
                </g>
              ) : null}
            </g>
          );
        })}
      </g>
    </svg>
  );
}

export function layoutRendererGraphData(graphData = {}, width = 0, height = 0, opts = {}) {
  const graph = normalizeRendererGraphData(graphData, width, height);
  if (graph.nodes.length < 2) return graph;

  const mode = opts.mode === 'local' ? 'local' : 'global';
  const budget = rendererLayoutBudget(graph.nodes.length, mode);
  const iterations = budget.iterations;
  const linkDistance = mode === 'local' ? 42 : 64;
  const chargeStrength = mode === 'local' ? 120 : 185;
  const centerStrength = mode === 'local' ? 0.012 : 0.006;
  const velocities = graph.nodes.map(() => ({ x: 0, y: 0 }));
  const forces = graph.nodes.map(() => ({ x: 0, y: 0 }));
  const indexById = new Map(graph.nodes.map((node, index) => [node.id, index]));
  const radii = graph.nodes.map((node) => safeRendererRadius(opts.radiusForNode, node));
  const sampledOrder = budget.exactRepulsion
    ? null
    : graph.nodes
      .map((node, index) => ({ index, hash: hashStr(String(node.id)) }))
      .sort((a, b) => a.hash - b.hash || a.index - b.index)
      .map(({ index }) => index);

  for (let iteration = 0; iteration < iterations; iteration += 1) {
    const alpha = 0.08 + 0.92 * (1 - iteration / iterations);
    for (const force of forces) {
      force.x = 0;
      force.y = 0;
    }

    for (let aIndex = 0; aIndex < graph.nodes.length; aIndex += 1) {
      const a = graph.nodes[aIndex];
      forces[aIndex].x -= a.x * centerStrength * alpha;
      forces[aIndex].y -= a.y * centerStrength * alpha;
    }

    if (budget.exactRepulsion) {
      for (let aIndex = 0; aIndex < graph.nodes.length; aIndex += 1) {
        for (let bIndex = aIndex + 1; bIndex < graph.nodes.length; bIndex += 1) {
          applyRendererRepulsion(
            graph.nodes,
            forces,
            aIndex,
            bIndex,
            chargeStrength,
            alpha,
          );
        }
      }
    } else {
      for (let position = 0; position < sampledOrder.length; position += 1) {
        const aIndex = sampledOrder[position];
        for (let offset = 1; offset <= budget.peerSpan; offset += 1) {
          const bIndex = sampledOrder[(position + offset) % sampledOrder.length];
          applyRendererRepulsion(
            graph.nodes,
            forces,
            aIndex,
            bIndex,
            chargeStrength,
            alpha,
          );
        }
      }
    }

    resolveRendererCollisions(graph.nodes, radii);

    for (const link of graph.links) {
      const sourceIndex = indexById.get(link.sourceId);
      const targetIndex = indexById.get(link.targetId);
      if (sourceIndex == null || targetIndex == null) continue;
      const source = graph.nodes[sourceIndex];
      const target = graph.nodes[targetIndex];
      const dx = target.x - source.x;
      const dy = target.y - source.y;
      const distance = Math.max(0.001, Math.hypot(dx, dy));
      const desired = link.kind === 'moc' ? linkDistance * 0.82 : linkDistance;
      const strength = link.kind === 'moc' ? 0.42 : 0.22;
      const magnitude = (distance - desired) * strength * alpha;
      const forceX = dx / distance * magnitude;
      const forceY = dy / distance * magnitude;
      forces[sourceIndex].x += forceX;
      forces[sourceIndex].y += forceY;
      forces[targetIndex].x -= forceX;
      forces[targetIndex].y -= forceY;
    }

    for (let index = 0; index < graph.nodes.length; index += 1) {
      const velocity = velocities[index];
      velocity.x = clamp((velocity.x + forces[index].x) * 0.68, -12, 12);
      velocity.y = clamp((velocity.y + forces[index].y) * 0.68, -12, 12);
      graph.nodes[index].x += velocity.x;
      graph.nodes[index].y += velocity.y;
    }
  }

  return graph;
}

export function rendererLayoutBudget(nodeCount, mode = 'global') {
  const count = Math.max(0, Math.floor(Number(nodeCount) || 0));
  const local = mode === 'local';
  const baseIterations = local ? 80 : 130;
  const exactRepulsion = count <= EXACT_REPULSION_NODE_LIMIT;
  const iterations = exactRepulsion
    ? baseIterations
    : Math.max(36, Math.round(baseIterations * Math.sqrt(
      EXACT_REPULSION_NODE_LIMIT / count,
    )));
  const peerSpan = exactRepulsion
    ? Math.max(0, count - 1)
    : Math.min(SAMPLED_REPULSION_SPAN, Math.max(0, Math.floor((count - 1) / 2)));
  const pairCount = exactRepulsion
    ? count * Math.max(0, count - 1) / 2
    : count * peerSpan;
  return { exactRepulsion, iterations, peerSpan, pairCount };
}

export function normalizeRendererGraphData(graphData = {}, width = 0, height = 0) {
  const rawNodes = Array.isArray(graphData.nodes) ? graphData.nodes : [];
  const rawLinks = Array.isArray(graphData.links) ? graphData.links : [];
  const spread = Math.max(80, Math.min(Math.max(width, 1), Math.max(height, 1)) * 0.34);
  const seen = new Set();
  const nodes = rawNodes
    .filter((node) => {
      if (!node?.id || seen.has(node.id)) return false;
      seen.add(node.id);
      return true;
    })
    .map((node, index) => {
      const seeded = seededGraphPosition(node.id, index, rawNodes.length || 1, spread);
      return {
        ...node,
        x: Number.isFinite(node.x) ? node.x : seeded.x,
        y: Number.isFinite(node.y) ? node.y : seeded.y,
      };
    });
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const links = rawLinks
    .map((link) => {
      const sourceId = typeof link.source === 'object' ? link.source.id : link.source;
      const targetId = typeof link.target === 'object' ? link.target.id : link.target;
      const source = byId.get(sourceId);
      const target = byId.get(targetId);
      if (!source || !target) return null;
      return { ...link, source, target, sourceId, targetId };
    })
    .filter(Boolean);
  return { nodes, links };
}

function applyRendererRepulsion(
  nodes,
  forces,
  aIndex,
  bIndex,
  chargeStrength,
  alpha,
) {
  const a = nodes[aIndex];
  const b = nodes[bIndex];
  let dx = a.x - b.x;
  let dy = a.y - b.y;
  if (Math.abs(dx) + Math.abs(dy) < 0.001) {
    const angle = ((hashStr(`${a.id}:${b.id}`) % 3600) / 3600) * Math.PI * 2;
    dx = Math.cos(angle) * 0.1;
    dy = Math.sin(angle) * 0.1;
  }
  const distanceSquared = Math.max(25, dx * dx + dy * dy);
  const hubMultiplier = a.type === 'moc' || b.type === 'moc' ? 1.16 : 1;
  const magnitude = chargeStrength * hubMultiplier * alpha * 0.18 / distanceSquared;
  const forceX = dx * magnitude;
  const forceY = dy * magnitude;
  forces[aIndex].x += forceX;
  forces[aIndex].y += forceY;
  forces[bIndex].x -= forceX;
  forces[bIndex].y -= forceY;
}

function resolveRendererCollisions(nodes, radii) {
  let largestRadius = 3;
  for (const radius of radii) largestRadius = Math.max(largestRadius, radius);
  const cellSize = largestRadius * 2 + 8;
  const buckets = new Map();
  for (let index = 0; index < nodes.length; index += 1) {
    const node = nodes[index];
    const cellX = Math.floor(node.x / cellSize);
    const cellY = Math.floor(node.y / cellSize);
    for (let offsetX = -1; offsetX <= 1; offsetX += 1) {
      for (let offsetY = -1; offsetY <= 1; offsetY += 1) {
        const nearby = buckets.get(`${cellX + offsetX}:${cellY + offsetY}`);
        if (!nearby) continue;
        for (const otherIndex of nearby) {
          const other = nodes[otherIndex];
          let dx = node.x - other.x;
          let dy = node.y - other.y;
          if (Math.abs(dx) + Math.abs(dy) < 0.001) {
            const angle = (
              (hashStr(`${node.id}:${other.id}:collision`) % 3600) / 3600
            ) * Math.PI * 2;
            dx = Math.cos(angle) * 0.1;
            dy = Math.sin(angle) * 0.1;
          }
          const distance = Math.max(0.001, Math.hypot(dx, dy));
          const minDistance = radii[index] + radii[otherIndex] + 8;
          if (distance >= minDistance) continue;
          const overlap = (minDistance - distance) * 0.34;
          const pushX = dx / distance * overlap;
          const pushY = dy / distance * overlap;
          node.x += pushX;
          node.y += pushY;
          other.x -= pushX;
          other.y -= pushY;
        }
      }
    }
    const key = `${cellX}:${cellY}`;
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(index);
  }
}

export function computeRendererFitTransform(nodes = [], width = 0, height = 0, opts = {}) {
  const finiteNodes = (Array.isArray(nodes) ? nodes : [])
    .filter((node) => Number.isFinite(node.x) && Number.isFinite(node.y));
  const padding = Number.isFinite(opts.padding) ? opts.padding : 64;
  const minScale = Number.isFinite(opts.minScale) ? opts.minScale : 0.35;
  const maxScale = Number.isFinite(opts.maxScale) ? opts.maxScale : 1.15;
  if (!finiteNodes.length || width <= 0 || height <= 0) {
    return { x: width / 2, y: height / 2, k: 1 };
  }
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const node of finiteNodes) {
    minX = Math.min(minX, node.x);
    minY = Math.min(minY, node.y);
    maxX = Math.max(maxX, node.x);
    maxY = Math.max(maxY, node.y);
  }
  const graphW = Math.max(1, maxX - minX);
  const graphH = Math.max(1, maxY - minY);
  const scale = clamp(
    Math.min(
      Math.max(1, width - padding * 2) / graphW,
      Math.max(1, height - padding * 2) / graphH,
    ),
    minScale,
    maxScale,
  );
  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2;
  return {
    x: width / 2 - centerX * scale,
    y: height / 2 - centerY * scale,
    k: scale,
  };
}

function buildRendererNeighborMap(links = []) {
  const map = new Map();
  const add = (a, b) => {
    if (!map.has(a)) map.set(a, new Set());
    map.get(a).add(b);
  };
  for (const link of links) {
    const source = link.source?.id || link.sourceId || link.source;
    const target = link.target?.id || link.targetId || link.target;
    if (!source || !target) continue;
    add(source, target);
    add(target, source);
  }
  return map;
}

function buildLabelRankMap(nodes = []) {
  const ranked = [...nodes]
    .map((node) => ({ node, score: labelScore(node) }))
    .sort((a, b) => b.score - a.score);
  return new Map(ranked.map(({ node }, index) => [node.id, index]));
}

function seededGraphPosition(id, index, total, spread) {
  const angle = ((hashStr(String(id)) % 3600) / 3600) * Math.PI * 2;
  const ring = 0.35 + ((hashStr(`${String(id)}:r`) % 1000) / 1000) * 0.65;
  const fallbackAngle = total > 0 ? (index / total) * Math.PI * 2 : angle;
  const resolvedAngle = Number.isFinite(angle) ? angle : fallbackAngle;
  return {
    x: Math.cos(resolvedAngle) * spread * ring,
    y: Math.sin(resolvedAngle) * spread * ring,
  };
}

function safeRendererRadius(radiusForNode, node) {
  try {
    const radius = radiusForNode?.(node) ?? nodeRadius(node);
    return Number.isFinite(radius) ? clamp(radius, 3, 40) : nodeRadius(node);
  } catch {
    return nodeRadius(node);
  }
}

function safeRendererColor(colorForNode, node) {
  try {
    return colorForNode?.(node) || cssVar('--muted', '#8a8a93');
  } catch {
    return cssVar('--muted', '#8a8a93');
  }
}

function truncateGraphLabel(label) {
  const text = String(label || '');
  if (text.length <= 34) return text;
  return `${text.slice(0, 31).trimEnd()}...`;
}

function graphNodeAriaLabel(node) {
  const title = String(node.title || node.id || 'Untitled note');
  if (node.type === 'moc') return `${title}, map of content`;
  if (node.localDepth === 0) return `${title}, current note`;
  return `${title}, memory note`;
}

function roundSvg(value) {
  return Number.isFinite(value) ? Math.round(value * 100) / 100 : 0;
}
