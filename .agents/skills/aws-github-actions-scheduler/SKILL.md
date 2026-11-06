---
name: aws-github-actions-scheduler
description: Provision, verify, troubleshoot and safely manage a reusable AWS EventBridge Scheduler -> Lambda -> GitHub Actions workflow_dispatch trigger. Use when a project needs reliable cron-based GitHub Actions triggering instead of relying on GitHub's native scheduled workflows.
---

# AWS-triggered GitHub Actions

## Goal and boundaries

Build a **reusable scheduling integration**, not a domain-specific data pipeline. AWS EventBridge Scheduler invokes a small Lambda; Lambda reads a fine-grained GitHub token from AWS Systems Manager Parameter Store SecureString and calls GitHub's workflow dispatch API. The target workflow performs the actual project-specific work.

Use the existing nasset implementation as an **example**, not as a universal template with hardcoded project identifiers:
- `scripts/aws_deribit_scheduler.py`: provisioning, reconciliation, status, disable, destroy.
- `aws/deribit_dispatcher.py`: scheduled-time validation and authenticated dispatch.
- `.github/workflows/aws-deribit-deploy.yml`: GitHub-controlled deployment.
- `config/aws-deribit-scheduler.json`: versioned desired state.
- `.github/workflows/deribit-archive.yml`: example receiving workflow.

Before implementing in another repository, inspect its instructions, workflows, desired schedule, permission model, runtime, and target workflow inputs. Ask only for genuinely missing requirements. Do not copy nasset-specific names, regions, datasets, or cron values blindly.

## Architecture

```text
EventBridge Scheduler (cron + timezone, flexible window OFF)
  -> IAM scheduler execution role (lambda:InvokeFunction on one function)
  -> Lambda (validate scheduled_time and reject stale/wrong-slot events)
  -> SSM SecureString (ssm:GetParameter on one parameter)
  -> GitHub REST POST /repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches
     Authorization: Bearer <fine-grained PAT>
     {"ref":"main","inputs":{...}}
  -> GitHub Actions workflow_dispatch
```

The Lambda execution role should have only the required SSM read and CloudWatch Logs permissions. The GitHub fine-grained PAT needs access to the **target repository** and **Actions: write** permission; configure additional permissions only when the target workflow itself needs them. Keep the token out of Lambda environment variables, source, repository files, workflow logs, command-line arguments and returned values. Store it in SSM SecureString and rotate via approved secret-management paths.

## Deployment workflow

1. **Plan**: choose AWS account/region, schedule name, UTC cron/timezone, target repository/workflow/ref, inputs and credential ownership. Confirm whether an existing schedule is already managed; do not take over unmanaged resources.
2. **IAM**: create a dedicated Lambda execution role (logs + scoped SSM read) and Scheduler execution role (scoped Lambda invoke). Restrict trust to the appropriate AWS services, account and supported source ARN. Prefer GitHub Actions OIDC for AWS deployment credentials when available; if existing access keys are used, keep them in GitHub Actions secrets with minimal permissions and rotation.
3. **Secrets**: create SSM SecureString through an approved secure channel; validate existence and type without printing plaintext. Do not embed the PAT in generated code or JSON.
4. **Lambda**: package a minimal Python handler. Validate Scheduler's `<aws.scheduler.scheduled-time>` input, expected minute, clock skew and staleness; refuse wrong-hour replay. Retrieve token at invocation, POST GitHub dispatch with timeout and sanitized error handling. A 204 means GitHub accepted the request, **not** that the workflow finished.
5. **Scheduler**: create a schedule targeting the Lambda with `FlexibleTimeWindow.Mode=OFF`, explicit timezone and bounded retries/event age. Initially deploy **disabled** until credentials, target workflow and permissions have been checked. Avoid enabling an already-disabled installation accidentally during updates.
6. **GitHub**: target workflow must declare `workflow_dispatch` and its inputs. Ensure target branch/ref and permissions match the intent. Deploy/reconcile only from a protected main branch or trusted environment; never expose AWS credentials to fork PRs.
7. **Enable**: after reviewing the plan and validating deployment, explicitly enable the schedule. Keep desired state in version control when using GitOps, and make changes idempotent. Avoid needless redeployments or schedule recreation.
8. **Verify**: observe at least one real scheduled invocation and corresponding GitHub run. Inspect failed steps and distinguish invocation from successful project work.

Example generic receiving workflow:

```yaml
name: Scheduled task
on:
  workflow_dispatch:
    inputs:
      slot:
        description: "UTC hour (YYYY-MM-DDTHH:00:00Z)"
        required: true
        type: string
permissions:
  contents: read
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - run: echo "Validated slot supplied by trusted dispatcher"
```

This is an illustration, **not** a secure slot validator; implement actual input validation and replay/idempotency controls for production jobs. GitHub Actions workflows also require attention to YAML parser behavior around `on` when generating them programmatically.

## Verification — three separate claims

- **Configured/enabled**: GitHub policy plus successful deployment logs indicate intended AWS state; an authorized live AWS `GetSchedule` result confirms actual state. A checked-in `enabled` value alone is insufficient.
- **GitHub dispatch accepted**: Lambda HTTP 204 and/or a matching GitHub `workflow_dispatch` run. Correlate event, timestamp, ref and inputs. A run's existence alone may not prove AWS provenance if manual dispatch is also allowed.
- **Workflow completed**: inspect GitHub run and job conclusions; for any downstream persistence/output, verify its own durable evidence separately. HTTP 204 is not job success.

Report each status as verified, failed or unverified with timestamps, run links, and the exact evidence source. Do not claim CloudWatch access without an AWS connector or other authorized AWS execution surface.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| No invocation | Schedule state, cron/timezone, Scheduler execution-role trust and Lambda invoke permissions, delivery retries; AWS metrics/logs if accessible |
| Lambda fails before dispatch | Scheduled-time payload, wrong minute, stale event, Lambda execution role, SSM parameter/type, runtime/network |
| GitHub API 401/403 | Token validity, repository access, Actions:write, token rotation; never log the token |
| GitHub API 404/422 | Repository, workflow file, ref, workflow_dispatch declaration and input schema |
| HTTP 204 but no identifiable run | GitHub Actions run list, event filters, propagation delay, target ref, concurrent/manual triggers |
| Run exists but failed | Workflow job logs and target project's business logic; distinguish scheduler health from job success |
| Duplicate run | EventBridge retries, ambiguous HTTP response, concurrency and application-level idempotency |

Avoid blind retries when the HTTP response is lost: the dispatch may have succeeded. A deterministic invocation/slot identifier and downstream idempotency are preferable to assuming exactly-once delivery. For urgent incidents, prefer safe diagnosis over re-running destructive workflows.

## Operations and safety

- Reconcile should be repeatable and should preserve intentional disabled state unless explicitly asked to enable it.
- Provide separate `plan`, `status`, `reconcile`, `enable`, `disable`, and guarded `destroy` operations. Destroy requires explicit confirmation and should preserve secrets/logs unless separate deletion is authorized.
- For repository changes, prefer a branch and PR; do not mutate the deployment policy just to test documentation.
- Use only an authorized GitHub connector for GitHub reads/writes when instructed; do not silently substitute local git. Do not invent AWS status, CloudWatch logs, successful dispatches or credentials.
- Do not paste AWS AK/SK, GitHub PAT, decrypted SSM values, or Authorization headers into chat, logs, PRs or source files.

## Completion checklist

Document the target repo/workflow/ref, AWS region, schedule expression/timezone, Lambda name, IAM roles, SSM parameter **name only**, GitHub token permissions, deployment PR/run, actual schedule state, real dispatch run, downstream job conclusion, and any outstanding checks. Only claim end-to-end success after each relevant layer is independently verified.
