"""Offline AWS Scheduler management contract tests (no AWS credentials)."""
import io
import json
import runpy
import zipfile

import pytest

S = runpy.run_path("scripts/aws_deribit_scheduler.py")


def test_plan_requires_no_boto3_or_aws_credentials(capsys):
    plan = S["main"](["plan"])
    assert plan["dry_run"] is True
    assert plan["cron"] == "cron(12 * * * ? *)"
    assert plan["initial_schedule_state"] == "DISABLED"
    assert plan["github_workflow"] == "deribit-archive.yml"
    assert "github-token" in plan["ssm_parameter"]
    assert "github_http_token" not in capsys.readouterr().out


def test_schedule_minute_has_safe_before_hour_margin():
    assert S["schedule_expression"](12) == "cron(12 * * * ? *)"
    assert S["schedule_expression"](35) == "cron(35 * * * ? *)"
    for bad in (0, 4, 36, 60, True, 1.0):
        with pytest.raises(ValueError):
            S["schedule_expression"](bad)


def test_lambda_archive_is_self_contained_and_deterministic():
    source = b"def handler(event, context):\n    return True\n"
    payload = S["lambda_zip"](source)
    assert payload == S["lambda_zip"](source)
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        assert z.namelist() == ["deribit_dispatcher.py"]
        assert z.read("deribit_dispatcher.py") == source


def test_scheduler_target_contains_real_scheduled_time_not_lambda_wall_clock():
    args = S["parse_args"](["plan"])
    task = S["desired_schedule"](
        args, "arn:aws:iam::123456789012:role/sched",
        "arn:aws:lambda:ap-southeast-1:123456789012:function:fn",
        state="DISABLED",
    )
    assert task["State"] == "DISABLED"
    assert task["FlexibleTimeWindow"] == {"Mode": "OFF"}
    assert task["ScheduleExpressionTimezone"] == "UTC"
    assert task["Target"]["RetryPolicy"] == {
        "MaximumEventAgeInSeconds": 600, "MaximumRetryAttempts": 2,
    }
    assert json.loads(task["Target"]["Input"]) == {
        "version": 1, "scheduled_time": "<aws.scheduler.scheduled-time>",
    }


def test_limited_aws_iam_permissions_for_scheduler_and_lambda():
    policy = S["scheduler_permissions"]("arn:aws:lambda:region:account:function:only-one")
    assert policy["Statement"][0]["Action"] == "lambda:InvokeFunction"
    assert policy["Statement"][0]["Resource"].endswith(":only-one")
    source = "arn:aws:scheduler:ap-southeast-1:123456789012:schedule-group/default"
    trust = S["assume_role_policy"](
        "scheduler.amazonaws.com", source_account="123456789012", source_arn=source,
    )
    assert trust["Statement"][0]["Condition"]["StringEquals"]["aws:SourceArn"] == source
    role = S["lambda_permissions"]("ap-southeast-1", "123456789012", "aws",
                                    "my-function", "/nasset/deribit/github-token")
    statements = role["Statement"]
    assert len(statements) == 2
    assert set(statements[0]["Action"]) == {"logs:CreateLogStream", "logs:PutLogEvents"}
    assert statements[1]["Action"] == "ssm:GetParameter"
    assert statements[1]["Resource"].endswith("parameter/nasset/deribit/github-token")


def test_plan_rejects_bad_names_without_aws_access():
    for name in ("", "../foo", "nasset/foo", "Nasset-deribit", "ab"):
        with pytest.raises(ValueError):
            S["resources"](name)


def test_existing_schedule_cannot_be_taken_over():
    class MockScheduler:
        def get_schedule(self, **kw):
            return {"Target": {
                "Arn": "arn:somebody-else", "RoleArn": "wrong", "Input": "{}",
            }}

    with pytest.raises(RuntimeError, match="refusing takeover"):
        S["owned_schedule"](MockScheduler(), S["resources"]("nasset-deribit"),
                            "expected", "expected")


def test_destroy_requires_explicit_confirmation():
    args = S["parse_args"](["destroy"])
    with pytest.raises(RuntimeError, match="--confirm-destroy"):
        S["destroy"](args, None, "123456789012", "aws")


def test_managed_existing_resources_require_tag():
    with pytest.raises(RuntimeError, match="not managed"):
        S["ensure_managed_tags"]([])
