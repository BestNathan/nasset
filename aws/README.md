# AWS Deribit Scheduler — GitHub Actions deployment

**No local AWS installation, local git, or command-line configuration needed.**
The nasset repository deploys AWS EventBridge Scheduler and Lambda directly
from GitHub Actions using AWS IAM credentials stored as GitHub repository
Secrets.

## One-time configuration

Add these three **repository Secrets** at
[nasset → Settings → Secrets and variables → Actions](https://github.com/BestNathan/nasset/settings/secrets/actions):

| Secret | Value |
| --- | --- |
| `NASSET_AWS_ACCESS_KEY_ID` | Access Key ID of a dedicated AWS IAM user |
| `NASSET_AWS_SECRET_ACCESS_KEY` | Secret Access Key of the same IAM user |
| `NASSET_GITHUB_DISPATCH_TOKEN` | Fine-grained GitHub PAT for `BestNathan/nasset`, with `Actions: write` permission |

Use an IAM user with only the permissions needed to provision the nasset
resources. **Never use root-account keys**, commit credentials to the
repository, or send them in chat or issue comments.

The deployer requires permissions to:
- Inspect its account identity via `sts:GetCallerIdentity`.
- Manage `nasset-deribit-lambda-role` and
  `nasset-deribit-scheduler-role` via IAM create/get/tag/inline-policy/
  trust-policy/delete operations; `iam:PassRole` to only the Lambda and
  Scheduler service principals.
- Create, read, update and delete the exact
  `nasset-deribit-github-dispatcher` Lambda function.
- Create, inspect, update and delete the exact
  `default/nasset-deribit-hourly` EventBridge schedule.
- Get/put the `/nasset/deribit/github-token` SSM Standard SecureString
  parameter, and manage the Lambda's own CloudWatch log group/retention.

The AWS region is fixed to **ap-southeast-1 (Singapore)**. The official
`aws-actions/configure-aws-credentials` GitHub Action uses the two protected
secrets only in the main-branch deployment job and obtains credentials for
that GitHub-hosted job. It does not print access keys or put them into git.

The GitHub PAT is synchronized by the deployment manager to AWS Systems
Manager Parameter Store as a **Standard SecureString**, encrypted with the
AWS-managed `aws/ssm` key; the deployed Lambda only receives the SSM
**parameter name**, not the plaintext PAT. Rotation: update the repository
Secret then run the `reconcile` operation.

## Deploy without a local machine

The source of truth is
[`config/aws-deribit-scheduler.json`](../config/aws-deribit-scheduler.json).
It contains only a version, desired state and revision — **no secrets**.

GitHub Actions workflow:
[**AWS Deribit Scheduler deployment**](https://github.com/BestNathan/nasset/actions/workflows/aws-deribit-deploy.yml).

| State | Effect |
| --- | --- |
| `not_deployed` | No AWS API calls; first merge is safe even without Secrets |
| `disabled` | Reconcile IAM, SSM, Lambda and EventBridge, but keep Cron disabled |
| `enabled` | Reconcile resources and turn on the hourly EventBridge schedule |

To deploy, commit to `main`:

```json
{"version":1,"state":"disabled","revision":1}
```

This starts GitHub Actions. Check that its real AWS logs show success and
`schedule_state=DISABLED`; if an IAM permission is missing, fix that permission
and rerun the job instead of guessing that a green code test means deployment.

Only after verifying the resources exist, commit:

```json
{"version":1,"state":"enabled","revision":2}
```

The second job enables `cron(12 * * * ? *)` in UTC. To pause, commit
`state=disabled` and increment the revision, or run the manual `disable`
action. Manual `status` and `reconcile` operations are also available.

**Destroy** is deliberately guarded: first disable the schedule, then use
manual `destroy` with explicit confirmation. The encrypted token and log
history are retained. Changing to `not_deployed` is a no-op, **not** a teardown.

The main-branch-only workflow has GitHub `contents: read` and no pull-request
trigger. GitHub-side code changes to the deployment workflow can affect AWS
privileges; protect the `main` branch and restrict the IAM deployment key.

## Data-flow and validation

```text
AWS EventBridge Scheduler (UTC HH:12, no flexible delivery window)
  -> scoped Scheduler execution IAM role
  -> Lambda Python 3.12 (scoped Lambda execution IAM role)
  -> SSM SecureString /nasset/deribit/github-token
  -> GitHub workflow_dispatch { ref: main, publish: true, slot: HH:00Z }
  -> Deribit live read-only acceptance + publish gate
  -> immutable per-slot receipt in orphan market-data Git branch
```

AWS delivery acceptance is **not proof** that GitHub's collection completed.
After enabling, verify actual Lambda invocations, successful GitHub
`workflow_dispatch` with `publish` job executed, and the archived
`deribit/slots/YYYY/MM/DD/HH00.receipt` on `market-data`.
If a scheduled invocation starts too late, Lambda and GitHub fail closed;
a missed historical live quote is not reconstructible from a later snapshot.
Native GitHub cron can stay as a same-hour idempotent fallback.

Free tiers may cover the runtime workload, but AWS logging and other
supporting resources may still incur charges. No AWS resources are created by
merging workflow code alone.

## Alternative: GitHub OIDC

If you later prefer short-lived AWS credentials over static keys, a
main-branch-only [CloudFormation OIDC bootstrap](oidc-bootstrap.yml)
exists as an optional reference. **The active deployment workflow uses
AK/SK**, so the OIDC stack and the variable
`NASSET_AWS_DEPLOY_ROLE_ARN` are not required.

## References

- [GitHub encrypted repository Secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)
- [AWS GitHub credentials action](https://github.com/aws-actions/configure-aws-credentials)
- [EventBridge Scheduler context attributes](https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-schedule-context-attributes.html)
- [GitHub workflow dispatch API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)
