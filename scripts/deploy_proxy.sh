#!/usr/bin/env bash
# Function URL in front of the AgentCore runtime, so the Vercel UI can call
# the pilot without ever holding AWS credentials.
set -euo pipefail

REGION=ap-southeast-2
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
FN=ramco-pilot-proxy
ROLE=RamcoPilotProxyRole
RUNTIME_ARN="arn:aws:bedrock-agentcore:$REGION:$ACCOUNT:runtime/ramco_payce_pilot-DQrgLS3Ay9"
KEY_FILE=${1:?usage: deploy_proxy.sh <file containing the shared secret>}
API_KEY=$(cat "$KEY_FILE")

if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  aws iam attach-role-policy --role-name "$ROLE" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  echo "created role $ROLE; waiting for IAM to propagate"
  sleep 12
fi

# One action, one resource: this function can run the pilot and nothing else.
aws iam put-role-policy --role-name "$ROLE" --policy-name invoke-pilot-only \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"bedrock-agentcore:InvokeAgentRuntime\",\"Resource\":[\"$RUNTIME_ARN\",\"$RUNTIME_ARN/*\"]}]}" >/dev/null
ROLE_ARN=$(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text)

TMP=$(mktemp -d)
cp infra/proxy/handler.py "$TMP/index.py"
(cd "$TMP" && zip -q function.zip index.py)

if aws lambda get-function --function-name "$FN" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FN" \
    --zip-file "fileb://$TMP/function.zip" --query LastUpdateStatus --output text
  aws lambda wait function-updated --function-name "$FN"
  aws lambda update-function-configuration --function-name "$FN" \
    --environment "Variables={RUNTIME_ARN=$RUNTIME_ARN,API_KEY=$API_KEY,QUALIFIER=DEFAULT}" \
    --query LastUpdateStatus --output text
else
  aws lambda create-function --function-name "$FN" --runtime python3.12 \
    --architectures arm64 --handler index.handler --timeout 60 --memory-size 256 \
    --role "$ROLE_ARN" --zip-file "fileb://$TMP/function.zip" \
    --environment "Variables={RUNTIME_ARN=$RUNTIME_ARN,API_KEY=$API_KEY,QUALIFIER=DEFAULT}" \
    --query FunctionArn --output text
fi
rm -rf "$TMP"
aws lambda wait function-updated --function-name "$FN"

if ! aws lambda get-function-url-config --function-name "$FN" >/dev/null 2>&1; then
  # Authenticated by the x-pilot-key header, not by SigV4: the browser-facing
  # server route cannot sign requests without AWS credentials, and putting
  # credentials on Vercel is the thing this function exists to avoid.
  aws lambda create-function-url-config --function-name "$FN" --auth-type NONE \
    --cors '{"AllowOrigins":["*"],"AllowHeaders":["content-type","x-pilot-key"],"AllowMethods":["POST"]}' \
    --query FunctionUrl --output text
  aws lambda add-permission --function-name "$FN" --statement-id public-url \
    --action lambda:InvokeFunctionUrl --principal '*' \
    --function-url-auth-type NONE
fi
aws lambda get-function-url-config --function-name "$FN" --query FunctionUrl --output text
