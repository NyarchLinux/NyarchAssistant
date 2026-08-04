import re
import os
import json
import shlex
from enum import Enum
from typing import Optional, Tuple


class CommandAction(Enum):
    ALLOW = "allow"
    ASK = "ask"
    BLOCK = "block"


class PathSecurityLevel(Enum):
    YOLO = "yolo"
    TRUSTED = "trusted"
    SANDBOXED = "sandboxed"
    RESTRICTED = "restricted"

    @property
    def restrictiveness(self) -> int:
        priorities = {
            PathSecurityLevel.YOLO: 0,
            PathSecurityLevel.TRUSTED: 1,
            PathSecurityLevel.SANDBOXED: 2,
            PathSecurityLevel.RESTRICTED: 3,
        }
        return priorities[self]


class RiskLevel(Enum):
    SAFE = "safe"
    MODERATE = "moderate"
    DANGEROUS = "dangerous"
    CRITICAL = "critical"
    
    @property
    def value_priority(self) -> int:
        priorities = {
            RiskLevel.SAFE: 0,
            RiskLevel.MODERATE: 1,
            RiskLevel.DANGEROUS: 2,
            RiskLevel.CRITICAL: 3,
        }
        return priorities[self]


BUILTIN_RISK_RULES = [
    (r'\brm\s+(-rf?|--recursive|--force)\s+', RiskLevel.CRITICAL, CommandAction.BLOCK),
    (r'\bmkfs\b', RiskLevel.CRITICAL, CommandAction.BLOCK),
    (r'\bdd\s+', RiskLevel.CRITICAL, CommandAction.BLOCK),
    (r'\bfdisk\b', RiskLevel.CRITICAL, CommandAction.BLOCK),
    (r'\bparted\b', RiskLevel.CRITICAL, CommandAction.BLOCK),
    (r'\bchmod\s+777\b', RiskLevel.CRITICAL, CommandAction.BLOCK),
    (r'\bchown\s+(-R\s+)?root\b', RiskLevel.CRITICAL, CommandAction.BLOCK),
    (r'\bapt\s+(install|remove|purge|upgrade|dist-upgrade)\b', RiskLevel.DANGEROUS, CommandAction.ASK),
    (r'\bapt-get\s+(install|remove|purge|upgrade|dist-upgrade)\b', RiskLevel.DANGEROUS, CommandAction.ASK),
    (r'\byum\s+(install|remove|update|upgrade)\b', RiskLevel.DANGEROUS, CommandAction.ASK),
    (r'\bdnf\s+(install|remove|update|upgrade)\b', RiskLevel.DANGEROUS, CommandAction.ASK),
    (r'\bpacman\s+(-S|-R|-Syu)\b', RiskLevel.DANGEROUS, CommandAction.ASK),
    (r'\bsudo\s+', RiskLevel.DANGEROUS, CommandAction.ASK),
    (r'\bsystemctl\s+(start|stop|restart|enable|disable)\b', RiskLevel.DANGEROUS, CommandAction.ASK),
    (r'\bservice\s+\w+\s+(start|stop|restart)\b', RiskLevel.DANGEROUS, CommandAction.ASK),
    (r'\bmount\s+', RiskLevel.DANGEROUS, CommandAction.ASK),
    (r'\bumount\s+', RiskLevel.DANGEROUS, CommandAction.ASK),
    (r'\buseradd\b', RiskLevel.DANGEROUS, CommandAction.ASK),
    (r'\buserdel\b', RiskLevel.DANGEROUS, CommandAction.ASK),
    (r'\busermod\b', RiskLevel.DANGEROUS, CommandAction.ASK),
    (r'\bpasswd\s+', RiskLevel.DANGEROUS, CommandAction.ASK),
    (r'\bgit\s+(push|reset|--hard|clean)\b', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\bdocker\s+(rm|rmi|kill|stop|system)\b', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\bcurl\s+.*\|\s*(bash|sh)\b', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\bwget\s+.*(-O\s*-)?\s*.*\|\s*(bash|sh)\b', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\bchmod\s+', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\bchown\s+', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\bmv\s+', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\bcp\s+(-[a-zA-Z]*r[a-zA-Z]*|--recursive)\b', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\btouch\s+/', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\bmkdir\s+(-p\s+)?/', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\bpython3?\s+-c\s+', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\bnode\s+(-e|--eval)\s+', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\bbash\s+-c\s+', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\bsh\s+-c\s+', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\bpip3?\s+(install|uninstall)\b', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\bnpm\s+(install|uninstall|-g)\b', RiskLevel.MODERATE, CommandAction.ASK),
    (r'(?:\s|&|\d)>+\s', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\benv\b', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\bprintenv\b', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\bawk\s+', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\bsed\s+', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\bxargs\b', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\btee\s+', RiskLevel.MODERATE, CommandAction.ASK),
    (r'\bls\b', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bcat\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bhead\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\btail\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bpwd\b', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bwhoami\b', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bid\b', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bdate\b', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\becho\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bfind\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bgrep\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bwc\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bsort\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\buniq\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bdf\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bdu\b', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bfree\b', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\buname\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bwhich\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\btype\s+\w+\b', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bgit\s+(status|log|diff|branch|tag|remote|show)\b', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bfile\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bstat\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\brealpath\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\breadlink\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bbasename\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bdirname\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bmd5sum\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bsha256sum\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bcut\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\btr\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\bcolumn\s+', RiskLevel.SAFE, CommandAction.ALLOW),
    (r'\btree\b', RiskLevel.SAFE, CommandAction.ALLOW),
]


# Only commands in this set can be approved by a built-in SAFE rule.  The
# command still has to pass ``_is_safe_simple_command`` below.  Shell syntax is
# deliberately excluded: a harmless command combined with a second command is
# no longer considered harmless as a whole.
READ_ONLY_COMMANDS = {
    "basename",
    "cat",
    "column",
    "cut",
    "date",
    "df",
    "dirname",
    "du",
    "echo",
    "file",
    "find",
    "free",
    "git",
    "grep",
    "head",
    "id",
    "ls",
    "md5sum",
    "pwd",
    "readlink",
    "realpath",
    "sha256sum",
    "sort",
    "stat",
    "tail",
    "tr",
    "tree",
    "type",
    "uname",
    "uniq",
    "wc",
    "which",
    "whoami",
}

SHELL_CONTROL_CHARACTERS = frozenset(";&|<>()")


class CommandPermissionManager:
    """Manages command execution permissions based on patterns, risk levels, and path security."""

    _instance = None
    _settings = None

    def __init__(self, settings):
        self.settings = settings
        self._permission_rules_cache = None
        self._path_rules_cache = None
        self._default_risk_cache = None

    @classmethod
    def get_instance(cls, settings):
        if cls._instance is None or cls._settings != settings:
            cls._instance = CommandPermissionManager(settings)
            cls._settings = settings
        return cls._instance

    @classmethod
    def invalidate_cache(cls):
        if cls._instance is not None:
            cls._instance._permission_rules_cache = None
            cls._instance._path_rules_cache = None
            cls._instance._default_risk_cache = None

    def _load_permission_rules(self):
        if self._permission_rules_cache is None:
            try:
                rules = json.loads(
                    self.settings.get_string("command-execution-permissions")
                )
                self._permission_rules_cache = rules if isinstance(rules, list) else []
            except Exception:
                self._permission_rules_cache = []
        return self._permission_rules_cache

    def _load_path_rules(self):
        if self._path_rules_cache is None:
            try:
                raw = self.settings.get_string("path-security-levels")
                rules = json.loads(raw)
                self._path_rules_cache = rules if isinstance(rules, list) else []
            except Exception:
                self._path_rules_cache = [
                    {"path": "{{main_path}}", "level": "trusted"},
                    {"path": "/tmp", "level": "sandboxed"},
                ]
        return self._path_rules_cache

    def _load_default_risk(self):
        if self._default_risk_cache is None:
            try:
                val = self.settings.get_string("default-risk-level")
                self._default_risk_cache = val if val in [e.value for e in CommandAction] else "ask"
            except Exception:
                self._default_risk_cache = "ask"
        return self._default_risk_cache

    def check_command(self, command: str, working_dir: str = None) -> Tuple[CommandAction, str]:
        """Check if a command should be allowed, asked, or blocked.

        Returns:
            tuple: (action, reason)
        """
        builtin_action, builtin_reason = self._check_builtin_risk(command)
        # Critical built-in blocks are invariants. They cannot be weakened by
        # custom allow rules or by a YOLO path.
        if builtin_action == CommandAction.BLOCK:
            return self._adjust_for_path(
                builtin_action, builtin_reason, working_dir, command
            )

        custom_action, custom_reason = self._check_custom_rules(command)
        if custom_action is not None:
            return self._adjust_for_path(
                custom_action, custom_reason, working_dir, command
            )

        if builtin_action is not None:
            return self._adjust_for_path(
                builtin_action, builtin_reason, working_dir, command
            )

        default = self._load_default_risk()
        action = CommandAction(default)
        return self._adjust_for_path(action, "No matching rule, using default", working_dir, command)

    def _check_custom_rules(self, command: str) -> Tuple[Optional[CommandAction], str]:
        rules = self._load_permission_rules()
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            pattern = rule.get("pattern", "")
            action_str = rule.get("action", "ask")
            if not isinstance(pattern, str) or not pattern:
                continue
            try:
                if re.search(pattern, command, re.IGNORECASE):
                    try:
                        action = CommandAction(action_str)
                    except ValueError:
                        action = CommandAction.ASK
                    return action, f"Matched custom rule: {pattern}"
            except re.error:
                if pattern.lower() in command.lower():
                    try:
                        action = CommandAction(action_str)
                    except ValueError:
                        action = CommandAction.ASK
                    return action, f"Matched custom rule (literal): {pattern}"
        return None, ""

    def _check_builtin_risk(self, command: str) -> Tuple[Optional[CommandAction], str]:
        lines = command.split("\n")
        
        highest_risk = None
        highest_action = None
        matched_line = None
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            for pattern, risk_level, action in BUILTIN_RISK_RULES:
                try:
                    if re.search(pattern, line):
                        if highest_risk is None or risk_level.value_priority > highest_risk.value_priority:
                            highest_risk = risk_level
                            highest_action = action
                            matched_line = line
                except re.error:
                    continue
        
        if highest_action == CommandAction.ALLOW and not self._is_safe_simple_command(command):
            return CommandAction.ASK, "Command uses shell syntax or options that are not safe to auto-run"

        if highest_action is not None:
            return highest_action, f"Built-in {highest_risk.value} risk in line: {matched_line[:50]}"
        return None, ""

    def _is_safe_simple_command(self, command: str) -> bool:
        """Return whether a built-in SAFE match is safe to auto-run.

        Regex matches are useful for classification, but cannot establish that
        an entire shell program is read-only.  Auto-run therefore accepts only
        one simple command and a small set of command-specific read-only forms.
        Everything ambiguous falls back to user confirmation.
        """
        if not command.strip() or "\n" in command or "\r" in command:
            return False
        if "$" in command or "`" in command:
            return False

        try:
            lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
            lexer.whitespace_split = True
            lexer.commenters = ""
            tokens = list(lexer)
        except ValueError:
            return False

        if not tokens:
            return False
        if any(token and all(char in SHELL_CONTROL_CHARACTERS for char in token) for token in tokens):
            return False

        executable = os.path.basename(tokens[0])
        if tokens[0] != executable:
            return False
        if executable not in READ_ONLY_COMMANDS:
            return False

        args = tokens[1:]
        if any(any(character in arg for character in "*?[") for arg in args):
            return False
        if executable == "find":
            unsafe_find_actions = {
                "-delete", "-exec", "-execdir", "-fls", "-fprint", "-fprint0",
                "-fprintf", "-ok", "-okdir",
            }
            if any(arg in unsafe_find_actions for arg in args):
                return False
        elif executable == "date":
            if any(arg.startswith(("-s", "--set")) for arg in args):
                return False
        elif executable == "sort":
            if any(
                arg.startswith("-o")
                or arg.startswith("--output")
                or arg.startswith("--compress-program")
                for arg in args
            ):
                return False
        elif executable == "tail":
            if any(arg == "-F" or arg == "-f" or arg.startswith("--follow") for arg in args):
                return False
        elif executable == "tree":
            if any(arg.startswith(("-o", "--output")) for arg in args):
                return False
        elif executable == "uniq":
            positional_args = [arg for arg in args if not arg.startswith("-")]
            if len(positional_args) > 1:
                return False
        elif executable == "git":
            return self._is_read_only_git_command(args)

        return True

    @staticmethod
    def _is_read_only_git_command(args: list[str]) -> bool:
        """Accept only unambiguously read-only git subcommands."""
        if not args or args[0].startswith("-"):
            return False

        subcommand = args[0]
        subcommand_args = args[1:]
        if subcommand in {"diff", "log", "show", "status"}:
            if any(
                arg.startswith("--output") or arg in {"--ext-diff", "--textconv"}
                for arg in subcommand_args
            ):
                return False
            return True
        if subcommand == "branch":
            return not subcommand_args or all(
                arg.startswith(("--list", "--contains", "--no-contains", "--merged", "--no-merged", "--show-current"))
                for arg in subcommand_args
            )
        if subcommand == "tag":
            return not subcommand_args or all(arg.startswith(("-l", "--list")) for arg in subcommand_args)
        if subcommand == "remote":
            return not subcommand_args or subcommand_args[0] in {"-v", "show", "get-url"}
        return False

    def _extract_command_paths(self, command: str, working_dir: str = None) -> list:
        """Extract file system paths referenced in a command string."""
        paths = []
        base_dir = os.path.abspath(os.path.expanduser(working_dir)) if working_dir else os.getcwd()

        path_patterns = [
            r'(?:^|\s|=|["\'])(/[^\s;"\'|&><)]*)',
            r'(?:^|\s|=|["\'])(~[^\s;"\'|&><)]*)',
            r'(?:^|\s|=|["\'])(\.{1,2}/[^\s;"\'|&><)]*)',
        ]

        seen = set()
        for pattern in path_patterns:
            for match in re.finditer(pattern, command):
                raw_path = match.group(1)
                if not raw_path or raw_path in seen:
                    continue
                seen.add(raw_path)
                abs_path = os.path.normpath(
                    os.path.join(base_dir, os.path.expanduser(raw_path))
                )
                paths.append(abs_path)

        # Resolve existing relative arguments as well. This catches ordinary
        # forms such as ``cat link`` and lets realpath-based rules detect when a
        # symlink escapes the trusted working directory.
        try:
            tokens = shlex.split(command, comments=False, posix=True)
        except ValueError:
            tokens = []
        for token in tokens[1:]:
            candidate = token.split("=", 1)[-1] if "=" in token else token
            if not candidate or candidate.startswith("-") or candidate in seen:
                continue
            candidate_path = os.path.join(base_dir, os.path.expanduser(candidate))
            if os.path.exists(candidate_path):
                seen.add(candidate)
                paths.append(os.path.normpath(candidate_path))

        return paths

    def _get_effective_security_level(self, command: str, working_dir: str = None) -> PathSecurityLevel:
        """Get the most restrictive security level among all paths involved in the command."""
        levels = []

        if working_dir:
            levels.append(self._get_path_security_level(working_dir))

        command_paths = self._extract_command_paths(command, working_dir)
        for path in command_paths:
            levels.append(self._get_path_security_level(path))

        if not levels:
            return PathSecurityLevel.SANDBOXED

        return max(levels, key=lambda level: level.restrictiveness)

    def _adjust_for_path(self, action: CommandAction, reason: str, working_dir: str = None, command: str = None) -> Tuple[CommandAction, str]:
        if not working_dir:
            return action, reason

        effective_level = self._get_effective_security_level(command or "", working_dir)

        # Explicit blocks are never weakened by a path rule, including YOLO.
        if action == CommandAction.BLOCK:
            return action, reason

        if effective_level == PathSecurityLevel.YOLO:
            return CommandAction.ALLOW, f"{reason} (yolo mode)"

        if effective_level in {PathSecurityLevel.SANDBOXED, PathSecurityLevel.RESTRICTED}:
            if action == CommandAction.ALLOW:
                return CommandAction.ASK, f"{reason} ({effective_level.value} path)"

        return action, reason

    def _get_path_security_level(self, path: str) -> PathSecurityLevel:
        rules = self._load_path_rules()

        try:
            main_path = self.settings.get_string("path")
            if main_path:
                main_path = os.path.realpath(os.path.abspath(os.path.expanduser(main_path)))
        except Exception:
            main_path = None

        abs_path = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
        best_match = None
        best_match_len = 0

        for rule in rules:
            if not isinstance(rule, dict):
                continue
            rule_path = rule.get("path", "")
            if not isinstance(rule_path, str) or not rule_path:
                continue

            if rule_path == "{{main_path}}":
                if main_path:
                    rule_path = main_path
                else:
                    continue

            abs_rule_path = os.path.realpath(os.path.abspath(os.path.expanduser(rule_path)))

            try:
                contains_path = os.path.commonpath([abs_path, abs_rule_path]) == abs_rule_path
            except ValueError:
                contains_path = False

            if contains_path and len(abs_rule_path) > best_match_len:
                best_match = rule
                best_match_len = len(abs_rule_path)

        if best_match:
            level_str = best_match.get("level", "sandboxed")
            try:
                return PathSecurityLevel(level_str)
            except ValueError:
                return PathSecurityLevel.SANDBOXED

        return PathSecurityLevel.SANDBOXED

    def get_risk_level_for_command(self, command: str) -> RiskLevel:
        lines = command.split("\n")
        highest_risk = None
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            for pattern, risk_level, action in BUILTIN_RISK_RULES:
                try:
                    if re.search(pattern, line):
                        if highest_risk is None or risk_level.value_priority > highest_risk.value_priority:
                            highest_risk = risk_level
                except re.error:
                    continue
        
        return highest_risk if highest_risk else RiskLevel.MODERATE
