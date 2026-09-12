import test from 'node:test'
import assert from 'node:assert/strict'
import { migrateAgentModels, RETIRED_MODEL_IDS } from '../model-selection.mjs'

test('UI migration maps exactly the retired model ids', () => {
  for (const [retired, current] of Object.entries(RETIRED_MODEL_IDS)) {
    const source = { model: retired, fallback_model: retired, keep: 7 }
    const migrated = migrateAgentModels(source)
    assert.deepEqual(migrated, { model: current, fallback_model: current, keep: 7 })
    assert.equal(migrateAgentModels(migrated), migrated)
  }
  const unknown = { model: 'future-model', fallback_model: 'gpt-5.5' }
  assert.equal(migrateAgentModels(unknown), unknown)
})
