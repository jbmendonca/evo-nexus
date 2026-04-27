const assert = require('assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const test = require('node:test');

const SessionStore = require('../src/utils/session-store');
const { TerminalServer } = require('../src/server');

function makeTempDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

test('SessionStore drops stale sessions when loading', async () => {
  const tempDir = makeTempDir('evonexus-session-store-');
  const sessionsFile = path.join(tempDir, 'sessions.json');
  const now = new Date();
  const staleAt = new Date(Date.now() - 48 * 60 * 60 * 1000).toISOString();
  const freshAt = new Date(Date.now() - 2 * 1000).toISOString();

  fs.writeFileSync(
    sessionsFile,
    JSON.stringify({
      version: '1.0',
      savedAt: now.toISOString(),
      sessions: [
        {
          id: 'stale',
          name: 'Stale session',
          created: staleAt,
          lastActivity: staleAt,
          archived: false,
        },
        {
          id: 'fresh',
          name: 'Fresh session',
          created: freshAt,
          lastActivity: freshAt,
          archived: false,
        },
      ],
    }, null, 2)
  );

  const store = new SessionStore({ storageDir: tempDir, sessionTtlMs: 5000, maxFileAgeDays: 30 });
  const sessions = await store.loadSessions();

  assert.equal(sessions.size, 1);
  assert.equal(sessions.has('fresh'), true);
  assert.equal(sessions.has('stale'), false);
});

test('TerminalServer purges stale sessions and reports health', async () => {
  const tempDir = makeTempDir('evonexus-terminal-server-');
  const sessionsFile = path.join(tempDir, 'sessions.json');
  fs.writeFileSync(
    sessionsFile,
    JSON.stringify({
      version: '1.0',
      savedAt: new Date().toISOString(),
      sessions: [],
    }, null, 2)
  );

  const server = new TerminalServer({
    port: 0,
    dev: false,
    sessionTtlMs: 1000,
    sessionGcIntervalMs: 0,
    autoSaveIntervalMs: 0,
  });

  await server.ready;
  server.claudeSessions = new Map();

  server.sessionStore.storageDir = tempDir;
  server.sessionStore.sessionsFile = sessionsFile;

  const staleAt = new Date(Date.now() - 2 * 60 * 60 * 1000);
  const freshAt = new Date();

  server.claudeSessions.set('stale', {
    id: 'stale',
    name: 'Stale session',
    created: staleAt,
    lastActivity: staleAt,
    active: false,
    archived: false,
    connections: new Set(),
    workingDir: tempDir,
  });
  server.claudeSessions.set('fresh', {
    id: 'fresh',
    name: 'Fresh session',
    created: freshAt,
    lastActivity: freshAt,
    active: false,
    archived: false,
    connections: new Set(),
    workingDir: tempDir,
  });

  const result = await server.purgeStaleSessions();
  const health = server.getHealthSnapshot(true);

  assert.equal(result.removed, 1);
  assert.equal(server.claudeSessions.has('stale'), false);
  assert.equal(server.claudeSessions.has('fresh'), true);
  assert.equal(health.counts.staleSessions, 0);
  assert.equal(health.checks.storage.status, 'ok');
  assert.equal(health.checks.workspace.status, 'ok');
  assert.equal(typeof health.checks.providers.status, 'string');
  assert.equal(['ok', 'warning', 'error'].includes(health.status), true);

  server.close();
});

test('TerminalServer closes stale WebSocket connections after heartbeat timeout', async () => {
  const tempDir = makeTempDir('evonexus-terminal-ws-');
  const server = new TerminalServer({
    port: 0,
    dev: false,
    baseFolder: tempDir,
    sessionTtlMs: 1000,
    sessionGcIntervalMs: 0,
    autoSaveIntervalMs: 0,
    wsHeartbeatTimeoutMs: 1000,
    wsHeartbeatSweepIntervalMs: 0,
  });

  await server.ready;

  const wsId = 'ws-1';
  let terminated = false;
  const ws = {
    readyState: 1,
    terminate: () => {
      terminated = true;
    },
  };

  server.claudeSessions = new Map([
    ['session-1', {
      id: 'session-1',
      name: 'Session 1',
      created: new Date(),
      lastActivity: new Date(),
      active: false,
      archived: false,
      connections: new Set([wsId]),
    }],
  ]);
  server.webSocketConnections.set(wsId, {
    id: wsId,
    ws,
    claudeSessionId: 'session-1',
    created: new Date(),
    lastHeartbeatAt: Date.now() - 5000,
    remoteAddress: '127.0.0.1',
  });

  const result = await server.purgeStaleWebSocketConnections();
  const auditLogPath = path.join(tempDir, 'workspace', 'ADWs', 'logs', 'terminal-audit.jsonl');

  assert.equal(result.closed, 1);
  assert.equal(terminated, true);
  assert.equal(server.webSocketConnections.has(wsId), false);
  assert.equal(server.claudeSessions.get('session-1').connections.size, 0);
  assert.equal(fs.existsSync(auditLogPath), true);
  const auditEntries = fs.readFileSync(auditLogPath, 'utf8').trim().split('\n').map((line) => JSON.parse(line));
  assert.equal(auditEntries.some((entry) => entry.action === 'ws_timeout'), true);

  server.close();
});

test('TerminalServer tracks platform events in health snapshots', async () => {
  const tempDir = makeTempDir('evonexus-terminal-platform-');
  const server = new TerminalServer({
    port: 0,
    dev: false,
    baseFolder: tempDir,
    sessionTtlMs: 1000,
    sessionGcIntervalMs: 0,
    autoSaveIntervalMs: 0,
    wsHeartbeatTimeoutMs: 1000,
    wsHeartbeatSweepIntervalMs: 0,
  });

  await server.ready;
  server.handlePlatformEvent({
    ts: new Date().toISOString(),
    topic: 'provider-routing-updated',
    source: 'dashboard',
    payload: { enabled: true },
  });

  const health = server.getHealthSnapshot(true);

  assert.equal(server.platformEvents.length, 1);
  assert.equal(health.counts.platformEvents, 1);
  assert.equal(health.status, 'ok');

  server.close();
});
