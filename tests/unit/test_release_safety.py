from hca.release_safety import scan_text
from hca.support import PROJECT_PROVENANCE


def _rules(path: str, text: str) -> set[str]:
    return {finding.rule for finding in scan_text(path, text)}


def test_public_safety_detects_secret_and_private_source_shapes():
    private_key = "-----BEGIN " + "PRIVATE KEY-----"
    assert "private-key" in _rules("src/key.txt", private_key)
    assert "github-token" in _rules("config.txt", "ghp_" + "a" * 40)
    assert "openai-key" in _rules("config.txt", "sk-" + "b" * 40)
    assert "credential-url" in _rules(
        "docs/config.md", "https://operator:password@example.invalid/v1"
    )
    assert "private-home" in _rules("README.md", "/home/alice/private/model")


def test_public_safety_detects_masked_contract_and_stable_cluster_start():
    masked_contract = "pytest -q tests/contract " + chr(124) * 2 + " true"
    assert "masked-required-check" in _rules(
        ".github/workflows/ci.yml", masked_contract
    )
    assert "unsupported-remote-placement-claim" in _rules(
        "README.md", "hca cluster nodes up"
    )
    assert "unsupported-remote-placement-claim" in _rules(
        "docs/deployment-guide.md", "hca up --role control"
    )
    assert "approval-bypass" in _rules(
        "scripts/smoke.sh", "hermes -z 'test' --yolo"
    )
    assert "model-server-provisioning" in _rules(
        "config/vllm/docker-compose.yml", "services: {}"
    )
    assert "historical-plan-marker" in _rules(
        "docs/plans/old.md", "# Old plan\n\nRun this deployment.\n"
    )


def test_public_safety_allows_fixtures_placeholders_and_harmless_fingerprint():
    assert _rules("tests/test_fixture.py", 'url = "https://user:secret@example.invalid"') == set()
    assert _rules("docs/config.md", "store files under /home/user/project") == set()
    assert _rules("src/hca/support.py", PROJECT_PROVENANCE) == set()
    assert _rules(
        "docs/plans/old.md",
        "# Old plan\n\n> Historical record — superseded and not executable.\n",
    ) == set()
    # The dedicated blocker document may name the legacy command while stable
    # quickstarts and product surfaces may not advertise it.
    assert _rules("docs/gb10-cluster.md", "`hca cluster nodes up` exits 3") == set()
