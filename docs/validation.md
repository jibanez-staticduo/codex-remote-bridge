# Release 0.2.0 validation

Evidence collected on 2026-09-11. Platform packaging and a functioning Codex
session are separate checks. This is not a claim that every client was tested.

## Automated coverage

- 121 Python tests pass, including retained upstream regressions and new tests.
- 13 Node launcher tests pass.
- Ruff lint/format, Python source type checks, and package allowlist checks pass.
- Independent release review completed; identified issues corrected before release.

Coverage includes durable request deduplication, partial failures, opaque cursor
handling, worktree retention, tool provenance, new task operations, historical
bridge inventory, WebSocket fragmentation/ping, and proxy teardown. Node tests
exercise bootstrap checksum enforcement, concurrent installation, editable-env
isolation, argv forwarding, npm symlinks, and Windows process-tree termination.

## Real workflow

On Linux x64 with Codex app-server 0.154.0:

1. Initialize the actual npm launcher MCP and discover its tools.
2. Create a retained read-only task and receive a completed model response.
3. Rename it and send a follow-up as tool output.
4. Replay the same request ID and verify no second send.
5. Read the response, fork without launching a turn, and archive/unarchive.
6. Recover the fork through the historical bridge inventory.
7. Archive the temporary validation tasks.

All steps pass. The current model was inherited from the host configuration.
The transport is model-independent; this does not claim a separate inference
probe for every model.

## Platform matrix

| Platform | npm bootstrap / MCP | Existing app-server connection |
| --- | --- | --- |
| Linux x64 | Pass on three independent hosts | Direct Unix initialize/list pass; CLI proxy also tested |
| Linux ARM64 | Pass on three independent hosts, including one provisioned with Node/npm | Direct Unix initialize/list pass |
| Windows x64 | Clean-cache uv/Python install, help, MCP initialize/tools list and EOF exit pass | No control socket on the test host; expected structured failure, live connection unverified |
| macOS | Assets configured; host unavailable | Not tested |
| Windows ARM64 | Assets configured | Not tested |

Windows cleanup additionally passed a programmatic SIGTERM event test through
the launcher: taskkill removed the uv/Python process tree. This is not a test of
external OS signal delivery. No app-server or Desktop process was restarted.

The default native `thread/list` hides tasks with an empty preview, including
some tool-output-only tasks. Naming them does not fix that. This release preserves
tool provenance and provides `list_bridge_threads` instead of changing the native
database or injecting user messages. Desktop sidebar registration is not guaranteed.

## Client boundaries

Desktop same-host messaging was demonstrated through the predecessor bridge.
The release workflow above validates the packaged MCP against the same backend;
actual client inventory is checked during deployment. Native Desktop-owned tools
and approvals still need their owning client. No mobile acceptance test has been
performed. An absent daemon socket is an environment prerequisite failure, not
proof of a working task-control connection.
