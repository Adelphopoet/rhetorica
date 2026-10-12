"""Narrow command policy for repository inspection and offline validation."""

from pathlib import PurePosixPath

from rhetorica_backend.domain.coding.errors import CodingToolAccessDenied


class CodingCommandPolicy:
    def validate(self, argv: list[str]) -> None:
        if not argv or argv[0] not in {"git", "rg", "ls", "cat", "python", "python3", "pytest"}:
            raise CodingToolAccessDenied("coding_command_not_allowed")
        if any(
            "\x00" in arg or PurePosixPath(arg).is_absolute() or ".." in PurePosixPath(arg).parts
            for arg in argv
        ):
            raise CodingToolAccessDenied("coding_command_path_not_allowed")
        if argv[0] == "git" and (
            len(argv) < 2
            or argv[1] not in {"status", "diff", "ls-files", "show", "log"}
            or any(
                arg.startswith(("--output", "--ext-diff", "--textconv", "--config")) for arg in argv
            )
        ):
            raise CodingToolAccessDenied("coding_git_command_not_allowed")
        if argv[0] == "rg" and any(arg.startswith(("--pre", "--hostname-bin")) for arg in argv):
            raise CodingToolAccessDenied("coding_command_not_allowed")
        if argv[0] in {"python", "python3"} and (
            len(argv) < 3
            or argv[1:3] not in [["-m", "pytest"], ["-m", "unittest"], ["-m", "compileall"]]
        ):
            raise CodingToolAccessDenied("coding_python_command_not_allowed")

    @staticmethod
    def is_validation(argv: list[str]) -> bool:
        return argv[0] == "pytest" or argv[:2] in [["python", "-m"], ["python3", "-m"]]
