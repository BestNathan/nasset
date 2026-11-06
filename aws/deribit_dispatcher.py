"""AWS EventBridge Scheduler -> GitHub Actions, with UTC-slot safety.

Only the exact, scheduled UTC hour may be dispatched. Credentials are stored
in AWS SSM Parameter Store SecureString, never in this package or Lambda env.
The operation is deliberately a single HTTP POST; a lost response must not
cause blind retries. Any duplicate dispatch is handled by archive receipts.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_WORKFLOW = "deribit-archive.yml"


def scheduled_slot(event: dict, *, now: datetime, minute: int = 12) -> str:
    """Validate Scheduler's genuine scheduled-time, not current-run time.

    Fail closed on stale retries, wrong cron minute, invalid timestamps, and
    next-hour crossings. A delayed event must never label a *current* quote
    with an old hourly archive filename.
    """
    if not isinstance(event, dict) or type(event.get("version")) is not int or event["version"] != 1:
        raise ValueError("invalid scheduler event version")
    raw = event.get("scheduled_time")
    if not isinstance(raw, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", raw):
        raise ValueError("missing/invalid AWS scheduled-time context")
    scheduled = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if scheduled.minute != minute or scheduled.second != 0:
        raise ValueError("unexpected scheduled cron minute")
    if now.tzinfo is None:
        raise ValueError("current time must be timezone aware")
    clock = now.astimezone(timezone.utc)
    lag = clock - scheduled
    if lag < timedelta(seconds=0) or lag > timedelta(minutes=20):
        raise RuntimeError("scheduled invocation expired or is in the future")
    if (clock.year, clock.month, clock.day, clock.hour) != (
        scheduled.year, scheduled.month, scheduled.day, scheduled.hour
    ):
        raise RuntimeError("scheduled invocation crossed UTC hourly boundary")
    return scheduled.strftime("%Y-%m-%dT%H:00:00Z")


def dispatch(repo: str, parameter: str, event: dict, *,
             minute: int, now: datetime, ssm, opener=urllib.request.urlopen) -> dict:
    """Dispatch only the preconfigured repository/branch/workflow.

    The GitHub fine-grained PAT needs only Actions:write on this one repo.
    Never put the token into logs, request URL, exception messages, or return.
    """
    if not _REPOSITORY.fullmatch(repo) or not parameter.startswith("/"):
        raise ValueError("invalid pinned GitHub repo or SSM parameter name")
    slot = scheduled_slot(event, now=now, minute=minute)
    token = ssm.get_parameter(Name=parameter, WithDecryption=True)["Parameter"]
    if token.get("Type") != "SecureString" or not token.get("Value"):
        raise RuntimeError("GitHub token must be stored as SSM SecureString")
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{_WORKFLOW}/dispatches"
    payload = json.dumps({
        "ref": "main",
        "inputs": {"publish": "true", "slot": slot},
    }, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Authorization": "Bearer " + token["Value"].strip(),
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "nasset-aws-scheduler/1",
    })
    try:
        with opener(req, timeout=10) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        # Deliberately omit response body and headers to avoid leaking secrets.
        raise RuntimeError(f"GitHub workflow dispatch rejected: HTTP {exc.code}") from None
    if status not in (200, 204):
        raise RuntimeError(f"unexpected GitHub workflow dispatch status: {status}")
    result = {"status": "accepted", "utc_slot": slot, "github_http_status": status}
    print(json.dumps(result, sort_keys=True))
    return result


def handler(event, _context):
    import boto3  # The AWS Lambda Python runtime supplies boto3.

    return dispatch(
        os.environ["GITHUB_REPOSITORY"],
        os.environ["GITHUB_TOKEN_PARAMETER"],
        event,
        minute=int(os.environ["SCHEDULE_MINUTE"]),
        now=datetime.now(timezone.utc),
        ssm=boto3.client("ssm"),
    )
