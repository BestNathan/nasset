"""No-AWS-credentials tests for the externally scheduled GitHub dispatcher."""
import io
import json
import runpy
from datetime import datetime, timezone

import pytest

MODULE = runpy.run_path("aws/deribit_dispatcher.py")
scheduled_slot = MODULE["scheduled_slot"]
dispatch = MODULE["dispatch"]

EVENT = {"version": 1, "scheduled_time": "2026-11-06T05:12:00Z"}
NOW = datetime(2026, 11, 6, 5, 12, 36, tzinfo=timezone.utc)


class FakeSSM:
    def __init__(self, value="dummy-github-token", kind="SecureString"):
        self.value, self.kind, self.calls = value, kind, []

    def get_parameter(self, **kw):
        self.calls.append(kw)
        return {"Parameter": {"Type": self.kind, "Value": self.value}}


class Response:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def test_exact_hour_from_aws_context():
    assert scheduled_slot(EVENT, now=NOW) == "2026-11-06T05:00:00Z"


@pytest.mark.parametrize("event", [
    {}, {"version": 1}, {"version": "1", "scheduled_time": EVENT["scheduled_time"]},
    {"version": 1, "scheduled_time": "2026-11-06T05:13:00Z"},
    {"version": 1, "scheduled_time": "2026-11-06T05:12:30Z"},
    {"version": 1, "scheduled_time": "2026-11-06T05:12:00+00:00"},
    {"version": 1, "scheduled_time": "<aws.scheduler.scheduled-time>"},
])
def test_rejects_invalid_scheduler_context(event):
    with pytest.raises((ValueError, RuntimeError)):
        scheduled_slot(event, now=NOW)


def test_dropped_or_stale_event_is_never_backfilled_with_current_quote():
    with pytest.raises(RuntimeError, match="expired"):
        scheduled_slot(EVENT, now=datetime(2026, 11, 6, 5, 33, tzinfo=timezone.utc))
    with pytest.raises(RuntimeError, match="expired"):
        scheduled_slot(EVENT, now=datetime(2026, 11, 6, 5, 11, tzinfo=timezone.utc))
    with pytest.raises(RuntimeError, match="expired"):
        scheduled_slot(EVENT, now=datetime(2026, 11, 6, 6, 1, tzinfo=timezone.utc))


def test_dispatch_single_authenticated_post_and_pinned_slot(capsys):
    ssm = FakeSSM()
    requests = []

    def opener(req, timeout):
        requests.append((req, timeout))
        return Response()

    result = dispatch("BestNathan/nasset", "/nasset/deribit/github-token", EVENT,
                      minute=12, now=NOW, ssm=ssm, opener=opener)
    assert result == {
        "status": "accepted",
        "utc_slot": "2026-11-06T05:00:00Z",
        "github_http_status": 204,
    }
    assert ssm.calls == [{
        "Name": "/nasset/deribit/github-token", "WithDecryption": True,
    }]
    assert len(requests) == 1
    req, timeout = requests[0]
    assert timeout == 10
    assert req.full_url == (
        "https://api.github.com/repos/BestNathan/nasset/actions/workflows/"
        "deribit-archive.yml/dispatches"
    )
    assert req.get_method() == "POST"
    assert json.loads(req.data) == {
        "ref": "main", "inputs": {"publish": "true", "slot": "2026-11-06T05:00:00Z"},
    }
    assert req.get_header("Authorization") == "Bearer dummy-github-token"
    assert "dummy-github-token" not in capsys.readouterr().out


def test_secret_must_be_securestring():
    ssm = FakeSSM(kind="String")
    with pytest.raises(RuntimeError, match="SecureString"):
        dispatch("BestNathan/nasset", "/nasset/token", EVENT, minute=12, now=NOW,
                 ssm=ssm, opener=lambda *_args, **_kw: None)


def test_stale_event_never_fetches_secret_or_dispatches():
    ssm = FakeSSM()
    with pytest.raises(RuntimeError):
        dispatch("BestNathan/nasset", "/nasset/token", EVENT, minute=12,
                 now=datetime(2026, 11, 6, 6, 1, tzinfo=timezone.utc),
                 ssm=ssm, opener=lambda *_args, **_kw: None)
    assert ssm.calls == []


def test_http_403_fails_without_leaking_secret():
    import urllib.error

    ssm = FakeSSM("ultra-secret-token")
    calls = []

    def opener(req, timeout):
        calls.append(req)
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b"secret"))

    with pytest.raises(RuntimeError, match="HTTP 403") as err:
        dispatch("BestNathan/nasset", "/nasset/token", EVENT, minute=12, now=NOW,
                 ssm=ssm, opener=opener)
    assert "ultra-secret-token" not in str(err.value)
    assert len(calls) == 1
