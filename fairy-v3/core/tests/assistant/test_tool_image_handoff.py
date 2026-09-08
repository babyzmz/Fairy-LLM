import hashlib
from uuid import uuid4

import pytest

from fairy_core.perception.store import ImageAttachmentStore
from fairy_core.providers import ModelImage


def _image(task_id, data=b"png-test"):
    return ModelImage.create(
        task_id=task_id,
        media_type="image/png",
        data=memoryview(bytearray(data)),
        content_hash=hashlib.sha256(data).hexdigest(),
        width=1,
        height=1,
        label="untrusted_screen_content",
        untrusted_data=True,
    )


def test_tool_images_are_scoped_ordered_and_consumed_only_once():
    store = ImageAttachmentStore()
    turn, task, other_turn = uuid4(), uuid4(), uuid4()
    first, second = _image(task, b"first"), _image(task, b"second")
    store.tool_images.put(turn, task, 1, 2, (second,))
    store.tool_images.put(turn, task, 1, 1, (first,))
    assert store.tool_images.take(other_turn, task, 1) == ()
    assert store.tool_images.take(turn, task, 1) == (first, second)
    assert store.tool_images.take(turn, task, 1) == ()
    assert bytes(first.data) == b"first"
    store.close()


@pytest.mark.parametrize("action", ["release", "close", "purge", "revision"])
def test_unconsumed_tool_images_are_zeroed_on_owner_lifecycle(action):
    store = ImageAttachmentStore()
    turn, task = uuid4(), uuid4()
    image = _image(task)
    store.tool_images.put(turn, task, 1, 1, (image,))
    if action == "release":
        store.release(turn)
    elif action == "close":
        store.close()
    elif action == "purge":
        store.purge_tasks((task,))
    else:
        assert store.tool_images.take(turn, task, 2) == ()
    assert not any(image.data)
    assert store.tool_images.take(turn, task, 1) == ()


def test_tool_image_task_mismatch_rejected_without_cross_scope_transfer():
    store = ImageAttachmentStore()
    turn, task, other = uuid4(), uuid4(), uuid4()
    image = _image(other)
    with pytest.raises(ValueError, match="scope"):
        store.tool_images.put(turn, task, 1, 1, (image,))
    assert not any(image.data)
    image = _image(task)
    store.tool_images.put(turn, task, 1, 1, (image,))
    with pytest.raises(ValueError, match="scope"):
        store.tool_images.take(turn, other, 1)
    assert store.tool_images.take(turn, task, 1) == (image,)
    store.close()


def test_tool_image_handoff_evicts_and_zeros_old_evidence_at_memory_limits(monkeypatch):
    from fairy_core.perception import tool_images

    monkeypatch.setattr(tool_images, "_TURN_BYTES", 8)
    monkeypatch.setattr(tool_images, "_TOTAL_BYTES", 12)
    store = ImageAttachmentStore()
    task, first_turn, second_turn, third_turn = uuid4(), uuid4(), uuid4(), uuid4()
    first, second, third = (_image(task, b"123456") for _ in range(3))
    store.tool_images.put(first_turn, task, 1, 1, (first,))
    store.tool_images.put(second_turn, task, 1, 1, (second,))
    store.tool_images.put(third_turn, task, 1, 1, (third,))
    assert not any(first.data)
    replacement = _image(task, b"abcdef")
    store.tool_images.put(third_turn, task, 1, 2, (replacement,))
    assert not any(third.data)
    assert bytes(second.data) == b"123456"
    store.close()
    assert not any(second.data)
    assert not any(replacement.data)
