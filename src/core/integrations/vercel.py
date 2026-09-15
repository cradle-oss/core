"""Vercel integration for CORE.

Wraps the Vercel CLI (`vercel`). Uses structured JSON output (`--json`).
Native CLI first: never scrape the dashboard.

Availability: `vercel` installed and authenticated (`vercel whoami`).
"""

import json

from core.integrations.base import (
    Availability,
    Integration,
    IntegrationError,
    truncate_output,
)
from core.tools.registry import ToolResult

MAX_OUTPUT = 20000


class VercelIntegration(Integration):
    name = "vercel"

    def _base_command(self) -> list[str]:
        return ["vercel"]

    def _probe_availability(self) -> Availability:
        version_res = self.run(["--version"], timeout=20)
        if not version_res.ok or not version_res.stdout.strip():
            return Availability(name=self.name, available=False)
        line = version_res.stdout.strip().splitlines()[0]
        version = line.split()[-1] if line else None

        whoami = self.run(["whoami"], timeout=20)
        identity = None
        if whoami.ok:
            # whoami output: sometimes has a first line prefix; last line is user
            for ln in whoami.stdout.strip().splitlines():
                stripped = ln.strip()
                if stripped and stripped != version:
                    identity = stripped
                    break
        return Availability(
            name=self.name,
            available=True,
            version=version,
            authenticated=whoami.ok,
            identity=identity,
            detail="vercel " + (version or "?"),
        )

    # ---- tool handlers ----

    def _vercel_deployments(self, limit=20, workdir=".") -> ToolResult:
        """List recent deployments for the current project or all."""
        try:
            self.require_available()
        except IntegrationError as e:
            return ToolResult(success=False, output="", error=str(e))
        out = self.run(
            ["ls", "--json", "--limit", str(min(limit, 50))],
            timeout=60,
            cwd=workdir,
        )
        if not out.ok:
            return ToolResult(
                success=False,
                output="",
                error=f"vercel ls failed: {out.stderr.strip() or out.stdout.strip()}",
            )
        try:
            data = json.loads(out.stdout)
        except json.JSONDecodeError:
            return ToolResult(
                success=False, output="", error="vercel ls returned invalid JSON"
            )
        lines = []
        for d in data[:25]:
            state = d.get("state", "?")
            url = d.get("url", "")
            lines.append(
                f"- {d.get('readyState', state)} {url} "
                f"on {d.get('created', '?')} ({d.get('target', '?')})"
            )
        output = "\n".join(lines) if lines else "(no deployments)"
        output, truncated = truncate_output(output, MAX_OUTPUT)
        return ToolResult(
            success=True,
            output=output,
            evidence=f"vercel ls: {len(data)} deployments"
            + (" [truncated]" if truncated else ""),
            truncated=truncated,
        )

    def _vercel_inspect(self, deployment, workdir=".") -> ToolResult:
        """Inspect a specific deployment."""
        try:
            self.require_available()
        except IntegrationError as e:
            return ToolResult(success=False, output="", error=str(e))
        if not deployment:
            return ToolResult(
                success=False,
                output="",
                error="deployment (URL or id) is required.",
            )
        out = self.run(
            ["inspect", deployment, "--json", "--no-wait"],
            timeout=60,
            cwd=workdir,
        )
        if not out.ok:
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"vercel inspect {deployment} failed: "
                    f"{out.stderr.strip() or out.stdout.strip()}"
                ),
            )
        try:
            data = json.loads(out.stdout)
        except json.JSONDecodeError:
            return ToolResult(
                success=False, output="", error="vercel returned invalid JSON"
            )
        compact = {
            "url": data.get("url"),
            "state": data.get("state") or data.get("readyState"),
            "target": data.get("target"),
            "created": data.get("created"),
            "buildTime": data.get("buildTime"),
            "creator": data.get("creator"),
            "inspectorUrl": data.get("inspectorUrl"),
            "meta": data.get("meta") or {},
        }
        output = json.dumps(compact, indent=2, default=str)
        output, truncated = truncate_output(output, MAX_OUTPUT)
        return ToolResult(
            success=True,
            output=output,
            evidence=(f"vercel inspect {deployment}: state={compact.get('state')}"),
            truncated=truncated,
        )

    # ---- tool registration ----

    def tools(self):
        from core.tools.registry import Tool

        return [
            Tool(
                name="vercel_deployments",
                description=(
                    "List recent Vercel deployments for the project. "
                    "Returns URL, state, target, date."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Max deployments",
                        },
                        "workdir": {
                            "type": "string",
                            "description": "Project directory with vercel.json",
                        },
                    },
                    "required": [],
                },
                handler=self._vercel_deployments,
                safe=True,
            ),
            Tool(
                name="vercel_inspect",
                description=(
                    "Inspect a specific Vercel deployment by URL or id. "
                    "Returns state, build time, creator, meta."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "deployment": {
                            "type": "string",
                            "description": "Deployment URL or id",
                        },
                        "workdir": {
                            "type": "string",
                            "description": "Project directory",
                        },
                    },
                    "required": ["deployment"],
                },
                handler=self._vercel_inspect,
                safe=True,
            ),
        ]
