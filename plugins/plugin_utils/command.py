"""Definitions for the command runner."""

from __future__ import absolute_import, division, print_function


# pylint: disable=invalid-name
__metaclass__ = type
# pylint: enable=invalid-name


from dataclasses import dataclass, field
from typing import Dict, List, Union

from .compatibility import shlex_join


@dataclass(frozen=False)
class Command:
    """Data structure for details of a command to be run.

    A ``Command`` is updated after instantiated with details from either
    ``stdout`` or ``stderr``.
    """

    # pylint: disable=too-many-instance-attributes

    command_parts: List[str]
    fail_msg: str
    no_log: Dict[str, str] = field(default_factory=dict)
    return_code: int = -1
    stdout: str = ""
    stderr: str = ""
    stdout_lines: List[str] = field(default_factory=list)
    stderr_lines: List[str] = field(default_factory=list)

    @property
    def command(self) -> str:
        """Return the command as a string.

        :return: The command as a string.
        """
        return shlex_join(self.command_parts)

    @property
    def cleaned(self) -> Dict[str, Union[int, List[str], str]]:
        """Return the sanitized details of the command for the log.

        :return: The sanitized details of the command for the log.
        """
        command_parts = self.command_parts
        stdout_lines = self.stdout_lines
        stderr_lines = self.stderr_lines

        if self.no_log:
            for find, replace in self.no_log.items():
                for data in (command_parts, stdout_lines, stderr_lines):
                    for idx, part in enumerate(data):
                        data[idx] = part.replace(find, replace)

        return {
            "command": shlex_join(command_parts),
            "stdout_lines": stdout_lines,
            "stderr_lines": stderr_lines,
            "return_code": self.return_code,
        }
