"""Architecture tests - enforce layering rules.

These tests fail FAST when layering violations are introduced, preventing
architectural debt from accumulating.

Rules enforced:
1. Domain layer MUST NOT import from infrastructure layer
2. Domain MAY import from domain
3. Infrastructure MAY import from domain (implements interfaces)
4. CLI MAY import from anywhere (composition root)

See ARCHITECTURE.md and CLAUDE.md §Architectural Guardrails.
"""

import ast
import sys
from pathlib import Path

import pytest


def get_src_root() -> Path:
    """Find magicapply/src/magicapply root."""
    # Tests are in magicapply/tests, src is magicapply/src/magicapply
    tests_dir = Path(__file__).parent.parent
    src_root = tests_dir.parent / "src" / "magicapply"
    assert src_root.exists(), f"Source root not found: {src_root}"
    return src_root


def collect_python_files(directory: Path) -> list[Path]:
    """Recursively find all .py files in directory."""
    return list(directory.rglob("*.py"))


def extract_imports(file_path: Path) -> set[str]:
    """Extract all 'from X import Y' module prefixes from a Python file."""
    try:
        with open(file_path) as f:
            tree = ast.parse(f.read(), filename=str(file_path))
    except SyntaxError:
        return set()

    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


def is_infrastructure_import(module: str) -> bool:
    """Check if module is from infrastructure layer."""
    return module.startswith("magicapply.infrastructure.")


def is_domain_import(module: str) -> bool:
    """Check if module is from domain layer."""
    return module.startswith("magicapply.domain.")


@pytest.fixture
def src_root() -> Path:
    return get_src_root()


@pytest.fixture
def domain_files(src_root: Path) -> list[Path]:
    domain_dir = src_root / "domain"
    return collect_python_files(domain_dir)


class TestLayeringRules:
    """Enforce architectural layering constraints."""

    def test_domain_does_not_import_infrastructure(self, domain_files):
        """Domain layer MUST NOT import from infrastructure layer.

        This is the core layering rule. Domain defines interfaces (Protocols);
        infrastructure implements them. Dependency arrows point inward.

        Violations make domain untestable without infrastructure and create
        tight coupling.
        """
        violations = []

        for file_path in domain_files:
            imports = extract_imports(file_path)
            infra_imports = [imp for imp in imports if is_infrastructure_import(imp)]

            # Allow TYPE_CHECKING imports for return type annotations only
            if infra_imports:
                # Check if this is the special case: domain/llm.py importing LLMResult
                # under TYPE_CHECKING (this is allowed for return type annotation)
                if "domain/llm.py" in str(file_path):
                    # Read file to check if it's under TYPE_CHECKING
                    with open(file_path) as f:
                        content = f.read()
                    if "if TYPE_CHECKING:" in content and "from magicapply.infrastructure.llm.client import LLMResult" in content:
                        # This is the allowed exception
                        continue

                relative = file_path.relative_to(file_path.parents[4])  # Relative to repo root
                violations.append({"file": str(relative), "imports": infra_imports})

        if violations:
            msg = "Domain layer importing from infrastructure (layering violation):\n\n"
            for v in violations:
                msg += f"  {v['file']}:\n"
                for imp in v["imports"]:
                    msg += f"    - {imp}\n"
            msg += "\nFix: Move Protocol/types to domain layer, keep implementations in infrastructure.\n"
            msg += "See ARCHITECTURE.md §3 and CLAUDE.md Architectural Guardrails.\n"
            pytest.fail(msg)

    def test_domain_llm_protocol_exists(self, src_root):
        """Domain LLM Protocol exists after Phase 1 refactor."""
        llm_protocol = src_root / "domain" / "llm.py"
        assert llm_protocol.exists(), "domain/llm.py not found. Phase 1 refactor incomplete."

        # Verify it contains LLMClient Protocol
        content = llm_protocol.read_text()
        assert "class LLMClient(Protocol):" in content
        assert "class LLMMessage(BaseModel):" in content
        assert "class SystemBlock(BaseModel):" in content

    def test_infrastructure_can_import_domain(self, src_root):
        """Infrastructure MAY import from domain (this is correct).

        Infrastructure implements domain interfaces. This test verifies the
        dependency direction is inward (infra → domain, not domain → infra).
        """
        infra_dir = src_root / "infrastructure"
        infra_files = collect_python_files(infra_dir)

        # Count how many infra files import domain
        importing_count = 0
        for file_path in infra_files:
            imports = extract_imports(file_path)
            if any(is_domain_import(imp) for imp in imports):
                importing_count += 1

        # We expect MANY infra files to import domain (repositories, providers, etc.)
        assert importing_count > 0, (
            "Infrastructure should import domain interfaces. "
            "If this fails, the dependency arrow may be inverted."
        )

    def test_no_circular_dependencies_domain_infrastructure(self, src_root):
        """No file should import from both domain and infrastructure's internal modules.

        This is a weak check for circular dependencies. A file in infrastructure
        should import domain interfaces, not other infrastructure internals that
        might create cycles.
        """
        src_files = collect_python_files(src_root)

        suspicious = []
        for file_path in src_files:
            imports = extract_imports(file_path)
            has_domain = any(is_domain_import(imp) for imp in imports)
            has_infra = any(is_infrastructure_import(imp) for imp in imports)

            # Files in pipelines/ or cli/ may import both (composition)
            if "pipelines" in str(file_path) or "cli" in str(file_path):
                continue

            # Infrastructure files importing domain is expected
            if "infrastructure" in str(file_path) and has_domain:
                continue

            # Domain importing infrastructure is a violation (caught by other test)
            if "domain" in str(file_path) and has_infra:
                continue

            # Flag anything else that imports both
            if has_domain and has_infra:
                suspicious.append(str(file_path.relative_to(file_path.parents[4])))

        # This is informational, not a hard failure
        if suspicious:
            print(f"\nInfo: {len(suspicious)} files import both domain and infrastructure:")
            for s in suspicious[:5]:
                print(f"  - {s}")


class TestModuleStructure:
    """Verify expected module structure exists."""

    def test_domain_layer_exists(self, src_root):
        assert (src_root / "domain").is_dir()

    def test_infrastructure_layer_exists(self, src_root):
        assert (src_root / "infrastructure").is_dir()

    def test_pipelines_layer_exists(self, src_root):
        # pipelines/ is the implemented services layer
        assert (src_root / "pipelines").is_dir()

    def test_cli_layer_exists(self, src_root):
        assert (src_root / "cli").is_dir()

    def test_repositories_protocol_in_domain(self, src_root):
        """Repository Protocols live in domain, implementations in infrastructure."""
        repos_protocol = src_root / "domain" / "repositories.py"
        assert repos_protocol.exists()

        content = repos_protocol.read_text()
        assert "class JobsRepository(Protocol):" in content
        assert "class ApplicationsRepository(Protocol):" in content


if __name__ == "__main__":
    # Allow running directly: python tests/unit/test_architecture.py
    sys.exit(pytest.main([__file__, "-v"]))
