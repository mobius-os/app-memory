import React, { useEffect, useMemo, useState } from 'react'
import { Moon, MemoryWriteSm, Pulse, Trash } from '@openai/apps-sdk-ui/components/Icon'

// What Memory is doing and whether it works: the last nightly run, the notes it
// changed, the facts chats saved for tonight, and how well live recall matched
// the nightly replay. Read-only; every figure comes from Memory's app-state.

function useShared(store, path, parse) {
  const [value, setValue] = useState(null)
  useEffect(() => store.subscribe(path, ({ body, present, error }) => {
    if (error && body == null) return
    if (!present || body == null) { setValue(null); return }
    try { setValue(parse(body)) } catch { setValue(null) }
  }), [store, path])
  return value
}

const parseLines = (body) => body.split('\n').filter(Boolean).flatMap((line) => {
  try { return [JSON.parse(line)] } catch { return [] }
})

function when(value, options = { dateStyle: 'medium', timeStyle: 'short' }) {
  const parsed = Date.parse(value || '')
  if (!Number.isFinite(parsed)) return ''
  return new Intl.DateTimeFormat(undefined, options).format(new Date(parsed))
}

function minutesBetween(start, end) {
  const a = Date.parse(start || '')
  const b = Date.parse(end || '')
  if (!Number.isFinite(a) || !Number.isFinite(b) || b < a) return null
  return Math.max(1, Math.round((b - a) / 60000))
}

function percent(values) {
  const known = values.filter((value) => typeof value === 'number')
  if (!known.length) return null
  return Math.round(100 * known.reduce((sum, value) => sum + value, 0) / known.length)
}

const humanize = (path) => String(path || '').replace(/^.*\//, '').replace(/\.md$/, '').replace(/-/g, ' ')

function headline(run, learned, retired) {
  if (!run) return 'No nightly run yet'
  if (run.status === 'running') return 'Memory is updating now'
  if (run.status === 'failed') return 'Last night’s update failed'
  if (run.status === 'degraded' || run.status === 'abandoned') {
    return 'Last night’s update didn’t finish'
  }
  if (!learned && !retired) return 'Nothing new last night'
  const parts = []
  if (learned) parts.push(`updated ${learned} ${learned === 1 ? 'note' : 'notes'}`)
  if (retired) parts.push(`retired ${retired}`)
  return `Memory ${parts.join(' and ')}`
}

function Stat({ value, label }) {
  return (
    <div className="mg-act-stat">
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  )
}

function Meter({ value, label, detail }) {
  return (
    <div className="mg-act-meter">
      <div className="mg-act-meter-head">
        <span>{label}</span>
        <strong>{value}%</strong>
      </div>
      <div className="mg-act-bar" role="presentation"><span style={{ width: `${value}%` }} /></div>
      <p>{detail}</p>
    </div>
  )
}

export function ActivityView({ store, graph, colorForNode, onOpenNode }) {
  const captures = useShared(store, 'app-state/captures.jsonl', parseLines) || []
  const run = useShared(store, 'app-state/run-status.json', JSON.parse)
  const stats = useShared(store, 'app-state/recall-stats.json', JSON.parse)

  const byPath = useMemo(() => {
    const map = new Map()
    for (const node of graph?.nodes || []) map.set(node.path, node)
    return map
  }, [graph])

  const published = run?.status === 'published'
  const learned = published
    ? (run.changed_paths || []).filter((path) => path.startsWith('notes/'))
      .map((path) => byPath.get(path)).filter(Boolean)
    : []
  const retired = published
    ? (run.deleted_paths || []).filter((path) => path.startsWith('notes/'))
    : []
  const duration = minutesBetween(run?.started_at, run?.finished_at)
  const replayed = (stats?.recent || []).filter((item) => 'deep_recall' in item)
  const found = percent(replayed.map((item) => item.deep_recall))
  const noise = percent(replayed.map((item) => item.deep_noise))

  return (
    <div className="mg-act mg-scroll">
      <div className="mg-act-col">
        <section className="mg-act-hero" aria-labelledby="mg-act-title">
          <p className="mg-act-kicker"><Moon aria-hidden="true" /> Latest update</p>
          <h2 id="mg-act-title">{headline(run, learned.length, retired.length)}</h2>
          {run && (
            <p className="mg-act-sub">
              {when(run.finished_at || run.started_at)}
              {duration != null && run.status !== 'running' && ` · took ${duration} min`}
            </p>
          )}
          {published && (
            <div className="mg-act-stats">
              <Stat value={run.capture_count ?? 0} label="saved facts read" />
              <Stat value={run.read_audit_count ?? 0} label="lookups checked" />
              <Stat value={retired.length} label="notes retired" />
            </div>
          )}
        </section>

        {(learned.length > 0 || retired.length > 0) && (
          <section className="mg-act-section" aria-labelledby="mg-act-learned">
            <h3 id="mg-act-learned">What changed</h3>
            <ul className="mg-act-rows">
              {learned.map((node) => (
                <li key={node.path}>
                  <button type="button" className="mg-act-row" onClick={() => onOpenNode(node)}>
                    <span className="mg-act-dot" style={{ background: colorForNode(node) }} />
                    <span className="mg-act-row-text">
                      <strong>{node.title || humanize(node.path)}</strong>
                      {node.description && <span>{node.description}</span>}
                    </span>
                  </button>
                </li>
              ))}
              {retired.map((path) => (
                <li key={path} className="mg-act-retired">
                  <Trash aria-hidden="true" />
                  <span>Retired · {humanize(path)}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="mg-act-section" aria-labelledby="mg-act-saved">
          <h3 id="mg-act-saved">
            Waiting for tonight
            {captures.length > 0 && <span className="mg-act-count">{captures.length}</span>}
          </h3>
          {captures.length ? (
            <ul className="mg-act-cards">
              {captures.slice().reverse().map((item) => (
                <li key={item.id}>
                  <MemoryWriteSm aria-hidden="true" />
                  <div>
                    <p>{item.text}</p>
                    <small>{when(item.at)}</small>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mg-act-empty">
              Nothing waiting. Chats save what they learn here, and tonight’s run
              files it into Memory.
            </p>
          )}
        </section>

        <section className="mg-act-section" aria-labelledby="mg-act-recall">
          <h3 id="mg-act-recall"><Pulse aria-hidden="true" /> How well chats recall</h3>
          {found == null ? (
            <p className="mg-act-empty">
              Measured once the nightly run replays recent chat lookups.
            </p>
          ) : (
            <>
              <Meter
                value={found}
                label="Found"
                detail={`Share of the notes a deeper nightly search picked that chats actually read, across the last ${replayed.length} lookups.`}
              />
              {noise != null && (
                <Meter
                  value={noise}
                  label="Off-target"
                  detail="Share of what chats read that the deeper search left out."
                />
              )}
            </>
          )}
        </section>
      </div>
    </div>
  )
}
