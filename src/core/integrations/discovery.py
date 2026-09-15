"""Capability discovery: probe the environment for available integrations."""

from core.integrations.base import Integration
from core.integrations.github import GitHubIntegration
from core.integrations.vercel import VercelIntegration


def discover_integrations() -> list[Integration]:
    """Probe all bundled external-CLI integrations."""
    return [
        GitHubIntegration(),
        VercelIntegration(),
    ]


def build_integration_briefing(integrations: list[Integration]) -> str:
    """Build an honest status line about which integrations are usable."""
    lines = ["INTEGRATION STATUS:"]
    for integration in integrations:
        lines.append(f"- {integration.availability().status}")
    return "\n".join(lines)
