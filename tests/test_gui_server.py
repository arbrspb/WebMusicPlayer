import gui_server


class _FakeOwner:
    pid = 4321

    def __init__(self, error=None):
        self.error = error
        self.joined = False

    def join(self):
        self.joined = True
        if self.error is not None:
            raise self.error


def test_owner_watchdog_exits_after_owner_finishes():
    owner = _FakeOwner()
    exit_codes = []

    gui_server._watch_process_owner(owner, exit_codes.append)

    assert owner.joined is True
    assert exit_codes == [0]


def test_owner_watchdog_does_not_exit_when_waiting_fails():
    owner = _FakeOwner(RuntimeError("cannot wait"))
    exit_codes = []

    gui_server._watch_process_owner(owner, exit_codes.append)

    assert owner.joined is True
    assert exit_codes == []
