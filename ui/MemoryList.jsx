import { useMemo } from 'react'
import { S } from '../constants.js'
import { effectiveReadCount, fmtBytes } from '../domain.js'
import { Th } from './Th.jsx'

export function sortMemoryNodes(nodes = [], sortKey, sortDir) {
  const direction = sortDir === 'asc' ? 1 : -1;
  return [...nodes].sort((a, b) => {
    let left;
    let right;
    if (sortKey === 'title') {
      left = (a.title || a.id).toLowerCase();
      right = (b.title || b.id).toLowerCase();
    } else {
      left = a[sortKey] || 0;
      right = b[sortKey] || 0;
    }
    if (left < right) return -direction;
    if (left > right) return direction;
    return 0;
  });
}

export function MemoryList({
  nodes,
  usageCounts,
  sortKey,
  sortDir,
  onSort,
  colorForNode,
  onOpenNode,
}) {
  const rows = useMemo(() => sortMemoryNodes(
    nodes.map((node) => ({
      ...node,
      access_count: effectiveReadCount(node, usageCounts),
    })),
    sortKey,
    sortDir,
  ), [nodes, usageCounts, sortKey, sortDir]);

  const openFromKeyboard = (event, node) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    onOpenNode(node);
  };

  return (
    <div style={S.listWrap} className="mg-scroll">
      <table style={S.table} className="mg-memory-list">
        <thead>
          <tr>
            <Th className="mg-col-note" label="Note" active={sortKey === 'title'} dir={sortDir} onSort={() => onSort('title')} align="left" />
            <Th className="mg-col-type" label="Type" align="left" />
            <Th className="mg-col-reads" label="Reads" active={sortKey === 'access_count'} dir={sortDir} onSort={() => onSort('access_count')} />
            <Th className="mg-col-size" label="Size" active={sortKey === 'bytes'} dir={sortDir} onSort={() => onSort('bytes')} />
          </tr>
        </thead>
        <tbody>
          {rows.map((node) => (
            <tr
              key={node.id}
              style={S.tr}
              onClick={() => onOpenNode(node)}
              className="mg-row"
              role="button"
              tabIndex={0}
              aria-label={`Open ${node.title || node.id}`}
              onKeyDown={(event) => openFromKeyboard(event, node)}
            >
              <td style={S.tdTitle} className="mg-col-note">
                <div style={S.rowTitle}>
                  <span style={{ ...S.rowDot, background: colorForNode(node) }} />
                  <span style={S.rowTitleText}>{node.title || node.id}</span>
                </div>
              </td>
              <td style={S.td} className="mg-col-type">
                <span style={{ ...S.typeTag, ...(node.type === 'moc' ? S.typeMoc : {}) }}>
                  {node.type === 'moc' ? 'hub' : 'note'}
                </span>
              </td>
              <td style={{ ...S.td, ...S.tdMeta }} className="mg-col-reads">{node.access_count}</td>
              <td style={{ ...S.td, ...S.tdMeta }} className="mg-col-size">{fmtBytes(node.bytes)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
