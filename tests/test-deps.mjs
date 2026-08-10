import { existsSync } from 'node:fs'
import { createRequire } from 'node:module'
import { delimiter, dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

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
    candidates.push(join(dir, 'platform', 'frontend', 'node_modules'))
    const parent = dirname(dir)
    if (parent === dir) break
    dir = parent
  }

  return [...new Set(candidates.map((candidate) => resolve(candidate)))]
}

function hasFrontendTestDeps(nodeModules) {
  return existsSync(join(nodeModules, 'rolldown'))
    && existsSync(join(nodeModules, 'react'))
}

export function findFrontendNodeModules() {
  for (const candidate of candidateNodeModules()) {
    if (hasFrontendTestDeps(candidate)) return candidate
  }
  throw new Error(
    'Could not find frontend test dependencies (rolldown, react). Run npm ci '
      + 'in the Möbius frontend or set MOBIUS_FRONTEND_NODE_MODULES.',
  )
}

export const frontendNodeModules = findFrontendNodeModules()

// Möbius compiles mini-apps with Rolldown, so the tests bundle the same way.
// Keeping the bundler behind one helper stops each test from re-encoding the
// compiler's command line.
export async function bundleModule({ entry, outfile, alias = {} }) {
  const requireFromFrontend = createRequire(join(frontendNodeModules, 'package.json'))
  const { rolldown } = await import(
    pathToFileURL(requireFromFrontend.resolve('rolldown')).href
  )
  const build = await rolldown({
    input: entry,
    platform: 'node',
    tsconfig: false,
    transform: { jsx: 'react-jsx' },
    resolve: { alias, modules: [frontendNodeModules, 'node_modules'] },
  })
  // codeSplitting:false matches the shell compiler: index.jsx's dynamic
  // imports (marked, dompurify) are inlined into one module rather than
  // emitted as side chunks, so the tests import what Möbius installs.
  await build.write({ file: outfile, format: 'es', codeSplitting: false })
  await build.close()
  return import(pathToFileURL(outfile).href)
}
