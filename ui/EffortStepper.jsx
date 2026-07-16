import React, { useMemo, useRef } from 'react'
import { EFFORT_LEVELS } from '../constants.js'

export function EffortStepper({ provider, value, onChange, disabled = false }) {
  const levels = EFFORT_LEVELS[provider] || []
  const refs = useRef([])
  const selectedIndex = useMemo(() => {
    const idx = levels.findIndex((level) => level.value === value)
    return idx >= 0 ? idx : 0
  }, [levels, value])
  const selected = levels[selectedIndex]

  if (!levels.length) return null

  const choose = (idx, focus = false) => {
    const next = levels[idx]
    if (!next || disabled) return
    if (next.value !== value) onChange?.(next.value)
    if (focus) refs.current[idx]?.focus?.()
  }

  const onKeyDown = (e) => {
    let next = selectedIndex
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = Math.min(levels.length - 1, selectedIndex + 1)
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = Math.max(0, selectedIndex - 1)
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = levels.length - 1
    else return
    e.preventDefault()
    choose(next, true)
  }

  return (
    <div className={`mobius-effort${disabled ? ' is-disabled' : ''}`}>
      <div
        className="mobius-effort-track"
        role="radiogroup"
        aria-label="Reasoning effort"
        onKeyDown={onKeyDown}
      >
        {levels.map((level, idx) => {
          const on = idx === selectedIndex
          return (
            <button
              key={level.value}
              ref={(el) => { refs.current[idx] = el }}
              type="button"
              className={`mobius-effort-stop${idx <= selectedIndex ? ' is-filled' : ''}${on ? ' is-active' : ''}`}
              role="radio"
              aria-checked={on}
              aria-label={level.label}
              tabIndex={on && !disabled ? 0 : -1}
              disabled={disabled}
              onClick={() => choose(idx)}
            />
          )
        })}
      </div>
      <span className="mobius-effort-label">{selected?.label || levels[0].label}</span>
    </div>
  )
}
