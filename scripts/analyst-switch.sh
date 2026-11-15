#!/usr/bin/env bash
# The analyst's emergency switch: stop or resume new model calls without a deploy.
#
#   bash scripts/analyst-switch.sh off      # no new question or briefing reaches the model
#   bash scripts/analyst-switch.sh on       # resume
#   bash scripts/analyst-switch.sh status
#
# Run it in the account and region the site is deployed to (AWS_REGION, or CloudShell's).
# It writes one control item to the analyst's counters table; the Lambda reads it before
# every model call, strongly consistent, so it takes effect on the next request. The
# static site is unaffected, and the panels say the analyst is switched off. A briefing
# already cached in a warm Lambda can still be served; it calls no model.
#
# Blunter switches, for when this one isn't enough:
#   - Stop the Lambda entirely (no invocations at all; resets on the next deploy):
#       aws lambda put-function-concurrency --function-name <AnalystFunctionName> --reserved-concurrent-executions 0
#     Undo: put back the deployed value (5 by default), or delete-function-concurrency if none.
#   - Persist "off" across deploys: deploy with -c headroom:analystEnabled=false.
#   - Revoke the Gemini API key in Google AI Studio. Stops model spend even if AWS is misconfigured.
set -euo pipefail

ACTION="${1:-status}"
REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null || true)}"
[ -n "$REGION" ] || { echo "No region set. Set AWS_REGION to the site's region." >&2; exit 1; }

TABLE="$(aws cloudformation describe-stacks --stack-name HeadroomSiteStack --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='AnalystCountersTable'].OutputValue" --output text 2>/dev/null || true)"
if [ -z "$TABLE" ] || [ "$TABLE" = None ]; then
  echo "No AnalystCountersTable output on HeadroomSiteStack in $REGION." >&2
  echo "Either the analyst isn't deployed here, or the stack predates the switch: use the Lambda concurrency command in this script's header." >&2
  exit 1
fi
KEY='{"pk": {"S": "control#analyst"}}'

case "$ACTION" in
  off)
    aws dynamodb put-item --region "$REGION" --table-name "$TABLE" \
      --item '{"pk": {"S": "control#analyst"}, "off": {"BOOL": true}}'
    echo "Analyst switched OFF in $REGION. New questions and briefings are refused." ;;
  on)
    aws dynamodb delete-item --region "$REGION" --table-name "$TABLE" --key "$KEY"
    echo "Analyst switched ON in $REGION. The daily and hourly limits still apply." ;;
  status)
    off="$(aws dynamodb get-item --region "$REGION" --table-name "$TABLE" --key "$KEY" \
      --consistent-read --query 'Item.off.BOOL' --output text)"
    if [ "$off" = True ]; then echo "OFF"; else echo "ON"; fi ;;
  *)
    echo "usage: analyst-switch.sh off|on|status" >&2; exit 2 ;;
esac
