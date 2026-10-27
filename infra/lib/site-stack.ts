import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import { Construct, IConstruct } from 'constructs';

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

/**
 * Static hosting for Headroom: a private S3 bucket behind CloudFront (OAC).
 * Requirement 1. No custom domain (Req 1.12 is optional and not configured).
 */
export class HeadroomSiteStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
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
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      errorResponses: [403, 404].map((httpStatus) => ({
        httpStatus,
        responseHttpStatus: 200,
        responsePagePath: '/index.html',
        ttl: cdk.Duration.seconds(0),
      })),
    });

    // Req 1.7, 1.8: upload web/dist and invalidate /* on every deploy.
    new s3deploy.BucketDeployment(this, 'DeploySite', {
      sources: [s3deploy.Source.asset(path.join(__dirname, '..', '..', 'web', 'dist'))],
      destinationBucket: bucket,
      distribution,
      distributionPaths: ['/*'],
    });

    // Req 1.9
    new cdk.CfnOutput(this, 'SiteUrl', { value: `https://${distribution.distributionDomainName}` });
    // Used by scripts/smoke.ps1
    new cdk.CfnOutput(this, 'BucketName', { value: bucket.bucketName });
    new cdk.CfnOutput(this, 'BucketRegionalDomainName', { value: bucket.bucketRegionalDomainName });

    cdk.Aspects.of(this).add(new TagRawCfnResources());
  }
}
