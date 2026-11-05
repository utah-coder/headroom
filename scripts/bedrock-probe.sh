#!/usr/bin/env bash
# Which Claude model can this AWS account actually call?
#
# Model listings don't answer that: a listed model can still say "not available for
# this account", and a model can work on one Bedrock route and not the other. This
# makes small real calls until one works (a fraction of a cent) and writes the result
# to ~/.headroom-analyst for scripts/cloudshell-deploy.sh.
#
# Usage: bash scripts/bedrock-probe.sh [region]     Needs only the AWS CLI and curl.
set -uo pipefail

REGION="${1:-${AWS_REGION:-$(aws configure get region 2>/dev/null)}}"
if [ -z "$REGION" ]; then
  echo "No region: pass one, or set AWS_REGION." >&2
  exit 2
fi
OUT="${HEADROOM_PROBE_OUT:-$HOME/.headroom-analyst}"

# Best first. Haiku 4.5's Bedrock profile ID carries a date, the rest don't.
MODELS="claude-opus-5-5 claude-sonnet-5-5 claude-opus-5 claude-sonnet-5 claude-opus-4-8 claude-haiku-5-5 claude-haiku-4-5"

case "$REGION" in
  us-*|ca-*) GEO="us" ;;
  eu-*) GEO="eu" ;;
  ap-northeast-1|ap-northeast-3) GEO="jp" ;;
  ap-southeast-2|ap-southeast-4) GEO="au" ;;
  *) GEO="" ;;
esac

# Credentials for signing the Messages endpoint call. CloudShell supplies them from
# its container endpoint rather than the environment, so ask the CLI.
eval "$(aws configure export-credentials --format env 2>/dev/null)" || true

try_mantle() {  # model id; prints the HTTP status
  curl -s -o /dev/null -m 45 -w '%{http_code}' \
    "https://bedrock-mantle.${REGION}.api.aws/anthropic/v1/messages" \
    --aws-sigv4 "aws:amz:${REGION}:bedrock-mantle" \
    --user "${AWS_ACCESS_KEY_ID:-}:${AWS_SECRET_ACCESS_KEY:-}" \
    -H "x-amz-security-token: ${AWS_SESSION_TOKEN:-}" \
    -H 'content-type: application/json' \
    -H 'anthropic-version: 2026-10-21' \
    -d "{\"model\":\"$1\",\"max_tokens\":64,\"messages\":[{\"role\":\"user\",\"content\":\"Reply with: ok\"}]}"
}

try_invoke() {  # model or profile id; returns 0 if it answered
  aws bedrock-runtime converse --region "$REGION" --model-id "$1" \
    --messages '[{"role":"user","content":[{"text":"Reply with: ok"}]}]' \
    --inference-config maxTokens=64 --query 'stopReason' --output text >/dev/null 2>&1
}

found() {  # route, model id
  local effort=medium
  case "$2" in *haiku-4-5*) effort="" ;; esac  # This model rejects the effort setting.
  {
    echo "ANALYST_MODEL=$2"
    echo "ANALYST_ROUTE=$1"
    echo "ANALYST_REGION=$REGION"
    echo "ANALYST_EFFORT=$effort"
  } > "$OUT"
  echo
  echo "FOUND: $2 on the '$1' route in $REGION."
  echo "Saved to $OUT for scripts/cloudshell-deploy.sh."
  exit 0
}

echo "Trying Claude models in $REGION as $(aws sts get-caller-identity --query Arn --output text 2>/dev/null)"
rm -f "$OUT"

for name in $MODELS; do
  id="anthropic.$name"
  code="$(try_mantle "$id")"
  printf '  mantle  %-52s %s\n' "$id" "$code"
  [ "$code" = "200" ] && found mantle "$id"

  suffix="$name"
  [ "$name" = "claude-haiku-4-5" ] && suffix="claude-haiku-4-5-20251001-v1:0"
  for prefix in ${GEO:+$GEO.}anthropic global.anthropic; do
    case "$prefix" in *.anthropic) ;; *) continue ;; esac
    id="$prefix.$suffix"
    if try_invoke "$id"; then
      printf '  invoke  %-52s OK\n' "$id"
      found invoke "$id"
    fi
    printf '  invoke  %-52s no\n' "$id"
  done
done

echo
echo "No Claude model answered in $REGION. Try another region, or check model access:"
echo "  https://console.aws.amazon.com/bedrock/home#/modelaccess"
exit 1
