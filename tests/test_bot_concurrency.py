from contextlib import contextmanager
from threading import Event, Thread
from unittest.mock import Mock

import pytest

import database as db
import handlers
from galaxy_store import AppRequest, DownloadGrant, StoreError
from tests.test_galaxy_store import release


@pytest.fixture
def transport(monkeypatch):
    bot, cards = Mock(), Mock()
    monkeypatch.setattr(handlers, "bot", bot)
    monkeypatch.setattr(handlers, "messages", cards)
    return bot, cards


def test_same_card_rejects_duplicate_before_starting_another_worker(
    monkeypatch, transport
):
    workers = []

    class QueuedThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            workers.append(self.target)

    monkeypatch.setattr(handlers.threading, "Thread", QueuedThread)
    operation = Mock()
    monkeypatch.setattr(handlers, "_link_once", operation)
    app = AppRequest("com.example.app", "CN")
    try:
        handlers.start_link(100, app, 55, "first", refresh=True)
        handlers.start_link(100, app, 55, "duplicate", refresh=True)
        assert len(workers) == 1
        transport[0].answer_callback_query.assert_called_once_with(
            "duplicate", "当前请求较多，请稍后重试。"
        )
        transport[0].send_message.assert_not_called()
        transport[1].edit.assert_not_called()
    finally:
        for worker in workers:
            worker()
    handlers.start_link(100, app, 55, "after", refresh=True)
    workers[-1]()
    assert operation.call_count == 2


@pytest.mark.parametrize("second_writer", ["link", "check", "batch"])
def test_same_app_serializes_all_cache_writers_and_keeps_real_region_rollback(
    monkeypatch, transport, second_writer
):
    first_started, release_first, second_started = Event(), Event(), Event()
    second_entered, second_finished = Event(), Event()
    old = release(region="US", version_name="1.2.4", version_code=124)
    new = release(
        region="US" if second_writer == "batch" else "CN",
        version_name="1.2.3",
        version_code=123,
    )
    app = AppRequest(old.package)
    if second_writer == "batch":
        db.add_subscription(100, app)
        db.cache_release(app, release(region="US", version_code=122), None)
    sequence, failures = [], []

    class Store:
        def __init__(self, selected):
            self.selected = selected

        def metadata(self, app):
            if self.selected == old:
                first_started.set()
                assert release_first.wait(3)
            else:
                second_entered.set()
            return self.selected

        def download_link(self, app):
            selected = self.metadata(app)
            return DownloadGrant(
                selected,
                "https://download.samsungapps.com/synthetic.apk",
                selected.size,
            )

        def update_metadata(self, candidate):
            return self.metadata(app)

        def batch_updates(self, baselines, region):
            return {app.package: {"GUID": app.package, "region": region}}

        def details(self, selected):
            return selected, None

    stores = iter([Store(old), Store(new)])

    @contextmanager
    def factory():
        yield next(stores)

    monkeypatch.setattr(handlers, "GalaxyStore", factory)
    monkeypatch.setattr(
        handlers,
        "notify_release",
        lambda app, selected, notes: sequence.append(selected.version_code),
    )
    transport[1].edit.side_effect = lambda chat, message, card, markup: sequence.append(
        124 if "1.2.4" in card.html() else 123
    )

    def run(operation, done=None):
        try:
            operation()
        except BaseException as exc:
            failures.append(exc)
        finally:
            if done:
                done.set()

    first = Thread(target=run, args=(lambda: handlers._link_once(100, app, 55),))
    first.start()
    assert first_started.wait(3)

    def second_operation():
        second_started.set()
        if second_writer == "link":
            handlers._link_once(100, app, 56)
        elif second_writer == "check":
            handlers.check_app(app)
        else:
            # Candidates must be confirmed after obtaining app ownership.
            handlers.run_check_all()

    second = Thread(target=run, args=(second_operation, second_finished))
    second.start()
    try:
        assert second_started.wait(3)
        assert not second_entered.wait(0.1)
    finally:
        release_first.set()
        first.join(3)
        second.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert failures == []
    assert second_finished.is_set()
    assert sequence == [124, 123]
    assert db.app_cache(app)[0] == new


def test_different_app_queries_stay_parallel(monkeypatch, transport):
    first_started, release_first, second_finished = Event(), Event(), Event()
    first_release = release()
    second_release = release(package="com.other.app")
    failures = []

    class Store:
        def download_link(self, app):
            if app.package == first_release.package:
                first_started.set()
                assert release_first.wait(3)
                selected = first_release
            else:
                selected = second_release
            return DownloadGrant(
                selected,
                "https://download.samsungapps.com/synthetic.apk",
                selected.size,
            )

        def details(self, selected):
            return selected, None

    @contextmanager
    def factory():
        yield Store()

    monkeypatch.setattr(handlers, "GalaxyStore", factory)

    def run(app, message, done=None):
        try:
            handlers._link_once(100, app, message)
        except BaseException as exc:
            failures.append(exc)
        finally:
            if done:
                done.set()

    first = Thread(target=run, args=(AppRequest(first_release.package), 55))
    second = Thread(
        target=run, args=(AppRequest(second_release.package), 56, second_finished)
    )
    first.start()
    assert first_started.wait(3)
    second.start()
    try:
        assert second_finished.wait(3)
        assert not release_first.is_set()
        assert db.app_cache(AppRequest(second_release.package))[0] == second_release
    finally:
        release_first.set()
        first.join(3)
        second.join(3)
    assert failures == []


@pytest.mark.parametrize("failure_stage", ["worker", "launch"])
def test_same_card_reservation_is_released_after_failure(
    monkeypatch, transport, failure_stage
):
    workers = []
    reject_launch = failure_stage == "launch"

    class QueuedThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            nonlocal reject_launch
            if reject_launch:
                reject_launch = False
                raise RuntimeError("Synthetic launch failure")
            workers.append(self.target)

    monkeypatch.setattr(handlers.threading, "Thread", QueuedThread)
    operation = (
        Mock(side_effect=[RuntimeError("Synthetic worker failure"), None])
        if failure_stage == "worker"
        else Mock()
    )
    monkeypatch.setattr(handlers, "_link_once", operation)
    app = AppRequest("com.example.app", "CN")
    if failure_stage == "worker":
        handlers.start_link(100, app, 55, "first", refresh=True)
        with pytest.raises(RuntimeError, match="Synthetic worker failure"):
            workers.pop()()
    else:
        with pytest.raises(RuntimeError, match="Synthetic launch failure"):
            handlers.start_link(100, app, 55, "first", refresh=True)
    handlers.start_link(100, app, 55, "after", refresh=True)
    assert len(workers) == 1
    workers.pop()()
    assert operation.call_count == (2 if failure_stage == "worker" else 1)


@pytest.mark.parametrize("waiter", ["link", "check"])
def test_same_app_wait_is_bounded_and_uses_existing_busy_feedback(
    monkeypatch, transport, waiter
):
    first_started, release_first = Event(), Event()
    selected = release()
    app = AppRequest(selected.package, "CN")
    monkeypatch.setattr(handlers.config, "REQUEST_TIMEOUT", 0)

    class Store:
        def download_link(self, app):
            first_started.set()
            assert release_first.wait(3)
            return DownloadGrant(
                selected,
                "https://download.samsungapps.com/synthetic.apk",
                selected.size,
            )

        def details(self, selected):
            return selected, None

    @contextmanager
    def factory():
        yield Store()

    monkeypatch.setattr(handlers, "GalaxyStore", factory)
    first = Thread(target=handlers._link_once, args=(100, app, 55))
    first.start()
    try:
        assert first_started.wait(3)
        if waiter == "link":
            handlers._link_once(100, app, 56, "busy", refresh=True)
            transport[0].answer_callback_query.assert_called_once_with(
                "busy", "当前请求较多，请稍后重试。"
            )
        else:
            with pytest.raises(StoreError, match="当前请求较多，请稍后重试。"):
                handlers.check_app(app)
        transport[1].edit.assert_not_called()
        transport[0].send_message.assert_not_called()
    finally:
        release_first.set()
        first.join(3)
    assert not first.is_alive()
    transport[1].edit.assert_called_once()
