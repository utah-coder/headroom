import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import { Construct, IConstruct } from 'constructs';
import { Analyst } from './analyst';

export const PROJECT_TAG = { key: 'Project', value: 'headroom' } as const;

// CloudFormation types that support Tags but that CDK can emit as raw CfnResource
// (e.g. the autoDeleteObjects custom-resource provider), which Tags.of() cannot reach.
const RAW_TAGGABLE_TYPES = new Set(['AWS::IAM::Role', 'AWS::Lambda::Function']);

/** Req 1.10: add Project=headroom to taggable raw CfnResources that Tags.of() skips. */
class TagRawCfnResources implements cdk.IAspect {
  visit(node: IConstruct): void {
    if (
      node instanceof cdk.CfnResource &&
      !cdk.TagManager.isTaggable(node) &&
      RAW_TAGGABLE_TYPES.has(node.cfnResourceType)
    ) {
      node.addPropertyOverride('Tags', [{ Key: PROJECT_TAG.key, Value: PROJECT_TAG.value }]);
    }
  }
}

export interface HeadroomSiteStackProps extends cdk.StackProps {
  /** An AWS WAF web ACL (scope CLOUDFRONT, in us-east-1) to attach, from HeadroomEdgeStack. */
  readonly webAclArn?: string;
}

/** Reads `-c headroom:<name>` as a whole number at least `min`. Fails the synth otherwise. */
function contextInt(value: string, name: string, min: number): number {
  const n = Number(value);
  if (value.trim() === '' || !Number.isInteger(n) || n < min) {
    throw new Error(`headroom:${name} must be a whole number of at least ${min}: ${value}`);
  }
  return n;
}

/**
 * Static hosting for Headroom: a private S3 bucket behind CloudFront (OAC).
 * Requirement 1. No custom domain (Req 1.12 is optional and not configured).
 */
export class HeadroomSiteStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: HeadroomSiteStackProps = {}) {
    super(scope, id, props);

    // Req 1.3: private bucket. Direct object URLs return 403 (Req 1.13).
    const bucket = new s3.Bucket(this, 'SiteBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // Req 1.4, 1.5, 1.6
    const distribution = new cloudfront.Distribution(this, 'SiteDistribution', {
      defaultRootObject: 'index.html',
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(bucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        compress: true,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        responseHeadersPolicy: cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
      },
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      webAclId: props.webAclArn,
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      errorResponses: [403, 404].map((httpStatus) => ({
        httpStatus,
        responseHttpStatus: 200,
        responsePagePath: '/index.html',
        ttl: cdk.Duration.seconds(0),
      })),
    });

    // The what-if analyst. Off with -c headroom:analyst=false, for an account that
    // blocks Lambda or has no Gemini API key: the rest of the site doesn't depend on it, and the
    // panel says it isn't connected.
    const context = (name: string, fallback: string): string =>
      String(this.node.tryGetContext(`headroom:${name}`) ?? fallback);
    if (context('analyst', 'true') !== 'false') {
      const concurrency = context('analystConcurrency', '5');
      const analyst = new Analyst(this, 'Analyst', {
        model: context('analystModel', 'gemini-4-hydrogen'),
        effort: context('analystEffort', 'low'),
        keyParameter: context('analystKeyParameter', '/headroom/gemini-api-key'),
        dailyCap: String(contextInt(context('analystDailyCap', '100'), 'analystDailyCap', 0)),
        hourlyPerVisitor: String(contextInt(context('analystHourlyPerVisitor', '10'), 'analystHourlyPerVisitor', 0)),
        // At most this many questions run at once. "none" for an account whose concurrency
        // quota leaves nothing to reserve; scripts/cloudshell-deploy.sh checks the quota.
        reservedConcurrency: concurrency === 'none' ? undefined : contextInt(concurrency, 'analystConcurrency', 1),
        // false: the function refuses every request before any cost; the static site is unaffected.
        enabled: context('analystEnabled', 'true') !== 'false',
      });

      distribution.addBehavior(
        '/api/*',
        origins.FunctionUrlOrigin.withOriginAccessControl(analyst.url, {
          readTimeout: cdk.Duration.seconds(60),
        }),
        {
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          // The managed policy AWS intends for function URLs. Host must not reach a
          // function URL. It also forwards x-amz-content-sha256, which OAC needs to
          // sign a POST body and which a custom allow list is not permitted to name,
          // and CloudFront-Viewer-Address, which the per-visitor limit reads.
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
          responseHeadersPolicy: cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
        },
      );

      // scripts/cloudshell-deploy.sh checks this refuses direct calls.
      new cdk.CfnOutput(this, 'AnalystFunctionUrl', { value: analyst.url.url });
      // Used by scripts/analyst-switch.sh.
      new cdk.CfnOutput(this, 'AnalystCountersTable', { value: analyst.countersTable });
      new cdk.CfnOutput(this, 'AnalystFunctionName', { value: analyst.functionName });
    }

    // Req 1.7, 1.8: upload web/dist and invalidate /* on every deploy.
    // no-cache makes browsers revalidate on every load (a 304 when unchanged).
    // Without it they cache index.html heuristically and can keep showing the
    // previous build after a deploy.
    new s3deploy.BucketDeployment(this, 'DeploySite', {
      sources: [s3deploy.Source.asset(path.join(__dirname, '..', '..', 'web', 'dist'))],
      destinationBucket: bucket,
      distribution,
      distributionPaths: ['/*'],
      cacheControl: [s3deploy.CacheControl.noCache()],
    });

    // Req 1.9
    new cdk.CfnOutput(this, 'SiteUrl', { value: `https://${distribution.distributionDomainName}` });
    // Used by scripts/smoke.ps1
    new cdk.CfnOutput(this, 'BucketName', { value: bucket.bucketName });
    new cdk.CfnOutput(this, 'BucketRegionalDomainName', { value: bucket.bucketRegionalDomainName });

    cdk.Aspects.of(this).add(new TagRawCfnResources());
  }
}
