import * as fs from 'fs';
import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';

/** Deploy settings, from `-c headroom:analyst*` context. Which model an account can call varies. */
export interface AnalystProps {
  readonly model: string;
  readonly route: string;
  readonly region: string;
  readonly effort: string;
  readonly dailyCap: string;
  readonly hourlyPerVisitor: string;
  /** Optional cap on simultaneous runs. New accounts may have too little concurrency to reserve any. */
  readonly reservedConcurrency?: number;
}

/**
 * The what-if analyst backend: one Lambda, reachable only through CloudFront
 * (its function URL requires IAM auth and the distribution signs with OAC), and
 * one table of counters that enforces the hard limits.
 */
export class Analyst extends Construct {
  readonly url: lambda.FunctionUrl;

  constructor(scope: Construct, id: string, props: AnalystProps) {
    super(scope, id);

    // Built by scripts/build_analyst.py; no Docker, so it works in CloudShell.
    const bundle =
      process.env.HEADROOM_ANALYST_BUNDLE ?? path.join(__dirname, '..', 'build', 'analyst');
    if (!fs.existsSync(path.join(bundle, 'analyst', 'handler.py'))) {
      throw new Error(
        `Analyst bundle not found at ${bundle}. Run: python scripts/build_analyst.py ` +
          '(or deploy without the analyst: -c headroom:analyst=false).',
      );
    }

    const counters = new dynamodb.Table(this, 'Counters', {
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'expires_at',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const fn = new lambda.Function(this, 'Handler', {
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.X86_64,
      handler: 'analyst.handler.handler',
      code: lambda.Code.fromAsset(bundle),
      memorySize: 1769, // One full vCPU; the engine is CPU-bound.
      timeout: cdk.Duration.seconds(58), // Just under CloudFront's 60 s read timeout.
      reservedConcurrentExecutions: props.reservedConcurrency,
      logGroup: new logs.LogGroup(this, 'Logs', {
        retention: logs.RetentionDays.TWO_WEEKS,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
      environment: {
        ANALYST_TABLE: counters.tableName,
        ANALYST_MODEL: props.model,
        ANALYST_ROUTE: props.route,
        ANALYST_REGION: props.region,
        ANALYST_EFFORT: props.effort,
        ANALYST_DAILY_CAP: props.dailyCap,
        ANALYST_HOURLY_PER_VISITOR: props.hourlyPerVisitor,
        ANALYST_SALT: cdk.Aws.STACK_ID,
      },
    });

    counters.grant(fn, 'dynamodb:UpdateItem');

    // Both Bedrock routes: which one works depends on the account and model.
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'InvokeAnthropicModels',
        actions: ['bedrock:InvokeModel'],
        resources: [
          `arn:${cdk.Aws.PARTITION}:bedrock:*::foundation-model/anthropic.*`,
          `arn:${cdk.Aws.PARTITION}:bedrock:*:${cdk.Aws.ACCOUNT_ID}:inference-profile/*anthropic.*`,
        ],
      }),
    );
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'CallMessagesEndpoint',
        actions: ['bedrock-mantle:CreateInference'],
        resources: ['*'], // The endpoint's docs give no resource ARN format to narrow this to.
      }),
    );

    this.url = fn.addFunctionUrl({ authType: lambda.FunctionUrlAuthType.AWS_IAM });
  }
}
