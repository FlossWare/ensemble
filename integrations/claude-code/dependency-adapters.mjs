/** Optional Node host adapters for POC-retained libraries.
 *
 * This module is deliberately not imported by the Python REST server. It is
 * loaded by Claude Code integrations that opt into the Node adapter package.
 * SQLite remains the local transactional store; Neo4j is an explicit,
 * separately configured interoperability adapter, not Ensemble's authority.
 */
import Database from 'better-sqlite3'
import { ChromaClient } from 'chromadb'
import neo4j from 'neo4j-driver'
import { v4 as uuidv4 } from 'uuid'
import yaml from 'js-yaml'
import { env, pipeline } from '@xenova/transformers'
import fs from 'node:fs'
import path from 'node:path'

export function createSqliteDatabase(filename) {
  if (typeof filename !== 'string' || filename.trim() === '') {
    throw new TypeError('SQLite filename is required')
  }
  fs.mkdirSync(path.dirname(path.resolve(filename)), { recursive: true })
  const database = new Database(filename)
  database.pragma('journal_mode = WAL')
  database.pragma('foreign_keys = ON')
  database.pragma('busy_timeout = 5000')
  return database
}

export function createChromaClient(pathname) {
  if (typeof pathname !== 'string' || pathname.trim() === '') {
    throw new TypeError('Chroma persistence path is required')
  }
  return new ChromaClient({ path: path.resolve(pathname) })
}

export function createNeo4jInteropDriver(uri, username, password) {
  if (![uri, username, password].every(value => typeof value === 'string' && value.length > 0)) {
    throw new TypeError('Neo4j URI, username, and password are required')
  }
  return neo4j.driver(uri, neo4j.auth.basic(username, password), {
    maxConnectionPoolSize: 10,
    connectionAcquisitionTimeout: 5000,
  })
}

export async function createLocalEmbeddingPipeline(modelDirectory) {
  if (typeof modelDirectory !== 'string' || modelDirectory.trim() === '') {
    throw new TypeError('local model directory is required')
  }
  const resolved = path.resolve(modelDirectory)
  if (!fs.existsSync(resolved)) throw new Error('local embedding model directory does not exist')
  env.allowRemoteModels = false
  env.allowLocalModels = true
  env.localModelPath = path.dirname(resolved)
  return pipeline('feature-extraction', path.basename(resolved), {
    quantized: true,
    local_files_only: true,
  })
}

export function loadYamlConfiguration(filename) {
  const source = fs.readFileSync(filename, 'utf8')
  const value = yaml.load(source, { schema: yaml.DEFAULT_SCHEMA })
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError('YAML configuration root must be an object')
  }
  return value
}

export function createId() {
  return uuidv4()
}
