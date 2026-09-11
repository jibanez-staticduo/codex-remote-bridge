import pytest

from codex_thread_bridge.bridge import Bridge
from codex_thread_bridge.ledger import Ledger
from codex_thread_bridge.rpc import RpcError
from codex_thread_bridge.server import make_server


class TaskRpc:
    def __init__(self):
        self.calls = []
        self.active = False
        self.ephemeral = False
        self.fail_fork = False
        self.goal = None
        self.fail_goal = False
        self.malformed_goal = False

    async def call(self, method, params):
        self.calls.append((method, params))
        if method == "thread/read":
            return {
                "thread": {
                    "ephemeral": self.ephemeral,
                    "status": {"type": "active" if self.active else "idle"},
                }
            }
        if method == "thread/goal/get":
            if self.fail_goal:
                raise RpcError(method, {"code": -32601, "message": "unsupported"})
            return {} if self.malformed_goal else {"goal": self.goal}
        if method == "thread/fork":
            if self.fail_fork:
                raise RpcError(method, {"code": -32602, "message": "unsupported"})
            return {"thread": {"id": "child"}, "approvalPolicy": "on-request"}
        return {}

    async def close(self):
        pass


@pytest.fixture
async def tasks(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    rpc = TaskRpc()
    try:
        yield Bridge(rpc, ledger), rpc
    finally:
        ledger.close()


async def test_fork_preserves_settings_and_replays(tasks):
    bridge, rpc = tasks
    first = await bridge.fork_thread("fork", "source")
    assert first["status"] == "accepted" and first["threadId"] == "child"
    assert first["creation"]["approvalPolicy"] == "on-request"
    assert rpc.calls[-1] == (
        "thread/fork",
        {
            "threadId": "source",
            "excludeTurns": True,
            "deferGoalContinuation": True,
        },
    )
    count = len(rpc.calls)
    assert (await bridge.fork_thread("fork", "source"))["replayed"]
    assert len(rpc.calls) == count
    with pytest.raises(ValueError, match="different arguments"):
        await bridge.fork_thread("fork", "other")
    assert not any(method == "turn/start" for method, _ in rpc.calls)


@pytest.mark.parametrize("attribute", ["active", "ephemeral"])
async def test_fork_refuses_unsafe_sources(tasks, attribute):
    bridge, rpc = tasks
    setattr(rpc, attribute, True)
    assert (await bridge.fork_thread("fork", "source"))["status"] == "failed"
    assert not any(method == "thread/fork" for method, _ in rpc.calls)


async def test_fork_no_fallback_after_rejection(tasks):
    bridge, rpc = tasks
    rpc.fail_fork = True
    assert (await bridge.fork_thread("fork", "source"))["status"] == "failed"
    assert (await bridge.fork_thread("fork", "source"))["replayed"]
    assert sum(method == "thread/fork" for method, _ in rpc.calls) == 1


@pytest.mark.parametrize("goal", [{"status": "active"}, {"status": "complete"}, {}])
async def test_fork_refuses_any_goal_without_mutation(tasks, goal):
    bridge, rpc = tasks
    rpc.goal = goal
    result = await bridge.fork_thread("goal-fork", "source")
    assert result["status"] == "failed"
    assert result["rpcError"]["code"] == "source_has_goal"
    assert not any(method == "thread/fork" for method, _ in rpc.calls)


@pytest.mark.parametrize("attribute", ["fail_goal", "malformed_goal"])
async def test_fork_refuses_unverifiable_goal_without_fallback(tasks, attribute):
    bridge, rpc = tasks
    setattr(rpc, attribute, True)
    result = await bridge.fork_thread("unknown-goal", "source")
    assert result["status"] == "failed"
    assert result["rpcError"]["code"] == "goal_inspection_failed"
    assert not any(method == "thread/fork" for method, _ in rpc.calls)


async def test_rename_exact_title_and_replay(tasks):
    bridge, rpc = tasks
    assert (await bridge.set_thread_title("rename", "source", " Exact "))["status"] == "accepted"
    assert rpc.calls == [("thread/name/set", {"threadId": "source", "name": " Exact "})]
    assert (await bridge.set_thread_title("rename", "source", " Exact "))["replayed"]
    assert len(rpc.calls) == 1


async def test_archive_ack_and_active_guard(tasks):
    bridge, rpc = tasks
    with pytest.raises(ValueError, match="descendants"):
        await bridge.set_thread_archived("archive", "source", True)
    assert not rpc.calls
    rpc.active = True
    result = await bridge.set_thread_archived("busy", "source", True, True)
    assert result["status"] == "failed"
    assert not any(method == "thread/archive" for method, _ in rpc.calls)


async def test_archive_unarchive_listing(tasks):
    bridge, rpc = tasks
    assert (await bridge.set_thread_archived("archive", "source", True, True))[
        "status"
    ] == "accepted"
    assert rpc.calls[-1] == ("thread/archive", {"threadId": "source"})
    assert (await bridge.set_thread_archived("archive", "source", True, True))["replayed"]
    assert (await bridge.set_thread_archived("unarchive", "source", False))["status"] == "accepted"
    assert rpc.calls[-1] == ("thread/unarchive", {"threadId": "source"})
    await bridge.list_threads(limit=3, cursor="opaque", archived=True)
    assert rpc.calls[-1] == (
        "thread/list",
        {
            "limit": 3,
            "useStateDbOnly": True,
            "archived": True,
            "cursor": "opaque",
        },
    )
    assert not any(method in {"thread/resume", "turn/start"} for method, _ in rpc.calls)


async def test_inventory(tasks):
    bridge, _ = tasks
    names = {tool.name for tool in await make_server(bridge).list_tools()}
    assert {
        "fork_thread",
        "set_thread_title",
        "set_thread_archived",
        "list_archived_threads",
        "create_thread",
        "send_message_to_thread",
    } <= names


async def test_initial_prompt_authority(bridge, fake_server, tmp_path):
    fake, _ = fake_server
    result = await bridge.create_thread("create-authority", str(tmp_path), prompt="exact")
    assert result["status"] == "accepted"
    params = next(params for method, params in fake.calls if method == "turn/start")
    assert params["input"] == []
    assert params["toolOutput"] == {
        "name": "create_thread",
        "namespace": "codex_remote_bridge",
        "output": "exact",
    }
