# Codex Remote Bridge

A local MCP server that lets MCP-capable agents and clients manage Codex tasks
through an existing Codex app-server on the same host. It is useful when the
task is available on a machine but the current client or harness does not expose
Codex's task-management tools.

This is a public fork of
[saidelike/codex-thread-bridge](https://github.com/saidelike/codex-thread-bridge).
We are grateful to the original author for the Python implementation, design,
tests, and MIT-licensed starting point. We preserve its history and attribution.
This fork is the home for our portable npm distribution and extensions; we
continue to propose generally useful changes to the original project when they
fit upstream.

This is an independent community project. It is not maintained by OpenAI and
does not impersonate the official `codex_app` or `codex_tui` servers.

## Why this bridge exists

Codex task control depends on which client, host, and session started a task.
Some remote SSH or mobile paths have reported missing task-management tools such
as `send_message_to_thread`, `create_thread`, or `handoff_thread`, even while the
same operations are available from a local Desktop task. See the reports in
[openai/codex#42973](https://github.com/openai/codex/issues/42973) and
[openai/codex#40865](https://github.com/openai/codex/issues/40865). This is a
client and host capability boundary, not a claim that every Codex setup has the
same limitation.

The app-server is Codex's integration interface, but it speaks Codex's own
JSON-RPC protocol. It is not an A2A endpoint. OpenAI's documentation also notes
that the former `codex mcp-server` command was removed and that app-server is
not an MCP drop-in replacement. This bridge adapts a carefully scoped set of
task operations into MCP tools for clients that already know how to call MCP.
See the [Codex app-server docs](https://developers.openai.com/codex/app-server)
and [MCP-server migration note](https://developers.openai.com/codex/mcp-server).

### A2A and MCP solve different problems

[A2A](https://a2a-protocol.org/latest/) is a good fit when both agents expose
compatible A2A interfaces, their endpoints are discoverable and reachable, and
the caller can satisfy the server's authentication and capability requirements.
Direct A2A cannot reach a Codex app-server merely because Codex is running: the
local app-server speaks JSON-RPC, and a local control socket is not a published
A2A service. A2A also does not supply a missing route through an SSH boundary,
or add Codex task tools to a client whose tool catalog omits them.

This project does **not** implement A2A or expose a network listener. It uses
the Codex host's existing local control socket and presents task actions as MCP
tools. For example:

- A remote SSH task can run code on a host but lack a callable
  `send_message_to_thread` tool. If the bridge is configured on that host, an
  MCP-capable client there can send a message to another Codex task without
  relying on that task's missing dynamic tool.
- A different harness can create or message Codex tasks if it supports MCP and
  launches the bridge on the same host and under the OS account that can access
  the Codex socket.
- A harness running on another computer still needs a secure way to run or
  reach an MCP process on the Codex host. This package does not create that
  cross-host route or publish the socket to the network.

Use A2A when the target agent provides a reachable A2A service. Use this bridge
when you need MCP tools for a Codex app-server already available on the local
host. The protocols can complement one another, but this bridge is an MCP
adapter, not an A2A-to-MCP gateway.

## What this fork adds

- **Portable npm distribution:** start the bridge with `npx` and share one
  configuration entry across compatible hosts instead of maintaining local
  source paths.
- **Windows transport:** use `codex app-server proxy` to relay bytes to the
  existing Codex socket.
- **More task-management tools:** fork, rename, archive/unarchive, list archived
  tasks, and recover bridge-created task IDs from retained operation receipts.
- **Active-task delivery:** deliver tool output to an observed active task
  without resuming it; resume an idle task before delivery.
- **Safer coexistence:** preserve tool provenance and leave unsolicited
  app-server requests to the Desktop client that owns their callbacks.

The original project remains the source of the retained Python implementation,
MIT license, operation ledger, and core test suite. The Python import package
remains `codex_thread_bridge` to keep upstream integration straightforward.

## Install

Requires Node.js 18.19+ and an existing authenticated Codex app-server with a
control socket accessible to the same OS user. The npm launcher automatically
prepares an isolated Python environment using a pinned, SHA-256-verified
official `uv` release and the included `uv.lock`. No global Python installation
is required. Initial setup needs internet access; setup diagnostics go to
stderr, not MCP stdout.

```sh
npx --yes @staticduo/codex-remote-bridge@0.3.0 --help
```

Add the server to Codex configuration **on the host running the target Codex
tasks**:

```toml
[mcp_servers.codex_remote_bridge]
command = "npx"
args = ["--yes", "@staticduo/codex-remote-bridge@0.3.0"]
startup_timeout_sec = 180
tool_timeout_sec = 60
```

Other MCP-capable harnesses can use the same command in their own MCP
configuration, provided the process runs on the Codex host as a user that can
access its control socket. A shared system config can use this entry once every
receiver has Node/npm and an accessible app-server; each host still manages its
own tasks. Refresh MCP configuration or open a fresh task, then verify its tool
inventory. The bridge never edits configuration or restarts services itself.

### Transport

Default socket: `$CODEX_HOME/app-server-control/app-server-control.sock`, with
`CODEX_HOME` defaulting to the user's `.codex` directory. Override with
`--socket`.

- Linux/macOS: direct Unix-domain WebSocket.
- Windows: `codex app-server proxy --sock PATH` relays bytes to the existing
  socket. The bridge performs the WebSocket handshake over the relay. No public
  listener or extra app-server is started.
- A private Desktop stdio-only app-server without a control socket is not
  reachable by this bridge. Installing the npm package does not change that.

Bootstrap assets cover Linux, macOS, and Windows on x64/ARM64. Packaging
support is different from verified runtime support: see
[validation](docs/validation.md). Mobile compatibility depends on the client
loading configured host MCPs; Desktop validation alone does not establish
mobile compatibility.

## Tools

| Tool | Behavior |
| --- | --- |
| `get_capabilities` | Report connection and compatibility limits |
| `create_thread` | Create a retained task in an existing directory, optionally name it and send its first instruction |
| `fork_thread` | Copy history into a retained task without starting a turn |
| `send_message_to_thread` | Send one instruction to an explicitly selected active or idle task |
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

## Delivery, permissions, and state

Each mutation requires a stable `request_id`. Reusing it with the same
arguments returns its stored receipt without redispatching. Different arguments
fail. Use new IDs for new intended operations, never to retry an uncertain
operation.

`accepted` means the API steps returned, not that a model turn finished. A send
receipt's `deliveryMode` records the status branch observed before dispatch:
`active_tool_output` or `idle_tool_output`. For an active task the bridge adds
tool output to the existing turn; it does not call Codex's native `turn/steer`
method. The status check and dispatch are not atomic; if an observed active turn
finishes first, Codex can start a new turn. Use `actualTurnId` as the
authoritative turn ID returned by Codex. A `failed` receipt can include partial
effects. `outcome_unknown` and `in_progress_or_unknown` require reconciliation.
Reuse the same request ID to inspect its retained receipt; never retry an
uncertain send under a new ID. This is conservative deduplication, not an
exactly-once guarantee.

Receipts can contain private metadata. They stay in the local endpoint-scoped
SQLite ledger, not npm/GitHub. Preserve the default
`$XDG_STATE_HOME/codex-thread-bridge` (fallback
`~/.local/state/codex-thread-bridge`) or your explicit `--state-dir` across
upgrades. The upstream directory name is retained for continuity. Do not run
old/new versions against one ledger during an upgrade.

Creation defaults to read-only and approval policy `never`; broader permissions
must be explicit. Messaging sends tool output without model, reasoning,
directory, or permission overrides. The active branch does not resume; the idle
branch resumes and refuses interactive approval policies. The bridge does not
handle approval prompts. Keep the owning client attached for client-owned
tools.

Forking refuses sources with any goal (or unavailable goal inspection), sends
no initial instruction, and also requests deferred goal continuation.
Concurrent changes by other clients remain the app-server's responsibility.

Codex currently omits tasks with an empty native preview from `thread/list`,
even when they have a name and completed tool-output turns. The bridge does not
inject fake user messages or edit Codex's database to populate that preview.
Use returned IDs or `list_bridge_threads` to recover these tasks, then
`read_thread` for their current state. That separate inventory contains
historical creation receipts, not verified current existence or archive status.
Native pagination is unchanged.

Native archive can cascade to spawned descendants and stop their work. The
archive tool requires explicit acknowledgement; files/worktrees are not cleaned
up. Reads/list/wait never resume tasks. Display text truncation is marked, while
opaque cursors, paths, and IDs remain intact. Pass cursors back unchanged.

Upstream's worktree tool creates detached, locked checkouts at an explicit full
commit. It disables hooks/filters and copies no dirty files. It never cleans up
automatically or guarantees Desktop project registration. The caller owns
retained artifacts and must reconcile partial failures.

## Limits and roadmap

Not implemented in this release:

- Native Codex active-turn steering (`turn/steer`), interruption, or background
  queues. Active-turn message delivery uses tool output as described above.
- Desktop's `handoff_thread` lifecycle and `automation_update` scheduler.
- Sidebar organization, sharing, voice, or UI panels.
- Desktop-managed worktree lifecycle or guaranteed sidebar/project registration.
- Cross-host routing, interactive approvals, A2A endpoints, or credential
  management.

Future candidates include bounded queued delivery with receipts, authenticated
sender context, and explicit cross-host routing. Each needs protocol and client
validation. Handoff and scheduling will only be added if an appropriate API
preserves their semantics. These are roadmap items, not functions available in
the current release.

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

Both old and new Python console-script names remain available. Public npm files
are explicitly allowlisted: runtime source, launcher, dependency locks, license,
and documentation, never local receipts or Codex configuration.

## Acknowledgements and links

Thanks again to the author and contributors of
[saidelike/codex-thread-bridge](https://github.com/saidelike/codex-thread-bridge)
for the original implementation and for making it available under the MIT
license. This fork preserves the original attribution in [LICENSE](LICENSE).

- Original project: <https://github.com/saidelike/codex-thread-bridge>
- Public fork and active home for this variant: <https://github.com/jibanez-staticduo/codex-remote-bridge>
- npm package: <https://www.npmjs.com/package/@staticduo/codex-remote-bridge>
