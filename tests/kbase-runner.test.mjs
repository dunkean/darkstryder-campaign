import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { mkdtemp, readFile, rm, writeFile, mkdir } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { decodeEvents, invokeCodex, jobPrompt, prepareJob, saveCitationRegistry, validateOutput } from '../tools/kbase/runner.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const schemaPath = path.join(root, 'tools/kbase/extraction.schema.json');

function extraction(jobId) {
  return {
    jobId,
    nodes: [{
      key: 'char-1', type: 'Character', family: 'WORLD', usage: 'darkstryder_scenario',
      name: 'A personnage', summary: 'Résumé en français.', content: 'Description en français.',
      tags: [], scope: 'source', visibility: 'gm_only', propertiesJSON: '{}', existingId: null,
      evidence: [{ unitId: 'p0002-u0000' }],
    }],
    relationships: [],
    segments: [],
    notes: [],
  };
}

test('strict output schema uses only closed objects and JSON text for arbitrary properties', async () => {
  const schema = JSON.parse(await readFile(schemaPath, 'utf8'));
  assert.equal(schema.additionalProperties, false);
  assert.ok(schema.required.includes('segments'));
  const node = schema.properties.nodes.items;
  assert.equal(node.additionalProperties, false);
  assert.equal(node.properties.propertiesJSON.type, 'string');
  assert.deepEqual(node.properties.existingId.type, ['string', 'null']);
  assert.equal(schema.properties.relationships.items.additionalProperties, false);
  assert.equal(schema.properties.segments.items.additionalProperties, false);
  assert.equal(schema.$defs.evidence.items.additionalProperties, false);
  assert.ok(schema.$defs.evidence.items.required.includes('unitId'));
  assert.deepEqual(schema.$defs.evidence.items.required, ['unitId']);
});

test('job prompt contains only one bounded excerpt plus compact existing-node context', () => {
  const prompt = jobPrompt({
    id: 'job-test', sourceId: 'src-1678b1eb8c4bd691', sourceName: 'Livre 1',
    collection: 'darkstryder', usage: 'darkstryder_scenario',
    pages: [{ page: 2, markdown: '[Page 2]\nA personnage agit.' }],
    existingNodes: [{ id: 'kb-existing', type: 'Character', name: 'A personnage', summary: 'Résumé', aliases: ['A'] }],
  });
  assert.match(prompt, /sourceId/);
  assert.match(prompt, /"id":"kb-existing"/);
  assert.match(prompt, /\[p0002-u0000\]/);
  assert.match(prompt, /A personnage agit/);
  assert.doesNotMatch(prompt, /undefined/);
  assert.match(prompt, /informative French synthesis of the cited paragraphs/);
  assert.match(prompt, /substantive Markdown/);
  assert.match(prompt, /do not copy the summary/);
  assert.match(prompt, /no minimum length/);
  assert.match(prompt, /only new facts/);
  assert.match(prompt, /editorial overlay/);
  assert.match(prompt, /sourceKey and targetKey must exactly match a key in this response’s nodes array/);
  assert.match(prompt, /existingId never substitutes for a local node key/);
});

test('output normalization injects an exact selected source unit and removes null existingId', () => {
  const prepared = prepareJob({
    id: 'job-test', sourceId: 'src-1678b1eb8c4bd691', pages: [{ page: 2, markdown: 'A personnage agit.' }],
  });
  const citationUnits = prepared.citationUnits;
  const result = validateOutput(JSON.stringify(extraction('job-test')), 'job-test', {
    citationUnits, sourceId: 'src-1678b1eb8c4bd691',
  });
  assert.deepEqual(result.nodes[0].properties, {});
  assert.equal(Object.hasOwn(result.nodes[0], 'propertiesJSON'), false);
  assert.equal(Object.hasOwn(result.nodes[0], 'existingId'), false);
  assert.deepEqual(result.nodes[0].evidence[0], {
    sourceId: 'src-1678b1eb8c4bd691', page: 2, quote: 'A personnage agit.', fields: ['name', 'summary', 'content'],
  });
  assert.throws(() => validateOutput('{"jobId":"wrong","nodes":[],"relationships":[],"segments":[],"notes":[]}', 'job-test', {
    citationUnits, sourceId: 'src-1678b1eb8c4bd691',
  }), /jobId mismatch/);
  const invalid = extraction('job-test');
  invalid.nodes[0].propertiesJSON = '[]';
  assert.throws(() => validateOutput(JSON.stringify(invalid), 'job-test', {
    citationUnits, sourceId: 'src-1678b1eb8c4bd691',
  }), /must encode an object/);
  invalid.nodes[0].propertiesJSON = '{}';
  invalid.nodes[0].evidence[0].unitId = 'p0002-u9999';
  assert.throws(() => validateOutput(JSON.stringify(invalid), 'job-test', {
    citationUnits, sourceId: 'src-1678b1eb8c4bd691',
  }), /Unknown citation unit/);
});

test('citation registry snapshots source units and hashes the replayable map', async () => {
  const runtime = await mkdtemp(path.join(os.tmpdir(), 'kbase-registry-test-'));
  await mkdir(path.join(runtime, 'outputs'));
  const job = { id: 'job-test', sourceId: 'src-1678b1eb8c4bd691' };
  const prepared = prepareJob({ ...job, pages: [{ page: 2, markdown: 'Passage source.' }] });
  try {
    const saved = await saveCitationRegistry(runtime, job, 2, prepared.citationUnits);
    const registry = JSON.parse(await readFile(path.join(runtime, 'outputs', saved.filename), 'utf8'));
    assert.equal(registry.protocolVersion, 'unit-v2-object-scope');
    assert.equal(registry.jobId, 'job-test');
    assert.equal(registry.units[0].quote, 'Passage source.');
    assert.equal(saved.sha256.length, 64);
  } finally {
    await rm(runtime, { recursive: true, force: true });
  }
});

test('long OCR lines split into bounded, unchanged citation units tied to one page', () => {
  const source = 'mot '.repeat(400).trim();
  const prepared = prepareJob({
    id: 'job-split', sourceId: 'src-1678b1eb8c4bd691',
    pages: [{ page: 9, markdown: source }],
  });
  const quotes = [...prepared.citationUnits.values()].map((unit) => unit.quote);
  assert.ok(quotes.length > 1);
  assert.ok(quotes.every((quote) => quote.length <= 1200));
  assert.equal(quotes.join(''), source);
  assert.ok([...prepared.citationUnits.values()].every((unit) => unit.page === 9));
});

test('Codex event parsing captures final message, usage, and requested model', () => {
  const parsed = decodeEvents([
    JSON.stringify({ type: 'turn.started', model: 'gpt-6-luna' }),
    JSON.stringify({ type: 'item.completed', item: { type: 'agent_message', text: '{"ok":true}' } }),
    JSON.stringify({ type: 'turn.completed', model: 'gpt-6-luna', usage: { input_tokens: 18, output_tokens: 7 } }),
  ]);
  assert.equal(parsed.finalText, '{"ok":true}');
  assert.equal(parsed.reportedModels.at(-1), 'gpt-6-luna');
  assert.deepEqual(parsed.usage, { input_tokens: 18, output_tokens: 7 });
});

test('Codex process is invoked with Luna, low reasoning, a schema, and no shell tools', async () => {
  const runtime = await mkdtemp(path.join(os.tmpdir(), 'kbase-runner-test-'));
  const workdir = path.join(runtime, 'agent-workdir');
  await mkdir(path.join(runtime, 'logs'));
  await mkdir(workdir);
  const outputPath = path.join(runtime, 'output.json');
  const logPath = path.join(runtime, 'logs', 'job.jsonl');
  const response = JSON.stringify(extraction('job-test'));
  let command;
  let args;
  const spawnImpl = (cmd, argv, options) => {
    command = cmd;
    args = argv;
    assert.equal(options.cwd, workdir);
    const child = new EventEmitter();
    child.stdin = new PassThrough();
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    child.kill = () => true;
    child.stdin.on('finish', async () => {
      await writeFile(outputPath, response);
      child.stdout.end([
        JSON.stringify({ type: 'turn.started', model: 'gpt-6-luna' }),
        JSON.stringify({ type: 'item.completed', item: { type: 'agent_message', text: response } }),
        JSON.stringify({ type: 'turn.completed', model: 'gpt-6-luna', usage: { input_tokens: 20, output_tokens: 9 } }),
      ].join('\n') + '\n');
      child.stderr.end();
      setImmediate(() => child.emit('close', 0, null));
    });
    return child;
  };
  try {
    const result = await invokeCodex('prompt text', { outputPath, logPath, runtime, timeoutMs: 2000, spawnImpl });
    assert.equal(command, '/usr/bin/codex');
    assert.ok(args.includes('gpt-6-luna'));
    assert.ok(args.includes('model_reasoning_effort="low"'));
    assert.ok(args.includes(schemaPath));
    for (const feature of ['shell_tool', 'browser_use', 'computer_use', 'apps', 'code_mode']) {
      assert.ok(args.includes(feature));
    }
    assert.equal(result.actualModel, 'gpt-6-luna');
    assert.equal(result.usage.input_tokens, 20);
    assert.equal(JSON.parse(await readFile(outputPath, 'utf8')).jobId, 'job-test');
  } finally {
    await rm(runtime, { recursive: true, force: true });
  }
});

test('quota text terminates the child and fails the job call without a retry', async () => {
  const runtime = await mkdtemp(path.join(os.tmpdir(), 'kbase-runner-quota-'));
  const workdir = path.join(runtime, 'agent-workdir');
  await mkdir(path.join(runtime, 'logs'));
  await mkdir(workdir);
  let killSignal;
  let spawnCount = 0;
  const spawnImpl = () => {
    spawnCount += 1;
    const child = new EventEmitter();
    child.stdin = new PassThrough();
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    child.kill = (signal) => {
      killSignal = signal;
      setImmediate(() => child.emit('close', 1, signal));
      return true;
    };
    child.stdin.on('finish', () => child.stderr.write('usage limit reached'));
    return child;
  };
  try {
    await assert.rejects(
      invokeCodex('prompt', {
        outputPath: path.join(runtime, 'out.json'),
        logPath: path.join(runtime, 'logs', 'job.jsonl'),
        runtime,
        timeoutMs: 2000,
        spawnImpl,
      }),
      /usage limit reached/,
    );
    assert.equal(killSignal, 'SIGTERM');
    assert.equal(spawnCount, 1);
  } finally {
    await rm(runtime, { recursive: true, force: true });
  }
});
