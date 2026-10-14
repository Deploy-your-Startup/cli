"""GIVEN portable instructions, WHEN following their link, THEN the test policy is explicit."""
from pathlib import Path
import re


def test_portable_testing_policy():
    # GIVEN this real standalone repository and its agent entrypoint.
    root = Path(__file__).resolve().parents[1]
    instructions = (root / "AGENTS.md").read_text()
    # WHEN a contributor follows the test-policy link.
    target = re.search(r"\[Testing policy and completion evidence\]\(([^)]+)\)", instructions)
    assert target is not None
    policy = (root / target.group(1)).read_text()
    # THEN required flow coverage, the limited exception and honest evidence survive.
    assert "Integration tests are the default" in policy
    assert "Unit tests are reserved for complex algorithms" in policy
    assert "Every enabled shard must succeed" in policy
    assert "not a pass" in policy
    assert "Record uncovered flows" in policy
    for old_rule in ("Never write unit tests", "never unit tests", "No unit tests:"):
        assert old_rule not in instructions


if __name__ == "__main__":
    test_portable_testing_policy()
    print("Portable agent instructions and testing policy verified")
