# Fix: Subagent model name 404 errors

## Root cause

vLLM serves the model with `--served-model-name nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4` (full path).
But documentation and SKILL.md examples use the short name `nemotron-30b-nvfp4`.
When `delegate_task` spawns subagents, they pick up the model name from config/docs — if it's the short name, vLLM returns 404.

## Affected files (5 references)

| File | Line | Bad value | Fix |
|------|------|-----------|-----|
| SKILL.md | 234 | `nemotron-30b-nvfp4` | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4` |
| README.md | 203 | `nemotron-30b-nvfp4` | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4` |
| docs/deployment-guide.md | 114 | `nemotron:30b-a3b-nvfp4` (Ollama) | Keep as-is (Ollama uses short names) |
| docs/deployment-guide.md | 155 | `nemotron-30b-nvfp4` | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4` |
| docs/tuning-guide.md | 100 | `nemotron-30b-nvfp4` | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4` |

## Additional fix: delegate_task model parameter

When using `delegate_task` from a profile that uses local-vllm, explicitly pass:
```python
model={"model": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4", "provider": "local-vllm"}
```
Don't rely on inheritance — the subagent may fall back to the default provider (nous) which doesn't have this model.

## Verification

After fix:
1. `grep -rn 'nemotron-30b-nvfp4' .` should return 0 results (except Ollama references)
2. All model references use the full `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4` path
3. delegate_task with explicit model parameter works without 404
