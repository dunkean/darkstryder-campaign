import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from 'jsdom';

const script = await readFile(new URL('../packages/sw_kbase/anthropic_compare.js', import.meta.url), 'utf8');
test('input/output inspector changes document and preserves the exact raw reply when toggling', () => {
  const dom = new JSDOM('<form><select id="article-select"><option>a</option><option>b</option></select></form><button id="toggle-raw">Voir le brut exact</button><pre id="response-readable">formatted</pre><pre id="response-raw" hidden>```json\n{"sections":[]}\n```</pre>', {runScripts:'outside-only'});
  try {
    const {window} = dom;
    let submissions = 0;
    window.document.querySelector('form').requestSubmit = () => submissions++;
    window.eval(script);
    const selector = window.document.querySelector('select');
    selector.selectedIndex = 1; selector.dispatchEvent(new window.Event('change'));
    assert.equal(submissions, 1);
    const button = window.document.getElementById('toggle-raw');
    button.click();
    assert.equal(window.document.getElementById('response-raw').hidden, false);
    assert.equal(window.document.getElementById('response-readable').hidden, true);
    assert.equal(window.document.getElementById('response-raw').textContent, '```json\n{"sections":[]}\n```');
    button.click();
    assert.equal(window.document.getElementById('response-raw').hidden, true);
    assert.equal(window.document.getElementById('response-readable').hidden, false);
  } finally { dom.window.close(); }
});
