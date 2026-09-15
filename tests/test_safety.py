from core.safety.approval import ApprovalPolicy


def test_read_only_tools_always_approved():
    policy = ApprovalPolicy()
    for tool in [
        "read_file",
        "git_status",
        "git_diff",
        "git_log",
        "inspect_repository",
    ]:
        decision = policy.assess(tool, {})
        assert decision.approved, f"{tool} should be approved"


def test_destructive_commands_blocked():
    policy = ApprovalPolicy()
    test_commands = [
        "git reset --hard HEAD",
        "git clean -fd",
        "git checkout -- .",
        "git restore .",
        "git push --force origin main",
        "rm -rf /tmp/something",
    ]
    for command in test_commands:
        decision = policy.assess("execute_command", {"command": command})
        assert not decision.approved, f"Should block: {command}"


def test_high_risk_commands_need_approval():
    policy = ApprovalPolicy()
    decision = policy.assess("execute_command", {"command": "git push origin main"})
    assert not decision.approved
    assert "requires approval" in decision.reason


def test_read_only_commands_auto_approved():
    policy = ApprovalPolicy()
    for command in ["git status", "git diff", "git log --oneline", "git branch"]:
        decision = policy.assess("execute_command", {"command": command})
        assert decision.approved, f"Should auto-approve: {command}"


def test_file_modification_requires_approval():
    policy = ApprovalPolicy()
    decision = policy.assess("write_file", {"path": "x.py", "content": "print(1)"})
    assert not decision.approved
    decision = policy.assess("delete_file", {"path": "x.py"})
    assert not decision.approved


def test_disabled_approval_allows_changes():
    policy = ApprovalPolicy(require_approval=False)
    decision = policy.assess("write_file", {"path": "x.py", "content": ""})
    assert decision.approved


def test_empty_command_blocked():
    policy = ApprovalPolicy()
    decision = policy.assess("execute_command", {"command": ""})
    assert not decision.approved
