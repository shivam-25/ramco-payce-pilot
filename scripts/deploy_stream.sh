#!/usr/bin/env bash
# The streaming proxy: a Node Lambda behind a Function URL in RESPONSE_STREAM
# mode. Deployed beside ramco-pilot-proxy, never over it, so a bad deploy here
# cannot take the console down -- the page falls back to the buffered path.
set -euo pipefail
cd "$(dirname "$0")/.."

FN=ramco-pilot-stream
SRC=ramco-pilot-proxy                   # borrow its role, arn and shared secret
REGION=${AWS_REGION:-ap-southeast-2}
ZIP=dist/stream.zip

ROLE=$(aws lambda get-function-configuration --function-name "$SRC" \
         --query Role --output text)
RUNTIME_ARN=$(aws lambda get-function-configuration --function-name "$SRC" \
         --query 'Environment.Variables.RUNTIME_ARN' --output text)
API_KEY=$(aws lambda get-function-configuration --function-name "$SRC" \
         --query 'Environment.Variables.API_KEY' --output text)

echo "packaging $FN"
rm -rf dist/stream && mkdir -p dist/stream
cp infra/stream/index.mjs dist/stream/
# The v3 SDK client for AgentCore is not in the Lambda Node runtime's bundled
# SDK, so it ships with the function.
( cd dist/stream \
  && npm init -y >/dev/null 2>&1 \
  && npm install --omit=dev --silent @aws-sdk/client-bedrock-agentcore >/dev/null 2>&1 )
( cd dist/stream && zip -qr "../stream.zip" . )

if aws lambda get-function --function-name "$FN" >/dev/null 2>&1; then
  echo "updating code"
  aws lambda update-function-code --function-name "$FN" \
    --zip-file "fileb://$ZIP" --query LastUpdateStatus --output text
  aws lambda wait function-updated --function-name "$FN"
else
  echo "creating $FN"
  aws lambda create-function --function-name "$FN" \
    --runtime nodejs22.x --role "$ROLE" --handler index.handler \
    --zip-file "fileb://$ZIP" --timeout 120 --memory-size 512 \
    --environment "Variables={RUNTIME_ARN=$RUNTIME_ARN,API_KEY=$API_KEY,QUALIFIER=DEFAULT}" \
    --query FunctionArn --output text
  aws lambda wait function-active --function-name "$FN"
fi

aws lambda update-function-configuration --function-name "$FN" \
  --environment "Variables={RUNTIME_ARN=$RUNTIME_ARN,API_KEY=$API_KEY,QUALIFIER=DEFAULT}" \
  --timeout 120 --memory-size 512 >/dev/null
aws lambda wait function-updated --function-name "$FN"

# RESPONSE_STREAM is the whole point: BUFFERED would hold every byte until the
# handler returned, which is exactly what the Python proxy already does.
if ! aws lambda get-function-url-config --function-name "$FN" >/dev/null 2>&1; then
  aws lambda create-function-url-config --function-name "$FN" \
    --auth-type NONE --invoke-mode RESPONSE_STREAM \
    --cors '{"AllowOrigins":["*"],"AllowMethods":["POST"],"AllowHeaders":["content-type","x-pilot-key"]}' \
    --query FunctionUrl --output text
  aws lambda add-permission --function-name "$FN" \
    --statement-id public-url --action lambda:InvokeFunctionUrl \
    --principal '*' --function-url-auth-type NONE >/dev/null
else
  aws lambda update-function-url-config --function-name "$FN" \
    --auth-type NONE --invoke-mode RESPONSE_STREAM >/dev/null
fi

URL=$(aws lambda get-function-url-config --function-name "$FN" \
        --query FunctionUrl --output text)
echo "stream url: $URL"
