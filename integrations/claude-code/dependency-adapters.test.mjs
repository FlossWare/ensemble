import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {
  createSqliteDatabase, createChromaClient, createNeo4jInteropDriver,
  createLocalEmbeddingPipeline, loadYamlConfiguration, createId,
} from './dependency-adapters.mjs'

test('better-sqlite3 adapter enables WAL and foreign keys', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ensemble-node-'))
  const database = createSqliteDatabase(path.join(directory, 'adapter.db'))
  assert.equal(database.pragma('journal_mode', { simple: true }), 'wal')
  assert.equal(database.pragma('foreign_keys', { simple: true }), 1)
  database.close()
  fs.rmSync(directory, { recursive: true, force: true })
})

test('Chroma adapter creates a persistent client without connecting to remote services', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ensemble-chroma-'))
  const client = createChromaClient(directory)
  assert.ok(client)
  fs.rmSync(directory, { recursive: true, force: true })
})

test('Neo4j adapter validates configuration before creating a driver', () => {
  assert.throws(() => createNeo4jInteropDriver('', 'user', 'secret'), /required/)
  const driver = createNeo4jInteropDriver('bolt://127.0.0.1:7687', 'user', 'secret')
  assert.ok(driver)
  return driver.close()
})

test('local Transformers.js adapter refuses missing model directories', async () => {
  await assert.rejects(
    createLocalEmbeddingPipeline(path.join(os.tmpdir(), 'missing-ensemble-model')),
    /does not exist/,
  )
})

test('YAML adapter loads object configuration and UUID adapter generates IDs', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ensemble-yaml-'))
  const filename = path.join(directory, 'config.yaml')
  fs.writeFileSync(filename, 'enabled: true\nmodels:\n  - local\n')
  assert.deepEqual(loadYamlConfiguration(filename), { enabled: true, models: ['local'] })
  assert.match(createId(), /^[0-9a-f-]{36}$/)
  fs.rmSync(directory, { recursive: true, force: true })
})
