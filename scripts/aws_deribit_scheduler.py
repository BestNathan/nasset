#!/usr/bin/env python3
"""Manage one AWS EventBridge Scheduler -> Lambda -> GitHub workflow dispatcher.

Local AWS profile/role supplies deployment credentials. No long-lived AWS keys
or GitHub tokens are accepted as CLI arguments or stored in the repository.
No AWS infrastructure is deployed on import or during 'plan'.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANAGED_TAG = {"Key": "nasset-managed", "Value": "deribit-scheduler"}
GH_REPO = "BestNathan/nasset"


def resources(prefix: str) -> dict[str, str]:
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,30}", prefix):
        raise ValueError("prefix must be 3-31 lowercase alphanumerics/hyphens")
    return {
        "lambda": prefix + "-github-dispatcher",
        "lambda_role": prefix + "-lambda-role",
        "scheduler_role": prefix + "-scheduler-role",
        "schedule": prefix + "-hourly",
        "group": "default",
    }


def schedule_expression(minute: int) -> str:
    if type(minute) is not int or not 5 <= minute <= 35:
        raise ValueError("schedule minute must be 5..35; allow execution before hour boundary")
    return f"cron({minute} * * * ? *)"


def schedule_payload() -> str:
    return json.dumps({"version": 1, "scheduled_time": "<aws.scheduler.scheduled-time>"},
                      separators=(",", ":"))


def lambda_zip(source: bytes) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo("deribit_dispatcher.py", date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        z.writestr(info, source)
    return output.getvalue()


def lambda_environment(args) -> dict:
    return {
        "Variables": {
            "GITHUB_REPOSITORY": GH_REPO,
            "GITHUB_TOKEN_PARAMETER": args.token_parameter,
            "SCHEDULE_MINUTE": str(args.minute),
        }
    }


def assume_role_policy(service: str, *, source_account=None, source_arn=None) -> dict:
    statement = {"Effect": "Allow", "Principal": {"Service": service},
                 "Action": "sts:AssumeRole"}
    if source_account:
        # Scheduler supplies a *schedule group* SourceArn, never the individual
        # schedule ARN (AWS confused-deputy prevention requirement).
        statement["Condition"] = {
            "StringEquals": {
                "aws:SourceAccount": source_account,
                "aws:SourceArn": source_arn,
            },
        }
    return {"Version": "2012-10-17", "Statement": [statement]}


def lambda_permissions(region: str, account: str, partition: str,
                       fn: str, parameter: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
             "Resource": f"arn:{partition}:logs:{region}:{account}:log-group:/aws/lambda/{fn}:*"},
            {"Effect": "Allow", "Action": "ssm:GetParameter",
             "Resource": f"arn:{partition}:ssm:{region}:{account}:parameter{parameter}"},
        ],
    }


def scheduler_permissions(function_arn: str) -> dict:
    return {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "lambda:InvokeFunction", "Resource": function_arn},
    ]}


def ensure_managed_tags(tags) -> None:
    if MANAGED_TAG not in tags:
        raise RuntimeError("resource name already exists but is not managed by nasset; refusing takeover")


def _json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def upsert_role(iam, name: str, trust: dict, policy: dict) -> str:
    try:
        value = iam.get_role(RoleName=name)["Role"]
        ensure_managed_tags(iam.list_role_tags(RoleName=name)["Tags"])
        iam.update_assume_role_policy(RoleName=name, PolicyDocument=_json(trust))
    except iam.exceptions.NoSuchEntityException:
        value = iam.create_role(
            RoleName=name, AssumeRolePolicyDocument=_json(trust),
            Description="Managed by nasset Deribit AWS scheduler",
            Tags=[MANAGED_TAG],
        )["Role"]
    iam.put_role_policy(RoleName=name, PolicyName="nasset-dispatcher-only",
                        PolicyDocument=_json(policy))
    return value["Arn"]


def ensure_secret(ssm, name: str) -> None:
    # Deliberately do not decrypt, print or otherwise expose the token here.
    parameter = ssm.get_parameter(Name=name, WithDecryption=False)["Parameter"]
    if parameter.get("Type") != "SecureString":
        raise RuntimeError("GitHub token parameter must be a SecureString")
    if parameter.get("Tier") not in (None, "Standard"):
        raise RuntimeError("use a Standard SSM parameter to avoid advanced-tier charges")


def create_or_update_lambda(client, config: dict, role_arn: str,
                            package: bytes, environment: dict) -> str:
    name = config["lambda"]
    digest = base64.b64encode(hashlib.sha256(package).digest()).decode("ascii")
    try:
        current = client.get_function(FunctionName=name)
        arn = current["Configuration"]["FunctionArn"]
        tags = client.list_tags(Resource=arn)["Tags"]
        ensure_managed_tags([{"Key": k, "Value": v} for k, v in tags.items()])
        if current["Configuration"]["CodeSha256"] != digest:
            client.update_function_code(FunctionName=name, ZipFile=package)
            client.get_waiter("function_updated_v2").wait(FunctionName=name)
        data = client.get_function_configuration(FunctionName=name)
        if (data.get("Role") != role_arn or data.get("Environment", {}).get("Variables") != environment["Variables"]
                or data.get("Timeout") != 20 or data.get("MemorySize") != 128
                or data.get("Handler") != "deribit_dispatcher.handler"):
            client.update_function_configuration(
                FunctionName=name, Role=role_arn, Handler="deribit_dispatcher.handler",
                Timeout=20, MemorySize=128, Environment=environment,
            )
            client.get_waiter("function_updated_v2").wait(FunctionName=name)
    except client.exceptions.ResourceNotFoundException:
        for attempt in range(8):
            try:
                value = client.create_function(
                    FunctionName=name, Runtime="python3.12",
                    Role=role_arn, Handler="deribit_dispatcher.handler",
                    Code={"ZipFile": package}, Timeout=20, MemorySize=128,
                    Environment=environment, Tags={MANAGED_TAG["Key"]: MANAGED_TAG["Value"]},
                    Publish=False,
                )
                arn = value["FunctionArn"]
                client.get_waiter("function_active_v2").wait(FunctionName=name)
                break
            except client.exceptions.InvalidParameterValueException as exc:
                if attempt == 7 or "cannot be assumed" not in str(exc).lower():
                    raise
                time.sleep(2)
    # Prevent Lambda's independent async retries from re-triggering old slots.
    client.put_function_event_invoke_config(
        FunctionName=name, MaximumRetryAttempts=0, MaximumEventAgeInSeconds=600,
    )
    return arn


def desired_schedule(args, role_arn: str, function_arn: str, *, state: str) -> dict:
    return {
        "Name": resources(args.prefix)["schedule"],
        "GroupName": "default",
        "ScheduleExpression": schedule_expression(args.minute),
        "ScheduleExpressionTimezone": "UTC",
        "FlexibleTimeWindow": {"Mode": "OFF"},
        "Target": {
            "Arn": function_arn, "RoleArn": role_arn,
            "Input": schedule_payload(),
            "RetryPolicy": {"MaximumEventAgeInSeconds": 600, "MaximumRetryAttempts": 2},
        },
        "Description": "nasset Deribit authenticated UTC-slot GitHub Actions dispatch",
        "State": state,
        "ActionAfterCompletion": "NONE",
    }


def owned_schedule(scheduler, names: dict, function_arn: str, role_arn: str):
    value = scheduler.get_schedule(Name=names["schedule"], GroupName=names["group"])
    target = value["Target"]
    if (target["Arn"] != function_arn or target["RoleArn"] != role_arn
            or target["Input"] != schedule_payload()):
        raise RuntimeError("existing schedule target is not nasset's; refusing takeover")
    return value


def apply(args, session, account: str, partition: str) -> dict:
    names = resources(args.prefix)
    region = session.region_name
    iam, lam, scheduler = session.client("iam"), session.client("lambda"), session.client("scheduler")
    ssm, logs = session.client("ssm"), session.client("logs")
    ensure_secret(ssm, args.token_parameter)
    arn = f"arn:{partition}:lambda:{region}:{account}:function:{names['lambda']}"
    scheduler_source = (
        f"arn:{partition}:scheduler:{region}:{account}:schedule-group/{names['group']}"
    )
    lambda_role = upsert_role(
        iam, names["lambda_role"],
        assume_role_policy("lambda.amazonaws.com"),
        lambda_permissions(region, account, partition, names["lambda"], args.token_parameter),
    )
    schedule_role = upsert_role(
        iam, names["scheduler_role"],
        assume_role_policy("scheduler.amazonaws.com", source_account=account,
                           source_arn=scheduler_source),
        scheduler_permissions(arn),
    )
    try:
        logs.create_log_group(logGroupName="/aws/lambda/" + names["lambda"])
    except logs.exceptions.ResourceAlreadyExistsException:
        pass
    logs.put_retention_policy(logGroupName="/aws/lambda/" + names["lambda"],
                              retentionInDays=14)
    source = (ROOT / "aws/deribit_dispatcher.py").read_bytes()
    actual_arn = create_or_update_lambda(
        lam, names, lambda_role, lambda_zip(source), lambda_environment(args),
    )
    try:
        old = owned_schedule(scheduler, names, actual_arn, schedule_role)
        # Applying updates an existing installation without inadvertently
        # enabling a previously paused schedule.
        params = desired_schedule(args, schedule_role, actual_arn, state=old["State"])
        scheduler.update_schedule(**params)
    except scheduler.exceptions.ResourceNotFoundException:
        params = desired_schedule(args, schedule_role, actual_arn, state="DISABLED")
        scheduler.create_schedule(**params)
    return {"schedule": names["schedule"], "expression": params["ScheduleExpression"],
            "state": params["State"], "function_arn": actual_arn,
            "region": region, "token_parameter": args.token_parameter}


def schedule_state(args, session, account: str, partition: str, desired: str | None):
    names = resources(args.prefix)
    arn = f"arn:{partition}:lambda:{session.region_name}:{account}:function:{names['lambda']}"
    role_arn = f"arn:{partition}:iam::{account}:role/{names['scheduler_role']}"
    scheduler = session.client("scheduler")
    current = owned_schedule(scheduler, names, arn, role_arn)
    if desired is not None:
        if current["State"] != desired:
            # UpdateSchedule replaces omitted optional fields: reconstruct
            # from the current schedule rather than resetting its retry policy.
            safe = {key: current[key] for key in (
                "Name", "GroupName", "ScheduleExpression", "ScheduleExpressionTimezone",
                "FlexibleTimeWindow", "Target", "Description",
                "ActionAfterCompletion",
            ) if key in current}
            safe["State"] = desired
            for key in ("StartDate", "EndDate", "KmsKeyArn"):
                if key in current:
                    safe[key] = current[key]
            scheduler.update_schedule(**safe)
        current["State"] = desired
    return {"schedule": names["schedule"], "state": current["State"],
            "expression": current["ScheduleExpression"], "target_arn": arn,
            "note": "schedule delivery is not proof of GitHub archive completion"}


def destroy(args, session, account: str, partition: str) -> dict:
    if not args.confirm_destroy:
        raise RuntimeError("destroy requires --confirm-destroy (SSM token and logs are retained)")
    names = resources(args.prefix)
    scheduler = session.client("scheduler")
    iam = session.client("iam")
    lam = session.client("lambda")
    arn = f"arn:{partition}:lambda:{session.region_name}:{account}:function:{names['lambda']}"
    role_arn = f"arn:{partition}:iam::{account}:role/{names['scheduler_role']}"
    try:
        current = owned_schedule(scheduler, names, arn, role_arn)
        if current["State"] != "DISABLED":
            raise RuntimeError("disable the schedule before destroy")
        scheduler.delete_schedule(Name=names["schedule"], GroupName=names["group"])
    except scheduler.exceptions.ResourceNotFoundException:
        pass
    try:
        current_fn = lam.get_function(FunctionName=names["lambda"])
        tags = lam.list_tags(Resource=current_fn["Configuration"]["FunctionArn"])["Tags"]
        ensure_managed_tags([{"Key": k, "Value": v} for k, v in tags.items()])
        lam.delete_function(FunctionName=names["lambda"])
    except lam.exceptions.ResourceNotFoundException:
        pass
    for role in (names["scheduler_role"], names["lambda_role"]):
        try:
            iam.get_role(RoleName=role)
            ensure_managed_tags(iam.list_role_tags(RoleName=role)["Tags"])
            iam.delete_role_policy(RoleName=role, PolicyName="nasset-dispatcher-only")
            iam.delete_role(RoleName=role)
        except iam.exceptions.NoSuchEntityException:
            pass
    return {"deleted": names["schedule"], "note": "SSM SecureString and CloudWatch logs retained"}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "apply", "status", "enable", "disable", "destroy"])
    parser.add_argument("--profile", help="AWS CLI profile; avoid pasting AK/SK into chat or code")
    parser.add_argument("--region", default="ap-southeast-1")
    parser.add_argument("--prefix", default="nasset-deribit")
    parser.add_argument("--minute", type=int, default=12)
    parser.add_argument("--token-parameter", default="/nasset/deribit/github-token")
    parser.add_argument("--confirm-destroy", action="store_true")
    args = parser.parse_args(argv)
    schedule_expression(args.minute)
    resources(args.prefix)
    if not re.fullmatch(r"/[A-Za-z0-9_.\-/]+", args.token_parameter):
        parser.error("token parameter must be an absolute SSM Parameter Store path")
    return args


def main(argv=None):
    args = parse_args(argv)
    names = resources(args.prefix)
    if args.command == "plan":
        result = {"schedule": names["schedule"], "cron": schedule_expression(args.minute),
                  "region": args.region, "function": names["lambda"],
                  "ssm_parameter": args.token_parameter,
                  "initial_schedule_state": "DISABLED",
                  "github_repo": GH_REPO, "github_workflow": "deribit-archive.yml",
                  "dry_run": True}
    else:
        import boto3

        kw = {"region_name": args.region}
        if args.profile:
            kw["profile_name"] = args.profile
        session = boto3.Session(**kw)
        identity = session.client("sts").get_caller_identity()
        account, partition = identity["Account"], identity["Arn"].split(":")[1]
        if args.command == "apply":
            result = apply(args, session, account, partition)
        elif args.command in ("enable", "disable", "status"):
            result = schedule_state(args, session, account, partition, {
                "enable": "ENABLED", "disable": "DISABLED", "status": None,
            }[args.command])
        else:
            result = destroy(args, session, account, partition)
    print(json.dumps(result, sort_keys=True))
    return result


if __name__ == "__main__":
    main()
