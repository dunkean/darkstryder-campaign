import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
let config = {};
if (existsSync(path.join(root, 'config.local.json'))) {
  config = JSON.parse(readFileSync(path.join(root, 'config.local.json'), 'utf8').replace(/^\uFEFF/, ''));
}
const local = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const python = process.env.DARKSTRYDER_PYTHON || config.pythonExecutable || (existsSync(local) ? local : process.platform === 'win32' ? 'python' : 'python3');
const result = spawnSync(python, process.argv.slice(2), { cwd: root, stdio: 'inherit' });
if (result.error) console.error(`Python indisponible (${python}): ${result.error.message}`);
process.exit(result.status ?? 1);
