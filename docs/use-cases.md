# Use cases

This repo is useful when work can be split across several local agents.

The pattern is: one local model server, several role-specific Hermes workers, and a shared kanban board so the workers do not duplicate work or step on each other.

## 1. Research sprint

Use this when you need to understand a topic quickly.

Example split:

- `research-worker`: gather papers, docs, blog posts, and benchmarks
- `research-worker`: collect competing tools and prior art
- `creative-worker`: write a plain-English summary
- `qa-worker`: fact-check claims and flag weak sources
- `orchestrator`: combine everything into a final brief

Good for:

- technical due diligence
- paper reviews
- product comparisons
- market scans
- "what should I build?" research

## 2. Software feature pipeline

Use this when a feature has clear stages.

Example split:

- `orchestrator`: write the plan
- `coder-worker`: implement the first pass
- `qa-worker`: write or run tests
- `coder-worker`: fix failures
- `creative-worker`: update docs
- `qa-worker`: do final review

Use kanban dependencies so the docs and QA work wait for the implementation. This avoids file collisions and stale documentation.

## 3. Benchmark production

Benchmark work has waiting time, logs, charts, and writeups. That makes it a good swarm task.

Example split:

- `coder-worker`: run the benchmark and collect metrics
- `qa-worker`: verify the artifact bundle is complete
- `research-worker`: compare results against previous runs
- `creative-worker`: write the report or release note
- `orchestrator`: decide whether the result is publishable

Good for local LLM benchmarks, hardware testing, throughput sweeps, and repeatable report generation.

## 4. Documentation rewrite

Docs are often either readable but wrong, or correct but painful to read. Split the job.

Example split:

- `research-worker`: find stale claims and missing sections
- `coder-worker`: update command examples
- `creative-worker`: rewrite the README in a clear voice
- `qa-worker`: run link checks and dry-run commands

## 5. Long-form content production

For larger writing projects, use fan-out/fan-in.

Example split:

- `creative-worker`: outline the piece
- `creative-worker`: draft section 1
- `creative-worker`: draft section 2
- `research-worker`: gather examples and references
- `qa-worker`: check claims
- `creative-worker`: do the final voice pass

Keep one worker responsible for the final voice. Otherwise the result can feel stitched together.

Good for:

- tutorials
- launch posts
- technical explainers
- internal reports
- newsletters

## 6. Competitive idea generation

Sometimes you want options, not one answer.

Create the same task several times and compare the outputs.

Examples:

- three README openings
- three architecture options
- three product names
- three landing page concepts
- three benchmark visualization ideas

This is the GLADIATOR pattern. It works well when taste matters and there is no single correct answer.

## 7. QA and review swarm

Use several workers to review from different angles.

Example split:

- `qa-worker`: bugs and edge cases
- `coder-worker`: implementation simplicity
- `research-worker`: API/dependency correctness
- `creative-worker`: docs and naming clarity

This is useful before publishing a repo. Different reviewers catch different failures.

## 8. Local operations team

On a strong local machine, the swarm can act like a small operations team.

Example split:

- one worker watches system health
- one worker processes implementation tasks
- one worker writes summaries
- one worker checks outputs before anything is sent or published

This is useful for local-first workflows where you want the machine doing real work without sending every step to a hosted model.

## When not to use this

Do not use a swarm when the work is tightly coupled and all agents need to edit the same file at the same time. Do not use it when one careful agent with full context would be safer. Do not use it without a shared task board or worktree isolation.

The point is not to maximize the number of agents. The point is to split work where splitting actually helps.
