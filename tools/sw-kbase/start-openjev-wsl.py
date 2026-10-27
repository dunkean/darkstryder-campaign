"""Start the existing phase-4 Simple-JEV contract with local Qwen 27B weights.

Run in its dedicated WSL venv. Loading is offline; GPU headroom is reserved in
the Accelerate device map. Optional int8 quantizes the same official weights
with bitsandbytes, keeping every module on GPU. OCR and writers are untouched.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gc
import importlib.metadata
import functools
import json
import os
from pathlib import Path
import subprocess
from contextlib import ExitStack
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--served-model-name", default="Qwen/Qwen3.8-27B")
    parser.add_argument("--gpu-name", default="RTX 5090",
                        help="Use exactly one matching physical GPU; defaults to the RTX 5090")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--gpu-reserve-gib", type=float, default=3)
    parser.add_argument("--cpu-memory-gib", type=int, default=32)
    parser.add_argument("--max-model-len", type=int, default=16384)
    parser.add_argument("--quantization", choices=("none", "int8", "nf4"), default="none")
    parser.add_argument("--max-batch-size", type=int, default=8)
    parser.add_argument("--weight-io", choices=("mmap", "pread"), default="pread",
                        help="pread avoids small mmap page faults across the WSL /mnt drive boundary")
    parser.add_argument("--report", type=Path)
    parser.add_argument('--save-quantized', type=Path,
                        help='Persist an offline quantized checkpoint to avoid repeated cold conversion')
    args = parser.parse_args()
    if (not 1 <= args.port <= 65535 or args.gpu_reserve_gib < 1 or args.cpu_memory_gib < 1
            or not 1 <= args.max_batch_size <= 32 or args.max_model_len < 1024):
        parser.error("Invalid port or memory budget")
    model_path = args.model.resolve()
    index_path = model_path / "model.safetensors.index.json"
    if not index_path.is_file() or not (model_path / "config.json").is_file():
        parser.error("A complete local Hugging Face sharded model is required")
    index = json.loads(index_path.read_text())
    shards = sorted(set(index["weight_map"].values()))
    if any(not (model_path / shard).is_file() for shard in shards):
        parser.error("Model download is incomplete; a required safetensors shard is missing")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ.setdefault("PYTORCH_ALLOC_CONF", "expandable_segments:True")
    inventory = subprocess.check_output(["nvidia-smi", "--query-gpu=uuid,name",
                                         "--format=csv,noheader"], text=True)
    selected = [line.split(",", 1)[0].strip() for line in inventory.splitlines()
                if args.gpu_name.lower() in line.split(",", 1)[-1].lower()]
    if len(selected) != 1:
        raise RuntimeError(f"Expected exactly one GPU matching {args.gpu_name!r}; found {len(selected)}")
    # Set before importing torch: cuda:0 now means this GPU only, regardless of
    # the physical ordering. Never spill classifier weights onto the desktop GPU.
    os.environ["CUDA_VISIBLE_DEVICES"] = selected[0]
    import torch
    from accelerate import init_empty_weights, infer_auto_device_map
    from transformers import AutoConfig, AutoModelForImageTextToText
    import hf_server
    import uvicorn

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable in the dedicated classifier environment")
    if torch.cuda.device_count() != 1:
        raise RuntimeError("Classifier requires exactly one visible GPU")
    config = AutoConfig.from_pretrained(model_path, local_files_only=True)
    # This constructs shapes on the meta device; it does not load a second model.
    with init_empty_weights():
        skeleton = AutoModelForImageTextToText.from_config(config, dtype=torch.bfloat16)
    skeleton.tie_weights()
    # Under WSL/WDDM cudaMemGetInfo may omit memory used by Windows graphics.
    # Take the lower free-memory figure, matched by GPU UUID rather than index.
    gpu_status = subprocess.check_output(["nvidia-smi", "--query-gpu=uuid,memory.free",
                                           "--format=csv,noheader,nounits"], text=True)
    physical_free = {line.split(",")[0].strip().removeprefix("GPU-").lower(): int(line.split(",")[1].strip()) * 1024 ** 2
                     for line in gpu_status.splitlines() if line.strip()}
    memory, gpus = {}, []
    for index in range(torch.cuda.device_count()):
        cuda_free, total = torch.cuda.mem_get_info(index)
        gpu_uuid = str(torch.cuda.get_device_properties(index).uuid).removeprefix("GPU-").lower()
        if gpu_uuid not in physical_free:
            raise RuntimeError("Cannot match CUDA GPU to nvidia-smi memory accounting")
        free = min(cuda_free, physical_free[gpu_uuid])
        allowed = int(free - args.gpu_reserve_gib * 1024 ** 3)
        if allowed > 1024 ** 3:
            memory[index] = allowed
        gpus.append({"device": index, "name": torch.cuda.get_device_name(index), "free_bytes": free,
                     "cuda_reported_free_bytes": cuda_free, "total_bytes": total, "weights_budget_bytes": max(0, allowed)})
    if not memory:
        raise RuntimeError("No GPU has enough free memory after the requested inference reserve")
    if args.quantization == "none":
        memory["cpu"] = args.cpu_memory_gib * 1024 ** 3
    device_map = infer_auto_device_map(skeleton, max_memory=memory, dtype=torch.bfloat16,
                                       no_split_module_classes=skeleton._no_split_modules)
    if args.quantization == "none" and "disk" in device_map.values():
        raise RuntimeError("Model exceeds GPU plus RAM budgets; disk weight offload is not enabled")
    del skeleton
    gc.collect()
    report = {"served_model": args.served_model_name, "revision": args.revision, "model_path": str(model_path),
              "selected_gpu_uuid": selected[0], "gpu_selection": args.gpu_name,
              "weight_shards": len(shards), "dtype": "bfloat16", "gpus": gpus,
              "cpu_budget_gib": args.cpu_memory_gib if args.quantization == "none" else 0,
              "device_map": device_map if args.quantization == "none" else "auto_gpu_only",
              "torch": torch.__version__, "max_model_len": args.max_model_len,
              "prompt_policy": "examples_binary", "quantization": args.quantization,
              "weight_io": args.weight_io, "max_batch_size": args.max_batch_size,
              "url": f"http://127.0.0.1:{args.port}/v1"}
    import transformers
    report["transformers"] = transformers.__version__
    for package in ("flash-linear-attention", "fla-core", "triton", "causal-conv1d"):
        try:
            report[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            report[package] = "absent"
    report["server_commit"] = subprocess.check_output(["git", "-C", str(Path(hf_server.__file__).parent), "rev-parse", "HEAD"], text=True).strip()
    loader_patch = None
    if args.quantization in {"int8", "nf4"}:
        import bitsandbytes
        from transformers import BitsAndBytesConfig
        report["bitsandbytes"] = bitsandbytes.__version__
        original_loader = AutoModelForImageTextToText.from_pretrained
        # Upstream load_service exposes device_map but not quantization kwargs.
        # Extend only its single loader call, restoring the original immediately.
        quantization_config = (BitsAndBytesConfig(load_in_8bit=True, llm_int8_threshold=6.0)
                               if args.quantization == "int8" else
                               BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                                  bnb_4bit_use_double_quant=True,
                                                  bnb_4bit_compute_dtype=torch.bfloat16))
        def quantized_loader(*positional, **kwargs):
            # The automatic int8 estimator reserves another 10% and can reject
            # a single card before loading. Measure this one GPU directly instead;
            # CUDA allocation still fails normally if weights do not fit.
            kwargs.update(device_map={"": 0}, max_memory=memory,
                          quantization_config=quantization_config)
            model = original_loader(*positional, **kwargs)
            actual_map = getattr(model, 'hf_device_map', None)
            if actual_map is None:
                if any(parameter.device.type != 'cuda' or parameter.device.index != 0 for parameter in model.parameters()):
                    raise RuntimeError('Single-card loading left weights outside the selected GPU')
                model.hf_device_map = {"": 0}
                actual_map = model.hf_device_map
            if any(str(value) in {"cpu", "disk"} for value in actual_map.values()):
                raise RuntimeError("Quantized weights must fit entirely on GPU; CPU/disk fallback is disabled")
            return model
        loader_patch = patch.object(AutoModelForImageTextToText, "from_pretrained", side_effect=quantized_loader)
        device_map = "auto"
    report["effective_revision"] = ":".join([args.revision, report["server_commit"][:12],
        "bnb" + report.get("bitsandbytes", "none"), args.quantization,
        "torch" + str(torch.__version__), "transformers" + transformers.__version__,
        "fla" + report["fla-core"], "triton" + report["triton"],
        "conv" + report["causal-conv1d"],
        "nf4double-bf16" if args.quantization == "nf4" else "int8threshold6",
        "cpu-logit-indices-v1", "examples_binary"])
    print(json.dumps({"loading": args.served_model_name, "device_module_counts": dict(Counter(str(v) for v in device_map.values())) if isinstance(device_map, dict) else "single selected GPU",
                      "gpus": gpus, "cpu_budget_gib": report["cpu_budget_gib"]}), flush=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    with ExitStack() as loading:
        if loader_patch is not None:
            loading.enter_context(loader_patch)
        if args.weight_io == "pread":
            import transformers.modeling_utils as modeling_utils
            original_safe_open = modeling_utils.safe_open
            def pread_open(*positional, **kwargs):
                kwargs["backend"] = "pread"
                return original_safe_open(*positional, **kwargs)
            loading.enter_context(patch.object(modeling_utils, "safe_open", side_effect=pread_open))
        service = hf_server.load_service(str(model_path), revision=args.revision, device=device_map, dtype="bfloat16",
                                     prompt_policy="examples_binary", max_model_len=args.max_model_len,
                                     max_batch_size=args.max_batch_size, max_batch_tokens=args.max_model_len, max_request_branches=32,
                                     served_model_name=args.served_model_name, enforce_model_id=True, max_choice_options=255)
    report["device_map"] = service.backend.model.hf_device_map
    if args.save_quantized and args.quantization != 'none':
        import shutil
        cache_path = args.save_quantized.resolve()
        if cache_path.exists():
            raise RuntimeError('Quantized checkpoint destination already exists')
        service.backend.model.save_pretrained(cache_path, safe_serialization=True)
        for filename in ('tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json',
                         'vocab.json', 'merges.txt', 'preprocessor_config.json', 'processor_config.json',
                         'chat_template.jinja', 'generation_config.json'):
            source = model_path / filename
            if source.is_file():
                shutil.copy2(source, cache_path / filename)
        (cache_path / 'conversion.json').write_text(json.dumps({'source_model': str(model_path),
            'source_revision': args.revision, 'effective_revision': report['effective_revision'],
            'quantization': args.quantization}, indent=2) + '\n')
        report['quantized_checkpoint'] = str(cache_path)
    gc.collect()
    torch.cuda.empty_cache()
    remaining, total = torch.cuda.mem_get_info(0)
    report["loaded_memory"] = {"model_footprint_bytes": service.backend.model.get_memory_footprint(),
                               "cuda_allocated_bytes": torch.cuda.memory_allocated(0),
                               "cuda_reserved_bytes": torch.cuda.memory_reserved(0),
                               "free_bytes": remaining, "total_bytes": total}
    report['loaded_memory']['reusable_allocator_bytes'] = torch.cuda.memory_reserved(0) - torch.cuda.memory_allocated(0)
    report['loaded_memory']['inference_headroom_bytes'] = remaining + report['loaded_memory']['reusable_allocator_bytes']
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print("LOADED_MEMORY " + json.dumps(report["loaded_memory"]), flush=True)
    if report['loaded_memory']['inference_headroom_bytes'] < args.gpu_reserve_gib * 1024 ** 3:
        raise RuntimeError("Weights loaded but requested inference headroom is unavailable; refusing to serve")
    # Qwen indexes final hidden states before invoking lm_head. On a split model,
    # upstream's CUDA0 logits_to_keep tensor cannot index CUDA1 hidden states.
    # Apply this *inside* the root Accelerate pre-hook, which otherwise moves
    # kwargs back to CUDA0. CPU indices are valid on either output device.
    model = service.backend.model
    forward_attribute = "_old_forward" if hasattr(model, "_old_forward") else "forward"
    original_forward = getattr(model, forward_attribute)
    @functools.wraps(original_forward)
    def forward_with_portable_indices(*positional, **kwargs):
        indices = kwargs.get("logits_to_keep")
        if isinstance(indices, torch.Tensor):
            kwargs["logits_to_keep"] = indices.cpu()
        return original_forward(*positional, **kwargs)
    setattr(model, forward_attribute, forward_with_portable_indices)
    report["logits_index_adapter"] = "cpu-logit-indices-v1"
    service.metadata.update(quantization=args.quantization, effective_revision=report["effective_revision"])
    service.advanced_metrics = True
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print("CLASSIFIER_READY " + report["url"], flush=True)
    uvicorn.run(hf_server.create_app(service), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
