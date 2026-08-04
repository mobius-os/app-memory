import { existsSync } from 'node:fs'
import { delimiter, dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const appRoot = resolve(here, '..')

function pathEntries(value) {
  return value ? value.split(delimiter).filter(Boolean) : []
}

function candidateNodeModules() {
  const candidates = [
    ...pathEntries(process.env.MOBIUS_FRONTEND_NODE_MODULES),
    ...pathEntries(process.env.NODE_PATH),
    join(appRoot, 'node_modules'),
    join(appRoot, '..', '..', 'frontend', 'node_modules'),
    join(appRoot, '..', '..', 'mobius', 'frontend', 'node_modules'),
    join(appRoot, '..', 'mobius', 'frontend', 'node_modules'),
  ]

  let dir = appRoot
  while (true) {
    candidates.push(join(dir, 'frontend', 'node_modules'))
    candidates.push(join(dir, 'mobius', 'frontend', 'node_modules'))
    const parent = dirname(dir)
    if (parent === dir) break
    dir = parent
  }

  return [...new Set(candidates.map((candidate) => resolve(candidate)))]
}

function hasFrontendRuntimeDeps(nodeModules) {
  return existsSync(join(nodeModules, 'react'))
}

export function findFrontendNodeModules() {
  for (const candidate of candidateNodeModules()) {
    if (hasFrontendRuntimeDeps(candidate)) return candidate
  }
  throw new Error(
    'Could not find frontend runtime dependencies. Run npm ci in '
      + 'mobius/frontend or set MOBIUS_FRONTEND_NODE_MODULES.',
  )
}

export const frontendNodeModules = findFrontendNodeModules()
export const esbuildPath = join(appRoot, 'node_modules', '.bin', 'esbuild')

if (!existsSync(esbuildPath)) {
  throw new Error('Could not find the app test compiler. Run npm ci in this app.')
}

export function buildEnv(extra = {}) {
  const nodePath = [frontendNodeModules, process.env.NODE_PATH]
    .filter(Boolean)
    .join(delimiter)
  return { ...process.env, NODE_PATH: nodePath, ...extra }
}
