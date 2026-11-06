"""GitHub-Secrets-only AWS provisioning contracts; no AWS account required."""
from pathlib import Path
import json
import runpy
from types import SimpleNamespace

import pytest
import yaml

S = runpy.run_path("scripts/aws_deribit_scheduler.py")
ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config/aws-deribit-scheduler.json"
WORKFLOW = ROOT / ".github/workflows/aws-deribit-deploy.yml"


def test_default_policy_cannot_call_aws_or_enable_scheduler(capsys, monkeypatch):
    policy = S["load_cloud_policy"]()
    assert policy == {"version": 1, "state": "not_deployed", "revision": 0}
    # boto3 and credentials are not loaded in this state; a merge is safe.
    result = S["main"](["reconcile"])
    assert result == {"status": "skipped", "reason": "AWS deployment not requested"}
    assert "token" not in capsys.readouterr().out.lower()


@pytest.mark.parametrize("invalid", [
    {}, [], None,
    {"version": 1, "state": "enabled"},
    {"version": True, "state": "disabled", "revision": 1},
    {"version": 1, "state": "enabled", "revision": True},
    {"version": 1, "state": "enabled", "revision": 0},
    {"version": 1, "state": "enabled", "revision": -1},
    {"version": 1, "state": "something_else", "revision": 1},
    {"version": 1, "state": "disabled", "revision": 1, "extra": "bad"},
])
def test_deployment_policy_fails_closed(invalid):
    with pytest.raises(ValueError, match="AWS scheduler deployment policy"):
        S["validate_cloud_policy"](invalid)


@pytest.mark.parametrize("state", ["not_deployed", "disabled", "enabled"])
def test_deployment_state_valid(state):
    rev = 0 if state == "not_deployed" else 1
    assert S["validate_cloud_policy"]({
        "version": 1, "state": state, "revision": rev,
    })["state"] == state


class MissingParameter(Exception):
    pass


class FakeSSM:
    exceptions = SimpleNamespace(ParameterNotFound=MissingParameter)

    def __init__(self, existing=None):
        self.existing = existing
        self.calls = []

    def get_parameter(self, **kwargs):
        self.calls.append(("get", kwargs))
        if self.existing is None:
            raise MissingParameter()
        return {"Parameter": self.existing}

    def put_parameter(self, **kwargs):
        self.calls.append(("put", kwargs))
        return {"Version": 1}


def test_github_secret_to_ssm_standard_securestring_only(capsys):
    secret = "github_pat_example_not_a_real_token"
    ssm = FakeSSM()
    result = S["sync_token_from_secret"](
        ssm, "/nasset/deribit/github-token", secret,
    )
    assert result == {
        "ssm_parameter": "/nasset/deribit/github-token",
        "type": "SecureString", "tier": "Standard", "updated": False,
    }
    put = ssm.calls[1][1]
    assert put["Value"] == secret
    assert put["Type"] == "SecureString"
    assert put["Tier"] == "Standard"
    assert put["Overwrite"] is False
    assert "KeyId" not in put  # use AWS-managed aws/ssm KMS key
    assert secret not in json.dumps(result)
    assert secret not in capsys.readouterr().out


def test_token_rotation_updates_only_secure_standard_parameter():
    secret = "rotated_github_token"
    ssm = FakeSSM({"Type": "SecureString", "Tier": "Standard"})
    result = S["sync_token_from_secret"](
        ssm, "/nasset/deribit/github-token", secret,
    )
    assert result["updated"] is True
    assert ssm.calls[1][1]["Overwrite"] is True


@pytest.mark.parametrize("parameter", [
    {"Type": "String", "Tier": "Standard"},
    {"Type": "SecureString", "Tier": "Advanced"},
])
def test_refuse_to_take_over_unexpected_ssm_secret(parameter):
    ssm = FakeSSM(parameter)
    with pytest.raises(RuntimeError, match="refusing"):
        S["sync_token_from_secret"](
            ssm, "/nasset/deribit/github-token", "dummy-token",
        )
    assert len(ssm.calls) == 1


@pytest.mark.parametrize("token", ["", " trailing", "a\nb", "a\tb", "x" * 4097])
def test_reject_bad_secret_before_aws_call(token):
    ssm = FakeSSM()
    with pytest.raises(ValueError):
        S["sync_token_from_secret"](
            ssm, "/nasset/deribit/github-token", token,
        )
    assert ssm.calls == []


def test_reconcile_requires_github_pat_before_any_mutation(monkeypatch):
    class Session:
        def client(self, name):
            pytest.fail("AWS client should not be called before token validation")

    monkeypatch.delenv("NASSET_GITHUB_DISPATCH_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="NASSET_GITHUB_DISPATCH_TOKEN"):
        S["reconcile_cloud_policy"](
            S["parse_args"](["reconcile"]), Session(), "123456789012", "aws",
            {"version": 1, "state": "enabled", "revision": 1},
        )


@pytest.mark.parametrize("state,expected", [
    ("disabled", "DISABLED"), ("enabled", "ENABLED"),
])
def test_reconcile_updates_secret_before_lambda_and_sets_desired_state(
        state, expected, monkeypatch):
    calls = []
    class Session:
        def client(self, name):
            assert name == "ssm"
            return "ssm-client"

    def secret(ssm, path, token):
        calls.append(("secret", ssm, token))
    def create(args, session, account, partition):
        calls.append(("apply",))
        return {"region": "ap-southeast-1", "function_arn": "test-function"}
    def schedule(args, session, account, partition, desired):
        calls.append(("state", desired))
        return {"state": desired, "schedule": "nasset-deribit-hourly"}

    fn = S["reconcile_cloud_policy"]
    monkeypatch.setitem(fn.__globals__, "sync_token_from_secret", secret)
    monkeypatch.setitem(fn.__globals__, "apply", create)
    monkeypatch.setitem(fn.__globals__, "schedule_state", schedule)
    monkeypatch.setenv("NASSET_GITHUB_DISPATCH_TOKEN", "not-a-real-token")
    result = fn(
        S["parse_args"](["reconcile"]), Session(), "123456789012", "aws",
        {"version": 1, "state": state, "revision": 2},
    )
    assert [step[0] for step in calls] == ["secret", "apply", "state"]
    assert calls[-1][1] == expected
    assert result["schedule_state"] == expected
    assert "not-a-real-token" not in json.dumps(result)


def test_deployment_workflow_only_runs_on_main_without_pr_or_schedule():
    root = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    triggers = root["on"]
    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"] == {
        "branches": ["main"], "paths": ["config/aws-deribit-scheduler.json"],
    }
    assert root["permissions"] == {"contents": "read"}
    assert root["concurrency"]["cancel-in-progress"] == "false"
    assert set(root["jobs"]) == {"manage"}
    job = root["jobs"]["manage"]
    assert "github.repository == 'BestNathan/nasset'" in job["if"]
    assert "github.ref == 'refs/heads/main'" in job["if"]
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert job["env"]["AWS_DEFAULT_REGION"] == "ap-southeast-1"
    assert "AWS_ACCESS_KEY_ID" not in job["env"]
    assert "AWS_SECRET_ACCESS_KEY" not in job["env"]
    steps = job["steps"]
    aws = next(step for step in steps if step.get("name", "").startswith("Obtain short-lived"))
    assert aws["uses"] == "aws-actions/configure-aws-credentials@v6.3.0"
    assert aws["if"] == "${{ steps.gate.outputs.need_aws == 'true' }}"
    assert aws["with"]["role-to-assume"] == "${{ vars.NASSET_AWS_DEPLOY_ROLE_ARN }}"
    assert aws["with"]["aws-region"] == "ap-southeast-1"
    assert aws["with"]["unset-current-credentials"] == "true"
    assert aws["with"]["mask-aws-account-id"] == "true"
    gate = next(step for step in steps if step.get("id") == "gate")
    assert "not_deployed" in gate["run"]
    assert 'echo "need_aws=false" >> "$GITHUB_OUTPUT"' in gate["run"]
    assert "NASSET_AWS_DEPLOY_ROLE_ARN" in next(
        step for step in steps if step.get("name", "").startswith("Validate OIDC")
    )["run"]
    assert "NASSET_AWS_ACCESS_KEY_ID" not in WORKFLOW.read_text()
    assert "NASSET_AWS_SECRET_ACCESS_KEY" not in WORKFLOW.read_text()
    assert "NASSET_AWS_SESSION_TOKEN" not in WORKFLOW.read_text()

    assert any(s.get("name") == "Reconcile Git-managed desired state" and
               s["env"]["NASSET_GITHUB_DISPATCH_TOKEN"] ==
               "${{ secrets.NASSET_GITHUB_DISPATCH_TOKEN }}"
               for s in steps)
    assert any(s.get("name") == "Confirm explicit teardown" and
               "CONFIRM_DESTROY" in s["env"]
               for s in steps)
    assert all(s.get("with", {}).get("persist-credentials") in (None, "false")
               for s in steps)


def test_management_workflow_has_only_exact_allowed_operations():
    root = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    manual = root["on"]["workflow_dispatch"]["inputs"]
    assert manual["operation"]["options"] == [
        "status", "reconcile", "disable", "destroy",
    ]
    assert manual["confirm_destroy"]["default"] == "false"
    assert "pull_request" not in root["on"]


def test_cloudformation_bootstrap_scopes_oidc_to_main_only_and_no_wildcard_iam():
    from yaml.nodes import ScalarNode, SequenceNode, MappingNode
    class CfnLoader(yaml.SafeLoader):
        pass

    def cfn_tag(loader, suffix, node):
        if isinstance(node, ScalarNode):
            return loader.construct_scalar(node)
        if isinstance(node, SequenceNode):
            return loader.construct_sequence(node)
        if isinstance(node, MappingNode):
            return loader.construct_mapping(node)
        raise AssertionError("unsupported YAML node")

    CfnLoader.add_multi_constructor("!", cfn_tag)
    template = yaml.load(
        (ROOT / "aws/oidc-bootstrap.yml").read_text(), Loader=CfnLoader,
    )
    assert template["Parameters"]["OidcProviderExists"]["Default"] == "false"
    assert template["Resources"]["GitHubOidcProvider"]["Type"] == "AWS::IAM::OIDCProvider"
    assert template["Resources"]["GitHubOidcProvider"]["Properties"]["Url"] == (
        "https://token.actions.githubusercontent.com"
    )
    assert template["Resources"]["GitHubOidcProvider"]["Properties"]["ClientIdList"] == [
        "sts.amazonaws.com"
    ]
    role = template["Resources"]["NassetDeribitDeployRole"]["Properties"]
    assert role["RoleName"] == "nasset-deribit-github-deployer"
    trust = role["AssumeRolePolicyDocument"]["Statement"]
    assert len(trust) == 1
    assert trust[0]["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert trust[0]["Condition"]["StringEquals"] == {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": "repo:BestNathan/nasset:ref:refs/heads/main",
    }
    grants = role["Policies"][0]["PolicyDocument"]["Statement"]
    assert len(grants) == 6
    for grant in grants:
        assert grant["Effect"] == "Allow"
        assert grant["Resource"] not in ("*", ["*"])
        assert "iam:CreateOpenIDConnectProvider" not in grant["Action"]
    pass_roles = next(g for g in grants if g["Sid"] == "PassOnlyRuntimeRolesToTargetServices")
    assert pass_roles["Action"] == "iam:PassRole"
    assert pass_roles["Condition"]["StringEquals"]["iam:PassedToService"] == [
        "lambda.amazonaws.com", "scheduler.amazonaws.com",
    ]
    assert all("nasset-deribit-" in arn for arn in pass_roles["Resource"])
    assert template["Outputs"]["DeploymentRoleArn"]["Description"].find(
        "NASSET_AWS_DEPLOY_ROLE_ARN"
    ) != -1


def test_cloudformation_template_does_not_require_aws_static_keys():
    source = (ROOT / "aws/oidc-bootstrap.yml").read_text()
    for identifier in (
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "NASSET_AWS_SECRET_ACCESS_KEY",
        "AdministratorAccess", "repo:BestNathan/nasset:pull_request",
    ):
        assert identifier not in source
