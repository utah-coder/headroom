#!/usr/bin/env bash
# Which Gemini model can this API key actually call?
#
# Makes small real calls, best model first, until one answers (a fraction of a cent),
# and writes the result to ~/.headroom-analyst for scripts/cloudshell-deploy.sh.
#
# The key comes from GEMINI_API_KEY if set, else from the SSM SecureString parameter
# the Lambda reads (default /headroom/gemini-api-key, or HEADROOM_KEY_PARAMETER).
#
# Usage: bash scripts/gemini-probe.sh [region]     Needs only curl, and the AWS CLI for SSM.
# Exit: 0 found, 1 no model answered, 2 no region, 3 no key.
set -uo pipefail

OUT="${HEADROOM_PROBE_OUT:-$HOME/.headroom-analyst}"
PARAM="${HEADROOM_KEY_PARAMETER:-/headroom/gemini-api-key}"
ENDPOINT="https://generativelanguage.googleapis.com/v1beta/interactions"

# Best first. The docs recommend Hydrogen for new projects; the rest are fallbacks.
MODELS="${HEADROOM_GEMINI_MODELS:-gemini-4-hydrogen gemini-3.8-flash gemini-3.6-flash gemini-3.5-flash-lite}"

KEY="${GEMINI_API_KEY:-}"
if [ -z "$KEY" ]; then
  REGION="${1:-${AWS_REGION:-$(aws configure get region 2>/dev/null)}}"
  if [ -z "$REGION" ]; then
    echo "No region: pass one, or set AWS_REGION (or set GEMINI_API_KEY)." >&2
    exit 2
  fi
  KEY="$(aws ssm get-parameter --region "$REGION" --name "$PARAM" --with-decryption \
    --query Parameter.Value --output text 2>/dev/null)" || KEY=""
  if [ -z "$KEY" ]; then
    echo "No Gemini API key: SSM parameter $PARAM is missing or unreadable in $REGION." >&2
    exit 3
  fi
  echo "Using the key in SSM parameter $PARAM ($REGION)."
else
  echo "Using the key in GEMINI_API_KEY."
fi

try() {  # model id; prints the HTTP status. The key goes in by stdin, not the command line.
  curl -s -o /dev/null -m 45 -w '%{http_code}' "$ENDPOINT" \
    -H @- \
    -H 'content-type: application/json' \
    -H 'api-revision: 2026-05-20' \
    -d "{\"model\":\"$1\",\"input\":\"Reply with: ok\",\"store\":false}" \
    <<<"x-goog-api-key: $KEY"
}

rm -f "$OUT"
last=""
for model in $MODELS; do
  code="$(try "$model")"
  printf '  %-28s %s\n' "$model" "$code"
  last="$code"
  if [ "$code" = "200" ]; then
    {
      echo "ANALYST_MODEL=$model"
      echo "ANALYST_EFFORT=low"
    } > "$OUT"
    echo
    echo "FOUND: $model."
    echo "Saved to $OUT for scripts/cloudshell-deploy.sh."
    exit 0
  fi
done

echo
echo "No Gemini model answered."
case "$last" in
  400|401|403) echo "HTTP $last usually means the key is wrong, restricted, or its project has no Gemini API access." ;;
  429) echo "HTTP 429 means the key is out of quota. Check billing: https://aistudio.google.com/" ;;
  000) echo "No reply at all: this machine could not reach generativelanguage.googleapis.com." ;;
esac
exit 1
