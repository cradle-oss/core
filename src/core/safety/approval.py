"""Approval/safety layer for CORE."""

from dataclasses import dataclass

DESTRUCTIVE_PATTERNS = [
    "reset --hard",
    "clean -fd",
    "checkout -- .",
    "restore .",
    "push --force",
    "push -f",
    "rebase",
    "filter-branch",
    "clean -fdx",
    "rm -rf",
    "branch -D",
    "branch --delete --force",
    "reset HEAD~",
]

HIGH_RISK_COMMANDS = [
    "git push",
    "git merge",
    "git rebase",
    "git cherry-pick",
    "git reset",
    "git revert",
    "git checkout -b",  # thinking: switching branches is safe, but keep inspection
    "git stash pop",
    "git clean",
    "git fetch --prune",
]

READ_ONLY_GIT_COMMANDS = [
    "git status",
    "git diff",
    "git log",
    "git show",
    "git branch",
    "git remote",
    "git tag",
    "git describe",
    "git blame",
]


@dataclass
class ApprovalDecision:
    approved: bool
    reason: str
    details: str = ""


class ApprovalPolicy:
    """Determines which operations require user approval."""

    def __init__(
        self,
        auto_approve_read_only: bool = True,
        require_approval: bool = True,
    ):
        self.auto_approve_read_only = auto_approve_read_only
        self.require_approval = require_approval

    def assess(self, tool_name: str, arguments: dict) -> ApprovalDecision:
        """Assess whether an operation requires approval."""
        # Read-only tools never require approval
        read_only_tools = {
            "read_file",
            "list_directory",
            "search_files",
            "search_text",
            "git_status",
            "git_diff",
            "git_log",
            "git_show",
            "git_branch",
            "git_summary",
            "inspect_repository",
            "relevant_files",
        }
        if tool_name in read_only_tools:
            return ApprovalDecision(
                approved=True,
                reason=f"Read-only tool: {tool_name}",
            )

        # Execute command: check for dangerous patterns
        if tool_name == "execute_command":
            command = arguments.get("command", "")
            return self._assess_command(command)

        # File modifications: require approval for non-trivial operations
        modifying_tools = {
            "write_file",
            "create_file",
            "edit_file",
            "append_file",
            "delete_file",
        }
        if tool_name in modifying_tools:
            if not self.require_approval:
                return ApprovalDecision(
                    approved=True,
                    reason="Approval disabled",
                )
            return ApprovalDecision(
                approved=False,
                reason=f"File modification requires approval: {tool_name}",
                details=f"Tool {tool_name} with arguments: {arguments}",
            )

        return ApprovalDecision(
            approved=True,
            reason=f"Approved per policy for {tool_name}",
        )

    def _assess_command(self, command: str) -> ApprovalDecision:
        """Assess a shell command."""
        # Auto-approve read-only commands
        if self.auto_approve_read_only and any(
            command.strip().startswith(cmd) for cmd in READ_ONLY_GIT_COMMANDS
        ):
            return ApprovalDecision(
                approved=True,
                reason=f"Recognized read-only command: {command}",
            )

        # Block destructive commands outright unless explicitly allowed
        for pattern in DESTRUCTIVE_PATTERNS:
            if pattern in command:
                return ApprovalDecision(
                    approved=False,
                    reason=f"Destructive pattern detected: '{pattern}'",
                    details=f"Command: {command}",
                )

        # High-risk commands need approval
        if any(command.strip().startswith(cmd) for cmd in HIGH_RISK_COMMANDS):
            return ApprovalDecision(
                approved=False,
                reason=f"High-risk command requires approval: {command}",
                details=f"Command: {command}",
            )

        if not self.require_approval:
            return ApprovalDecision(approved=True, reason="Approval disabled")

        return ApprovalDecision(
            approved=False,
            reason=f"Command requires approval: {command}",
            details=f"Command: {command}",
        )


class InteractiveApprover:
    """Prompts the user for approval in the terminal."""

    def approve(self, decision: ApprovalDecision) -> bool:
        """Ask the user whether to proceed."""
        if decision.approved:
            return True

        print(f"\n[CORE] Approval required: {decision.reason}")
        if decision.details:
            print(f"[CORE] {decision.details}")

        response = input("[CORE] Proceed? [y/N] ").strip().lower()
        return response in {"y", "yes"} or response == "y"

    def confirm_destructive(self, description: str) -> bool:
        """Ask the user to confirm a destructive operation."""
        print(f"\n[CORE] DESTRUCTIVE OPERATION: {description}")
        response = input("[CORE] Type 'yes' to confirm: ").strip().lower()
        return response == "yes"


class NonInteractiveApprover:
    """Auto-approves only safe operations; blocks everything else."""

    def approve(self, decision: ApprovalDecision) -> bool:
        return decision.approved

    def confirm_destructive(self, description: str) -> bool:
        return False
