#!/usr/bin/env bash
# Stop a Headroom deploy aimed at the wrong region, before anything is built or created.
#
#   bash scripts/region-guard.sh <region>     # exit 0: go ahead; exit 1: stop
#
# cloudshell-deploy.sh runs this. It deploys to whatever region CloudShell is in, and
# CloudShell can open somewhere other than the site. A second HeadroomSiteStack can't
# work: CloudFront is global, so the new stack's origin access control collides with the
# live one by name. On 2026-11-14 that failed only after building and publishing
# everything. Read-only: it describes stacks and lists CloudFront distributions.
set -euo pipefail

REGION="${1:?usage: region-guard.sh <region>}"
STACK=HeadroomSiteStack

status="$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo MISSING)"

leftover=0
case "$status" in
  MISSING) ;;                    # Nothing here yet: check the other regions below.
  ROLLBACK_COMPLETE) leftover=1 ;;  # A failed first attempt: empty, but it blocks a new stack.
  *)
    echo "Updating the existing $STACK in $REGION ($status)."
    exit 0 ;;
esac

delete_leftover() {
  echo "A failed attempt also left an empty $STACK in $REGION. It holds nothing; delete it with:" >&2
  echo "  aws cloudformation delete-stack --stack-name $STACK --region $REGION" >&2
}

# No stack here. The site's bucket is the CloudFront origin, and its address names its region.
if ! origins="$(aws cloudfront list-distributions \
    --query 'DistributionList.Items[].[DomainName, join(`,`, Origins.Items[].DomainName)]' \
    --output text 2>/dev/null)"; then
  echo "Couldn't list CloudFront distributions to check for a site in another region." >&2
  if [ "$leftover" = 1 ]; then delete_leftover; exit 1; fi
  echo "Continuing, as a first deploy into $REGION." >&2
  exit 0
fi

elsewhere="$(printf '%s\n' "$origins" \
  | grep -o '^[^[:space:]]*[[:space:]].*headroomsitestack-sitebucket[^,]*\.s3\.[a-z0-9-]*\.amazonaws\.com' \
  | sed -E 's/^([^[:space:]]+)[[:space:]].*\.s3\.([a-z0-9-]+)\.amazonaws\.com$/\2 \1/' \
  | grep -v "^$REGION " || true)"

if [ -n "$elsewhere" ]; then
  read -r other site <<<"$(printf '%s\n' "$elsewhere" | head -1)"
  echo "Headroom already runs in $other (https://$site), not $REGION." >&2
  echo "A second copy in $REGION would fail on CloudFront names, which are global. To update the site:" >&2
  echo "  AWS_REGION=$other bash scripts/cloudshell-deploy.sh" >&2
  if [ "$leftover" = 1 ]; then delete_leftover; fi
  exit 1
fi

if [ "$leftover" = 1 ]; then
  delete_leftover
  echo "Then rerun this script." >&2
  exit 1
fi
echo "No $STACK in this account yet: a first deploy into $REGION."
