#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { access, chmod, mkdir, mkdtemp, readFile, realpath, rename, rm, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const UV_VERSION = '0.12.13';
export const ASSETS = {
  'linux-x64': ['x86_64-unknown-linux-gnu.tar.gz', '745765a3b6e360ad76743599ae5c42e9278c7edf8bbff9fc76d05bf2623a04dd'],
  'linux-arm64': ['aarch64-unknown-linux-gnu.tar.gz', '2eaa5d94f5db7b3a1a092156b9420459e42ab0217d917fe74a876309cef9b5e9'],
  'darwin-x64': ['x86_64-apple-darwin.tar.gz', '5e287ef61cb6a9b61b3a83fef124fd143e400468a7dac794230147a810e17119'],
  'darwin-arm64': ['aarch64-apple-darwin.tar.gz', '7e6ddb9316acc00f2296c82ff4d99977870ee34b2f0ddcae9444d714db9364ed'],
  'win32-x64': ['x86_64-pc-windows-msvc.zip', 'a86c9dc7bad9b03f388583b7187c05fe9951c2e0d392217e8fd43d97787f6ec2'],
  'win32-arm64': ['aarch64-pc-windows-msvc.zip', '1efb2654b06e7063d4ac1fc9d49a9bda9a6704d82f035b589a2751a592f14151'],
};
const sha256 = (data) => createHash('sha256').update(data).digest('hex');
const exists = async (path) => access(path).then(() => true, () => false);

export function cacheRoot(env = process.env, platform = process.platform) {
  const base = platform === 'win32' ? (env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local'))
    : platform === 'darwin' ? join(homedir(), 'Library', 'Caches')
      : (env.XDG_CACHE_HOME || join(homedir(), '.cache'));
  return join(base, 'codex-remote-bridge');
}

export async function download(url) {
  const response = await fetch(url, { signal: AbortSignal.timeout(120_000) });
  if (!response.ok) throw new Error(`uv download HTTP ${response.status}`);
  const chunks = [];
  let size = 0;
  for await (const chunk of response.body) {
    size += chunk.length;
    if (size > 100 * 1024 * 1024) throw new Error('uv archive exceeds 100 MiB');
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

function runChecked(command, args, options = {}) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, args, { stdio: ['ignore', 'ignore', 'inherit'], ...options });
    child.once('error', reject);
    child.once('exit', (code, signal) => code === 0 ? resolvePromise() : reject(new Error(`${command} failed (${signal || code})`)));
  });
}

export async function extract(archive, directory, platform) {
  if (platform === 'win32') {
    const script = "$ProgressPreference='SilentlyContinue'; Expand-Archive -LiteralPath $env:CODEX_BRIDGE_ARCHIVE -DestinationPath $env:CODEX_BRIDGE_EXTRACT -Force";
    await runChecked('powershell.exe', ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')], {
      env: { ...process.env, CODEX_BRIDGE_ARCHIVE: archive, CODEX_BRIDGE_EXTRACT: directory },
    });
  } else {
    await runChecked('tar', ['-xzf', archive, '-C', directory, '--strip-components=1']);
  }
}

export async function ensureUv(root, {
  platform = process.platform, arch = process.arch, fetchArchive = download, extractArchive = extract,
  assets = ASSETS,
} = {}) {
  const asset = assets[`${platform}-${arch}`];
  if (!asset) throw new Error(`Unsupported platform ${platform}/${arch}; supported: ${Object.keys(ASSETS).join(', ')}`);
  const destination = join(root, `uv-${UV_VERSION}-${platform}-${arch}`);
  const executable = join(destination, platform === 'win32' ? 'uv.exe' : 'uv');
  if (await exists(executable)) return executable;
  await mkdir(root, { recursive: true, mode: 0o700 });
  const temporary = await mkdtemp(join(root, '.uv-install-'));
  try {
    process.stderr.write(`codex-remote-bridge: preparing uv ${UV_VERSION}\n`);
    const bytes = await fetchArchive(`https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${asset[0]}`);
    if (sha256(bytes) !== asset[1]) throw new Error('uv archive SHA256 mismatch');
    const archive = join(temporary, platform === 'win32' ? 'archive.zip' : 'archive.tar.gz');
    await writeFile(archive, bytes, { mode: 0o600 });
    const unpacked = join(temporary, 'unpacked');
    await mkdir(unpacked, { mode: 0o700 });
    await extractArchive(archive, unpacked, platform);
    const unpackedExecutable = join(unpacked, platform === 'win32' ? 'uv.exe' : 'uv');
    await access(unpackedExecutable);
    if (platform !== 'win32') await chmod(unpackedExecutable, 0o700);
    // Each caller stages privately; the first atomic rename wins without a stale lock.
    try { await rename(unpacked, destination); }
    catch (error) { if (!(await exists(executable))) throw error; }
    return executable;
  } finally { await rm(temporary, { recursive: true, force: true }); }
}

export function launchSpec(project, root, version, lock, args, env = process.env) {
  const identity = `${version}-${sha256(lock).slice(0, 16)}-${sha256(project).slice(0, 16)}`;
  return {
    args: ['run', '--project', project, '--frozen', '--no-dev', '--python', '3.13', '--managed-python', '--', 'codex-remote-bridge', ...args],
    env: {
      ...env,
      UV_PROJECT_ENVIRONMENT: join(root, 'environments', identity),
      UV_PYTHON_INSTALL_DIR: join(root, 'python'),
      UV_PYTHON_DOWNLOADS: 'automatic',
      UV_NO_PROGRESS: '1',
    },
  };
}

export function terminateChild(child, signal, platform = process.platform, spawnCommand = spawn) {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
  if (platform !== 'win32') {
    child.kill(signal);
    return;
  }
  // Killing only uv on Windows can leave its Python MCP child running.
  const killer = spawnCommand('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], {
    stdio: 'ignore', windowsHide: true,
  });
  killer.once('error', () => child.kill(signal));
  killer.once('exit', code => { if (code !== 0) child.kill(signal); });
}

export async function main(args = process.argv.slice(2)) {
  const [major, minor] = process.versions.node.split('.').map(Number);
  if (major < 18 || (major === 18 && minor < 19)) throw new Error('Node.js 18.19 or later is required');
  const project = await realpath(resolve(dirname(fileURLToPath(import.meta.url)), '..'));
  const metadata = JSON.parse(await readFile(join(project, 'package.json'), 'utf8'));
  if (args.length === 1 && args[0] === '--version') {
    process.stdout.write(`${metadata.version}\n`);
    return;
  }
  const root = cacheRoot();
  const uv = await ensureUv(root);
  const spec = launchSpec(project, root, metadata.version, await readFile(join(project, 'uv.lock')), args);
  const child = spawn(uv, spec.args, { env: spec.env, stdio: 'inherit' });
  const handlers = new Map(['SIGINT', 'SIGTERM', 'SIGHUP'].map(signal => [signal, () => terminateChild(child, signal)]));
  for (const [signal, handler] of handlers) process.on(signal, handler);
  try {
    await new Promise((resolvePromise, reject) => {
      child.once('error', reject);
      child.once('exit', (code, signal) => {
        process.exitCode = code ?? ({ SIGINT: 130, SIGTERM: 143, SIGHUP: 129 }[signal] || 1);
        resolvePromise();
      });
    });
  } finally {
    for (const [signal, handler] of handlers) process.off(signal, handler);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(await realpath(resolve(process.argv[1]))).href) {
  main().catch(error => { process.stderr.write(`codex-remote-bridge: ${error.message}\n`); process.exitCode = 1; });
}
