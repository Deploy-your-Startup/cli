"""GIVEN the real Markdown instructions, WHEN following every relative link, THEN each target exists.

Agent instructions stay short by linking to the documents that hold the details.
This check keeps those links working; it deliberately asserts no wording.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIPPED_DIRECTORIES = {
    ".git",
    "node_modules",
    ".venv",
    "dist",
    "build",
    ".pytest_cache",
}
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
IMPORT = re.compile(r"^@(\S+)$", re.MULTILINE)


def markdown_files():
    for path in sorted(ROOT.rglob("*.md")):
        if not SKIPPED_DIRECTORIES.intersection(path.relative_to(ROOT).parts):
            yield path


def broken_links(document):
    text = re.sub(r"```.*?```", "", document.read_text(), flags=re.DOTALL)
    targets = LINK.findall(text) + IMPORT.findall(text)
    for target in targets:
        if (
            re.match(r"^[a-z][a-z0-9+.-]*:", target)
            or target.startswith("#")
            or "{{" in target
        ):
            continue
        path = target.split("#", 1)[0]
        if path and not (document.parent / path).exists():
            yield f"{document.relative_to(ROOT)} → {target}"


def test_relative_links_resolve():
    # GIVEN every Markdown document in this standalone repository.
    documents = list(markdown_files())
    # WHEN following their relative links and CLAUDE.md imports.
    broken = [link for document in documents for link in broken_links(document)]
    # THEN every target exists, so short instructions lead to their details.
    assert not broken, "Broken links:\n" + "\n".join(broken)


def test_agent_instructions_link_the_testing_policy():
    # GIVEN each agent entrypoint that sits next to a testing policy,
    # WHEN looking for the policy it delegates to,
    # THEN it links the policy instead of restating it.
    for policy in ROOT.rglob("docs/testing-policy.md"):
        instructions = policy.parent.parent / "AGENTS.md"
        if (
            SKIPPED_DIRECTORIES.intersection(policy.relative_to(ROOT).parts)
            or not instructions.exists()
        ):
            continue
        assert "](docs/testing-policy.md)" in instructions.read_text(), (
            f"{instructions.relative_to(ROOT)} must link docs/testing-policy.md"
        )


if __name__ == "__main__":
    for check in (
        test_relative_links_resolve,
        test_agent_instructions_link_the_testing_policy,
    ):
        check()
    print("Agent instruction links resolve and delegate to the testing policy.")
    sys.exit(0)
