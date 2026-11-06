# AWS external scheduler for Deribit

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
policy for its CloudWatch log group. It **does not** create or manage the SSM
secret, GitHub token, or AWS account credentials.

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
depend on boto3. No new GitHub repository secrets or actions are required.

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
