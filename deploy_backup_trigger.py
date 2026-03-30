import boto3
import json

lambda_client = boto3.client('lambda', region_name='us-west-2')
events_client = boto3.client('events', region_name='us-west-2')

# Fetch account ID dynamically — never hard-code it
AWS_ACCOUNT_ID = boto3.client('sts').get_caller_identity()['Account']
AWS_REGION = 'us-west-2'

# 1. Get existing trigger lambda
resp = lambda_client.get_function(FunctionName='trigger-rotation-oregon')
role_arn = resp['Configuration']['Role']

# Code for the new lambda
code_str = """import boto3
import logging
import uuid
logger = logging.getLogger()
logger.setLevel(logging.INFO)
SECRET_ID = 'db-rotation-backup-secret'
def lambda_handler(event, context):
    client = boto3.client('secretsmanager', region_name='us-west-2') 
    try:
        metadata = client.describe_secret(SecretId=SECRET_ID)
        versions = metadata.get('VersionIdsToStages', {})
        for version_id, stages in versions.items():
            if 'AWSPENDING' in stages and 'AWSCURRENT' not in stages:
                client.update_secret_version_stage(SecretId=SECRET_ID, VersionStage='AWSPENDING', RemoveFromVersionId=version_id)
        try:
            client.cancel_rotate_secret(SecretId=SECRET_ID)
        except Exception:
            pass
        response = client.rotate_secret(SecretId=SECRET_ID, ClientRequestToken=str(uuid.uuid4()))
        return {'statusCode': 200, 'body': 'Rotation triggered for ' + SECRET_ID}
    except Exception as e:
        logger.error(e)
        raise e
"""

import zipfile
with zipfile.ZipFile('trigger_backup.zip', 'w') as z:
    z.writestr('lambda_function.py', code_str)

# 2. Create new lambda
try:
    lambda_resp = lambda_client.create_function(
        FunctionName='trigger-rotation-backup',
        Runtime='python3.10',
        Role=role_arn,
        Handler='lambda_function.lambda_handler',
        Code={'ZipFile': open('trigger_backup.zip', 'rb').read()},
        Timeout=3,
        MemorySize=128
    )
    lambda_arn = lambda_resp['FunctionArn']
except lambda_client.exceptions.ResourceConflictException:
    lambda_arn = lambda_client.get_function(FunctionName='trigger-rotation-backup')['Configuration']['FunctionArn']

# 3. Create EventBridge rule for Backup
events_client.put_rule(
    Name='rotate-backup-user',
    ScheduleExpression='cron(2/5 * * * ? *)',
    State='ENABLED'
)

# Add permission to invoke lambda
try:
    lambda_client.add_permission(
        FunctionName='trigger-rotation-backup',
        StatementId='EventBridgeInvoke',
        Action='lambda:InvokeFunction',
        Principal='events.amazonaws.com',
        SourceArn=f'arn:aws:events:{AWS_REGION}:{AWS_ACCOUNT_ID}:rule/rotate-backup-user'
    )
except Exception:
    pass

# Put target
events_client.put_targets(
    Rule='rotate-backup-user',
    Targets=[{'Id': '1', 'Arn': lambda_arn}]
)

# 4. Update existing primary rule to 5-min cron
events_client.put_rule(
    Name='rotate-every-2min',
    ScheduleExpression='cron(0/5 * * * ? *)',
    State='ENABLED'
)

print("SUCCESS")
