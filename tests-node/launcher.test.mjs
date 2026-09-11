import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, rm, writeFile, readFile, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { ASSETS, UV_VERSION, ensureUv, launchSpec, cacheRoot, terminateChild } from '../bin/codex-remote-bridge.mjs';

const bytes = Buffer.from('verified fixture');
const digest = createHash('sha256').update(bytes).digest('hex');
async function temp(t) {
  const root = await mkdtemp(join(tmpdir(), 'codex-bridge-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}
function mocked(overrides = {}) {
  return {
    platform: 'linux', arch: 'x64', assets: { 'linux-x64': ['fixture.tar.gz', digest] },
    fetchArchive: async url => { assert.match(url, new RegExp(`/download/${UV_VERSION}/`)); return bytes; },
    extractArchive: async (_archive, directory) => { await writeFile(join(directory, 'uv'), 'uv fixture'); },
    ...overrides,
  };
}

test('version requires no network or Python bootstrap', () => {
  const result = spawnSync(process.execPath, ['bin/codex-remote-bridge.mjs', '--version'], { encoding: 'utf8' });
  assert.equal(result.status, 0);
  assert.equal(result.stdout, '0.2.0\n');
  assert.equal(result.stderr, '');
});

test('all six requested platform archives are pinned', () => {
  assert.equal(Object.keys(ASSETS).length, 6);
  for (const [name, digest] of Object.values(ASSETS)) {
    assert.match(name, /\.(tar\.gz|zip)$/);
    assert.match(digest, /^[a-f0-9]{64}$/);
  }
});

test('checksum rejection prevents extraction and leaves no cached executable', async t => {
  const root = await temp(t);
  let extracted = false;
  await assert.rejects(ensureUv(root, mocked({ fetchArchive: async () => Buffer.from('tampered'), extractArchive: async () => { extracted = true; } })), /SHA256 mismatch/);
  assert.equal(extracted, false);
  const path = await ensureUv(root, mocked());
  assert.equal(await readFile(path, 'utf8'), 'uv fixture');
});

test('successful cache is reused without download', async t => {
  const root = await temp(t);
  const first = await ensureUv(root, mocked());
  const second = await ensureUv(root, mocked({ fetchArchive: () => { throw Error('must not download'); } }));
  assert.equal(first, second);
});

test('concurrent bootstrap callers safely use the same final executable', async t => {
  const root = await temp(t);
  const paths = await Promise.all(Array.from({ length: 8 }, () => ensureUv(root, mocked())));
  assert.equal(new Set(paths).size, 1);
  assert.equal(await readFile(paths[0], 'utf8'), 'uv fixture');
});

test('unsupported platforms fail before network use', async t => {
  await assert.rejects(ensureUv(await temp(t), mocked({ arch: 'ia32' })), /Unsupported platform/);
});

test('venv identity isolates editable projects and changes with version or lock', () => {
  const first = launchSpec('/cache/npm/a', '/private', '0.2.0', 'lock', []);
  const second = launchSpec('/cache/npm/b', '/private', '0.2.0', 'lock', []);
  assert.notEqual(first.env.UV_PROJECT_ENVIRONMENT, second.env.UV_PROJECT_ENVIRONMENT);
  assert.equal(first.env.UV_PROJECT_ENVIRONMENT, launchSpec('/cache/npm/a', '/private', '0.2.0', 'lock', []).env.UV_PROJECT_ENVIRONMENT);
  assert.notEqual(first.env.UV_PROJECT_ENVIRONMENT, launchSpec('/cache/npm/a', '/private', '0.2.1', 'lock', []).env.UV_PROJECT_ENVIRONMENT);
  assert.notEqual(first.env.UV_PROJECT_ENVIRONMENT, launchSpec('/cache/npm/a', '/private', '0.2.0', 'newlock', []).env.UV_PROJECT_ENVIRONMENT);
});

test('arguments remain opaque and Python downloads and cache cannot be redirected by inherited uv settings', () => {
  const args = ['--socket-path', 'x $(touch /tmp/pwn); with spaces', '--other', '`whoami`'];
  const spec = launchSpec('/npm path', '/private', '0.2.0', 'lock', args, { UV_PROJECT_ENVIRONMENT: '/wrong', UV_PYTHON_DOWNLOADS: 'never' });
  assert.deepEqual(spec.args.slice(-args.length), args);
  assert.ok(spec.args.includes('--frozen'));
  assert.ok(spec.args.includes('--managed-python'));
  assert.equal(spec.env.UV_PYTHON_DOWNLOADS, 'automatic');
  assert.notEqual(spec.env.UV_PROJECT_ENVIRONMENT, '/wrong');
});

test('platform cache roots follow supported OS conventions', () => {
  assert.equal(cacheRoot({ XDG_CACHE_HOME: '/xdg' }, 'linux'), join('/xdg', 'codex-remote-bridge'));
  assert.equal(cacheRoot({ LOCALAPPDATA: '/local' }, 'win32'), join('/local', 'codex-remote-bridge'));
});

 test('npm-style symlink bin invokes the launcher', async t => {
  if (process.platform === 'win32') return t.skip('npm uses cmd shims on Windows');
  const root = await temp(t);
  const link = join(root, 'codex-remote-bridge');
  await symlink(resolve('bin/codex-remote-bridge.mjs'), link);
  const result = spawnSync(process.execPath, [link, '--version'], { encoding: 'utf8' });
  assert.equal(result.status, 0);
  assert.equal(result.stdout, '0.2.0\n');
 });


test('Windows termination targets the uv process tree without a shell', () => {
  const killed = [];
  const child = { pid: 4321, exitCode: null, signalCode: null, kill: signal => killed.push(signal) };
  const killer = new EventEmitter();
  terminateChild(child, 'SIGTERM', 'win32', (command, args, options) => {
    assert.equal(command, 'taskkill.exe');
    assert.deepEqual(args, ['/PID', '4321', '/T', '/F']);
    assert.equal(options.shell, undefined);
    assert.equal(options.stdio, 'ignore');
    return killer;
  });
  killer.emit('exit', 0);
  assert.deepEqual(killed, []);
});

test('Windows termination falls back if taskkill cannot start', () => {
  const killed = [];
  const child = { pid: 4321, exitCode: null, signalCode: null, kill: signal => killed.push(signal) };
  const killer = new EventEmitter();
  terminateChild(child, 'SIGTERM', 'win32', () => killer);
  killer.emit('error', new Error('ENOENT'));
  assert.deepEqual(killed, ['SIGTERM']);
});

test('Unix termination forwards original signal; exited children are ignored', () => {
  const killed = [];
  const child = { pid: 4321, exitCode: null, signalCode: null, kill: signal => killed.push(signal) };
  terminateChild(child, 'SIGINT', 'linux');
  assert.deepEqual(killed, ['SIGINT']);
  child.exitCode = 0;
  terminateChild(child, 'SIGTERM', 'win32', () => { throw new Error('must not spawn'); });
  assert.deepEqual(killed, ['SIGINT']);
});
