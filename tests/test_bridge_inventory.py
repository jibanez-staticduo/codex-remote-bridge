import base64

import pytest

from codex_thread_bridge.bridge import Bridge
from codex_thread_bridge.ledger import Ledger
from codex_thread_bridge.server import make_server


@pytest.fixture
def inventory(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    try:
        yield ledger
    finally:
        ledger.close()


def add(ledger, request_id, operation="create_thread", thread_id="task", **extra):
    _, receipt = ledger.begin(request_id, operation, {})
    receipt.update(extra)
    if thread_id is not None:
        receipt["threadId"] = thread_id
    return ledger.save(receipt)


def test_creation_receipts_only_and_no_private_content(inventory):
    add(inventory, "a", prompt="SECRET", creation={"auth": "SECRET"}, startedAt=0)
    add(inventory, "send", operation="send_message_to_thread")
    add(inventory, "unfinished", thread_id=None)
    add(inventory, "fork", operation="fork_thread", thread_id="child", status="failed")
    add(inventory, "worktree", operation="create_worktree_thread", thread_id="isolated")
    page = inventory.list_threads()
    assert [item["requestId"] for item in page["data"]] == ["worktree", "fork", "a"]
    assert page["data"][1]["status"] == "failed"
    assert page["data"][2]["startedAt"] == 0
    assert set(page["data"][0]) == {"requestId", "threadId", "operation", "status", "startedAt"}
    assert "SECRET" not in str(page)
    assert page["liveStateChecked"] is False
    assert page["nextCursor"] is None


def test_pagination_stable_across_updates_and_new_receipts(inventory):
    for number in range(5):
        add(inventory, str(number), thread_id=f"thread-{number}")
    first = inventory.list_threads(limit=2)
    assert [item["requestId"] for item in first["data"]] == ["4", "3"]
    original = inventory.get("3")
    inventory.save({**original, "status": "accepted"})
    add(inventory, "new")
    second = inventory.list_threads(limit=2, cursor=first["nextCursor"])
    assert [item["requestId"] for item in second["data"]] == ["2", "1"]
    third = inventory.list_threads(limit=2, cursor=second["nextCursor"])
    assert [item["requestId"] for item in third["data"]] == ["0"]
    assert third["nextCursor"] is None
    assert inventory.begin("3", "create_thread", {})[0] is False
    assert len(inventory.list_threads()["data"]) == 6


def test_zero_cursor_does_not_restart_inventory(inventory):
    add(inventory, "a")
    cursor = "bridge-v1:" + base64.urlsafe_b64encode(b"0").decode()
    assert inventory.list_threads(cursor=cursor)["data"] == []


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "opaque",
        "bridge-v1:!!!!",
        "bridge-v1:LTI=",
        "bridge-v1:" + base64.urlsafe_b64encode(b"999999999999999999999").decode(),
        0,
        True,
    ],
)
def test_invalid_cursor_rejected(inventory, cursor):
    with pytest.raises(ValueError, match="cursor"):
        inventory.list_threads(cursor=cursor)


@pytest.mark.parametrize("limit", [0, 101, -1, True, 2.5])
def test_invalid_limits_rejected(inventory, limit):
    with pytest.raises(ValueError, match="limit"):
        inventory.list_threads(limit=limit)


async def test_bridge_inventory_never_contacts_server(inventory):
    add(inventory, "retained", thread_id="archived-or-deleted")
    bridge = Bridge(None, inventory)
    assert bridge.list_bridge_threads()["data"][0]["threadId"] == "archived-or-deleted"
    tools = await make_server(bridge).list_tools()
    assert "list_bridge_threads" in {tool.name for tool in tools}
