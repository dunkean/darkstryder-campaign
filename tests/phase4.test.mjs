import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

test('phase 4 offline regression fixtures: provenance, identity, resume and confinement', () => {
  const root = fileURLToPath(new URL('../', import.meta.url));
  const result = spawnSync(process.execPath, ['tools/python.mjs', 'tests/phase4_pipeline.py'], {
    cwd: root,
    encoding: 'utf8',
    timeout: 60000,
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stderr, /Ran \d+ tests/);
  assert.match(result.stderr, /\bOK\b/);
});
