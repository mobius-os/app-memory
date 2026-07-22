import React, { useCallback, useEffect, useRef, useState } from 'react'

function GripVertical() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <circle cx="9" cy="5" r="1" fill="currentColor" stroke="none" />
      <circle cx="15" cy="5" r="1" fill="currentColor" stroke="none" />
      <circle cx="9" cy="12" r="1" fill="currentColor" stroke="none" />
      <circle cx="15" cy="12" r="1" fill="currentColor" stroke="none" />
      <circle cx="9" cy="19" r="1" fill="currentColor" stroke="none" />
      <circle cx="15" cy="19" r="1" fill="currentColor" stroke="none" />
    </svg>
  )
}

export function BackgroundAgentList({ children, onMove }) {
  const items = React.Children.toArray(children)
  const rowRefs = useRef([])
  const dragRef = useRef(null)
  const pointerYRef = useRef(0)
  const commitRafRef = useRef(null)
  const [drag, setDrag] = useState(null)
  const [committing, setCommitting] = useState(false)

  useEffect(() => { dragRef.current = drag }, [drag])
  useEffect(() => { rowRefs.current.length = items.length }, [items.length])

  const indexFromY = useCallback((clientY, slots) => {
    if (!slots.length) return 0
    for (let index = 0; index < slots.length - 1; index += 1) {
      if (clientY < (slots[index].center + slots[index + 1].center) / 2) return index
    }
    return slots.length - 1
  }, [])

  const beginCommit = useCallback(() => {
    if (commitRafRef.current) cancelAnimationFrame(commitRafRef.current)
    setCommitting(true)
    commitRafRef.current = requestAnimationFrame(() => {
      commitRafRef.current = requestAnimationFrame(() => {
        commitRafRef.current = null
        setCommitting(false)
      })
    })
  }, [])

  const startReorder = useCallback((index, event) => {
    const node = rowRefs.current[index]
    if (!node) return
    const rect = node.getBoundingClientRect()
    const slots = rowRefs.current.map((rowNode) => {
      const rowRect = rowNode.getBoundingClientRect()
      return { top: rowRect.top, height: rowRect.height, center: rowRect.top + rowRect.height / 2 }
    })
    try { event.currentTarget.setPointerCapture?.(event.pointerId) } catch { /* best effort */ }
    pointerYRef.current = event.clientY
    setDrag({
      fromIndex: index,
      toIndex: index,
      grabOffsetY: event.clientY - rect.top,
      rowHeight: rect.height,
      slots,
    })
  }, [])

  const activeFromIndex = drag?.fromIndex ?? null
  useEffect(() => {
    if (activeFromIndex === null) return undefined
    const start = dragRef.current
    if (!start) return undefined
    const { fromIndex, grabOffsetY, rowHeight, slots } = start
    const originTop = slots[fromIndex]?.top ?? 0
    const minOffset = slots[0]?.top - originTop || 0
    const maxOffset = slots[slots.length - 1]?.top - originTop || 0
    const followOffset = (clientY) => Math.max(
      minOffset,
      Math.min(maxOffset, clientY - grabOffsetY - originTop),
    )

    const onPointerMove = (event) => {
      event.preventDefault()
      if (!dragRef.current) return
      pointerYRef.current = event.clientY
      const node = rowRefs.current[fromIndex]
      if (node) node.style.transform = `translateY(${followOffset(event.clientY)}px) scale(1.02)`
      const center = event.clientY - grabOffsetY + rowHeight / 2
      const toIndex = indexFromY(center, slots)
      setDrag((current) => current && current.toIndex !== toIndex ? { ...current, toIndex } : current)
    }

    const finish = (event) => {
      event.preventDefault()
      const current = dragRef.current
      if (!current) return
      const center = event.clientY - grabOffsetY + rowHeight / 2
      const toIndex = indexFromY(center, slots)
      setDrag(null)
      if (toIndex !== fromIndex) {
        beginCommit()
        onMove(fromIndex, toIndex)
      }
    }

    const cancel = () => setDrag(null)
    window.addEventListener('pointermove', onPointerMove, { passive: false })
    window.addEventListener('pointerup', finish, { passive: false })
    window.addEventListener('pointercancel', cancel)
    return () => {
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', finish)
      window.removeEventListener('pointercancel', cancel)
    }
  }, [activeFromIndex, beginCommit, indexFromY, onMove])

  useEffect(() => () => {
    if (commitRafRef.current) cancelAnimationFrame(commitRafRef.current)
  }, [])

  const styleForIndex = (index) => {
    if (!drag) return undefined
    const { fromIndex, toIndex, grabOffsetY, slots } = drag
    if (index === fromIndex) {
      const originTop = slots[fromIndex]?.top ?? 0
      const minOffset = slots[0]?.top - originTop || 0
      const maxOffset = slots[slots.length - 1]?.top - originTop || 0
      const offset = Math.max(minOffset, Math.min(maxOffset, pointerYRef.current - grabOffsetY - originTop))
      return { transform: `translateY(${offset}px) scale(1.02)`, zIndex: 3, transition: 'none' }
    }
    if (toIndex > fromIndex && index > fromIndex && index <= toIndex) {
      return { transform: `translateY(${(slots[index - 1]?.top || 0) - (slots[index]?.top || 0)}px)` }
    }
    if (toIndex < fromIndex && index >= toIndex && index < fromIndex) {
      return { transform: `translateY(${(slots[index + 1]?.top || 0) - (slots[index]?.top || 0)}px)` }
    }
    return undefined
  }

  const moveBy = (index, delta) => {
    if (dragRef.current) return
    const toIndex = index + delta
    if (toIndex < 0 || toIndex >= items.length) return
    beginCommit()
    onMove(index, toIndex)
  }

  return (
    <div className={`mobius-agent-priority-list${committing ? ' is-committing' : ''}`}>
      {items.map((child, index) => (
        <div
          key={child.key || index}
          ref={(node) => { rowRefs.current[index] = node }}
          className={`mobius-agent-priority-row${drag?.fromIndex === index ? ' is-dragging' : ''}${drag?.toIndex === index && drag?.fromIndex !== index ? ' is-drop-target' : ''}`}
          style={styleForIndex(index)}
          aria-label={`Background agent priority ${index + 1}`}
        >
          <button
            type="button"
            className="mobius-agent-priority-handle"
            aria-label={`Move background agent priority ${index + 1}`}
            onPointerDown={(event) => {
              if (event.button !== undefined && event.button !== 0) return
              event.preventDefault()
              event.stopPropagation()
              startReorder(index, event)
            }}
            onClick={(event) => event.preventDefault()}
            onKeyDown={(event) => {
              if (event.key === 'ArrowUp') { event.preventDefault(); moveBy(index, -1) }
              if (event.key === 'ArrowDown') { event.preventDefault(); moveBy(index, 1) }
            }}
          >
            <GripVertical />
          </button>
          <div className="mobius-agent-priority-body">{child}</div>
        </div>
      ))}
    </div>
  )
}
