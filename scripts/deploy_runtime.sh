#!/usr/bin/env bash
# Deploy the Ramco Payce pilot to Bedrock AgentCore Runtime.
#
# The runtime runs INSIDE the VPC. That is the whole point: the two databases
# are not publicly accessible, so the only thing that can reach them is a
# workload holding the RuntimeSG security group. Bedrock, Secrets Manager and
# S3 are reached over VPC endpoints rather than the internet.
#
# Run it from the project root with AWS scoped by ../aws-env.sh.
set -euo pipefail

REGION=ap-southeast-2
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
NAME=ramco_payce_pilot
ROLE=RamcoPilotAgentCoreRole
STACK=ramco-payce-pilot
BUCKET=bedrock-agentcore-runtime-${ACCOUNT}-${REGION}-e1kgo71hz
ZIP=dist/ramco-runtime.zip

out() { aws cloudformation describe-stacks --stack-name "$STACK" \
          --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }

SG=$(out RuntimeSecurityGroup)
# The private subnets, not the default VPC's public ones. In VPC mode the
# runtime's network interfaces need a default route to the NAT gateway; a
# public subnet's interfaces get no public IP and therefore no egress at all,
# which is how the first deploy came up READY and then 400'd every call.
SUBNETS=$(out RuntimeSubnetList)
EP_IN=$(out EndpointIN)
EP_UAE=$(out EndpointUAE)
echo "sg=$SG subnets=$SUBNETS"

# ---- execution role --------------------------------------------------------
# Scoped deliberately: three model ids, two secrets, the artifact bucket, and
# the ENI permissions AgentCore needs to attach to the VPC. Not a wildcard.
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document "{
    \"Version\":\"2012-10-17\",
    \"Statement\":[{
      \"Effect\":\"Allow\",
      \"Principal\":{\"Service\":\"bedrock-agentcore.amazonaws.com\"},
      \"Action\":\"sts:AssumeRole\",
      \"Condition\":{
        \"StringEquals\":{\"aws:SourceAccount\":\"$ACCOUNT\"},
        \"ArnLike\":{\"aws:SourceArn\":\"arn:aws:bedrock-agentcore:$REGION:$ACCOUNT:*\"}}}]}" >/dev/null
  echo "created role $ROLE"
fi

aws iam put-role-policy --role-name "$ROLE" --policy-name pilot-runtime --policy-document "{
  \"Version\":\"2012-10-17\",
  \"Statement\":[
    {\"Sid\":\"Models\",\"Effect\":\"Allow\",
     \"Action\":[\"bedrock:InvokeModel\",\"bedrock:InvokeModelWithResponseStream\"],
     \"Resource\":[
       \"arn:aws:bedrock:$REGION::foundation-model/*\",
       \"arn:aws:bedrock:*::foundation-model/*\",
       \"arn:aws:bedrock:$REGION:$ACCOUNT:inference-profile/*\"]},
    {\"Sid\":\"GuardrailScreening\",\"Effect\":\"Allow\",
     \"Action\":[\"bedrock:ApplyGuardrail\"],
     \"Resource\":[\"arn:aws:bedrock:$REGION:$ACCOUNT:guardrail/*\"]},
    {\"Sid\":\"DbCredentialsOnly\",\"Effect\":\"Allow\",
     \"Action\":\"secretsmanager:GetSecretValue\",
     \"Resource\":[\"$(out SecretArnIN)\",\"$(out SecretArnUAE)\"]},
    {\"Sid\":\"Artifact\",\"Effect\":\"Allow\",
     \"Action\":[\"s3:GetObject\",\"s3:ListBucket\"],
     \"Resource\":[\"arn:aws:s3:::$BUCKET\",\"arn:aws:s3:::$BUCKET/*\"]},
    {\"Sid\":\"Vpc\",\"Effect\":\"Allow\",
     \"Action\":[\"ec2:CreateNetworkInterface\",\"ec2:DescribeNetworkInterfaces\",
                 \"ec2:DeleteNetworkInterface\",\"ec2:DescribeSubnets\",
                 \"ec2:DescribeSecurityGroups\",\"ec2:DescribeVpcs\"],
     \"Resource\":\"*\"},
    {\"Sid\":\"Telemetry\",\"Effect\":\"Allow\",
     \"Action\":[\"logs:CreateLogGroup\",\"logs:CreateLogStream\",
                 \"logs:PutLogEvents\",\"logs:DescribeLogStreams\",
                 \"cloudwatch:PutMetricData\",\"xray:PutTraceSegments\",
                 \"xray:PutTelemetryRecords\",
                 \"bedrock-agentcore:GetWorkloadAccessToken\",
                 \"bedrock-agentcore:GetWorkloadAccessTokenForJWT\",
                 \"bedrock-agentcore:GetWorkloadAccessTokenForUserId\"],
     \"Resource\":\"*\"}]}" >/dev/null
ROLE_ARN=$(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text)

# ---- artifact --------------------------------------------------------------
# Always rebuild. This used to be `test -f "$ZIP"`, which silently shipped
# whatever zip happened to be on disk: two runtime versions went out carrying
# an artifact from an earlier session, and the code under test was never the
# code running. A deploy script that can publish stale source is worse than
# no deploy script.
echo "packaging runtime from working tree"
"${PYTHON:-.venv/bin/python}" scripts/package_runtime.py
test -f "$ZIP" || { echo "packaging produced no $ZIP"; exit 1; }
KEY="v$(date +%s)-ramco-runtime.zip"
aws s3 cp "$ZIP" "s3://$BUCKET/$KEY" --only-show-errors
echo "uploaded s3://$BUCKET/$KEY"

ARTIFACT="{\"codeConfiguration\":{\"code\":{\"s3\":{\"bucket\":\"$BUCKET\",\"prefix\":\"$KEY\"}},\"runtime\":\"PYTHON_3_12\",\"entryPoint\":[\"runtime.py\"]}}"
SUBNET_JSON=$(echo "$SUBNETS" | awk -F, '{for(i=1;i<=NF;i++) printf "%s\"%s\"", (i>1?",":""), $i}')
# requireServiceS3Endpoint is deliberately omitted: AgentCore rejects it at
# creation time ("cannot be set during agent creation"). The S3 gateway
# endpoint the stack creates is still used — the flag is about enforcing it,
# not about enabling it.
NET="{\"networkMode\":\"VPC\",\"networkModeConfig\":{\"securityGroups\":[\"$SG\"],\"subnets\":[$SUBNET_JSON]}}"
PROTO='{"serverProtocol":"HTTP","port":8080,"path":"/invocations"}'
# Defence 1.5. Resolved by name so the deploy does not carry a stale id:
# if the guardrail is absent these stay empty, guardrail.configured()
# returns false, and the pipeline runs on the regex screens alone.
GR_ID=$(aws bedrock list-guardrails --region "$REGION" \
          --query "guardrails[?name=='ramco-payce-pilot']|[0].id" \
          --output text 2>/dev/null || echo "")
[ "$GR_ID" = "None" ] && GR_ID=""
if [ -n "$GR_ID" ]; then
  # Pin the highest PUBLISHED version. list-guardrails returns DRAFT too,
  # and DRAFT would silently pick up unpublished edits in production.
  GR_VER=$(aws bedrock list-guardrails --region "$REGION" \
            --guardrail-identifier "$GR_ID" \
            --query "guardrails[?version!='DRAFT'].version" --output text \
           | tr '\t' '\n' | sort -n | tail -1)
  if [ -z "$GR_VER" ]; then
    echo "ERROR: guardrail $GR_ID has no published version."
    echo "       run ./scripts/setup_guardrail.sh to publish one."
    exit 1
  fi
else
  GR_VER=""
  echo "WARNING: no guardrail found -- deploying with regex screens only."
  echo "         run ./scripts/setup_guardrail.sh first."
fi
echo "guardrail: ${GR_ID:-none} v${GR_VER:-?}"

ENVV="{\"RAMCO_DB_HOST_IN\":\"$EP_IN\",\"RAMCO_DB_HOST_UAE\":\"$EP_UAE\",\"AWS_REGION\":\"$REGION\",\"PYTHONPATH\":\"/app/src\",\"GUARDRAIL_ID\":\"$GR_ID\",\"GUARDRAIL_VERSION\":\"$GR_VER\"}"

ID=$(aws bedrock-agentcore-control list-agent-runtimes \
       --query "agentRuntimes[?agentRuntimeName=='$NAME'].agentRuntimeId" --output text)

if [ -z "$ID" ] || [ "$ID" = "None" ]; then
  echo "creating runtime $NAME"
  ID=$(aws bedrock-agentcore-control create-agent-runtime \
    --agent-runtime-name "$NAME" --role-arn "$ROLE_ARN" \
    --agent-runtime-artifact "$ARTIFACT" --network-configuration "$NET" \
    --protocol-configuration "$PROTO" --environment-variables "$ENVV" \
    --description "Ramco Payce support triage pilot: PII gate, RAG with citations, human hand-off" \
    --query agentRuntimeId --output text)
else
  echo "updating runtime $NAME ($ID)"
  aws bedrock-agentcore-control update-agent-runtime --agent-runtime-id "$ID" \
    --role-arn "$ROLE_ARN" --agent-runtime-artifact "$ARTIFACT" \
    --network-configuration "$NET" --protocol-configuration "$PROTO" \
    --environment-variables "$ENVV" --query agentRuntimeVersion --output text
fi

# ---- recycle the proxy's warm sessions -------------------------------------
# The proxy reuses one AgentCore session per Lambda execution environment,
# which is what took first-reply latency from 6.17 s to 2.51 s at p95. The
# cost is that a warm session keeps serving the runtime version it started
# on: deploy a fix, and requests landing on a warm session still run the old
# code for up to the idle timeout. That is not a theory -- it happened on the
# deploy before this line existed, and produced a "failure" in a check that
# had already been fixed and verified locally.
#
# Updating any environment variable on the function replaces its execution
# environments, which drops the module-level session id with them, so the
# next request opens a session on the new version. Stamping the version in
# makes it a visible record of which runtime the proxy was last pointed at.
recycle_proxy() {
  local ver="$1"
  aws lambda get-function --function-name ramco-pilot-proxy >/dev/null 2>&1 || return 0
  local env
  env=$(aws lambda get-function-configuration --function-name ramco-pilot-proxy \
          --query 'Environment.Variables' --output json)
  env=$(RUNTIME_VERSION="$ver" python3 -c '
import json, os, sys
e = json.load(sys.stdin)
e["RUNTIME_VERSION"] = os.environ["RUNTIME_VERSION"]
print(json.dumps({"Variables": e}))' <<<"$env")
  aws lambda wait function-updated --function-name ramco-pilot-proxy
  aws lambda update-function-configuration --function-name ramco-pilot-proxy \
    --environment "$env" --query LastUpdateStatus --output text >/dev/null
  aws lambda wait function-updated --function-name ramco-pilot-proxy
  echo "  proxy recycled onto runtime version $ver"
}

echo -n "waiting for READY"
for _ in $(seq 1 60); do
  S=$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$ID" --query status --output text)
  [ "$S" = "READY" ] && { echo " -> READY"; break; }
  case "$S" in *FAILED*) echo " -> $S"; aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$ID"; exit 1;; esac
  echo -n "."; sleep 5
done

recycle_proxy "$(aws bedrock-agentcore-control get-agent-runtime \
  --agent-runtime-id "$ID" --query agentRuntimeVersion --output text)"

aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$ID" \
  --query '{arn:agentRuntimeArn,version:agentRuntimeVersion,status:status,net:networkConfiguration.networkMode}'
