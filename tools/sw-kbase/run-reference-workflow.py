"""Resume native questions, then start all-source reference/paragraph selection.

Explicit user-run orchestration only. The 5090 is yielded to foreign GPU work or
a local request marker. No Strata writer, embedding, scraping or deployment runs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages'))
from sw_kbase.enrichment import atomic_json
from sw_kbase.gpu_priority import Windows5090Probe, owned_server_pids, descendants, yield_reason, PriorityConfirmation, GPU_UUID
from sw_kbase.store import Store, process_lock
from sw_kbase.native_jev import NativeJevClient
from sw_kbase import enrichment, grouped_paragraphs


def utc():
    return datetime.now(timezone.utc).isoformat()


class WorkflowPaused(RuntimeError):
    pass


class Workflow:
    def __init__(self, args):
        self.args = args; self.root = Path(__file__).resolve().parents[2]
        self.output = args.output; self.runtime = args.jev_runtime
        self.pause = args.database.parent / 'openjev.pause'
        self.request = self.runtime / 'gpu5090.request'
        self.stop = self.output / 'stop'
        self.stage = 'initializing'; self.child = None; self.server_owned = False
        self.server_pid = None; self.owned_roots = set()
        self.baseline = None; self.pending = None; self.finished = threading.Event()
        self._state_lock = threading.Lock(); self.probe = Windows5090Probe()
        self.confirmation = PriorityConfirmation()
        self.env = os.environ.copy(); self.env['PYTHONIOENCODING'] = 'utf-8'; self.env['PYTHONUNBUFFERED'] = '1'

    def write(self, stage=None, **extra):
        with self._state_lock:
            if stage:
                self.stage = stage
            state = {'utc': utc(), 'pid': os.getpid(), 'stage': self.stage, 'gpu_uuid': GPU_UUID,
                     'run_id': self.args.run_id, 'enrichment_output': str(self.args.enrichment_output),
                     'child_pid': self.child.pid if self.child and self.child.poll() is None else None,
                     'priority_yield': self.pending, 'strata_started': False, **extra}
            atomic_json(self.output / 'workflow.json', state)

    def check(self):
        if self.request.exists():
            self.set_pending({'reason': 'gpu_request_marker'})
        if self.pending:
            raise WorkflowPaused(self.pending['reason'])
        if self.stop.exists():
            self.set_pending({'reason': 'workflow_stop_requested'})
            raise WorkflowPaused('workflow_stop_requested')

    def set_pending(self, reason):
        with self._state_lock:
            if self.pending:
                return
            self.pending = {**reason, 'utc': utc()}
            self.pause.write_text('gpu_priority_yield\n', encoding='utf-8')
            atomic_json(self.output / 'yield.json', self.pending)

    def guard(self):
        while not self.finished.is_set():
            try:
                if self.request.exists() or self.stop.exists():
                    self.set_pending({'reason': 'gpu_request_marker' if self.request.exists() else 'workflow_stop_requested'})
                elif not self.pending:
                    snapshot = self.probe.read()
                    owned = set()
                    for root_pid in self.owned_roots:
                        owner = next((p for p in snapshot['process_tree'] if p['pid'] == root_pid), None)
                        if owner and (not owner.get('path') or Path(owner['path']).resolve() != self.runtime.resolve() / 'install/bin/ollaya.exe'):
                            continue  # A recycled PID must not exempt a foreign process.
                        owned.update(descendants(snapshot['process_tree'], root_pid))
                    if self.server_owned and self.server_pid:
                        owned_server_pids(snapshot, self.runtime)  # Validate current PID/executable binding.
                    reason = yield_reason(snapshot, owned, baseline_mib=self.baseline)
                    confirmed = self.confirmation.consider(reason)
                    if confirmed:
                        self.set_pending(confirmed)
                    atomic_json(self.output / 'gpu-priority.json', {'utc': utc(),
                        'gpu_uuid': GPU_UUID, 'adapter_luid': snapshot['adapter_luid'],
                        'used_mib': snapshot['used_mib'], 'free_mib': snapshot['free_mib'],
                        'owned_pids': sorted(owned), 'yield': self.pending})
                if self.pending:
                    # The pause marker stops the next inference; free VRAM even
                    # if the caller is still doing a CPU hash/snapshot operation.
                    self.finished.wait(1)
                    self.stop_server()
                    self.write('paused')
                    return
            except Exception as error:
                self.set_pending({'reason': 'gpu_observer_failed', 'error': str(error)})
                try:
                    self.stop_server()
                finally:
                    self.write('paused')
                return
            self.finished.wait(2)

    def ps(self, script, name):
        with (self.output / (name + '.log')).open('a', encoding='utf-8') as log:
            return subprocess.run(['pwsh.exe', '-NoProfile', '-NonInteractive', '-File', str(script)],
                stdout=log, stderr=subprocess.STDOUT, env=self.env, timeout=60,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).returncode

    def stop_server(self):
        if not self.server_owned or self.server_pid is None:
            return
        if int((self.runtime / 'server.pid').read_text().strip()) != self.server_pid:
            raise RuntimeError('Server PID file changed; refusing to stop a different process')
        code = self.ps(self.runtime / 'stop-server.ps1', 'server-stop')
        if code:
            raise RuntimeError('Owned Ollaya shutdown failed; inspect server-stop.log')
        self.server_owned = False; self.baseline = None

    def warm_server(self, model='jeb-local:27b-v2.1-q8_0', base_url='http://127.0.0.1:11437'):
        self.check()
        snapshot = self.probe.read()
        reason = yield_reason(snapshot, require_free=True)
        if reason:
            self.set_pending(reason); self.check()
        # Record old logs before the unchanged, hash-pinned launcher replaces them.
        prefix = str(time.time_ns())
        for name in ('server.err.log', 'server.out.log'):
            if (self.runtime / name).exists():
                shutil.copyfile(self.runtime / name, self.output / (prefix + '-' + name))
        self.write('loading_5090_model')
        self.server_owned = True
        if self.ps(self.runtime / 'start-server.ps1', 'server-start'):
            self.server_owned = False
            raise RuntimeError('Isolated Ollaya start failed; inspect server-start.log')
        self.server_pid = int((self.runtime / 'server.pid').read_text().strip())
        self.owned_roots.add(self.server_pid)
        self.check()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        payload = {'model': model, 'state': 'The control light is green.',
            'questions': {'warmup': {'type': 'choice', 'instructions': 'Is the control light green?',
                'criteria': {'yes': 'It is green.', 'no': 'It is another color.', 'unknown': 'The color is missing.'}}},
            'keep_alive': -1}
        for attempt in range(10):
            self.check()
            try:
                request = urllib.request.Request(base_url.rstrip('/') + '/api/decide',
                    data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
                with opener.open(request, timeout=120) as response:
                    result = json.load(response)
                if result.get('model') != payload['model'] or result.get('state_truncated') is not False:
                    raise RuntimeError('Native warmup returned an unexpected model/truncation flag')
                break
            except urllib.error.URLError:
                self.check()
                if attempt == 9:
                    raise
                self.finished.wait(1)
        self.check()
        if (self.runtime / 'warmup-max.json').exists():
            # Clef's CUDA arena grows with sequence shape. Reserve the tested
            # maximum before establishing the foreign-memory baseline.
            maximum = json.loads((self.runtime / 'warmup-max.json').read_text(encoding='utf-8'))
            if maximum.get('model') != model:
                raise RuntimeError('Max-context warmup model mismatch')
            request = urllib.request.Request(base_url.rstrip('/') + '/api/decide',
                data=json.dumps(maximum).encode(), headers={'Content-Type': 'application/json'})
            with opener.open(request, timeout=120) as response:
                warmed = json.load(response)
            if (warmed.get('model') != model or warmed.get('state_truncated') is not False
                    or warmed.get('usage', {}).get('input_tokens') != 4096):
                raise RuntimeError('Max-context warmup model/truncation mismatch')
            self.check()
        sampled = self.probe.read()
        reason = yield_reason(sampled, owned_server_pids(sampled, self.runtime))
        if sampled['used_mib'] > 28500:
            reason = {'reason': 'unexpected_memory_during_model_load', 'used_mib': sampled['used_mib']}
        if reason:
            self.set_pending(reason); self.check()
        self.baseline = sampled['used_mib']
        self.write(model_baseline_mib=self.baseline)

    def run_child(self, arguments, name):
        self.check()
        with (self.output / (name + '.log')).open('a', encoding='utf-8') as log:
            self.child = subprocess.Popen([sys.executable, *map(str, arguments)], cwd=self.root,
                stdout=log, stderr=subprocess.STDOUT, env=self.env,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.write()
            while self.child.poll() is None:
                # Independent guard unloads the server on priority contention.
                time.sleep(.5)
            code = self.child.returncode; self.child = None
        self.check()
        if code:
            raise RuntimeError(f'{name} failed with exit code {code}; see {name}.log')

    def run(self):
        self.output.mkdir(parents=True, exist_ok=True)
        with process_lock(self.runtime / 'reference-workflow-lock'):
            self.write('checking_checkpoint')
            # Starting a new controller is explicitly authorized by the user.
            if self.pause.exists():
                shutil.copyfile(self.pause, self.output / 'previous-pause.txt')
                self.pause.unlink()
            thread = threading.Thread(target=self.guard, daemon=True); thread.start()
            try:
                native = self.args.native_run_output if self.args.selection_only else self.output / 'native-resume'
                if not self.args.selection_only:
                    self.warm_server()
                    self.write('finishing_document_questions')
                    self.run_child([self.root / 'tools/sw-kbase/run-native-monitored.py',
                        '--database', self.args.database, '--jev-runtime', self.runtime,
                        '--run-id', self.args.run_id, '--sample', '0', '--output', native], 'native-resume')
                    progress = json.loads((native / 'progress.json').read_text())
                    if progress['stage'] != 'complete':
                        raise WorkflowPaused('Native worker did not finish its scope')
                    # The native worker restores this marker after successful exit.
                    if self.pause.exists():
                        if self.pause.read_bytes() != b'monitored_native_run_finished\n':
                            raise WorkflowPaused('An external pause superseded the phase transition')
                        self.pause.unlink()
                self.stop_server()
                self.write('auditing_document_questions')
                self.run_child([self.root / 'tools/sw-kbase/audit-native-run.py', '--database', self.args.database,
                    '--run-id', self.args.run_id, '--run-output', native, '--output', self.output / 'document-final-audit.json',
                    '--verify-snapshots'], 'document-final-audit')
                self.write('selecting_references_cpu')
                if not (self.args.enrichment_output / 'enrichment.sqlite').exists():
                    self.run_child([self.root / 'tools/sw-kbase/enrich.py', '--database', self.args.database,
                        '--output', self.args.enrichment_output, 'plan', '--config', self.args.config], 'enrichment-plan')
                if not self.args.joint_runtime or not (self.args.joint_runtime / 'joint-runtime.json').exists():
                    self.write('awaiting_joint_paragraph_backend', message='Grouped paragraph selection requires the prepared joint backend; serial fallback is disabled')
                    return
                self.runtime = self.args.joint_runtime
                joint_settings = json.loads((self.runtime / 'joint-runtime.json').read_text(encoding='utf-8-sig'))
                self.warm_server(joint_settings['model'], joint_settings['base_url'])
                self.write('selecting_paragraphs_jev')
                from sw_kbase.joint_jev import JointJevClient
                client = JointJevClient.from_runtime(self.runtime)
                monitored = EnrichmentMonitor(client, self.args.enrichment_output, self.check)
                with process_lock(self.args.enrichment_output):
                    main = Store(self.args.database, readonly=True)
                    try:
                        result = grouped_paragraphs.score(main, self.args.enrichment_output, monitored)
                    finally:
                        main.close()
                self.check()
                atomic_json(self.output / 'enrichment-final.json', result)
                self.write('complete', result=result)
            except WorkflowPaused as error:
                if not self.pause.exists():
                    self.pause.write_text('reference_workflow_paused\n', encoding='utf-8')
                self.write('paused', message=str(error))
            except Exception as error:
                if self.pending or (isinstance(error, ValueError) and str(error).startswith('Enrichment paused') and self.pause.exists()):
                    self.write('paused', message=str(error))
                else:
                    self.write('error', error=str(error)); raise
            finally:
                self.finished.set(); thread.join(timeout=20)
                self.stop_server()
                if not self.pause.exists():
                    self.pause.write_text('reference_workflow_stopped\n', encoding='utf-8')


class EnrichmentMonitor:
    def __init__(self, client, output, check=None):
        self.client = client; self.output = output; self.model = client.model
        self.capabilities = getattr(client, 'capabilities', {})
        self.count = 0; self.started = time.monotonic(); self.tasks = Counter()
        self.execution = str(time.time_ns())
        self.check = check or (lambda: None)
    @property
    def identity(self):
        return self.client.identity
    def validate_runtime(self):
        return self.client.validate_runtime()
    def preflight(self, request):
        return self.client.preflight(request)
    def __call__(self, request):
        if self.count == 100 and not (self.output / 'continue-after-100').exists():
            atomic_json(self.output / 'first-100.json', {'utc': utc(), 'native_calls': self.count,
                'execution': self.execution, 'elapsed_seconds': time.monotonic()-self.started,
                'status': 'waiting_for_sample_check'})
            while not (self.output / 'continue-after-100').exists():
                self.check()
                time.sleep(.5)
        self.check()
        started = time.monotonic(); response = self.client(request)
        self.count += int(not response.get('deferred_reason'))
        state = request['state']; task = state.get('evaluation', {}).get('stage', 'joint_paragraphs'); self.tasks[task] += 1
        row = {'utc': utc(), 'execution': self.execution, 'sequence': self.count, 'task': task,
            'entity_id': state['ENTITY']['id'], 'source_id': state['SOURCE']['id'],
            'paragraph': state['SOURCE'].get('paragraph'), 'latency_ms': 1000*(time.monotonic()-started),
            'paragraph_scores': len(response.get('paragraphs', {})),
            'question_tokens': response.get('_native_jev', {}).get('question_tokens'),
            'joint_preflight': response.get('_native_jev', {}).get('preflight'),
            'deferred_reason': response.get('deferred_reason')}
        with (self.output / 'inferences.jsonl').open('a', encoding='utf-8') as log:
            log.write(json.dumps(row) + '\n')
        if self.count % 10 == 0 or self.count <= 3:
            atomic_json(self.output / 'live-progress.json', {'utc': utc(), 'stage': 'scoring',
                'execution': self.execution, 'native_calls': self.count, 'tasks': dict(self.tasks),
                'elapsed_seconds': time.monotonic()-self.started, 'last': row})
        return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--jev-runtime', type=Path, required=True)
    parser.add_argument('--joint-runtime', type=Path)
    parser.add_argument('--selection-only', action='store_true', help='Skip Jeb loading; require the complete document audit before selection')
    parser.add_argument('--native-run-output', type=Path, help='Original completed native execution logs for selection-only audit')
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--enrichment-output', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    if args.selection_only and (not args.native_run_output or not (args.native_run_output / 'started.json').is_file()):
        parser.error('Selection-only requires --native-run-output pointing to the completed execution logs')
    if (args.output / 'workflow.json').exists():
        parser.error('Use a fresh controller output; source/paragraph caches can be resumed separately')
    Workflow(args).run()


if __name__ == '__main__':
    main()
