"""Read-only Windows/WSL 5090 probes and conservative yielding policy.

No GPU is initialized by this module. DXGI identifies the actual Windows adapter
LUID; Windows process counters and WSL nvidia-smi then observe only that adapter.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

GPU_UUID = 'GPU-d7f6652e-9fa3-6b5f-024a-e6bc0338dc52'
GPU_NAME = 'NVIDIA GeForce RTX 5090'
DESKTOP = {'dwm.exe', 'csrss.exe', 'system', 'idle', 'lockapp.exe', 'explorer.exe',
           'shellexperiencehost.exe', 'startmenuexperiencehost.exe', 'searchhost.exe',
           'textinputhost.exe', 'windowsterminal.exe', 'taskmgr.exe'}


def adapter_luid(name=GPU_NAME):
    """Enumerate DXGI adapters without creating a device/context or changing settings."""
    if not hasattr(ctypes, 'WINFUNCTYPE'):
        raise RuntimeError('The GPU priority observer requires Windows')
    class Luid(ctypes.Structure):
        _fields_ = [('low', ctypes.c_uint32), ('high', ctypes.c_int32)]
    class Desc(ctypes.Structure):
        _fields_ = [('description', ctypes.c_wchar * 128), ('vendor', ctypes.c_uint32),
                    ('device', ctypes.c_uint32), ('subsystem', ctypes.c_uint32), ('revision', ctypes.c_uint32),
                    ('video', ctypes.c_size_t), ('system', ctypes.c_size_t), ('shared', ctypes.c_size_t),
                    ('luid', Luid), ('flags', ctypes.c_uint32)]
    def method(pointer, index, result, *arguments):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *arguments)(table[index])
    factory = ctypes.c_void_p()
    iid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID('770aae78-f26f-4dba-a829-253c83d1b387').bytes_le)
    create = ctypes.WinDLL('dxgi').CreateDXGIFactory1
    create.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]; create.restype = ctypes.c_long
    if create(ctypes.byref(iid), ctypes.byref(factory)) != 0:
        raise RuntimeError('Cannot inspect DXGI adapters')
    found = []
    try:
        enumerate_ = method(factory, 12, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))
        for index in range(32):
            adapter = ctypes.c_void_p()
            if enumerate_(factory, index, ctypes.byref(adapter)) != 0:
                break
            try:
                desc = Desc()
                if method(adapter, 10, ctypes.c_long, ctypes.POINTER(Desc))(adapter, ctypes.byref(desc)) != 0:
                    raise RuntimeError('Cannot inspect DXGI adapter description')
                if desc.description == name:
                    found.append(f'luid_0x{desc.luid.high & 0xffffffff:08x}_0x{desc.luid.low:08x}_')
            finally:
                method(adapter, 2, ctypes.c_ulong)(adapter)
    finally:
        method(factory, 2, ctypes.c_ulong)(factory)
    if len(found) != 1:
        raise RuntimeError('Authorized 5090 must match exactly one Windows adapter')
    return found[0]


def descendants(processes, root_pid):
    result = {root_pid} if root_pid else set()
    while True:
        added = {int(p['pid']) for p in processes if int(p.get('parent_pid') or 0) in result}
        if added <= result:
            return result
        result.update(added)


def yield_reason(snapshot, owned_pids=(), *, require_free=False, baseline_mib=None):
    """Foreign compute/VRAM wins. Never acts on or stops the foreign process."""
    owned = set(owned_pids)
    for row in snapshot['processes']:
        if int(row['pid']) in owned or row['name'].casefold() in DESKTOP:
            continue
        if row.get('dedicated_bytes', 0) >= 64 * 1024 * 1024 or row.get('engine_percent', 0) >= 1:
            return {'reason': 'foreign_5090_process', 'pid': row['pid'], 'name': row['name'],
                    'dedicated_mib': round(row.get('dedicated_bytes', 0) / 1048576, 1),
                    'engine_percent': row.get('engine_percent', 0)}
    if require_free and snapshot['free_mib'] < 28500:
        return {'reason': '5090_not_available', 'free_mib': snapshot['free_mib']}
    if baseline_mib is not None and snapshot['used_mib'] > baseline_mib + 256:
        return {'reason': 'additional_5090_memory_use', 'used_mib': snapshot['used_mib'], 'own_baseline_mib': baseline_mib}
    return None


class PriorityConfirmation:
    """Ignore a one-sample, low-memory activity spike; allocations yield at once."""
    def __init__(self):
        self.previous = None

    def consider(self, reason):
        if reason and reason.get('reason') == 'foreign_5090_process' and reason.get('dedicated_mib', 0) < 64:
            pid = reason['pid']
            confirmed = self.previous == pid
            self.previous = pid
            return reason if confirmed else None
        self.previous = None
        return reason


class Windows5090Probe:
    def __init__(self):
        self.luid = adapter_luid()
        system = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        candidates = [Path(p) for p in (shutil.which('nvidia-smi.exe'),) if p]
        candidates += [system / 'System32/nvidia-smi.exe', Path(r'C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe')]
        candidates += sorted((system / 'System32/DriverStore/FileRepository').glob('nv*/nvidia-smi.exe'),
                             key=lambda p: p.stat().st_mtime, reverse=True)
        self.smi = next((p for p in candidates if p.is_file()), None)

    def read(self):
        # LUID is produced by DXGI and contains only fixed hex punctuation.
        if not re.fullmatch(r'luid_0x[0-9a-f]{8}_0x[0-9a-f]{8}_', self.luid):
            raise RuntimeError('Invalid adapter identifier')
        script = r'''
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$adapter='ADAPTER_LUID'
$memory=@(Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUProcessMemory -Property Name,DedicatedUsage -Filter "Name LIKE '%$adapter%'" )
$engines=@(Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine -Property Name,UtilizationPercentage -Filter "Name LIKE '%$adapter%'" )
$processes=@(Get-CimInstance Win32_Process -Property ProcessId,ParentProcessId,Name,ExecutablePath | ForEach-Object { [pscustomobject]@{pid=$_.ProcessId;parent_pid=$_.ParentProcessId;name=$_.Name;path=$_.ExecutablePath} })
[pscustomobject]@{memory=@($memory|Select-Object Name,DedicatedUsage);engines=@($engines|Select-Object Name,UtilizationPercentage);processes=$processes}|ConvertTo-Json -Depth 4 -Compress
'''.replace('ADAPTER_LUID', self.luid)
        completed = subprocess.run(['pwsh.exe', '-NoProfile', '-NonInteractive', '-Command', script],
            capture_output=True, text=True, encoding='utf-8', timeout=30, check=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        raw = json.loads(completed.stdout.lstrip('\ufeff'))
        processes = {int(p['pid']): {**p, 'dedicated_bytes': 0, 'engine_percent': 0} for p in raw['processes']}
        def pid_of(label):
            match = re.match(r'pid_(\d+)_', label)
            if not match:
                raise RuntimeError('Unrecognized GPU counter process identifier')
            return int(match[1])
        gpu_pids = set()
        for row in raw['memory']:
            pid = pid_of(row['Name']); gpu_pids.add(pid)
            if pid in processes:
                processes[pid]['dedicated_bytes'] += int(row['DedicatedUsage'])
        for row in raw['engines']:
            pid = pid_of(row['Name']); gpu_pids.add(pid)
            if pid in processes:
                processes[pid]['engine_percent'] += float(row['UtilizationPercentage'])
        command = [str(self.smi)] if self.smi else ['wsl.exe', '-d', 'Ubuntu-22.04', '--exec', '/usr/lib/wsl/lib/nvidia-smi']
        sampled = subprocess.run([*command, '--id=' + GPU_UUID,
            '--query-gpu=uuid,name,memory.used,memory.free,utilization.gpu', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, encoding='utf-8', timeout=15, check=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        columns = [p.strip() for p in sampled.stdout.strip().split(',')]
        if len(columns) != 5 or columns[:2] != [GPU_UUID, GPU_NAME]:
            raise RuntimeError('Observed GPU differs from the authorized 5090 UUID')
        return {'gpu_uuid': GPU_UUID, 'adapter_luid': self.luid, 'used_mib': int(columns[2]),
                'free_mib': int(columns[3]), 'utilization_percent': int(columns[4]),
                'processes': [processes[pid] for pid in sorted(gpu_pids) if pid in processes],
                'process_tree': list(processes.values())}


def owned_server_pids(snapshot, runtime):
    runtime = Path(runtime).resolve(); pid_file = runtime / 'server.pid'
    if not pid_file.exists():
        return set()
    server = int(pid_file.read_text().strip())
    process = next((p for p in snapshot['process_tree'] if p['pid'] == server), None)
    expected = runtime / 'install' / 'bin' / 'ollaya.exe'
    if not process:
        return set()
    if not process.get('path') or Path(process['path']).resolve() != expected:
        raise RuntimeError('Own server PID was reused by another executable')
    return descendants(snapshot['process_tree'], server)
