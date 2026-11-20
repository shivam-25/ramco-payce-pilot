#!/usr/bin/env bash
# The streaming transport: an API Gateway WebSocket in front of a Lambda that
# forwards the runtime's event stream frame by frame. Deployed beside the
# buffered path, never over it -- the console falls back if this is missing.
set -euo pipefail
cd "$(dirname "$0")/.."

FN=ramco-pilot-ws
SRC=ramco-pilot-proxy                   # borrow its role, arn and shared secret
API=ramco-pilot-ws-api
STAGE=live
ZIP=dist/ws.zip

ROLE=$(aws lambda get-function-configuration --function-name "$SRC" --query Role --output text)
ROLE_NAME=${ROLE##*/}
RUNTIME_ARN=$(aws lambda get-function-configuration --function-name "$SRC" \
                --query 'Environment.Variables.RUNTIME_ARN' --output text)
API_KEY=$(aws lambda get-function-configuration --function-name "$SRC" \
                --query 'Environment.Variables.API_KEY' --output text)
ACCT=$(aws sts get-caller-identity --query Account --output text)
REGION=${AWS_REGION:-ap-southeast-2}

echo "packaging $FN"
rm -rf dist/ws && mkdir -p dist/ws
cp infra/ws/handler.py dist/ws/
( cd dist/ws && zip -qr ../ws.zip . )

if aws lambda get-function --function-name "$FN" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FN" --zip-file "fileb://$ZIP" \
    --query LastUpdateStatus --output text
  aws lambda wait function-updated --function-name "$FN"
else
  aws lambda create-function --function-name "$FN" \
    --runtime python3.12 --role "$ROLE" --handler handler.handler \
    --zip-file "fileb://$ZIP" --timeout 120 --memory-size 512 \
    --environment "Variables={RUNTIME_ARN=$RUNTIME_ARN,API_KEY=$API_KEY,QUALIFIER=DEFAULT}" \
    --query FunctionArn --output text
  aws lambda wait function-active --function-name "$FN"
fi
aws lambda update-function-configuration --function-name "$FN" \
  --environment "Variables={RUNTIME_ARN=$RUNTIME_ARN,API_KEY=$API_KEY,QUALIFIER=DEFAULT}" \
  --timeout 120 --memory-size 512 >/dev/null
aws lambda wait function-updated --function-name "$FN"

# Pushing a frame back to the browser is a separate permission from invoking
# the runtime, and the proxy role did not have it.
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name ManageWsConnections \
  --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
    "Action":"execute-api:ManageConnections","Resource":"arn:aws:execute-api:*:*:*/*/POST/@connections/*"}]}'

FN_ARN=$(aws lambda get-function --function-name "$FN" --query 'Configuration.FunctionArn' --output text)
API_ID=$(aws apigatewayv2 get-apis --query "Items[?Name=='$API'].ApiId | [0]" --output text)
if [ "$API_ID" = "None" ] || [ -z "$API_ID" ]; then
  API_ID=$(aws apigatewayv2 create-api --name "$API" --protocol-type WEBSOCKET \
             --route-selection-expression '$request.body.action' \
             --query ApiId --output text)
  echo "created websocket api $API_ID"
fi

INT=$(aws apigatewayv2 create-integration --api-id "$API_ID" \
        --integration-type AWS_PROXY \
        --integration-uri "arn:aws:apigateway:$REGION:lambda:path/2015-03-31/functions/$FN_ARN/invocations" \
        --query IntegrationId --output text)
for R in '$connect' '$disconnect' '$default'; do
  EXIST=$(aws apigatewayv2 get-routes --api-id "$API_ID" \
            --query "Items[?RouteKey=='$R'].RouteId | [0]" --output text)
  if [ "$EXIST" = "None" ] || [ -z "$EXIST" ]; then
    aws apigatewayv2 create-route --api-id "$API_ID" --route-key "$R" \
      --target "integrations/$INT" >/dev/null
  else
    aws apigatewayv2 update-route --api-id "$API_ID" --route-id "$EXIST" \
      --target "integrations/$INT" >/dev/null
  fi
done

aws lambda add-permission --function-name "$FN" --statement-id ws-invoke \
  --action lambda:InvokeFunction --principal apigateway.amazonaws.com \
  --source-arn "arn:aws:execute-api:$REGION:$ACCT:$API_ID/*" >/dev/null 2>&1 || true

aws apigatewayv2 create-stage --api-id "$API_ID" --stage-name "$STAGE" \
  --auto-deploy >/dev/null 2>&1 || true
aws apigatewayv2 create-deployment --api-id "$API_ID" --stage-name "$STAGE" >/dev/null 2>&1 || true

echo "ws url: wss://$API_ID.execute-api.$REGION.amazonaws.com/$STAGE"
