from __future__ import annotations

from dataclasses import dataclass
from enum import auto, Enum
import subprocess
from collections.abc import Callable, Set


DEFAULT_END_CHARS = frozenset('-()[]{}:;\'"/\\,.?!\n \t')


@dataclass(frozen=True)
class GlobalHotstringOptions:
    end_chars: Set[str] = DEFAULT_END_CHARS
    flags: Set[HotstringFlags] = frozenset()


@dataclass(frozen=True)
class HotstringDefinition:
    hotstring: str
    action: Action
    flags: Set[HotstringFlags] = frozenset()


class HotstringFlags(Enum):
    NO_END_CHAR = auto()
    NO_BACKSPACE = auto()
    CASE_SENSITIVE = auto()
    IGNORE_CASE = auto()  # do not conform to typed case
    OMIT_END_CHAR = auto()


Action = Callable[[], str]


def Replace(replacement_str: str) -> Action:
    return lambda: replacement_str


def RunReplaceRaw(command: list[str]) -> Action:
    def func():
        with subprocess.Popen(command, stdout=subprocess.PIPE,
                              universal_newlines=True) as process:
            return process.stdout.read()

    return func


def RunReplace(command: list[str]) -> Action:
    func = RunReplaceRaw(command)
    return lambda: func().strip()


def Run(command: list[str]) -> Action:
    func = RunReplaceRaw(command)
    return lambda: func() and ''
