// Runs Python for this project. Prefers the project's virtual environment (.venv, created by
// `npm run setup`), then whatever system Python exists (python3 on macOS/Linux, python or py on Windows).
// Set PYTHON=/path/to/python to force a specific interpreter.
import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const isWin = process.platform === 'win32';
const venv = join(root, '.venv', isWin ? 'Scripts/python.exe' : 'bin/python');

export function findSystemPython() {
  return ['python3', 'python', 'py'].find((cmd) => {
    const r = spawnSync(cmd, ['--version'], { encoding: 'utf8', shell: isWin });
    return r.status === 0 && /Python 3\.(1\d|[89])/.test(`${r.stdout}${r.stderr}`);
  });
}

function pick() {
  if (process.env.PYTHON) return process.env.PYTHON;
  if (existsSync(venv)) return venv;
  return findSystemPython();
}

const isMain = process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1];
if (isMain) {
  const python = pick();
  if (!python) {
    console.error('Python 3.10+ not found. Install it from https://python.org, or set PYTHON to its path.');
    process.exit(1);
  }
  const child = spawn(python, process.argv.slice(2), { stdio: 'inherit', shell: isWin && !python.endsWith('.exe') });
  for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => child.kill(sig));
  child.on('exit', (code, signal) => process.exit(signal ? 1 : code ?? 0));
}
