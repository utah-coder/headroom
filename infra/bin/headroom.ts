#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { HeadroomSiteStack, PROJECT_TAG } from '../lib/site-stack';

const app = new cdk.App();
const region: string = app.node.tryGetContext('headroom:region') ?? 'us-west-2';

new HeadroomSiteStack(app, 'HeadroomSiteStack', {
  // Account is undefined in CI (no credentials), so synth still works.
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region },
});

// Req 1.10: every taggable resource carries Project=headroom.
cdk.Tags.of(app).add(PROJECT_TAG.key, PROJECT_TAG.value);
