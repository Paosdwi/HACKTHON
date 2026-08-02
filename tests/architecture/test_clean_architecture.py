from __future__ import annotations

import ast
import importlib
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
PACKAGE_ROOT = SRC_ROOT / "crypto_trust_agent"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

LAYERS = ("domain", "application", "infrastructure", "presentation")
VENDOR_ROOTS = {
    "agentcore",
    "anthropic",
    "aws_cdk",
    "bedrock",
    "boto3",
    "botocore",
    "fastapi",
    "httpx",
    "langchain",
    "openai",
    "playwright",
    "requests",
    "sagemaker",
}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


class PackageBoundaryTests(unittest.TestCase):
    def test_all_clean_architecture_layers_are_explicit_packages(self) -> None:
        for layer in LAYERS:
            with self.subTest(layer=layer):
                package_marker = PACKAGE_ROOT / layer / "__init__.py"
                self.assertTrue(package_marker.is_file(), package_marker)
                module = importlib.import_module(f"crypto_trust_agent.{layer}")
                self.assertIsNotNone(module)

    def test_domain_has_no_outward_or_vendor_imports(self) -> None:
        forbidden = {
            "crypto_trust_agent.application",
            "crypto_trust_agent.infrastructure",
            "crypto_trust_agent.presentation",
            *VENDOR_ROOTS,
        }
        self._assert_no_forbidden_imports(PACKAGE_ROOT / "domain", forbidden)

    def test_application_depends_only_on_domain_and_owned_boundaries(self) -> None:
        forbidden = {
            "crypto_trust_agent.infrastructure",
            "crypto_trust_agent.presentation",
            *VENDOR_ROOTS,
        }
        self._assert_no_forbidden_imports(PACKAGE_ROOT / "application", forbidden)

    def test_presentation_only_wires_local_adapters_in_named_composition_root(self) -> None:
        presentation = PACKAGE_ROOT / "presentation"
        composition = presentation / "api" / "demo_ui_composition.py"
        compositions = {
            composition,
            presentation / "api" / "aws_demo_composition.py",
        }
        violations: list[str] = []
        for source_file in sorted(presentation.rglob("*.py")):
            if source_file in compositions:
                continue
            for module in sorted(imported_modules(source_file)):
                if module == "crypto_trust_agent.infrastructure" or module.startswith(
                    "crypto_trust_agent.infrastructure."
                ):
                    violations.append(
                        f"{source_file.relative_to(PROJECT_ROOT)} imports {module}"
                    )
        self.assertEqual([], violations, "\n".join(violations))

        composition_imports = imported_modules(composition)
        allowed_roots = {
            "crypto_trust_agent.infrastructure.fakes",
            "crypto_trust_agent.infrastructure.identity",
        }
        concrete_imports = {
            module
            for module in composition_imports
            if module == "crypto_trust_agent.infrastructure"
            or module.startswith("crypto_trust_agent.infrastructure.")
        }
        unexpected = {
            module
            for module in concrete_imports
            if not any(
                module == root or module.startswith(f"{root}.")
                for root in allowed_roots
            )
        }
        self.assertEqual(set(), unexpected)
        self.assertTrue(
            any(module.startswith("crypto_trust_agent.infrastructure.fakes") for module in concrete_imports)
        )
        self.assertTrue(
            any(module.startswith("crypto_trust_agent.infrastructure.identity") for module in concrete_imports)
        )

    def _assert_no_forbidden_imports(
        self,
        directory: Path,
        forbidden_roots: set[str],
    ) -> None:
        violations: list[str] = []
        for source_file in sorted(directory.rglob("*.py")):
            for module in sorted(imported_modules(source_file)):
                if any(
                    module == root or module.startswith(f"{root}.")
                    for root in forbidden_roots
                ):
                    violations.append(
                        f"{source_file.relative_to(PROJECT_ROOT)} imports {module}"
                    )
        self.assertEqual([], violations, "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
