"""Fail-closed invariants for the pre-PA73 AWS deployment foundation."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = ROOT / "deploy" / "aws" / "ecs-preview.json"
DOCKERFILE_PATH = ROOT / "Dockerfile"


class DeploymentScaffoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.template_text = TEMPLATE_PATH.read_text(encoding="utf-8")
        cls.template = json.loads(cls.template_text)
        cls.resources = cls.template["Resources"]
        cls.dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")

    def test_foundation_defaults_to_zero_tasks_and_uses_explicit_aws_demo_module(self) -> None:
        parameters = self.template["Parameters"]
        self.assertEqual(0, parameters["DesiredCount"]["Default"])
        self.assertEqual(
            "crypto_trust_agent.presentation.api.aws_demo_api:app",
            parameters["AsgiAppModule"]["Default"],
        )
        self.assertNotIn("demo_ui_api", self.template_text)
        self.assertIn("CRYPTOTRUST_ASGI_APP", self.dockerfile)
        self.assertIn(":?set CRYPTOTRUST_ASGI_APP", self.dockerfile)

    def test_container_and_fargate_boundaries_are_hardened(self) -> None:
        task = self.resources["WebTaskDefinition"]["Properties"]
        container = task["ContainerDefinitions"][0]
        self.assertEqual(["FARGATE"], task["RequiresCompatibilities"])
        self.assertEqual("awsvpc", task["NetworkMode"])
        self.assertTrue(container["ReadonlyRootFilesystem"])
        self.assertEqual("10001:10001", container["User"])
        self.assertFalse(self.resources["WebService"]["Properties"]["EnableExecuteCommand"])
        self.assertIn("USER 10001:10001", self.dockerfile)
        self.assertNotIn(":latest", self.dockerfile)

    def test_900_second_run_has_queue_margin_and_alb_outer_guard(self) -> None:
        queue = self.resources["FormalRunQueue"]["Properties"]
        self.assertGreaterEqual(queue["VisibilityTimeout"], 900)
        self.assertEqual(2, queue["RedrivePolicy"]["maxReceiveCount"])
        attributes = self.resources["ApplicationLoadBalancer"]["Properties"]["LoadBalancerAttributes"]
        idle = next(item["Value"] for item in attributes if item["Key"] == "idle_timeout.timeout_seconds")
        self.assertGreaterEqual(int(idle), 900)
        self.assertLessEqual(int(idle), 4000)

    def test_no_secret_account_bucket_or_endpoint_is_hardcoded(self) -> None:
        self.assertEqual("", self.template["Parameters"]["RuntimeSecretArn"]["Default"])
        self.assertEqual(
            "us.anthropic.claude-opus-4-8",
            self.template["Parameters"]["BedrockModelId"]["Default"],
        )
        self.assertNotRegex(self.template_text, re.compile(r"\b\d{12}\b"))
        for forbidden in ("AKIA", "aws_secret_access_key", "password=", "api_key="):
            self.assertNotIn(forbidden, self.template_text.casefold())

    def test_artifacts_and_queues_are_private_encrypted_and_retained(self) -> None:
        bucket = self.resources["ArtifactBucket"]
        public_access = bucket["Properties"]["PublicAccessBlockConfiguration"]
        self.assertTrue(all(public_access.values()))
        self.assertEqual("Retain", bucket["DeletionPolicy"])
        self.assertTrue(self.resources["FormalRunQueue"]["Properties"]["SqsManagedSseEnabled"])
        self.assertTrue(self.resources["FormalRunDeadLetterQueue"]["Properties"]["SqsManagedSseEnabled"])


if __name__ == "__main__":
    unittest.main()
