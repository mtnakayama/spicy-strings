from collections.abc import Collection
from enum import auto, Enum
import re

from pygtrie import CharTrie

from .actions import (DEFAULT_END_CHARS, GlobalHotstringOptions,
                      HotstringDefinition, Replace)


def read_mapping(fp) -> tuple[Collection[HotstringDefinition],
                              GlobalHotstringOptions]:
    end_chars = DEFAULT_END_CHARS

    hotstrings: list[HotstringDefinition] = []
    for line in (x.strip() for x in fp.readlines()):
        if not line:
            pass
        elif line.startswith(':'):
            hotstrings.append(parse_hotstring_line(line))
        elif line.startswith('#'):
            end_chars = frozenset(parse_directive(line))

    return hotstrings, GlobalHotstringOptions(end_chars=end_chars)


def parse_directive(line: str) -> str:
    if match_obj := re.match(r'#Hotstring\s+EndChars\s+', line):
        skip = len(match_obj[0])
        endchars = line[skip:]
    else:
        raise ValueError

    return unescape_ahk_string(endchars)


class ParseHotstringState(Enum):
    START = auto()
    OPTIONS = auto()
    HOTSTRING = auto()
    HOTSTRING_FIRST_COLON = auto()
    REPLACEMENT = auto()


def parse_hotstring_line(line: str) -> HotstringDefinition:
    parse_state = ParseHotstringState.START

    for i, char in enumerate(line):
        if parse_state == ParseHotstringState.START:
            if char != ':':
                raise ValueError
            substring_start = i + 1
            parse_state = ParseHotstringState.OPTIONS
        elif parse_state == ParseHotstringState.OPTIONS:
            if char == ':':
                options = line[substring_start:i]
                substring_start = i + 1
                parse_state = ParseHotstringState.HOTSTRING
        elif parse_state == ParseHotstringState.HOTSTRING:
            if char == ':':
                parse_state = ParseHotstringState.HOTSTRING_FIRST_COLON
        elif parse_state == ParseHotstringState.HOTSTRING_FIRST_COLON:
            if char == ':':
                hotstring = line[substring_start:i-1]
                substring_start = i + 1
                parse_state = ParseHotstringState.REPLACEMENT
            else:
                parse_state = ParseHotstringState.HOTSTRING
        else:
            break  # rest of line is replacement text

    if parse_state != ParseHotstringState.REPLACEMENT:
        raise ValueError

    replacement = line[substring_start:]

    return HotstringDefinition(hotstring, Replace(replacement))


AHK_ESCAPE_SEQUENCES = CharTrie({
    '`,': ',',
    '`%': '%',
    '``': '`',
    '`;': ';',
    '`::': '::',
    '`n': '\n',
    '`r': '\r',
    '`b': '\b',
    '`t': '\t',
    '`v': '\v',
    '`a': '\a',
    '`f': '\f'
})


def unescape_ahk_string(escaped_string: str) -> str:
    """https://www.autohotkey.com/docs/misc/EscapeChar.htm"""

    unescaped: list[str] = []

    i = 0
    while i < len(escaped_string):
        escaped, actual = AHK_ESCAPE_SEQUENCES.longest_prefix(
            escaped_string[i:])
        if escaped:
            unescaped.append(actual)
            i += len(escaped)
        else:
            unescaped.append(escaped_string[i])
            i += 1

    return ''.join(unescaped)
