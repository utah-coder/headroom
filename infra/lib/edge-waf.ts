import * as cdk from 'aws-cdk-lib';
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';
import { Construct } from 'constructs';

/** Same as ANALYST_MAX_BODY_BYTES in sim/config.py; a test keeps the two equal. */
export const DEFAULT_MAX_BODY_BYTES = 4096;
/** Requests to /api/* per IP per 5 minutes. The app's own limit is 10 questions an hour. */
export const DEFAULT_RATE_LIMIT = 30;

export interface HeadroomEdgeStackProps extends cdk.StackProps {
  /** Requests to /api/* allowed per IP in any 5-minute window (AWS WAF's minimum is 10). */
  readonly rateLimit: number;
  /** Larger /api/* bodies are blocked at the edge. */
  readonly maxBodyBytes: number;
  /** Add AWS's Bot Control rule group (common level) for /api/*. Extra monthly and per-request cost. */
  readonly botControl: boolean;
}

/**
 * Optional AWS WAF web ACL for the distribution, off unless `-c headroom:waf=true`.
 *
 * A web ACL for CloudFront must live in us-east-1, so this is its own stack there and the
 * site stack reads its ARN across regions. Every rule is scoped to /api/*: the static site
 * is never blocked, so a false positive costs a visitor the analyst, not the demo.
 *
 * Paid: a web ACL, a charge per rule, and a charge per request inspected (see README,
 * "Costs"). WAF rate limits are approximate and react within a minute or so: they stop
 * floods, while the DynamoDB counters stay the exact limit.
 */
export class HeadroomEdgeStack extends cdk.Stack {
  readonly webAclArn: string;

  constructor(scope: Construct, id: string, props: HeadroomEdgeStackProps) {
    super(scope, id, props);
    if (!Number.isInteger(props.rateLimit) || props.rateLimit < 10) {
      throw new Error(`headroom:wafRateLimit must be an integer of at least 10: ${props.rateLimit}`);
    }
    if (!Number.isInteger(props.maxBodyBytes) || props.maxBodyBytes < 1) {
      throw new Error(`headroom:wafMaxBodyBytes must be a positive integer: ${props.maxBodyBytes}`);
    }

    // URL-decoded first, so an encoded path can't slip past the scope.
    const isApi: wafv2.CfnWebACL.StatementProperty = {
      byteMatchStatement: {
        fieldToMatch: { uriPath: {} },
        positionalConstraint: 'STARTS_WITH',
        searchString: '/api/',
        textTransformations: [{ priority: 0, type: 'URL_DECODE' }],
      },
    };
    const visibility = (metricName: string): wafv2.CfnWebACL.VisibilityConfigProperty => ({
      cloudWatchMetricsEnabled: true,
      metricName,
      sampledRequestsEnabled: true,
    });
    // JSON replies the page can show. A plain 403 would be rewritten to index.html by the
    // distribution's error responses, and the page would say "not connected" instead.
    const block = (status: number, key: string): wafv2.CfnWebACL.RuleActionProperty => ({
      block: { customResponse: { responseCode: status, customResponseBodyKey: key } },
    });
    const managed = (name: string, priority: number, extra: Partial<wafv2.CfnWebACL.ManagedRuleGroupStatementProperty> = {}) => ({
      name: `Api${name.replace('AWSManagedRules', '')}`,
      priority,
      overrideAction: { none: {} },
      statement: {
        managedRuleGroupStatement: { vendorName: 'AWS', name, scopeDownStatement: isApi, ...extra },
      },
      visibilityConfig: visibility(`headroom-${name}`),
    });

    const rules: wafv2.CfnWebACL.RuleProperty[] = [
      {
        name: 'ApiBodyTooLarge',
        priority: 0,
        action: block(413, 'too_large'),
        statement: {
          andStatement: {
            statements: [
              isApi,
              {
                sizeConstraintStatement: {
                  fieldToMatch: { body: { oversizeHandling: 'MATCH' } },
                  comparisonOperator: 'GT',
                  size: props.maxBodyBytes,
                  textTransformations: [{ priority: 0, type: 'NONE' }],
                },
              },
            ],
          },
        },
        visibilityConfig: visibility('headroom-api-body-too-large'),
      },
      {
        name: 'ApiRateLimit',
        priority: 1,
        action: block(429, 'rate_limited'),
        statement: {
          rateBasedStatement: {
            limit: props.rateLimit,
            evaluationWindowSec: 300,
            aggregateKeyType: 'IP',
            scopeDownStatement: isApi,
          },
        },
        visibilityConfig: visibility('headroom-api-rate-limit'),
      },
      managed('AWSManagedRulesAmazonIpReputationList', 2),
    ];
    if (props.botControl) {
      rules.push(
        managed('AWSManagedRulesBotControlRuleSet', 3, {
          managedRuleGroupConfigs: [{ awsManagedRulesBotControlRuleSet: { inspectionLevel: 'COMMON' } }],
        }),
      );
    }

    const acl = new wafv2.CfnWebACL(this, 'WebAcl', {
      scope: 'CLOUDFRONT',
      defaultAction: { allow: {} },
      visibilityConfig: visibility('headroom-web-acl'),
      customResponseBodies: {
        too_large: {
          contentType: 'APPLICATION_JSON',
          content: JSON.stringify({ status: 'error', message: 'The request is too large.' }),
        },
        rate_limited: {
          contentType: 'APPLICATION_JSON',
          content: JSON.stringify({
            status: 'rate_limited',
            message: 'Too many requests from your network. Try again in a few minutes. Everything else on this page still works.',
          }),
        },
      },
      rules,
    });
    this.webAclArn = acl.attrArn;
    new cdk.CfnOutput(this, 'WebAclArn', { value: acl.attrArn });
  }
}
