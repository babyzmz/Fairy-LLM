from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from fairy_core.application.cancellation_cleanup import CancellationCleanupQueue
from fairy_core.domain.errors import CommandRejectedError


def test_full_stop_queue_rejects_before_acceptance_and_releases_failed_reservations():
    queue = CancellationCleanupQueue(capacity=1)
    entered, release = Event(), Event()
    try:
        with queue.reserve() as submit:
            submit(lambda: (entered.set(), release.wait(3)))
        assert entered.wait(1)
        with pytest.raises(CommandRejectedError) as rejected, queue.reserve():
            pytest.fail("full queue entered cancellation acceptance")
        assert rejected.value.code == "RPC_CAPACITY_EXCEEDED"
    finally:
        release.set()
        queue.close()


def test_close_does_not_invalidate_an_already_accepted_reservation():
    queue = CancellationCleanupQueue()
    closing, closed, cleaned = Event(), Event(), Event()
    callers = ThreadPoolExecutor(max_workers=1)

    def close():
        closing.set()
        queue.close()
        closed.set()

    try:
        with queue.reserve() as submit:
            future = callers.submit(close)
            assert closing.wait(1)
            assert not closed.wait(0.1)
            submit(cleaned.set)
        future.result(timeout=2)
        assert cleaned.is_set()
        with pytest.raises(CommandRejectedError), queue.reserve():
            pytest.fail("closed queue accepted a stop")
    finally:
        callers.shutdown(wait=True)
        queue.close()
