// Creates .venv (if missing) and installs the API and pipeline Python packages into it.
// A project-local environment avoids "externally managed environment" errors on
// Homebrew and Linux Pythons, and keeps this project's packages separate from others.
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { findSystemPython } from './py.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const isWin = process.platform === 'win32';
const venvPython = join(root, '.venv', isWin ? 'Scripts/python.exe' : 'bin/python');
const run = (cmd, args) => {
  const r = spawnSync(cmd, args, { stdio: 'inherit', cwd: root, shell: isWin && !cmd.endsWith('.exe') });
  if (r.status !== 0) process.exit(r.status ?? 1);
};

if (!existsSync(venvPython)) {
  const sys = process.env.PYTHON || findSystemPython();
  if (!sys) {
    console.error('Python 3.10+ not found. Install it from https://python.org (on Windows, tick "Add to PATH").');
    process.exit(1);
  }
  console.log(`Creating .venv with ${sys}`);
  run(sys, ['-m', 'venv', '.venv']);
}
run(venvPython, ['-m', 'pip', 'install', '--upgrade', 'pip', '--quiet']);
run(venvPython, ['-m', 'pip', 'install', '-r', 'api/requirements.txt', '-r', 'pipeline/python/requirements.txt']);
console.log('Python packages installed in .venv');
