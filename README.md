# 🔐 Zero-Downtime AWS RDS Credential Rotation

A production-ready implementation of **zero-downtime database credential rotation** using AWS Secrets Manager, Lambda, and EventBridge — with staggered dual-user rotation so the database is never unreachable.

---

## Architecture

```
EventBridge  cron(0/5 * * * ? *)  →  trigger-rotation-primary  →  rotates primary secret
EventBridge  cron(2/5 * * * ? *)  →  trigger-rotation-backup   →  rotates backup secret
                                                                          ↓
                                              lambda_function.py (rotation handler)
                                                                          ↓
                                                              RDS MySQL (password updated)
```

- **Primary user** rotates every 5 minutes (at :00, :05, :10 …)
- **Backup user** rotates every 5 minutes, **staggered by 2 minutes** (at :02, :07, :12 …)
- While one user is rotating, the other is always available → **zero downtime**

---

## 📄 Documentation

For a detailed walkthrough of the architecture and setup steps, see the full guide:

👉 [Zero-Downtime Dual-Secret Rotation Guide](docs/Zero-Downtime-Dual-Secret-Rotation-Guide.pdf)


---

## Files

| File | Purpose |
|------|---------|
| `lambda_function.py` | Secrets Manager rotation Lambda handler (master-user strategy, RDS MySQL) |
| `deploy_primary_trigger.py` | One-time script to deploy the **primary** rotation Lambda + EventBridge rule |
| `deploy_backup_trigger.py` | One-time script to deploy the **backup** rotation Lambda + EventBridge rule |

---

## Prerequisites

- AWS CLI configured (IAM role or user with Secrets Manager + Lambda + EventBridge permissions)
- Python 3.10+
- An RDS MySQL instance
- Two Secrets Manager secrets:
  - `db-rotation-new-secret` — primary app user credentials
  - `db-rotation-backup-secret` — backup app user credentials
- The rotation Lambda deployed and connected to both secrets in Secrets Manager

Install Python dependencies for the Lambda layer:

```bash
pip install pymysql
```

---

## Deploying the Backup Trigger

Run **once** to create the backup rotation Lambda and its staggered EventBridge schedule:

```bash
python deploy_backup_trigger.py
```

> No AWS account IDs are hardcoded — the account ID is fetched dynamically at runtime via STS.

---

## Security

- **No credentials in code.** Passwords are fetched at runtime from AWS Secrets Manager.
- **No hardcoded AWS Account IDs.** `deploy_backup_trigger.py` uses `boto3 STS` to resolve the account ID dynamically.
- Authentication via IAM roles — never static access keys.
- `.gitignore` excludes `.env` files, `*.pem` keys, `*.zip` deployment packages, and binary files.

---

## License

MIT-0 — See file headers for attribution.
