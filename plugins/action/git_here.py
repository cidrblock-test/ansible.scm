# -*- coding: utf-8 -*-
# Copyright 2022 Red Hat
# GNU General Public License v3.0+
# (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
"""The git_here action plugin."""

from __future__ import absolute_import, division, print_function

import datetime
import tempfile

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from ansible.errors import AnsibleActionFail
from ansible.parsing.dataloader import DataLoader
from ansible.playbook.play_context import PlayContext
from ansible.playbook.task import Task
from ansible.plugins import loader as Loader
from ansible.plugins.connection.local import Connection
from ansible.template import Templar

# pylint: disable=import-error
from ansible_collections.ansible.utils.plugins.module_utils.common.argspec_validate import (
    AnsibleArgSpecValidator,
)

from ..modules.git_here import DOCUMENTATION
from ..plugin_utils.command import Command
from ..plugin_utils.git_base import ActionInit, GitBase, ResultBase


# pylint: disable=invalid-name
__metaclass__ = type
# pylint: enable=invalid-name

JSONTypes = Union[bool, int, str, Dict, List]


@dataclass(frozen=False)
class Result(ResultBase):
    """Data structure for the task result."""

    branch_name: str = ""
    branches: List[str] = field(default_factory=list)
    name: str = ""
    path: str = ""


class ActionModule(GitBase):
    """The retrieve action plugin."""

    # pylint: disable=too-many-arguments
    # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        connection: Connection,
        loader: DataLoader,
        play_context: PlayContext,
        shared_loader_obj: Loader,
        task: Task,
        templar: Templar,
    ) -> None:
        """Initialize the action plugin.

        :param connection: The connection
        :param loader: The data loader
        :param play_context: The play context
        :param shared_loader_obj: The shared loader object
        :param task: The task
        :param templar: The templar
        """
        super().__init__(
            ActionInit(
                connection=connection,
                loader=loader,
                play_context=play_context,
                shared_loader_obj=shared_loader_obj,
                templar=templar,
                task=task,
            ),
        )

        self._base_command: Tuple[str, ...]
        self._branches: List[str]
        self._branch_name: str
        self._parent_directory: str
        self._repo_path: str
        self._play_name: str = ""
        self._supports_async = True
        self._result: Result = Result()

    def _check_argspec(self) -> None:
        """Check the argspec for the action plugin.

        :raises AnsibleActionFail: If the argspec is invalid
        """
        aav = AnsibleArgSpecValidator(
            data=self._task.args,
            schema=DOCUMENTATION,
            name=self._task.action,
        )
        valid, errors, self._task.args = aav.validate()
        if not valid:
            raise AnsibleActionFail(errors)
        if self._task.args["origin"].get("token") == "":
            raise AnsibleActionFail("Origin token can not be an empty string")
        if self._task.args["upstream"].get("token") == "":
            raise AnsibleActionFail("Upstream token can not be an empty string")

    @property
    def _branch_exists(self) -> bool:
        """Return True if the branch exists.

        :returns: True if the branch exists
        """
        return self._branch_name in self._branches

    def _clone(self) -> None:
        """Clone the repository.

        Additionally set the base command to the repository path.
        """
        command_parts = list(self._base_command)
        no_log = {}
        origin = self._task.args["origin"]["url"]
        token = self._task.args["origin"].get("token")
        if token is not None and "https" in origin:
            token_base64, cli_parameters = self._git_auth_header(token=token)
            command_parts.extend(cli_parameters)
            no_log[token_base64] = "<TOKEN>"

        command_parts.extend(["clone", "--depth=1", "--progress", "--no-single-branch", origin])

        command = Command(
            command_parts=command_parts,
            fail_msg=f"Failed to clone repository: {origin}",
            no_log=no_log,
        )
        self._run_command(command=command)

        if self._result.failed:
            return

        repo_name = command.stderr.splitlines()[0].split("'")[1]
        self._result.name = repo_name
        self._repo_path = str(Path(self._parent_directory) / repo_name)
        self._result.path = self._repo_path
        self._base_command = ("git", "-C", self._repo_path)
        return

    def _get_branches(self) -> None:
        """Get the branches."""
        command_parts = list(self._base_command)
        command_parts.extend(["branch", "-a"])
        origin = self._task.args["origin"]["url"]
        command = Command(
            command_parts=command_parts,
            fail_msg=f"Failed to list branches: {origin}",
        )
        self._run_command(command=command)

        if self._result.failed:
            return

        self._branches = []
        for line in command.stdout_lines:
            if line.startswith("*"):
                self._branches.append(line.split()[1])
            else:
                self._branches.append(line.split("/")[-1])
        self._result.branches = self._branches

        timestamp = (
            datetime.datetime.now(tz=datetime.timezone.utc)
            .astimezone()
            .isoformat()
            .replace(":", "")
        )

        branch_name = self._task.args["branch"]["name"]

        self._branch_name = branch_name.format(
            play_name=self._play_name,
            timestamp=timestamp,
        )
        self._result.branch_name = self._branch_name
        return

    def _detect_duplicate_branch(self) -> None:
        """Detect duplicate branch."""
        duplicate_detection = self._task.args["branch"]["duplicate_detection"]
        if duplicate_detection and self._branch_exists:
            self._result.failed = True
            self._result.msg = f"Branch '{self._branch_name}' already exists"

    def _switch_checkout(self) -> None:
        """Switch to or checkout the branch."""
        command_parts = list(self._base_command)
        branch = self._branch_name

        if self._branch_exists:
            command_parts.extend(["switch", branch])
        else:
            command_parts.extend(["checkout", "-t", "-b", branch])

        command = Command(
            command_parts=command_parts,
            fail_msg=f"Failed to change branches to: {branch}",
        )
        self._run_command(command=command)

    def _add_upstream_remote(self) -> None:
        """Add the upstream remote."""
        if not self._task.args["upstream"].get("url"):
            return

        command_parts = list(self._base_command)
        upstream = self._task.args["upstream"]["url"]
        command_parts.extend(["remote", "add", "upstream", upstream])
        command = Command(
            command_parts=command_parts,
            fail_msg=f"Failed to add upstream: {upstream}",
        )
        self._run_command(command=command)
        return

    def _pull_upstream(self) -> None:
        """Pull from upstream."""
        if not self._task.args["upstream"].get("url"):
            return

        command_parts = list(self._base_command)
        no_log = {}
        upstream = self._task.args["upstream"]["url"]
        token = self._task.args["upstream"].get("token")
        if token is not None and "https" in upstream:
            token_base64, cli_parameters = self._git_auth_header(token=token)
            command_parts.extend(cli_parameters)
            no_log[token_base64] = "<TOKEN>"

        branch = self._task.args["upstream"]["branch"]

        command_parts.extend(["pull", "upstream", branch, "--rebase"])

        command = Command(
            command_parts=command_parts,
            fail_msg=f"Failed to pull upstream branch: {branch}",
            no_log=no_log,
        )
        self._run_command(command=command)
        return

    def run(
        self,
        tmp: None = None,
        task_vars: Optional[Dict[str, JSONTypes]] = None,
    ) -> Dict[str, JSONTypes]:
        """Run the action plugin.

        :param tmp: The temporary directory
        :param task_vars: The task variables
        :returns: The result
        """
        if isinstance(task_vars, dict):
            self._play_name = str(task_vars["ansible_play_name"])
        self._task.diff = False
        super().run(task_vars=task_vars)

        self._check_argspec()
        if self._result.failed:
            return asdict(self._result)

        self._parent_directory = self._task.args["parent_directory"].format(
            temporary_directory=tempfile.mkdtemp(),
        )
        self._base_command = ("git", "-C", self._parent_directory)

        steps = (
            self._clone,
            self._get_branches,
            self._detect_duplicate_branch,
            self._switch_checkout,
            self._add_upstream_remote,
            self._pull_upstream,
        )

        for step in steps:
            step()
            if self._result.failed:
                return asdict(self._result)

        self._result.msg = f"Successfully retrieved repository: {self._task.args['origin']['url']}"
        return asdict(self._result)
