#!/usr/bin/env python3
"""Prove a built bundle runs on Linux, without AWS. CI runs this on Python 3.12.

Loads everything from the bundle alone, then answers one question through the real
handler and engine, with a scripted model and in-memory counters.
"""

import json
import os
import platform
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLE = os.environ.get("HEADROOM_ANALYST_BUNDLE", os.path.join(ROOT, "infra", "build", "analyst"))

if platform.system() != "Linux" or sys.version_info[:2] != (3, 12):
    sys.exit("This check needs Linux and Python 3.12, like Lambda. Saw {} {}.{}.".format(
        platform.system(), *sys.version_info[:2]))

sys.path.insert(0, BUNDLE)
os.chdir(BUNDLE)

import anthropic  # noqa: E402
import boto3  # noqa: E402
import numpy  # noqa: E402

from analyst import handler as lambda_handler  # noqa: E402
from analyst.limits import Limits  # noqa: E402
from sim import whatif  # noqa: E402

for module in (anthropic, boto3, numpy, whatif):
    if not os.path.abspath(module.__file__).startswith(os.path.abspath(BUNDLE)):
        sys.exit("{} loaded from outside the bundle: {}".format(module.__name__, module.__file__))

# Both Bedrock clients must construct from the bundle (no network until first use).
anthropic.AnthropicBedrock(aws_region="us-west-2", aws_access_key="x", aws_secret_key="x")
anthropic.AnthropicBedrockMantle(aws_region="us-west-2", aws_access_key="x", aws_secret_key="x")


class Model:
    model, input_tokens, output_tokens = "scripted", 0, 0
    replies = [
        {"in_scope": True, "scenario": "peak",
         "changes": {**{k.key: None for k in whatif.KNOBS}, whatif.MOVE_SWITCH: None,
                     "demand_charge_usd_per_kw": 36.0}},
        {"answer": "In this run the site saves {{whatif.usd_saved}}."},
    ]

    def structured(self, *args, **kwargs):
        return self.replies.pop(0)


class Counters:
    def update_item(self, **kwargs):
        return None


lambda_handler.llm_module.from_environment = Model
lambda_handler._limits = Limits(client=Counters(), table="t", daily_cap=1, hourly_per_visitor=1)
reply = lambda_handler.handler({
    "requestContext": {"http": {"method": "POST"}},
    "headers": {},
    "body": json.dumps({"question": "What if the demand charge doubled?", "scenario": "peak"}),
}, None)
body = json.loads(reply["body"])
if reply["statusCode"] != 200 or body.get("answer_source") != "model_verified":
    sys.exit("Unexpected reply: {}".format(reply["body"][:400]))
print("Bundle OK: {}".format(body["answer"]))
