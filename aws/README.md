# AWS external scheduler for Deribit

## Recommended: deploy entirely from GitHub Actions (no local setup)

The AWS deployment is now **fully operated through GitHub Actions**.
You do not need Python, AWS CLI, `boto3`, a git checkout, or an AWS profile on
your own computer.

### Once: store three GitHub repository Secrets

Open **[nasset → Settings → Secrets and variables → Actions](https://github.com/BestNathan/nasset/settings/secrets/actions)**,
select **New repository secret**, and add exactly these:

| Repository secret | Value |
| --- | --- |
| `NASSET_AWS_ACCESS_KEY_ID` | Access Key ID of a dedicated AWS IAM deployment identity |
| `NASSET_AWS_SECRET_ACCESS_KEY` | Secret Access Key of that same AWS IAM identity |
| `NASSET_GITHUB_DISPATCH_TOKEN` | Fine-grained GitHub PAT for **BestNathan/nasset**, repository **Actions: write** only |

For **temporary** AWS credentials, optionally also set
`NASSET_AWS_SESSION_TOKEN` and rotate the full set before it expires.
Use AWS standard/commercial Region `ap-southeast-1` (Singapore), as configured
in the workflow. **Never use an AWS root account access key**, paste credentials
into chat, PRs, issue comments, YAML, or Git-tracked files, and never put a PAT
into the existing `market-data` archive branch.

The AWS IAM identity needs **provisioning** permissions for these explicitly
named resources (not just Lambda invocation): IAM role create/get/tag/update
trust/put inline policy/pass/delete, Lambda create/get/tag/configure/update/
delete, EventBridge Scheduler create/get/update/delete, SSM GetParameter and
PutParameter, CloudWatch Logs CreateLogGroup and PutRetentionPolicy, and STS
GetCallerIdentity. In the AWS console, grant these through a scoped permission
policy restricted to the `nasset-deribit-*` roles/function/schedule,
`/nasset/deribit/github-token` parameter, and matching log group.
`iam:PassRole` should be limited to the two nasset-managed roles and the
Lambda/Scheduler service principals. Consider a GitHub OIDC role instead of
long-lived AK/SK when you can configure the initial AWS trust relationship.

### Two-stage GitOps activation

The source of truth is
[`config/aws-deribit-scheduler.json`](../config/aws-deribit-scheduler.json),
initially:

```json
{"version": 1, "state": "not_deployed", "revision": 0}
```

It contains **no secrets or AWS credentials**. After you add the GitHub Secrets,
tell the maintainer (or ask ChatGPT to update the repository via its GitHub
connector) to commit these two successive changes:

1. Set `state="disabled"`, `revision=1`. The push to `main` launches
   [AWS Deribit Scheduler deployment](https://github.com/BestNathan/nasset/actions/workflows/aws-deribit-deploy.yml).
   The job takes the GitHub PAT *only* from a protected GitHub Secret and
   creates/rotates a **Standard SSM SecureString** via `boto3`. It then
   creates or updates IAM Roles, Lambda and an **initially DISABLED** AWS
   EventBridge schedule. No scheduler firing yet.
2. Verify the first workflow succeeded and inspected the AWS account/region/
   disabled schedule. Then set `state="enabled"`, `revision=2`. A second
   push to `main` runs the same workflow, converges the resources, and
   enables `cron(12 * * * ? *)` in UTC.

Further edits to Lambda code or the manager script do not automatically
deploy cloud changes; **reconciliation is deliberate**. Commit the control
file with `revision += 1`, or use the manual **Run workflow → reconcile**
operation from GitHub Actions. This also rotates the stored PAT when the
GitHub Secret has been updated.

To pause an active schedule, commit `state="disabled"` and increment the
revision (declarative), or use **Run workflow → disable** immediately as an
emergency override. The manual **status** operation checks AWS state without
needing the GitHub PAT secret. The manual **destroy** operation requires the
schedule to have been disabled first *and* a separate
`confirm_destroy=true` control; SSM PAT and CloudWatch logs are retained.
Changing to `not_deployed` simply stops reconciling and does **not** tear
down an existing deployed schedule; use `disabled` or confirmed `destroy`.

**Only `main` may deploy**. The deployment workflow has no PR trigger and
`contents: read` only, runs on GitHub-hosted ephemeral runners, never logs
the AWS Secret or GitHub PAT, and does not place the PAT in Lambda environment
variables. SSM encrypts it with AWS-managed `aws/ssm` by default.
A missing secret fails the deployment; adding Secrets alone does **not**
create cloud resources or enable a schedule.

Once enabled, each successful Scheduler invocation sends
`workflow_dispatch {publish:true, slot:UTC-hour}` to the existing
`deribit-archive.yml`; it does not perform the collection inside AWS.
Verify the **GitHub publish job** and the resulting **market-data receipt**.
An HTTP 204 is only proof that GitHub accepted the dispatch, not that
the archive was committed.

### Optional: using the script directly

The commands below are retained for AWS operators who prefer AWS profiles,
but **local execution is not required** by the GitHub Secrets deployment path.


This is an **optional, separately deployed external trigger** for the existing
GitHub Actions Deribit archive. It does not move market data or archival
permissions to AWS. AWS only sends a pinned `workflow_dispatch` request;
GitHub's read-only gate and immutable orphan `market-data` publisher remain the
source of truth.

## Components and security

```text
EventBridge Scheduler (UTC minute 12)
  -- scheduled-time --> Lambda (Python 3.12)
     -- SSM GetParameter SecureString --> fine-grained GitHub PAT
     -- workflow_dispatch { publish: true, slot: YYYY-MM-DDTHH:00:00Z }
        --> GitHub Actions (main + checked-in activation=true)
           --> Deribit live batch --> Git Data API --> orphan market-data
```

The deploy script manages a dedicated Scheduler execution role, a dedicated
Lambda execution role, the function, one UTC schedule, and a 14-day retention
policy for its CloudWatch log group. Its GitHub Actions reconcile operation
**also creates/rotates the SSM SecureString from a GitHub Secret**; it never
creates or manages an AWS account credential.

- Use an AWS **commercial** region with internet access to `api.github.com`;
  the default is `ap-southeast-1`. No VPC configuration is needed.
- Lambda IAM can only `ssm:GetParameter` for the configured path and append
  logs to its own log group. The AWS Scheduler role can invoke only this Lambda.
  Both IAM roles have limited trust policies; Scheduler trust is scoped to
  `arn:aws:scheduler:REGION:ACCOUNT:schedule-group/default` (not a schedule ARN).
- Place your fine-grained GitHub PAT in **AWS Systems Manager Parameter Store**,
  tier **Standard**, type **SecureString**, encrypted with the AWS-managed
  `aws/ssm` key. Restrict the PAT to `BestNathan/nasset` and **Actions: write**
  repository permission. This permission enables workflow dispatch but does
  not directly grant AWS GitHub `contents: write`; the existing GitHub job
  gate controls publication.
- AWS credentials are used only by the local deployment process. **Do not send
  AK/SK/PAT in chat**, in a GitHub issue, PR, workflow variable, source file,
  or tracked `.env`. Prefer AWS SSO or an assumed role. If using a long-lived
  AK/SK, configure it in a local named AWS profile with minimum required
  provisioning permissions; rotate if shared.
- Standard Parameter Store parameters have no additional storage charge,
  but applicable KMS, CloudWatch and network charges depend on usage, region
  and account. AWS Scheduler/Lambda have large free quotas, but not every
  auxiliary service is guaranteed free.

## Prepare once

1. Create an AWS account/profile with permissions to provision IAM roles,
   Lambda, EventBridge Scheduler, CloudWatch log groups and read the SSM
   parameter metadata. The deploying identity needs `iam:PassRole` for the
   two exact nasset-managed roles. Prefer restricting these AWS permissions
   to your own account, region, and resource name prefix.
2. Create the SecureString using the AWS Console: **Systems Manager ->
   Parameter Store -> Create parameter**. Name:
   `/nasset/deribit/github-token`, tier: **Standard**, type: **SecureString**,
   KMS key: default `alias/aws/ssm`. Paste the **fine-grained GitHub PAT**
   in the console. Avoid putting a token in a shell command line, history,
   CI log, or file in the repository.
3. On your own computer, install Python 3.12+, `boto3`, AWS CLI. For example:

   ```sh
   python3 -m pip install boto3
   aws configure --profile nasset
   aws sts get-caller-identity --profile nasset --region ap-southeast-1
   ```

   The named profile is local to your machine. `aws configure` stores access
   keys in the AWS CLI credential store; AWS SSO is preferable when available.

## Lifecycle commands

Run these in the **nasset repository root**.

```sh
# No credentials, network, or AWS mutations:
python3 scripts/aws_deribit_scheduler.py plan

# Idempotent: creates roles, Lambda, and a DISABLED UTC schedule.
python3 scripts/aws_deribit_scheduler.py apply --profile nasset

# Inspect effective schedule state (does not decrypt or print the PAT):
python3 scripts/aws_deribit_scheduler.py status --profile nasset

# Explicitly enable hourly dispatch after auditing deployment:
python3 scripts/aws_deribit_scheduler.py enable --profile nasset

# Stop future AWS-driven dispatch without changing GitHub's archive data:
python3 scripts/aws_deribit_scheduler.py disable --profile nasset

# Optional full teardown: first disable; token + log group are retained.
python3 scripts/aws_deribit_scheduler.py destroy --profile nasset --confirm-destroy
```

Defaults: Region `ap-southeast-1`, cron `cron(12 * * * ? *)` in UTC,
schedule `nasset-deribit-hourly`, Lambda
`nasset-deribit-github-dispatcher`, SSM parameter
`/nasset/deribit/github-token`. For alternate values, pass the same
`--region`, `--prefix`, `--minute` and `--token-parameter` flags to each
command. `apply` preserves an existing schedule's state instead of
silently enabling something the operator paused.

The AWS management script uses **boto3** but the nasset collector does not
depend on boto3. For the no-local cloud deployment workflow, the three GitHub
repository Secrets listed above are required.

## Confirm actual dispatch, not just scheduled invocation

1. After enabling, check the AWS EventBridge Scheduler configuration, CloudWatch
   Lambda invocation metrics and log group `/aws/lambda/nasset-deribit-github-dispatcher`.
   Each successful invocation logs a structured `status=accepted` and the
   intended `utc_slot`, never the token.
2. Find a **`workflow_dispatch`** run of `deribit-archive.yml` on GitHub
   Actions. The `publish` job must run (not be skipped) and complete.
3. Verify that `market-data` contains the corresponding
   `deribit/slots/YYYY/MM/DD/HH00.receipt` and
   `HH00.manifest.json`, and that the commit is non-forced, append-only.
   Repeated invocations of the same slot are no-ops when receipts match.
4. If the scheduled AWS invocation arrives more than 20 minutes late, the Lambda
   fails closed. If GitHub starts the job after its requested hour, the
   GitHub workflow also rejects publication. This is intentional: the live
   quote at a later time **cannot** be passed off as a missed historical
   hourly option quote.

**Important:** EventBridge Scheduler considers invoking Lambda successfully as
delivery; it does **not** wait for GitHub to finish its workflow. Monitor Lambda
`Errors`, GitHub workflow conclusions and exact archive receipts. AWS retry
limits are bounded; automatic replay of ambiguous GitHub POST responses is not
implemented. The existing native GitHub cron may remain as a fallback, and
the archive receipt handles duplicate same-hour requests safely. Missed
original quote snapshots cannot be backfilled faithfully.

## Reference

- [AWS Scheduler context attributes](https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-schedule-context-attributes.html)
- [AWS Scheduler Lambda targets](https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-targets-templated.html)
- [Scheduler trust policy must use a schedule-group ARN](https://docs.aws.amazon.com/scheduler/latest/UserGuide/cross-service-confused-deputy-prevention.html)
- [GitHub workflow dispatch endpoint](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)
