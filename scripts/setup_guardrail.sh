#!/usr/bin/env bash
# Creates (or updates) the Bedrock guardrail that backs defence 1.5, then
# publishes a version and prints the two values the pipeline needs.
#
# Idempotent: run it as often as you like. If a guardrail with this name
# already exists it is updated in place and a new version is published.
#
#   ./scripts/setup_guardrail.sh
#
set -euo pipefail

export AWS_PROFILE="${AWS_PROFILE:-shivam2505}"
export AWS_REGION="${AWS_REGION:-ap-southeast-2}"

cd "$(dirname "$0")/.."
CFG="scripts/guardrail_config.json"
NAME="$(python3 -c "import json;print(json.load(open('$CFG'))['name'])")"

echo "region=$AWS_REGION profile=$AWS_PROFILE name=$NAME"

ID="$(aws bedrock list-guardrails --query \
  "guardrails[?name=='$NAME']|[0].id" --output text 2>/dev/null || echo None)"

if [ "$ID" = "None" ] || [ -z "$ID" ]; then
  echo "creating..."
  ID="$(aws bedrock create-guardrail --cli-input-json "file://$CFG" \
        --query guardrailId --output text)"
  echo "created $ID"
else
  echo "updating $ID..."
  # update-guardrail takes the same body; it needs the identifier too.
  python3 - "$CFG" "$ID" > /tmp/_gr_update.json <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1]))
cfg.pop("tags", None)                    # not accepted on update
cfg["guardrailIdentifier"] = sys.argv[2]
json.dump(cfg, sys.stdout)
PY
  aws bedrock update-guardrail --cli-input-json file:///tmp/_gr_update.json \
      --query guardrailId --output text > /dev/null
  rm -f /tmp/_gr_update.json
  echo "updated $ID"
fi

VER="$(aws bedrock create-guardrail-version --guardrail-identifier "$ID" \
       --description "automated" --query version --output text)"

echo
echo "  GUARDRAIL_ID=$ID"
echo "  GUARDRAIL_VERSION=$VER"
echo
echo "Set those two in the runtime and the proxy, or export them locally:"
echo "  export GUARDRAIL_ID=$ID GUARDRAIL_VERSION=$VER"
