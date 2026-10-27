import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

for (const fixture of ['sw_semantic.py', 'sw_resolution.py', 'sw_kbase.py', 'sw_service.py', 'sw_review.py', 'sw_review_workspace.py', 'sw_type_mining.py', 'sw_provenance.py', 'sw_rechunk.py', 'sw_grouping.py', 'sw_grouped_pipeline.py', 'sw_cleaning.py', 'sw_web.py', 'sw_disambiguation.py', 'sw_native_jev.py', 'sw_joint_jev.py', 'sw_perplexity_jev.py', 'sw_anthropic_extraction.py', 'sw_document_curation.py', 'sw_curation_profiles.py', 'sw_curation_store.py', 'sw_paragraph_curation.py', 'sw_enrichment.py', 'sw_grouped_paragraphs.py', 'sw_gpu_priority.py']) {
  test(`unified SW knowledge: ${fixture}`, () => {
    const result = spawnSync(process.execPath, ['tools/python.mjs', '-m', 'unittest', 'discover', '-s', 'tests', '-p', fixture], {
      cwd: fileURLToPath(new URL('../', import.meta.url)), encoding: 'utf8', timeout: 60000,
    });
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.match(result.stderr, /Ran \d+ tests/);
    assert.match(result.stderr, /\bOK\b/);
  });
}
