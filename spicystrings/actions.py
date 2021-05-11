from __future__ import annotations

import subprocess


class Action:
    def replacement(self) -> str:
        raise NotImplementedError

    @staticmethod
    def from_list(action_list: list[str]) -> Action:
        action, *arguments = action_list

        if action == 'replace':
            replacement_str, = arguments
            return Replace(replacement_str)
        elif action == 'run-replace':
            return RunReplace(arguments)
        elif action == 'run-replace-raw':
            return RunReplaceRaw(arguments)
        elif action == 'run':
            return Run(arguments)
        else:
            raise ValueError(f'Unrecognized action: "{action}".')


class Replace(Action):
    """Remove typed hotstring before typing replacement"""
    def __init__(self, replacement_str: str):
        self.replacement_str = replacement_str

    def replacement(self) -> str:
        return self.replacement_str


class RunReplace(Action):
    """The same as Run, but replaces the hotstring with the stdout of the
    executed process
    """
    def __init__(self, command: list[str]):
        self.command = command

    def replacement(self) -> str:
        with subprocess.Popen(self.command, stdout=subprocess.PIPE,
                              universal_newlines=True) as process:
            return process.stdout.read().strip()


class RunReplaceRaw(RunReplace):
    """The same as "run-replace" but doesn't strip whitespace at the ends"""

    def replacement(self) -> str:
        with subprocess.Popen(self.command, stdout=subprocess.PIPE,
                              universal_newlines=True) as process:
            return process.stdout.read()


class Run(RunReplace):
    """Perform no replacement. Simply runs a command."""

    def replacement(self) -> str:
        subprocess.Popen(self.command)
        return ''
