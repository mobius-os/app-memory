function explicitAgent(slot) {
  return slot?.mode === 'app' && typeof slot.provider === 'string' && slot.provider.trim().length > 0
}

export function canReorderAgentSlots(slots) {
  return Array.isArray(slots) && slots.length > 1 && slots.every(explicitAgent)
}

/** Reorder only concrete app overrides; inherited Settings slots are positional. */
export function reorderAgentSlots(slots, fromIndex, toIndex) {
  if (!canReorderAgentSlots(slots)) return slots
  if (!Number.isInteger(fromIndex) || !Number.isInteger(toIndex)) return slots
  if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0 || fromIndex >= slots.length || toIndex >= slots.length) return slots
  const next = slots.map((slot) => ({ ...slot }))
  const [moved] = next.splice(fromIndex, 1)
  next.splice(toIndex, 0, moved)
  return next
}

export function agentSlotLabel(slot, groups, inheritedLabel) {
  if (!explicitAgent(slot)) return inheritedLabel
  const group = groups?.find((item) => item.key === slot.provider)
  const model = group?.models?.find((item) => item.id === slot.model)
  const identity = model?.name || slot.model || group?.label || slot.provider
  return slot.effort ? `${identity}, ${slot.effort} effort` : identity
}
