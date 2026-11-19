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
test -f "$ZIP" || { echo "build it first: .venv/bin/python scripts/package_runtime.py"; exit 1; }
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
ENVV="{\"RAMCO_DB_HOST_IN\":\"$EP_IN\",\"RAMCO_DB_HOST_UAE\":\"$EP_UAE\",\"AWS_REGION\":\"$REGION\",\"PYTHONPATH\":\"/app/src\"}"

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

echo -n "waiting for READY"
for _ in $(seq 1 60); do
  S=$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$ID" --query status --output text)
  [ "$S" = "READY" ] && { echo " -> READY"; break; }
  case "$S" in *FAILED*) echo " -> $S"; aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$ID"; exit 1;; esac
  echo -n "."; sleep 5
done

aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$ID" \
  --query '{arn:agentRuntimeArn,version:agentRuntimeVersion,status:status,net:networkConfiguration.networkMode}'
