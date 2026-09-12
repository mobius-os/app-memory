export const RETIRED_MODEL_IDS = Object.freeze({
  'claude-opus-4-5-20251001': 'claude-opus-4-5-20251101',
  'claude-sonnet-4-5-20251001': 'claude-sonnet-4-5-20250929',
  'claude-opus-4-6-20251015': 'claude-opus-4-6',
  'claude-opus-4-7-20251215': 'claude-opus-4-7',
  'claude-sonnet-4-7-20251215': 'claude-sonnet-4-6',
})

export function migrateAgentModels(settings) {
  if (!settings || typeof settings !== 'object' || Array.isArray(settings)) return settings
  let migrated = settings
  for (const key of ['model', 'fallback_model']) {
    const replacement = RETIRED_MODEL_IDS[settings[key]]
    if (!replacement) continue
    if (migrated === settings) migrated = { ...settings }
    migrated[key] = replacement
  }
  return migrated
}
