import * as fs from 'fs';
import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';

/** Deploy settings, from `-c headroom:analyst*` context. */
export interface AnalystProps {
  /** A Gemini model id, such as `gemini-4-hydrogen`. */
  readonly model: string;
  /** The model's thinking level; empty for the model's default. */
  readonly effort: string;
  /**
   * Name of the SSM SecureString parameter that holds the Gemini API key. The owner
   * creates it; the stack only grants the function read access, so the key never
   * appears in the template or in the function's configuration.
   */
  readonly keyParameter: string;
  readonly dailyCap: string;
  readonly hourlyPerVisitor: string;
  /**
   * Cap on simultaneous runs, or undefined for none. Reserving takes from the account's
   * concurrency quota, which must keep at least 100 unreserved; new accounts often have a
   * quota of 10 and can reserve nothing.
   */
  readonly reservedConcurrency?: number;
  /** false: every request is refused before the counters or the model. */
  readonly enabled: boolean;
}

/**
 * The what-if analyst backend: one Lambda, reachable only through CloudFront
 * (its function URL requires IAM auth and the distribution signs with OAC), and
 * one table of counters that enforces the hard limits.
 */
export class Analyst extends Construct {
  readonly url: lambda.FunctionUrl;
  readonly countersTable: string;
  readonly functionName: string;

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
        ANALYST_EFFORT: props.effort,
        ANALYST_KEY_PARAMETER: props.keyParameter,
        ANALYST_DAILY_CAP: props.dailyCap,
        ANALYST_HOURLY_PER_VISITOR: props.hourlyPerVisitor,
        ANALYST_SALT: cdk.Aws.STACK_ID,
        ANALYST_ENABLED: props.enabled ? 'true' : 'false',
      },
    });

    // UpdateItem for the counters, GetItem for the operator's switch (scripts/analyst-switch.sh).
    counters.grant(fn, 'dynamodb:UpdateItem', 'dynamodb:GetItem');
    this.countersTable = counters.tableName;
    this.functionName = fn.functionName;

    // The model is Gemini, called over the public internet with an API key. The only
    // AWS permission that needs is reading the key. The parameter must be encrypted
    // with the account's default SSM key (alias/aws/ssm), which needs no KMS grant.
    if (!props.keyParameter.startsWith('/')) {
      throw new Error(`headroom:analystKeyParameter must start with "/": ${props.keyParameter}`);
    }
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'ReadGeminiApiKey',
        actions: ['ssm:GetParameter'],
        resources: [
          `arn:${cdk.Aws.PARTITION}:ssm:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:parameter${props.keyParameter}`,
        ],
      }),
    );

    this.url = fn.addFunctionUrl({ authType: lambda.FunctionUrlAuthType.AWS_IAM });
  }
}
