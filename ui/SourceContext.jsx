function sourceDate(value) {
  const parsed = Date.parse(value || '')
  if (!Number.isFinite(parsed)) return ''
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(parsed))
}

function roleLabel(value) {
  if (value === 'user') return 'You'
  if (value === 'assistant') return 'Agent'
  return String(value || 'Message')
}

function UnavailableSource({ kind }) {
  return (
    <div className="mg-source-unavailable">
      <strong>{kind === 'legacy_deleted' ? 'Deleted chat' : 'Source chat'}</strong>
      <span>
        {kind === 'legacy_deleted'
          ? 'This memory predates source retention, so its deleted chat text is no longer available.'
          : 'Memory will attach this source snapshot on a future consolidation run.'}
      </span>
    </div>
  )
}

export function SourceContext({ state }) {
  if (!state || state.status === 'idle') return null
  if (state.status === 'loading') {
    return (
      <section className="mg-source-context" aria-label="Source context">
        <div className="mg-source-heading">
          <div>
            <strong>Source context</strong>
            <span>Loading the text Memory reviewed…</span>
          </div>
        </div>
        <div className="mg-source-loading" aria-hidden="true" />
      </section>
    )
  }
  const items = Array.isArray(state.items) ? state.items : []
  if (!items.length) return null

  return (
    <section className="mg-source-context" aria-labelledby="mg-source-heading">
      <div className="mg-source-heading">
        <div>
          <strong id="mg-source-heading">Source context</strong>
          <span>
            The structurally redacted chat text behind this memory.
          </span>
        </div>
        <span className="mg-source-count">{items.length}</span>
      </div>

      <div className="mg-source-list">
        {items.map(({ ref = {}, record }, index) => {
          if (!record) {
            return <UnavailableSource key={`${ref.kind || 'source'}-${index}`} kind={ref.kind} />
          }
          const deleted = Boolean(record.deleted_at || ref.kind === 'deleted')
          const snapshots = Array.isArray(record.snapshots) ? record.snapshots : []
          return (
            <details
              className="mg-source-card"
              key={record.source_id || index}
              open={items.length === 1}
            >
              <summary>
                <span className="mg-source-summary-copy">
                  <strong>{record.title || (deleted ? 'Deleted chat' : 'Source chat')}</strong>
                  <span>
                    {deleted ? 'Deleted chat · retained by Memory' : 'Active chat'}
                    {snapshots.length ? ` · ${snapshots.length} snapshot${snapshots.length === 1 ? '' : 's'}` : ''}
                  </span>
                </span>
                <span className={`mg-source-state${deleted ? ' is-deleted' : ''}`}>
                  {deleted ? 'Retained' : 'Source'}
                </span>
              </summary>

              <div className="mg-source-snapshots">
                {snapshots.length === 0 && (
                  <div className="mg-source-unavailable">
                    <span>No retained source text is available for this entry yet.</span>
                  </div>
                )}
                {snapshots.map((snapshot, snapshotIndex) => {
                  const input = snapshot && typeof snapshot.input === 'object'
                    ? snapshot.input
                    : {}
                  const messages = Array.isArray(input.messages) ? input.messages : []
                  const backfilled = snapshot.capture_kind === 'backfill'
                  return (
                    <section className="mg-source-snapshot" key={snapshot.hash || snapshotIndex}>
                      <div className="mg-source-snapshot-head">
                        <strong>
                          {backfilled ? 'Captured for an older memory' : 'Reviewed by Memory'}
                        </strong>
                        <span>{sourceDate(snapshot.captured_at)}</span>
                      </div>
                      {backfilled && (
                        <p className="mg-source-caveat">
                          This source was recovered after the memory was created, so it may include later turns from the same chat.
                        </p>
                      )}
                      <div className="mg-source-messages">
                        {messages.map((message, messageIndex) => (
                          <div
                            className={`mg-source-message is-${message?.role === 'user' ? 'user' : 'agent'}`}
                            key={`${messageIndex}-${message?.role || ''}`}
                          >
                            <span>{roleLabel(message?.role)}</span>
                            <p>{String(message?.text || '')}</p>
                          </div>
                        ))}
                      </div>
                    </section>
                  )
                })}
              </div>
            </details>
          )
        })}
      </div>
    </section>
  )
}
