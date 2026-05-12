# Workflow Patterns

Detailed examples for each multi-agent workflow pattern.

## Pattern A: Parallel Independent Tasks

**When to use:** Batch research, data processing, parallel file analysis.
**Key benefit:** Linear scaling — N workers finish N tasks in roughly 1/N the time.

```bash
# Create independent tasks (no parent links)
hermes kanban create "Analyze sales data Q1" --assignee research-worker
hermes kanban create "Analyze sales data Q2" --assignee research-worker
hermes kanban create "Analyze sales data Q3" --assignee research-worker
hermes kanban create "Analyze sales data Q4" --assignee research-worker

# All 4 run simultaneously (up to your concurrency limit)
# Each worker saves results to its own workspace
```

**Kanban view:**
```
[Analyze Q1] ──────────────→ ready → running → done
[Analyze Q2] ──────────────→ ready → running → done
[Analyze Q3] ──────────────→ ready → running → done
[Analyze Q4] ──────────────→ ready → running → done
```

## Pattern B: Pipeline with Dependencies

**When to use:** Software development, document drafting with review stages.
**Key benefit:** Quality gates between stages.

```bash
# Stage 1: Plan
PLAN_ID=$(hermes kanban create "Plan user auth feature" --assignee orchestrator --json | jq -r .task_id)

# Stage 2: Implement (depends on plan)
IMPL_ID=$(hermes kanban create "Implement user auth" --assignee coder-worker --parent $PLAN_ID --json | jq -r .task_id)

# Stage 3: Test (depends on implementation)
TEST_ID=$(hermes kanban create "Test user auth" --assignee qa-worker --parent $IMPL_ID --json | jq -r .task_id)

# Stage 4: Fix if needed (depends on test, assigned back to coder)
# This is created dynamically if QA blocks with findings
```

**Kanban view:**
```
[Plan] ──→ [Implement] ──→ [Test] ──→ [Fix] ──→ [Re-test]
           (waits for plan)  (waits for impl)   (if test fails)
```

## Pattern C: Fan-Out / Fan-In

**When to use:** Long-form writing, multi-part content, report compilation.
**Key benefit:** Parallel writing with unified editing.

```bash
# Step 1: Create outline
OUTLINE_ID=$(hermes kanban create "Write story outline" --assignee creative-worker --json | jq -r .task_id)

# Step 2: Fan out — each chapter depends on outline
CH1_ID=$(hermes kanban create "Write chapter 1" --assignee creative-worker --parent $OUTLINE_ID --json | jq -r .task_id)
CH2_ID=$(hermes kanban create "Write chapter 2" --assignee creative-worker --parent $OUTLINE_ID --json | jq -r .task_id)
CH3_ID=$(hermes kanban create "Write chapter 3" --assignee creative-worker --parent $OUTLINE_ID --json | jq -r .task_id)

# Step 3: Fan in — edit depends on all chapters
hermes kanban create "Edit and unify all chapters" --assignee creative-worker --parent $CH1_ID --parent $CH2_ID --parent $CH3_ID
```

**Kanban view:**
```
                ┌─→ [Ch1] ─┐
[Outline] ──→ ├─→ [Ch2] ──┼─→ [Edit & Unify]
                └─→ [Ch3] ─┘
```

## Pattern D: Competitive (GLADIATOR)

**When to use:** Creative exploration, A/B testing, design alternatives.
**Key benefit:** Multiple independent approaches, pick the best.

```bash
# Same task to 3 different workers
hermes kanban create "Design landing page — approach A" --assignee creative-worker
hermes kanban create "Design landing page — approach B" --assignee creative-worker
hermes kanban create "Design landing page — approach C" --assignee creative-worker

# After all complete, human or orchestrator picks the best
hermes kanban create "Evaluate and select best design" --assignee orchestrator     --parent $DESIGN_A --parent $DESIGN_B --parent $DESIGN_C
```

## Pattern E: Continuous Worker Pool

**When to use:** Ongoing task queues, CI/CD-like workflows, cron-triggered work.
**Key benefit:** Always-on workers processing tasks as they arrive.

```bash
# Start persistent workers
bash scripts/spawn.sh 3

# Start gateway dispatcher
hermes gateway start

# Create tasks anytime — dispatcher auto-assigns to idle workers
hermes kanban create "Process incoming PR #123" --assignee coder-worker
hermes kanban create "Review documentation update" --assignee qa-worker
hermes kanban create "Write blog post about release" --assignee creative-worker

# Workers continuously claim and process tasks
# Add more tasks as needed — workers are always running
```

## Mixing Patterns

You can combine patterns in a single workflow:

```bash
# Research phase: parallel independent
hermes kanban create "Research competitor A" --assignee research-worker
hermes kanban create "Research competitor B" --assignee research-worker

# Analysis phase: depends on research
ANALYSIS_ID=$(hermes kanban create "Synthesize competitive analysis" --assignee research-worker     --parent $RESEARCH_A --parent $RESEARCH_B --json | jq -r .task_id)

# Implementation phase: depends on analysis, fan-out
CODE_ID=$(hermes kanban create "Build competitive features" --assignee coder-worker --parent $ANALYSIS_ID --json | jq -r .task_id)
DOCS_ID=$(hermes kanban create "Write competitive docs" --assignee creative-worker --parent $ANALYSIS_ID --json | jq -r .task_id)

# QA: depends on both implementation tracks
hermes kanban create "Test and verify" --assignee qa-worker --parent $CODE_ID --parent $DOCS_ID
```

## Choosing the Right Pattern

| Pattern | Parallelism | Dependencies | Best For |
|---------|-------------|--------------|----------|
| A: Parallel Independent | High | None | Batch processing |
| B: Pipeline | Low | Sequential | Quality-gated workflows |
| C: Fan-Out/Fan-In | Medium | Tree structure | Long-form content |
| D: Competitive | High | None (same task) | Creative exploration |
| E: Continuous Pool | Variable | Per-task | Always-on processing |
