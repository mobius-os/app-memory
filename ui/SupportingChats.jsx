function activityDate(value) {
  const parsed = Date.parse(value || '')
  if (!Number.isFinite(parsed)) return 'Last activity unavailable'
  return `Last activity ${new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
  }).format(new Date(parsed))}`
}

export function SupportingChats({ refs = [], contribution = '', onOpenChat }) {
  const items = Array.isArray(refs) ? refs : []
  if (!items.length) return null
  const support = String(contribution || '').trim()

  return (
    <section className="mg-supporting" aria-labelledby="mg-supporting-heading">
      <div className="mg-supporting-heading">
        <strong id="mg-supporting-heading">Supporting chats</strong>
        <span>{items.length}</span>
      </div>
      <ul className="mg-supporting-list">
        {items.map((ref = {}, index) => {
          const deleted = ref.kind === 'deleted' || ref.kind === 'legacy_deleted'
          const title = deleted ? 'Deleted chat' : (ref.title || 'Supporting chat')
          const canOpen = !deleted && typeof ref.chat_id === 'string' && ref.chat_id
          return (
            <li className="mg-supporting-item" key={ref.source_id || ref.chat_id || index}>
              <div className="mg-supporting-main">
                <strong>{title}</strong>
                <span>{activityDate(ref.last_activity)}</span>
                {support && <p><b>Contributed</b> {support}</p>}
              </div>
              {canOpen && (
                <button type="button" onClick={() => onOpenChat?.(ref.chat_id)}>
                  Open chat
                </button>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
