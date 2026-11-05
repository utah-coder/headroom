#!/usr/bin/env bash
# Deploy Headroom from AWS CloudShell, into the account and region CloudShell is open in.
# You run this by hand; nothing else in the repo deploys. Safe to rerun.
#
#   git clone -b <branch> https://github.com/utah-coder/headroom.git ~/headroom
#   cd ~/headroom && bash scripts/cloudshell-deploy.sh
#
# Options:
#   --no-analyst   Deploy only the static site (no Lambda, DynamoDB or Bedrock).
#   --yes          Skip the confirmation prompt.
#
# It uses the committed scenario files, so it needs no EIA key. Build output goes under
# /tmp because the 1 GB home directory can't hold it. If CloudShell recycles /tmp, rerun.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
WORK=/tmp/headroom-build
export HEADROOM_ANALYST_BUNDLE="$WORK/analyst"
export npm_config_cache=/tmp/npm-cache
export PIP_CACHE_DIR=/tmp/pip-cache
CDK_OUT="$WORK/cdk.out"

WITH_ANALYST=1
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --no-analyst) WITH_ANALYST=0 ;;
    --yes) ASSUME_YES=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$1"; }

REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null || true)}"
[ -n "$REGION" ] || { echo "No region set. Open CloudShell in the region you want." >&2; exit 1; }
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

step "Target: account $ACCOUNT, region $REGION, commit $(git log -1 --format='%h %s')"

# --- Node: Vite 8 needs ^20.19 or >=22.12, and CloudShell's may be older.
node_ok() {
  command -v node >/dev/null || return 1
  node -e 'const [a,b]=process.versions.node.split(".").map(Number);process.exit((a===20&&b>=19)||(a===22&&b>=12)||a>22?0:1)'
}
[ -x "$HOME/node/bin/node" ] && export PATH="$HOME/node/bin:$PATH"
if ! node_ok; then
  step "Installing Node 22 into ~/node"
  mkdir -p "$HOME/node"
  (
    cd "$HOME/node"
    BASE=https://nodejs.org/dist/latest-v22.x
    curl -fsSLO "$BASE/SHASUMS256.txt"
    F="$(grep -o 'node-v.*-linux-x64\.tar\.gz$' SHASUMS256.txt)"
    curl -fsSLO "$BASE/$F"
    sha256sum -c --ignore-missing SHASUMS256.txt
    tar -xzf "$F" --strip-components=1
    rm "$F"
  )
  export PATH="$HOME/node/bin:$PATH"
fi
echo "node $(node -v), $(python3 --version), $(df -h "$HOME" | awk 'NR==2 {print $4}') free in home"

# --- Which model can this account call?
CONTEXT=(-c "headroom:region=$REGION")
if [ "$WITH_ANALYST" = 1 ]; then
  step "Finding a Claude model this account can call"
  if HEADROOM_PROBE_OUT="$WORK.env" bash scripts/bedrock-probe.sh "$REGION"; then
    # shellcheck disable=SC1090
    . "$WORK.env"
    CONTEXT+=(-c "headroom:analystModel=$ANALYST_MODEL" -c "headroom:analystRoute=$ANALYST_ROUTE"
              -c "headroom:analystRegion=$ANALYST_REGION" -c "headroom:analystEffort=${ANALYST_EFFORT:-}")
  else
    echo "No Claude model available here: deploying the static site only."
    WITH_ANALYST=0
  fi
fi
[ "$WITH_ANALYST" = 1 ] || CONTEXT+=(-c "headroom:analyst=false")

# --- Confirm.
if [ "$ASSUME_YES" = 0 ]; then
  echo
  echo "This creates or updates stack HeadroomSiteStack in account $ACCOUNT ($REGION):"
  echo "  S3 bucket, CloudFront distribution, IAM roles, helper Lambdas"
  [ "$WITH_ANALYST" = 0 ] || echo "  plus the analyst: Lambda, DynamoDB table, Bedrock access ($ANALYST_MODEL)"
  read -r -p "Continue? [y/N] " reply
  case "$reply" in y|Y|yes) ;; *) echo "Stopped. Nothing was deployed."; exit 0 ;; esac
fi

# --- Build.
mkdir -p "$WORK"
step "Building the web app"
(cd web && npm ci --no-audit --no-fund && npm run build)
step "Installing CDK"
(cd infra && npm ci --no-audit --no-fund)
if [ "$WITH_ANALYST" = 1 ]; then
  step "Building the analyst bundle"
  python3 scripts/build_analyst.py
fi

# --- Bootstrap once per account and region.
cd "$ROOT/infra"
status="$(aws cloudformation describe-stacks --stack-name CDKToolkit --region "$REGION" \
  --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo MISSING)"
case "$status" in
  CREATE_COMPLETE|UPDATE_COMPLETE) echo "CDK is already bootstrapped ($status)." ;;
  *) step "Bootstrapping CDK"; npx cdk bootstrap "aws://$ACCOUNT/$REGION" --output "$CDK_OUT" "${CONTEXT[@]}" ;;
esac

# --- Deploy. Press a key now and then: CloudShell can time out when idle. If it does,
# CloudFormation keeps going; reconnect and rerun this script.
step "Deploying (up to 30 minutes)"
npx cdk deploy HeadroomSiteStack --require-approval never --output "$CDK_OUT" "${CONTEXT[@]}"
cd "$ROOT"

# --- Smoke test.
step "Smoke test"
out() {
  aws cloudformation describe-stacks --stack-name HeadroomSiteStack --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
SITE_URL="$(out SiteUrl)"
FAILED=0
check() {  # name, got, want
  if [ "$2" = "$3" ]; then echo "  ok    $1 ($2)"; else echo "  FAIL  $1: got $2, want $3"; FAILED=1; fi
}
code() { curl -s -o "${2:-/dev/null}" -m 20 -w '%{http_code}' "$1"; }

check "GET /" "$(code "$SITE_URL/")" 200
check "GET peak.json" "$(code "$SITE_URL/scenarios/peak.json" /tmp/peak.json)" 200
node -e "JSON.parse(require('fs').readFileSync('/tmp/peak.json'))" \
  && echo "  ok    peak.json parses" || { echo "  FAIL  peak.json is not JSON"; FAILED=1; }
check "direct S3 is private" "$(code "https://$(out BucketRegionalDomainName)/index.html")" 403

if [ "$WITH_ANALYST" = 1 ]; then
  check "direct function URL is refused" "$(code "$(out AnalystFunctionUrl)")" 403
  BODY='{"question":"What if the demand charge doubled?","scenario":"peak"}'
  HASH="$(printf '%s' "$BODY" | sha256sum | cut -d' ' -f1)"
  API_CODE="$(curl -s -o /tmp/whatif.json -m 70 -w '%{http_code}' -X POST "$SITE_URL/api/whatif" \
    -H 'content-type: application/json' -H "x-amz-content-sha256: $HASH" -d "$BODY")"
  check "POST /api/whatif" "$API_CODE" 200
  node -e "
    let b; try { b = JSON.parse(require('fs').readFileSync('/tmp/whatif.json')); }
    catch { console.log('  FAIL  the API returned HTML: CloudFront could not reach the Lambda'); process.exit(1); }
    console.log('  status: ' + b.status + (b.answer_source ? ' (' + b.answer_source + ')' : ''));
    console.log('  ' + (b.answer || b.message));
    process.exit(b.status === 'answered' ? 0 : 1);
  " || FAILED=1
fi

echo
echo "Site: $SITE_URL"
if [ "$FAILED" = 0 ]; then
  echo "DEPLOY OK"
else
  echo "DEPLOYED, BUT A CHECK FAILED (see above)."
  exit 1
fi
