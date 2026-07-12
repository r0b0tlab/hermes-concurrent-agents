# Observability

Human operators must answer, without raw tmux expertise:

1. What is running?
2. What is this agent doing?
3. Is it healthy / waiting / stuck?
4. What did it produce?
5. Can I intervene without disrupting others?

## Observation ladder (least → most intrusive)

| Command | Intrudes? | Use |
|---|---|---|
| `hca status` / `ps` | No | Slot table |
| `hca watch` | No | Live board + capacity |
| `hca explain <id>` | No | Admission / drain / wait reason |
| `hca peek <slot\|task>` | No | Pane snapshot |
| `hca activity --follow` | No | Event stream |
| `hca logs <run>` | No | Run log file / peek fallback |
| `hca transcript <id>` | No | Messages or activity fallback |
| `hca inspect <id>` | No | Full mapping dump |
| `hca attach <slot>` | **Yes** | Interactive only when needed |
| `hca dashboard` | No | Points at Hermes UI (no second HCA UI) |

## Redaction

Peek/transcript apply redact patterns for API keys / bearer tokens / passwords.

## Sources

- HCA SQLite: runs, leases, activity
- tmux: pane capture, pid
- Hermes: session DBs when session id known
- Engine: capacity snapshot

Default posture: **watch/peek first; attach is opt-in.**
