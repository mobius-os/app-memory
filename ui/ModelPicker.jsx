import { useCallback, useEffect, useRef, useState } from 'react'

function ProviderMark({ provider }) {
  return <span className="mobius-model-mark" aria-hidden="true">{provider === 'claude' ? '✳' : provider === 'codex' ? '◎' : '·'}</span>
}

export function ModelPicker({
  provider, model, groups, connectedProviders, onChange,
  title = 'Model', navKey = 'model-picker', allowProviderDefault = true,
}) {
  const [open, setOpen] = useState(false)
  const sheetRef = useRef(null)
  const closeRef = useRef(null)
  const triggerRef = useRef(null)
  const navRef = useRef(null)
  const activeGroup = groups?.find((group) => group.key === provider)
  const activeModel = activeGroup?.models?.find((item) => item.id === model)
  const modelName = activeModel?.name || (model || (activeGroup ? `${activeGroup.label} default` : 'Choose model'))

  const closeSheet = useCallback(() => {
    const handle = navRef.current
    navRef.current = null
    setOpen(false)
    try { handle?.close?.() } catch {}
  }, [])

  const openSheet = useCallback(async () => {
    if (open) return
    if (window.mobius?.nav?.open) {
      const handle = window.mobius.nav.open(navKey, () => {
        navRef.current = null
        setOpen(false)
      })
      navRef.current = handle
      const ready = handle.ready ? await handle.ready.catch(() => false) : true
      if (navRef.current !== handle) return
      if (ready === false) {
        navRef.current = null
        try { handle.close?.() } catch {}
        return
      }
    }
    setOpen(true)
  }, [navKey, open])

  useEffect(() => {
    if (!open) return undefined
    const onKey = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        closeSheet()
        return
      }
      if (event.key !== 'Tab') return
      const focusable = sheetRef.current?.querySelectorAll('button:not([disabled]), [tabindex]:not([tabindex="-1"])')
      if (!focusable?.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    closeRef.current?.focus?.()
    return () => {
      document.removeEventListener('keydown', onKey)
      triggerRef.current?.focus?.()
    }
  }, [open, closeSheet])

  useEffect(() => () => {
    try { navRef.current?.close?.() } catch {}
  }, [])

  return (
    <>
      <button ref={triggerRef} type="button" className="mobius-model-trigger" onClick={openSheet} aria-haspopup="dialog">
        <span className="mobius-model-trigger__icon"><ProviderMark provider={provider} /></span>
        <span className="mobius-model-trigger__main">
          <span className="mobius-model-trigger__name">{modelName}</span>
          <span className="mobius-model-trigger__id">{model || 'Provider default'}</span>
        </span>
        <span className="mobius-model-trigger__caret" aria-hidden="true">▾</span>
      </button>
      {open && (
        <div className="mobius-model-sheet__backdrop" role="presentation" onPointerDown={(event) => {
          if (event.target === event.currentTarget) closeSheet()
        }}>
          <div ref={sheetRef} className="mobius-model-sheet" role="dialog" aria-modal="true" aria-label={`Choose ${title.toLowerCase()}`}>
            <div className="mobius-model-sheet__head">
              <span className="mobius-model-sheet__title">{title}</span>
              <button ref={closeRef} type="button" className="mobius-model-sheet__close" onClick={closeSheet}>Close</button>
            </div>
            <div className="mobius-model-sheet__body">
              {(!groups || groups.length === 0) && <div className="mobius-model-sheet__empty">No models available.</div>}
              {groups?.map((group) => {
                const connected = !connectedProviders || connectedProviders.has(group.key)
                const defaultOn = provider === group.key && !model
                const row = (item, selected, id) => (
                  <button
                    key={id}
                    type="button"
                    className={`mobius-model-sheet__row${selected ? ' is-selected' : ''}`}
                    disabled={!connected && !selected}
                    onClick={() => { onChange(group.key, item?.id || ''); closeSheet() }}
                  >
                    <span className="mobius-model-sheet__row-icon"><ProviderMark provider={group.key} /></span>
                    <span className="mobius-model-sheet__row-main">
                      <span className="mobius-model-sheet__row-title">{item?.name || `${group.label} default`}</span>
                      <span className="mobius-model-sheet__row-id">{item?.id || 'Provider default'}</span>
                    </span>
                    {selected && <span className="mobius-model-sheet__check" aria-hidden="true" />}
                  </button>
                )
                return (
                  <div key={group.key} className="mobius-model-sheet__group">
                    <div className="mobius-model-sheet__group-head">
                      <ProviderMark provider={group.key} />
                      <span>{group.label}</span>
                      {!connected && <span className="mobius-model-sheet__group-hint">not connected</span>}
                    </div>
                    {allowProviderDefault && row(null, defaultOn, `${group.key}-default`)}
                    {group.models.map((item) => row(item, provider === group.key && model === item.id, `${group.key}-${item.id}`))}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}
    </>
  )
}
