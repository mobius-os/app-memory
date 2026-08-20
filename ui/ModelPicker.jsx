import React, { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { OpenaiLogoRegular } from '@openai/apps-sdk-ui/components/Icon'

function ClaudeLogo() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="m4.7144 15.9555 4.7174-2.6471.079-.2307-.079-.1275h-.2307l-.7893-.0486-2.6956-.0729-2.3375-.0971-2.2646-.1214-.5707-.1215-.5343-.7042.0546-.3522.4797-.3218.686.0608 1.5179.1032 2.2767.1578 1.6514.0972 2.4468.255h.3886l.0546-.1579-.1336-.0971-.1032-.0972L6.973 9.8356l-2.55-1.6879-1.3356-.9714-.7225-.4918-.3643-.4614-.1578-1.0078.6557-.7225.8803.0607.2246.0607.8925.686 1.9064 1.4754 2.4893 1.8336.3643.3035.1457-.1032.0182-.0728-.164-.2733-1.3539-2.4467-1.445-2.4893-.6435-1.032-.17-.6194c-.0607-.255-.1032-.4674-.1032-.7285L6.287.1335 6.6997 0l.9957.1336.419.3642.6192 1.4147 1.0018 2.2282 1.5543 3.0296.4553.8985.2429.8318.091.255h.1579v-.1457l.1275-1.706.2368-2.0947.2307-2.6957.0789-.7589.3764-.9107.7468-.4918.5828.2793.4797.686-.0668.4433-.2853 1.8517-.5586 2.9021-.3643 1.9429h.2125l.2429-.2429.9835-1.3053 1.6514-2.0643.7286-.8196.85-.9046.5464-.4311h1.0321l.759 1.1293-.34 1.1657-1.0625 1.3478-.8804 1.1414-1.2628 1.7-.7893 1.36.0729.1093.1882-.0183 2.8535-.607 1.5421-.2794 1.8396-.3157.8318.3886.091.3946-.3278.8075-1.967.4857-2.3072.4614-3.4364.8136-.0425.0304.0486.0607 1.5482.1457.6618.0364h1.621l3.0175.2247.7892.522.4736.6376-.079.4857-1.2142.6193-1.6393-.3886-3.825-.9107-1.3113-.3279h-.1822v.1093l1.0929 1.0686 2.0035 1.8092 2.5075 2.3314.1275.5768-.3218.4554-.34-.0486-2.2039-1.6575-.85-.7468-1.9246-1.621h-.1275v.17l.4432.6496 2.3436 3.5214.1214 1.0807-.17.3521-.6071.2125-.6679-.1214-1.3721-1.9246L14.38 17.959l-1.1414-1.9428-.1397.079-.674 7.2552-.3156.3703-.7286.2793-.6071-.4614-.3218-.7468.3218-1.4753.3886-1.9246.3157-1.53.2853-1.9004.17-.6314-.0121-.0425-.1397.0182-1.4328 1.9672-2.1796 2.9446-1.7243 1.8456-.4128.164-.7164-.3704.0667-.6618.4008-.5889 2.386-3.0357 1.4389-1.882.929-1.0868-.0062-.1579h-.0546l-6.3385 4.1164-1.1293.1457-.4857-.4554.0608-.7467.2307-.2429 1.9064-1.3114Z" />
    </svg>
  )
}

function OpenAILogo() {
  return <OpenaiLogoRegular aria-hidden="true" />
}

function ProviderLogo({ provider }) {
  return provider === 'claude' ? <ClaudeLogo /> : provider === 'codex' ? <OpenAILogo /> : null
}

export function ModelPicker({
  provider,
  model,
  groups,
  connectedProviders,
  onChange,
  title = 'Model',
  navKey = 'model-picker',
  allowProviderDefault = false,
  useSettingsDefault = false,
  onSettingsDefault,
}) {
  const [open, setOpen] = useState(false)
  const sheetRef = useRef(null)
  const closeRef = useRef(null)
  const triggerRef = useRef(null)
  const navRef = useRef(null)
  const activeGroup = groups?.find((group) => group.key === provider)
  const activeModel = activeGroup?.models?.find((item) => item.id === model)
  const modelName = useSettingsDefault
    ? 'Default from settings'
    : (activeModel?.name || (model ? model : activeGroup ? `${activeGroup.label} default` : 'Choose model'))
  const triggerLabel = useSettingsDefault
    ? `${title}: Default from settings`
    : `${title}: ${modelName}`

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
      const focusable = sheetRef.current?.querySelectorAll(
        'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      )
      if (!focusable?.length) {
        event.preventDefault()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
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
      <button
        ref={triggerRef}
        type="button"
        className="mobius-model-trigger"
        onClick={openSheet}
        aria-haspopup="dialog"
        aria-label={triggerLabel}
      >
        <span className="mobius-model-trigger__icon" aria-hidden="true">
          {useSettingsDefault ? '—' : <ProviderLogo provider={provider} />}
        </span>
        <span className="mobius-model-trigger__main">
          <span className="mobius-model-trigger__name">{modelName}</span>
          {!useSettingsDefault && (
            <span className="mobius-model-trigger__id">{model || 'Provider default'}</span>
          )}
        </span>
      </button>
      {open && createPortal(
        <div
          className="mobius-model-sheet__backdrop"
          role="presentation"
          onPointerDown={(event) => {
            if (event.target === event.currentTarget) closeSheet()
          }}
        >
          <div
            ref={sheetRef}
            className="mobius-model-sheet"
            role="dialog"
            aria-modal="true"
            aria-label={`Choose ${title.toLowerCase()}`}
          >
            <div className="mobius-model-sheet__head">
              <span className="mobius-model-sheet__title">{title}</span>
              <button ref={closeRef} type="button" className="mobius-model-sheet__close" onClick={closeSheet}>
                Close
              </button>
            </div>
            <div className="mobius-model-sheet__body">
              <button
                type="button"
                className={`mobius-model-sheet__row${useSettingsDefault ? ' is-selected' : ''}`}
                aria-pressed={useSettingsDefault}
                onClick={() => { onSettingsDefault?.(); closeSheet() }}
              >
                <span className="mobius-model-sheet__row-icon" aria-hidden="true">—</span>
                <span className="mobius-model-sheet__row-main">
                  <span className="mobius-model-sheet__row-title">Default from settings</span>
                </span>
                {useSettingsDefault && <span className="mobius-model-sheet__check" aria-hidden="true" />}
              </button>
              {(!groups || groups.length === 0) && (
                <div className="mobius-model-sheet__empty">No models available.</div>
              )}
              {groups?.map((group) => {
                const connected = !connectedProviders || connectedProviders.has(group.key)
                const defaultOn = provider === group.key && !model
                return (
                  <div key={group.key} className="mobius-model-sheet__group">
                    <div className="mobius-model-sheet__group-head">
                      <span className="mobius-model-sheet__group-icon" aria-hidden="true">
                        <ProviderLogo provider={group.key} />
                      </span>
                      <span>{group.label}</span>
                      {!connected && <span className="mobius-model-sheet__group-hint">not connected</span>}
                    </div>
                    {allowProviderDefault && (
                      <button
                        type="button"
                        className={`mobius-model-sheet__row${defaultOn ? ' is-selected' : ''}`}
                        aria-pressed={defaultOn}
                        disabled={!connected && !defaultOn}
                        onClick={() => { onChange(group.key, ''); closeSheet() }}
                      >
                        <span className="mobius-model-sheet__row-icon" aria-hidden="true"><ProviderLogo provider={group.key} /></span>
                        <span className="mobius-model-sheet__row-main">
                          <span className="mobius-model-sheet__row-title">{group.label} default</span>
                          <span className="mobius-model-sheet__row-id">Provider default</span>
                        </span>
                        {defaultOn && <span className="mobius-model-sheet__check" aria-hidden="true" />}
                      </button>
                    )}
                    {group.models.map((item) => {
                      const selected = provider === group.key && model === item.id
                      const disabled = !connected && !selected
                      return (
                        <div key={`${group.key}-${item.id}`}>
                          <button
                            type="button"
                            className={`mobius-model-sheet__row${selected ? ' is-selected' : ''}`}
                            aria-pressed={selected}
                            disabled={disabled}
                            onClick={() => {
                              onChange(group.key, item.id)
                              closeSheet()
                            }}
                          >
                            <span className="mobius-model-sheet__row-icon" aria-hidden="true"><ProviderLogo provider={group.key} /></span>
                            <span className="mobius-model-sheet__row-main">
                              <span className="mobius-model-sheet__row-title">{item.name || item.id}</span>
                              <span className="mobius-model-sheet__row-id">{item.id}</span>
                            </span>
                            {selected && <span className="mobius-model-sheet__check" aria-hidden="true" />}
                          </button>
                        </div>
                      )
                    })}
                  </div>
                )
              })}
            </div>
          </div>
        </div>,
        document.body,
      )}
    </>
  )
}
