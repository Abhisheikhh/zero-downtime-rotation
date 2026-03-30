import boto3
import json
import zipfile
import io

lambda_client = boto3.client('lambda', region_name='us-west-2')
events_client = boto3.client('events', region_name='us-west-2')
iam_client    = boto3.client('iam')

# Fetch account ID dynamically — never hard-code it
AWS_ACCOUNT_ID = boto3.client('sts').get_caller_identity()['Account']
AWS_REGION = 'us-west-2'

PRIMARY_FUNCTION_NAME = 'trigger-rotation-primary'
PRIMARY_SECRET_ID     = 'db-rotation-new-secret'
EVENTBRIDGE_RULE_NAME = 'rotate-primary-user'
SCHEDULE              = 'cron(0/5 * * * ? *)'   # every 5 min, starting at :00

# ── Lambda role ───────────────────────────────────────────────────────────────
# Reuse the same execution role as the existing rotation Lambda, or supply your own.
ROLE_ARN = f'arn:aws:iam::{AWS_ACCOUNT_ID}:role/lambda-secrets-rotation-role'

# ── Inline trigger code ───────────────────────────────────────────────────────
code_str = """
import boto3
import logging
import uuid

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SECRET_ID = 'db-rotation-new-secret'

def lambda_handler(event, context):
    client = boto3.client('secretsmanager', region_name='us-west-2')
    try:
        # Clear any stuck AWSPENDING version before triggering a fresh rotation
        metadata = client.describe_secret(SecretId=SECRET_ID)
        versions = metadata.get('VersionIdsToStages', {})
        for version_id, stages in versions.items():
            if 'AWSPENDING' in stages and 'AWSCURRENT' not in stages:
                client.update_secret_version_stage(
                    SecretId=SECRET_ID,
                    VersionStage='AWSPENDING',
                    RemoveFromVersionId=version_id
                )
        try:
            client.cancel_rotate_secret(SecretId=SECRET_ID)
        except Exception:
            pass

        response = client.rotate_secret(
            SecretId=SECRET_ID,
            ClientRequestToken=str(uuid.uuid4())
        )
        logger.info(f"Rotation triggered: {response}")
        return {'statusCode': 200, 'body': 'Rotation triggered for ' + SECRET_ID}

    except Exception as e:
        logger.error(e)
        raise e
"""

# ── Build ZIP in-memory ───────────────────────────────────────────────────────
zip_buffer = io.BytesIO()
with zipfile.ZipFile(zip_buffer, 'w') as zf:
    zf.writestr('lambda_function.py', code_str)
zip_bytes = zip_buffer.getvalue()

# ── 1. Create (or update) the primary trigger Lambda ─────────────────────────
try:
    lambda_resp = lambda_client.create_function(
        FunctionName=PRIMARY_FUNCTION_NAME,
        Runtime='python3.10',
        Role=ROLE_ARN,
        Handler='lambda_function.lambda_handler',
        Code={'ZipFile': zip_bytes},
        Timeout=30,
        MemorySize=128
    )
    lambda_arn = lambda_resp['FunctionArn']
    print(f"Created Lambda: {lambda_arn}")
except lambda_client.exceptions.ResourceConflictException:
    # Function already exists — update its code instead
    lambda_client.update_function_code(
        FunctionName=PRIMARY_FUNCTION_NAME,
        ZipFile=zip_bytes
    )
    lambda_arn = lambda_client.get_function(
        FunctionName=PRIMARY_FUNCTION_NAME
    )['Configuration']['FunctionArn']
    print(f"Updated existing Lambda: {lambda_arn}")

# ── 2. Create EventBridge rule ────────────────────────────────────────────────
events_client.put_rule(
    Name=EVENTBRIDGE_RULE_NAME,
    ScheduleExpression=SCHEDULE,
    State='ENABLED'
)
print(f"EventBridge rule '{EVENTBRIDGE_RULE_NAME}' set to: {SCHEDULE}")

# ── 3. Grant EventBridge permission to invoke the Lambda ─────────────────────
try:
    lambda_client.add_permission(
        FunctionName=PRIMARY_FUNCTION_NAME,
        StatementId='EventBridgeInvokePrimary',
        Action='lambda:InvokeFunction',
        Principal='events.amazonaws.com',
        SourceArn=f'arn:aws:events:{AWS_REGION}:{AWS_ACCOUNT_ID}:rule/{EVENTBRIDGE_RULE_NAME}'
    )
except lambda_client.exceptions.ResourceConflictException:
    pass  # Permission already exists

# ── 4. Register Lambda as the EventBridge target ──────────────────────────────
events_client.put_targets(
    Rule=EVENTBRIDGE_RULE_NAME,
    Targets=[{'Id': '1', 'Arn': lambda_arn}]
)
print(f"Target set: {lambda_arn}")

print("\nSUCCESS — Primary rotation trigger deployed.")
print(f"  Lambda  : {PRIMARY_FUNCTION_NAME}")
print(f"  Schedule: {SCHEDULE}  (rotates at :00, :05, :10 ...)")
print(f"  Secret  : {PRIMARY_SECRET_ID}")
