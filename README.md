# Codex Remote Bridge

A same-host MCP bridge for managing Codex tasks from Desktop SSH sessions,
remote TUI clients, and other clients that load configured MCP servers.

**This is a fork of [saidelike/codex-thread-bridge](https://github.com/saidelike/codex-thread-bridge).**
The original Python implementation, MIT attribution, Git history, operation
ledger, and tests are retained. This fork adds npm distribution, a Windows
transport, and more task-management tools. It is independent of OpenAI and does
not impersonate the official `codex_app` or `codex_tui` servers.

## Why this exists

Some Codex Desktop versions provide delegation through `codex_app` only to local
sessions, while excluding send/create/fork/handoff/automation from the remote
dynamic-tool path. The TUI also restricts its delegation MCP to qualifying local
connections. An app-server can support task operations without exposing a tool
for the model to request them. See [#42973](https://github.com/openai/codex/issues/42973)
and [#40865](https://github.com/openai/codex/issues/40865).

This MCP runs **beside the existing app-server**. It does not start another
app-server, restart Codex, or require an open terminal. Each installation manages
its own host's tasks; syncing its configuration does not enable cross-host routing.

## Install

Requires Node.js 18.19+ and an existing authenticated Codex app-server with a
control socket accessible to the same OS user. The npm launcher automatically
prepares an isolated Python environment using a pinned, SHA-256-verified official
uv release and the included `uv.lock`. No global Python installation is required.
Initial setup needs internet access; setup diagnostics go to stderr, not MCP stdout.

```sh
npx --yes @staticduo/codex-remote-bridge@0.2.0 --help
```

Add to Codex configuration **on the host running the task**:

```toml
[mcp_servers.codex_remote_bridge]
command = "npx"
args = ["--yes", "@staticduo/codex-remote-bridge@0.2.0"]
startup_timeout_sec = 180
tool_timeout_sec = 60
```

A shared system config can use this entry once every receiver has Node/npm and
an accessible app-server. No NAS-specific checkout path or credential is shared.
Refresh MCP configuration or open a fresh task, then verify its tool inventory.
The bridge never edits configuration or restarts services itself.

### Transport

Default socket: `$CODEX_HOME/app-server-control/app-server-control.sock`, with
`CODEX_HOME` defaulting to the user's `.codex` directory. Override with `--socket`.

- Linux/macOS: direct Unix-domain WebSocket.
- Windows: `codex app-server proxy --sock PATH` relays bytes to the existing
  socket. The bridge performs the WebSocket handshake over the relay. No public
  listener or extra daemon is started.
- A private Desktop stdio-only app-server without a control socket is not
  reachable by this bridge. Installing the npm package does not change that.

Bootstrap assets cover Linux, macOS, and Windows on x64/ARM64. Packaging support
is different from verified runtime support: see [validation](docs/validation.md).
Mobile compatibility depends on the client loading configured host MCPs; Desktop
validation alone does not establish mobile compatibility.

## Tools

| Tool | Behavior |
| --- | --- |
| `get_capabilities` | Report connection and compatibility limits |
| `create_thread` | Create a retained task in an existing directory, optionally name it and send its first instruction |
| `fork_thread` | Copy history into a retained task without starting a turn |
| `send_message_to_thread` | Send one instruction to an explicitly selected idle task |
| `set_thread_title` | Rename a task |
| `set_thread_archived` | Archive/unarchive; archiving requires acknowledgement of descendant effects |
| `list_threads` | List unarchived tasks with pagination |
| `list_archived_threads` | List archived tasks with pagination |
| `list_bridge_threads` | Recover IDs from this bridge's historical creation receipts, including tasks hidden by native listing |
| `read_thread` | Read metadata and a page of turns without resuming |
| `wait_thread` | Wait up to 50 seconds for a specific recent turn |
| `get_goal` | Read an existing goal without changing it |
| `get_operation` | Recover a retained mutation receipt |
| `create_worktree_thread` | Create an explicitly requested retained Git worktree/task, inherited from upstream |

Discover the schemas before calling. These tools use the bridge's argument and
receipt contract, not identical Desktop schemas. Ordinary execution and patch
tools are unaffected. No arbitrary RPC or general shell tool is exposed.

## Changes in this fork

- **npm distribution:** portable startup, pinned dependencies, and a shared config
  entry without machine-specific source paths.
- **Windows transport:** use Codex's byte proxy instead of assuming Python's Unix
  socket connector is available on Windows.
- **Task management:** add fork, rename, archive/unarchive, and archived listing
  with the same durable receipts as existing mutations.
- **Tool provenance:** delivered instructions use `turn/start.toolOutput`, not
  user-authored messages. This preserves tool provenance but does not authenticate
  the identity of the sending agent.
- **Desktop coexistence:** unsolicited server requests are left to their owning
  client. Even an error response could consume Desktop's pending approval/tool
  callback, so the bridge does not answer those requests.
- **Explicit limits:** unsupported Desktop functions are not silently emulated.

## Delivery and permissions

Each mutation requires a stable `request_id`. Reusing it with the same arguments
returns its stored receipt without redispatching. Different arguments fail. Use
new IDs for new intended operations, never to retry an uncertain operation.

`accepted` means the API steps returned, not that a model turn finished. A
`failed` receipt can include partial effects. `outcome_unknown` and
`in_progress_or_unknown` require reconciliation. Inspect retained IDs/artifacts;
this is conservative deduplication, not an exactly-once guarantee.

Receipts can contain private metadata. They stay in the local endpoint-scoped
SQLite ledger, not npm/GitHub. Preserve the default
`$XDG_STATE_HOME/codex-thread-bridge` (fallback `~/.local/state/codex-thread-bridge`)
or your explicit `--state-dir` across upgrades. The upstream directory name is
retained for continuity. Do not run old/new versions against one ledger during
an upgrade.

Creation defaults to read-only and approval policy `never`; broader permissions
must be explicit. Messaging resumes without model/reasoning/directory/permission
overrides and refuses interactive approval policies. The bridge does not handle
approval prompts. Keep the owning client attached for client-owned tools.
Forking refuses sources with any goal (or unavailable goal inspection), sends no
initial instruction, and also requests deferred goal continuation. Concurrent
changes by other clients remain the app-server's responsibility.

Codex currently omits tasks with an empty native preview from `thread/list`, even
when they have a name and completed tool-output turns. The bridge does not inject
fake user messages or edit Codex's database to populate that preview. Use returned
IDs or `list_bridge_threads` to recover these tasks, then `read_thread` for their
current state. That separate inventory contains historical creation receipts,
not verified current existence or archive status. Native pagination is unchanged.

Native archive can cascade to spawned descendants and stop their work. The
archive tool requires explicit acknowledgement; files/worktrees are not cleaned
up. Reads/list/wait never resume tasks. Display text truncation is marked, while
opaque cursors, paths and IDs remain intact. Pass cursors back unchanged.

Upstream's worktree tool creates detached, locked, retained checkouts at an
explicit full commit. It disables hooks/filters and copies no dirty files. It
never cleans up automatically or guarantees Desktop project registration. The
caller owns retained artifacts and must reconcile partial failures.

## Limits and roadmap

Not implemented in this release:

- Active-turn messaging, automatic steering, interruption, or background queues.
- Desktop's `handoff_thread` lifecycle and `automation_update` scheduler.
- Sidebar organization, sharing, voice, or UI panels.
- Desktop-managed worktree lifecycle or guaranteed sidebar/project registration.
- Cross-host routing, interactive approvals, or credential management.

Future candidates are active-turn delivery preserving tool provenance, bounded
queued delivery with receipts, authenticated sender context, and explicit
cross-host routing. Each needs protocol/client validation. Handoff and
scheduling will only be added if an appropriate API preserves their semantics.
These are roadmap items, not functions available in the current release.

## Development

```sh
uv sync --frozen --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check src
npm test
npm pack --dry-run
```

The Python import package remains `codex_thread_bridge` to simplify upstream
merges. Both old and new Python console-script names remain available. Public
npm files are explicitly allowlisted: runtime source, launcher, dependency locks,
license and documentation, never local receipts or Codex configuration.

Original: [saidelike/codex-thread-bridge](https://github.com/saidelike/codex-thread-bridge).
Fork: [jibanez-staticduo/codex-remote-bridge](https://github.com/jibanez-staticduo/codex-remote-bridge).
See [LICENSE](LICENSE) for the retained MIT attribution.
