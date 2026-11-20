"""Policy discovery and loading engine (P20).

Engineered by Amal P S for AMstraLift Generation 2.
"""

from pathlib import Path

from amstralift.policy.models import EnterprisePolicy


def get_default_policy() -> EnterprisePolicy:
    """Return default compliant EnterprisePolicy."""
    return EnterprisePolicy()


def load_policy(
    repo_path: Path | str | None = None,
    explicit_policy_path: Path | str | None = None,
) -> EnterprisePolicy:
    """Discover and load enterprise policy from workspace or fallback to default."""
    if explicit_policy_path:
        p = Path(explicit_policy_path).resolve()
        if p.exists():
            return EnterprisePolicy.load(p)
        raise FileNotFoundError(f"Explicit policy file not found: {p}")

    if repo_path:
        rp = Path(repo_path).resolve()
        candidates = [
            rp / ".amstralift" / "policy.yaml",
            rp / ".amstralift" / "policy.yml",
            rp / "policy.yaml",
            rp / "policy.yml",
        ]
        for candidate in candidates:
            if candidate.exists():
                return EnterprisePolicy.load(candidate)

    # Check global home config
    home_policy = Path.home() / ".amstralift" / "policy.yaml"
    if home_policy.exists():
        return EnterprisePolicy.load(home_policy)

    return get_default_policy()
