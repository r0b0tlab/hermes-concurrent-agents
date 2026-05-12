# hermes-concurrent-agents: Community Release Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make hermes-concurrent-agents production-ready and shareable with the Hermes Agent community — all docs accurate, all scripts tested, GitHub updated.

**Architecture:** vLLM 0.20.1 + Marlin backend on GB10 (SGLang broken on sm121). 3-6 concurrent hermes agents via tmux, coordinated by kanban board.

**Tech Stack:** vLLM, Nemotron-3-Nano-30B-A3B-NVFP4, Hermes Agent v0.13+, tmux, bash

---

## Gaps Found (Audit)

### Critical (blocks sharing)
1. SGLang configs reference broken backend — sgl_kernel has no sm121 binaries
2. vLLM docker-compose missing Marlin env vars — CUTLASS FP4 crashes on SM121
3. vLLM docker-compose missing key flags (--served-model-name, --moe-backend, --kv-cache-dtype, --max-num-seqs)
4. README/SKILL.md position SGLang as primary — should be vLLM
5. spawn.sh patched locally but not committed (--continue → chat)
6. profile-template.yaml missing providers section — causes "Unknown provider" error

### Medium (confusing for users)
7. Deployment guide references MPS daemon (untested, may not be needed)
8. Tuning guide uses SGLang-specific flags (--mem-fraction-static)
9. setup.sh doesn't apply config from profile-template.yaml
10. benchmark.sh not validated with actual vLLM setup
11. Docs reference port 30000 throughout — should be 8000

### Low (polish)
12. No CONTRIBUTING.md
13. No CHANGELOG.md
14. SOUL.md files are minimal

---

## Phase 1: Fix Configs and Scripts

### Task 1: Update vLLM docker-compose with working Marlin config
**Files:** `config/vllm/docker-compose.yml`

Replace with tested config:
- Remove `avarok/vllm-nvfp4-gb10-sm120:v14` image (untested)
- Use `vllm/vllm-openai:latest` or build-from-venv instructions
- Add Marlin env vars: VLLM_USE_FLASHINFER_MOE_FP4=0, VLLM_NVFP4_GEMM_BACKEND=marlin, VLLM_TEST_FORCE_FP8_MARLIN=1
- Add flags: --served-model-name, --moe-backend marlin, --kv-cache-dtype fp8, --max-num-seqs 16, --max-model-len 65536
- Port 8000

### Task 2: Mark SGLang config as experimental
**Files:** `config/sglang/docker-compose.yml`, `config/sglang/launch.sh`

Add header comments:
```
# WARNING: SGLang is EXPERIMENTAL on GB10 (SM121).
# sgl_kernel has no sm121 binaries — requires building from source.
# Use vLLM config (config/vllm/) instead for production.
```

### Task 3: Commit spawn.sh fix
**Files:** `scripts/spawn.sh`

Already patched (--continue → chat). Just needs git commit.

### Task 4: Update profile-template.yaml with providers section
**Files:** `config/profile-template.yaml`

Add providers section so hermes recognizes local-vllm:
```yaml
providers:
  local-vllm:
    base_url: http://127.0.0.1:8000/v1
    api_key: local
```

### Task 5: Update setup.sh to apply profile config
**Files:** `setup.sh`

After creating profiles, copy profile-template.yaml to each profile's config.yaml.

---

## Phase 2: Update Documentation

### Task 6: Rewrite README.md for vLLM-first
**Files:** `README.md`

Changes:
- Quick Start: vLLM command instead of SGLang docker-compose
- Performance table: use actual benchmark data (23 tok/s per agent, 69 tok/s total at c=3)
- Key flags: vLLM flags (--gpu-memory-utilization, --moe-backend marlin, --kv-cache-dtype fp8)
- Memory budget: update with actual numbers (85GB GPU at 0.70)
- Pitfalls: add "SGLang broken on SM121" and "CUTLASS FP4 broken on SM121"

### Task 7: Rewrite SKILL.md for vLLM
**Files:** `SKILL.md`

Same changes as README — vLLM primary, SGLang marked experimental.

### Task 8: Update deployment-guide.md
**Files:** `docs/deployment-guide.md`

- Phase 1: Remove MPS daemon section (untested, add note)
- Phase 2: vLLM as Option A (recommended), SGLang as Option B (experimental)
- All port references: 30000 → 8000
- Add Marlin backend section
- Update verify commands

### Task 9: Update tuning-guide.md
**Files:** `docs/tuning-guide.md`

- Replace --mem-fraction-static with --gpu-memory-utilization
- Add vLLM-specific tuning flags
- Update concurrency table with actual benchmark data

### Task 10: Update references/research-report-summary.md
**Files:** `references/research-report-summary.md`

- Note SGLang incompatibility with SM121
- Reference Marlin backend workaround

---

## Phase 3: GitHub Push

### Task 11: Commit all changes and push
```bash
cd /home/mr-r0b0t/projects/hermes-concurrent-agents
git add -A
git commit -m "feat: vLLM Marlin backend, tested on GB10 with 3-agent swarm

- Switch primary backend from SGLang to vLLM (sgl_kernel has no sm121 binaries)
- Add Marlin NVFP4 backend (CUTLASS FP4 broken on SM121)
- Fix spawn.sh: use 'chat' instead of '--continue' for fresh sessions
- Add providers section to profile-template.yaml
- Update all docs for vLLM + port 8000
- Mark SGLang configs as experimental
- Benchmark: 3 concurrent agents, ~23 tok/s each, ~69 tok/s total"
git push origin main
```

### Task 12: Create GitHub release v1.1.0
Tag with tested benchmark results and changelog.

---

## Verification

After all tasks:
1. `bash setup.sh` — creates profiles without errors
2. `bash scripts/spawn.sh 3` — all 3 workers stay alive
3. Send "Say hello" to all 3 — all respond via Nemotron
4. `bash scripts/benchmark.sh` — runs without errors
5. `bash scripts/status.sh` — shows all workers + GPU status
6. README quick start commands work end-to-end
