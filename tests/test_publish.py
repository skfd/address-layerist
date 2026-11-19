"""The publish step's push retry.

The push is the last thing in a long build, so these cover the failure that
costs the most: a finished tile pyramid thrown away by a resolver blip.
"""

import pytest

from addresslayerist import publish as pub

RESOLVER_FAILURE = (
    "git push --force origin gh-pages failed (exit 128):\n"
    "fatal: unable to access 'https://github.com/x/y.git/': "
    "Could not resolve host: github.com"
)
REJECTED = (
    "git push --force origin gh-pages failed (exit 1):\n"
    " ! [rejected] gh-pages -> gh-pages (non-fast-forward)"
)


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(pub, "PUSH_WAIT", 0)


def failing_git(calls, message, fail_times=99):
    def git(cfg, args, env):
        calls.append(args)
        if len(calls) <= fail_times:
            raise RuntimeError(message)
        return ""
    return git


def test_a_resolver_failure_is_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(pub, "_git", failing_git(calls, RESOLVER_FAILURE, fail_times=2))
    pub._push(None, {})
    assert len(calls) == 3  # two blips, then through


def test_a_rejected_push_is_not_retried(monkeypatch):
    # A rejected push fails the same way every time. Surface it now rather than
    # ten minutes from now.
    calls = []
    monkeypatch.setattr(pub, "_git", failing_git(calls, REJECTED))
    with pytest.raises(RuntimeError):
        pub._push(None, {})
    assert len(calls) == 1


def test_a_link_that_stays_down_gives_up_after_the_retries(monkeypatch):
    calls = []
    monkeypatch.setattr(pub, "_git", failing_git(calls, RESOLVER_FAILURE))
    with pytest.raises(RuntimeError):
        pub._push(None, {})
    assert len(calls) == pub.PUSH_RETRIES + 1
