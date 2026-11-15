#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { DEFAULT_MAX_BODY_BYTES, DEFAULT_RATE_LIMIT, HeadroomEdgeStack } from '../lib/edge-waf';
import { HeadroomSiteStack, PROJECT_TAG } from '../lib/site-stack';

const app = new cdk.App();
const context = (name: string, fallback: string): string =>
  String(app.node.tryGetContext(`headroom:${name}`) ?? fallback);
const region = context('region', 'us-west-2');
// Account is undefined in CI (no credentials), so synth still works.
const account = process.env.CDK_DEFAULT_ACCOUNT;

// Optional AWS WAF in front of /api/* (paid; see README, "Costs"). A CloudFront web ACL must
// be in us-east-1, so it gets its own stack there; deploying the site stack deploys it first.
let webAclArn: string | undefined;
const waf = context('waf', 'false') === 'true';
if (waf) {
  const edge = new HeadroomEdgeStack(app, 'HeadroomEdgeStack', {
    env: { account, region: 'us-east-1' },
    crossRegionReferences: true,
    rateLimit: Number(context('wafRateLimit', String(DEFAULT_RATE_LIMIT))),
    maxBodyBytes: Number(context('wafMaxBodyBytes', String(DEFAULT_MAX_BODY_BYTES))),
    botControl: context('wafBotControl', 'false') === 'true',
  });
  webAclArn = edge.webAclArn;
}

new HeadroomSiteStack(app, 'HeadroomSiteStack', {
  env: { account, region },
  crossRegionReferences: waf,
  webAclArn,
});

// Req 1.10: every taggable resource carries Project=headroom.
cdk.Tags.of(app).add(PROJECT_TAG.key, PROJECT_TAG.value);
