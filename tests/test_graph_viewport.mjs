import test from 'node:test'
import assert from 'node:assert/strict'

import {
  MAX_ZOOM,
  MIN_ZOOM,
  pinchRendererTransform,
  scaleRendererTransformAt,
} from '../graph/viewport.mjs'

test('anchored zoom keeps the graph point under the chosen screen point', () => {
  const start = { x: 40, y: -10, k: 0.5 }
  const anchor = { x: 140, y: 90 }
  const next = scaleRendererTransformAt(start, anchor, 1.25)

  assert.equal((anchor.x - start.x) / start.k, (anchor.x - next.x) / next.k)
  assert.equal((anchor.y - start.y) / start.k, (anchor.y - next.y) / next.k)
})

test('pinch zoom follows both finger spread and moving centroid', () => {
  const next = pinchRendererTransform(
    { x: 0, y: 0, k: 1 },
    { x: 100, y: 100 },
    { x: 120, y: 90 },
    2,
  )

  assert.deepEqual(next, { x: -80, y: -110, k: 2 })
})

test('pinch zoom remains inside the graph zoom range', () => {
  const center = { x: 0, y: 0 }
  assert.equal(pinchRendererTransform({ x: 0, y: 0, k: 1 }, center, center, 100).k, MAX_ZOOM)
  assert.equal(pinchRendererTransform({ x: 0, y: 0, k: 1 }, center, center, 0).k, MIN_ZOOM)
})
