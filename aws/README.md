# AWS external scheduler for Deribit — GitHub OIDC

**No AWS Access Key / Secret Access Key, no local AWS CLI, no local repository
setup.** GitHub Actions assumes an AWS IAM role with a short-lived OIDC token.
The AWS role is restricted to `BestNathan/nasset` on `refs/heads/main`.

The only GitHub secret required for runtime dispatch is a **fine-grained GitHub
PAT** limited to repository `BestNathan/nasset`, permission **Actions: write**.
It is copied securely from GitHub Actions Secrets into an AWS Systems Manager
**Standard SecureString** parameter and only decrypted by the Lambda at
invocation time. Never put this secret or AWS credentials in issues, commits,
workflow variables, logs or this conversation.

## Step 1 — One-time AWS Console bootstrap

GitHub cannot assume a new AWS role until your AWS account has explicitly
granted trust. This **one-time AWS console operation is unavoidable**; it does
not require creating AK/SK, installing scripts, or using a local computer
terminal.

1. Open [AWS CloudFormation in Singapore](https://ap-southeast-1.console.aws.amazon.com/cloudformation/home?region=ap-southeast-1#/stacks/create). Choose **Create stack** → **With new resources (standard)**.
2. Get [`oidc-bootstrap.yml`](oidc-bootstrap.yml) from this repository
   (use **Raw → Download**, or the browser's **Save** action). Select
   **Choose an existing template → Upload a template file**. The file is a
   readable CloudFormation template, not an executable script.
3. Stack name: `nasset-deribit-oidc-bootstrap`. Set
   `OidcProviderExists=false` if your AWS account does **not** already
   have `token.actions.githubusercontent.com` as an IAM Identity Provider.
   If one exists, set `OidcProviderExists=true` so it is reused instead of
   attempting to create an incompatible duplicate.
4. Review the IAM permissions displayed by CloudFormation, acknowledge
   **CAPABILITY_NAMED_IAM**, and create the stack. The stack creates
   `nasset-deribit-github-deployer` and optionally the GitHub OIDC provider.
   It does **not** deploy or activate EventBridge/Lambda yet.
5. Once the stack is **CREATE_COMPLETE**, open **Outputs** and copy
   `DeploymentRoleArn`.

The trust policy accepts only OIDC tokens with
`aud=sts.amazonaws.com` and
`sub=repo:BestNathan/nasset:ref:refs/heads/main`.
A PR, fork, other repository or other branch cannot assume the role.
The permission policy limits AWS mutations to the exact nasset Deribit
Lambda, Scheduler, SSM parameter, log group and two runtime IAM roles.
There is **no AdministratorAccess policy**. Protect your GitHub `main`
branch: anyone who can modify the deployment workflow on `main` can
exercise this scoped AWS role.

Alternatively, from **AWS CloudShell** *inside the AWS web console*,
the same template can be deployed without local tooling:

```bash
curl -fsSL https://raw.githubusercontent.com/BestNathan/nasset/main/aws/oidc-bootstrap.yml -o /tmp/nasset-oidc-bootstrap.yml
aws cloudformation deploy \
  --template-file /tmp/nasset-oidc-bootstrap.yml \
  --stack-name nasset-deribit-oidc-bootstrap \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-southeast-1
```

Add `--parameter-overrides OidcProviderExists=true` if the GitHub OIDC
identity provider already exists. Prefer the regular CloudFormation UI if
you want to review the changes before creation.

## Step 2 — One GitHub Actions Variable + one Secret

Open [nasset → Settings → Secrets and variables → Actions](https://github.com/BestNathan/nasset/settings/secrets/actions):

| Type | Name | Value |
| --- | --- | --- |
| **Variable** | `NASSET_AWS_DEPLOY_ROLE_ARN` | `DeploymentRoleArn` from CloudFormation Outputs (not confidential) |
| **Secret** | `NASSET_GITHUB_DISPATCH_TOKEN` | Fine-grained GitHub PAT for **BestNathan/nasset**, repository **Actions: write** only |

Do **not** create `NASSET_AWS_ACCESS_KEY_ID`,
`NASSET_AWS_SECRET_ACCESS_KEY` or `NASSET_AWS_SESSION_TOKEN`.
These old Secrets are no longer read by the deployment workflow. If you
previously added them, delete them and revoke the unused AWS IAM access
key in AWS IAM. The configuration workflow uses
[`aws-actions/configure-aws-credentials`](https://github.com/aws-actions/configure-aws-credentials)
with `id-token: write` to get temporary AWS STS credentials.
The AWS role ARN is a GitHub **Variable**, not a Secret.

AWS region is pinned to **ap-southeast-1 (Singapore)** in both the
CloudFormation role and the GitHub deployment workflow.

## Step 3 — Deploy disabled, then enable explicitly

Desired state lives in
[`config/aws-deribit-scheduler.json`](../config/aws-deribit-scheduler.json),
initially:

```json
{"version": 1, "state": "not_deployed", "revision": 0}
```

The deployment workflow
[`aws-deribit-deploy.yml`](../.github/workflows/aws-deribit-deploy.yml)
runs only on `main`: on a push that changes this policy file, or
manually from GitHub Actions. PR events **never** receive an AWS OIDC
credential.

- **First change:** commit `{"version":1,"state":"disabled","revision":1}`
  to `main`. GitHub Actions assumes the AWS role via OIDC; the deployment
  script stores/rotates the PAT in AWS SSM Standard SecureString and creates
  the dedicated runtime roles, Lambda, CloudWatch log group and a
  **DISABLED** `nasset-deribit-hourly` schedule. Inspect the run.
- **Second change:** after a successful disabled deployment, commit
  `{"version":1,"state":"enabled","revision":2}`. A new GitHub run
  reconciles and enables the schedule.
- For future code changes, increment `revision` to reconcile, or use
  **Actions → AWS Deribit Scheduler deployment → Run workflow → reconcile**.
  Manual `status`, emergency `disable`, and guarded `destroy` are also
  supported. `destroy` requires prior disable and an explicit confirmation;
  it keeps the SSM secret and historical logs.
- `not_deployed` is a **no-op**, not a teardown. A fresh merge of this
  OIDC implementation does not require an AWS account or GitHub secrets and
  does not create cloud resources.

**Actual production validation:** after enabling, check the AWS
EventBridge schedule state, Lambda invocation logs/metrics, GitHub
`workflow_dispatch` run of `deribit-archive.yml`, the `publish` job,
and the exact immutable UTC-slot receipt on `market-data`.
An AWS schedule invocation or GitHub HTTP 204 alone does **not** prove
a market-data batch was committed.

## Architecture and operational boundaries

```text
EventBridge Scheduler (cron(12 * * * ? *), UTC, no flexible window)
   -> dedicated Scheduler role
   -> Lambda Python 3.12 (dedicated execution role)
   -> SSM /nasset/deribit/github-token (encrypted SecureString)
   -> GitHub workflow_dispatch {ref: main, publish: true, slot: UTC hour}
   -> GitHub Actions production activation / acceptance / publish gates
   -> Deribit live quotes -> market-data immutable orphan branch
```

- The Lambda checks the genuine
  `<aws.scheduler.scheduled-time>` context before dispatch and rejects
  malformed/stale (>20 minute) invocations. The GitHub workflow rejects
  an expired hour before writing. Missing earlier live quotes must never
  be fabricated or relabeled as freshly captured historical snapshots.
- AWS Scheduler has a 10-minute event-age/retry limit and Lambda's own
  asynchronous event retries are disabled; archive receipts provide
  at-most-once *publication per UTC slot*, not guaranteed delivery.
- The GitHub-native cron may remain as a fallback; duplicated same-hour
  events are safe when the immutable slot receipt matches.
- AWS Scheduler/Lambda have free allowances, but CloudWatch Logs and
  other auxiliary resources may incur costs. No production AWS resources
  have been created merely by committing this configuration.
- If the OIDC provider or deployment role already exists and belongs to
  another stack, **do not overwrite it blindly**: use
  `OidcProviderExists=true` or inspect the other stack first.

## References

- [AWS IAM GitHub OIDC roles and trust conditions](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-idp_oidc.html)
- [AWS CloudFormation OIDC provider](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-iam-oidcprovider.html)
- [AWS configure-aws-credentials action](https://github.com/aws-actions/configure-aws-credentials)
- [Scheduler context attributes](https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-schedule-context-attributes.html)
- [GitHub workflow dispatch endpoint](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)
