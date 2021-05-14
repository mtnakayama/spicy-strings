#!/usr/bin/env python
from __future__ import annotations

import argparse
from collections import deque
from collections.abc import Iterable
from itertools import takewhile
import logging
import os
import re
import signal
from typing import Callable, Optional

from toolz import functoolz

from pygtrie import CharTrie

import Xlib
import Xlib.X
import Xlib.XK
import Xlib.display
import Xlib.ext.record
import Xlib.protocol

from . import ahk_parser
from .actions import (Action, GlobalHotstringOptions, HotstringDefinition,
                      HotstringFlags)
from .keyboard import BaseTyper, Typer

EXIT_FAILURE = 1
RECORD_CONTEXT_ARGUMENTS = (
    0,
    (Xlib.ext.record.AllClients,),
    ({
         'core_requests': (0, 0),
         'core_replies': (0, 0),
         'ext_requests': (0, 0, 0, 0),
         'ext_replies': (0, 0, 0, 0),
         'delivered_events': (0, 0),
         'device_events': (Xlib.X.KeyPress, Xlib.X.KeyRelease),
         'errors': (0, 0),
         'client_started': False,
         'client_died': False
     },)
)


# Load xkb to access XK_ISO_Level3_Shift
Xlib.XK.load_keysym_group('xkb')
event_field = Xlib.protocol.rq.EventField(None)


def main():
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument(
        'path', metavar='PATH', nargs='?',
        help='Path to JSON file containing hotstring definitions. '
             'Default: %(default)s',
        default=os.path.join(get_xdg_config_home(), 'hotstrings.json'))
    argument_parser.add_argument('--verbose', '-v', action='store_true')
    arguments = argument_parser.parse_args()

    if arguments.verbose:
        logging.basicConfig(level=logging.INFO)

    path = os.path.expanduser(arguments.path)
    if not os.path.exists(path):
        argument_parser.exit(EXIT_FAILURE,
                             path + ': No such file or directory.\n')

    connection = Xlib.display.Display()
    record_connection = Xlib.display.Display()

    if not record_connection.has_extension('RECORD'):
        argument_parser.exit(EXIT_FAILURE,
                             'X Record Extension Library not found.\n')

    with open(path) as file:
        hotstrings, global_hotstring_options = ahk_parser.read_mapping(file)

    print(hotstrings)
    print(global_hotstring_options)

    record_context = record_connection.record_create_context(
        *RECORD_CONTEXT_ARGUMENTS)

    hotstring_detector = HotstringDetector(hotstrings,
                                           global_hotstring_options)

    # Only keep at maximum the amount of characters of the longest hotstring
    # in the HotstringProcessor queue
    hotstring_processor = HotstringProcessor(
        hotstring_detector,
        Typer(connection)
    )
    record_handler = RecordHandler(connection, record_connection,
                                   hotstring_processor)

    def clean_up(*args):
        record_connection.record_free_context(record_context)
        record_connection.close()
        connection.close()
        argument_parser.exit()

    # Make sure to free structs and close connections
    for signal_ in signal.SIGINT, signal.SIGTERM:
        signal.signal(signal_, clean_up)

    logging.info('Listening for hotstrings...')
    record_connection.record_enable_context(record_context, record_handler)


def parse_event_fields(data, display):
    while data:
        event, data = event_field.parse_binary_value(data, display, None, None)
        yield event


def get_xdg_config_home():
    xdg_config_home = os.getenv('XDG_CONFIG_HOME')
    if xdg_config_home is not None and os.path.isabs(xdg_config_home):
        return xdg_config_home

    return os.path.expanduser('~/.config')


class RecordHandler:
    MODIFIER_KEY_MASKS = {
        'Shift': Xlib.X.ShiftMask,
        'Lock': Xlib.X.LockMask,
        'Control': Xlib.X.ControlMask,
        'Alt': Xlib.X.Mod1Mask,
        'Mod1': Xlib.X.Mod1Mask,
        'Mod2': Xlib.X.Mod2Mask,
        'Mod3': Xlib.X.Mod3Mask,
        'Mod4': Xlib.X.Mod4Mask,
        'Mod5': Xlib.X.Mod5Mask
    }

    def __init__(self, connection, record_connection, callback):
        self.connection = connection
        self.record_connection = record_connection
        self.callback = callback

        # Support for XK_ISO_Level3_Shift/AltGr:
        self.alt_gr_pressed = False
        self.alt_gr_keycodes = set(i[0] for i in self.connection.keysym_to_keycodes(Xlib.XK.XK_ISO_Level3_Shift))

    def get_modifier_state_index(self, state):
        # None = 0, Shift = 1, Alt = 2, Alt + Shift = 3, AltGr = 4, AltGr + Shift = 5
        pressed = {n: (state & m) == m for n, m in self.MODIFIER_KEY_MASKS.items()}
        index = 0
        if pressed['Shift']:
            index += 1
        if pressed['Alt']:
            index += 2
        if self.alt_gr_pressed:
            index += 4

        return index

    def key_pressed(self, event):
        # Manually keep track of AltGr state because it is not encoded in the event.state byte
        if event.detail in self.alt_gr_keycodes:
            self.alt_gr_pressed = True

        keysym = self.connection.keycode_to_keysym(event.detail, self.get_modifier_state_index(event.state))
        character = self.connection.lookup_string(keysym)
        if character:
            self.callback(character)

    def key_released(self, event):
        if event.detail in self.alt_gr_keycodes:
            self.alt_gr_pressed = False

    def __call__(self, reply):
        # Ignore all replies that can't be parsed by parse_event_fields
        if not reply.category == Xlib.ext.record.FromServer:
            return

        for event in parse_event_fields(reply.data, self.record_connection.display):
            if event.type == Xlib.X.KeyPress:
                self.key_pressed(event)
            else:
                self.key_released(event)


class HotstringProcessor:
    def __init__(self, hotstring_detector: HotstringDetector,
                 typer: BaseTyper):
        self.hotstring_detector = hotstring_detector
        self.typer = typer

    def __call__(self, char):
        matched = self.hotstring_detector.next_typed_char(char)
        if matched:
            trigger_str, action = matched

            self.typer.type_string('\b' * len(trigger_str))

            replacement = action()
            # Linefeeds don't seem to be sent by Xlib, so replace them with
            # carriage returns
            replacement = replace_newlines_with_cr(replacement)

            self.typer.type_string(replacement)


MATCH_NEWLINE = re.compile(r'\r?\n')


def replace_newlines_with_cr(string: str):
    """Replace linefeeds with carriage returns"""
    return MATCH_NEWLINE.sub('\r', string)


class HotstringDetector:
    MIN_BUFFER_SIZE = 128

    def __init__(self, hotstring_definitions: Iterable[HotstringDefinition],
                 global_hotstring_options: GlobalHotstringOptions):
        """Contains the state of recently typed characters and determines when an
        Action should be triggered.

        Args:
            hotstring_definitions: An Iterable containing the hotstring
                definitions to detect. The definitions at the beginning have a
                higher precedence than the definitions at the end.
        """

        self.global_hotstring_options = global_hotstring_options

        self._hotstring_map: CharTrie[
            tuple[int, HotstringDefinition]] = CharTrie()

        maxlen = self._calc_maxlen(hotstring_definitions)
        self._char_stack: deque[str] = deque(maxlen=maxlen)

        self._add_hotstrings(hotstring_definitions)

    def _calc_maxlen(self, hotstring_definitions):
        longest = max(
            (len(x.hotstring) for x in hotstring_definitions),
            default=0
        )
        return max(self.MIN_BUFFER_SIZE, longest * 2)

    def _add_hotstrings(self,
                        hotstring_definitions: Iterable[HotstringDefinition]):
        for i, hotstring_def in enumerate(hotstring_definitions):
            match_str = ''.join(reversed(hotstring_def.hotstring))
            if HotstringFlags.CASE_SENSITIVE not in hotstring_def.flags:
                match_str = match_str.lower()
            self._hotstring_map[match_str] = (i, hotstring_def)

    def next_typed_char(self, char: str) -> \
            Optional[tuple[str, Action]]:
        """Adds `char` to the internal buffer of typed character. Returns the
        triggering string and the HotstringDefinition if a hotstring was
        triggered.

        Args:
            char: the character that was just typed

        Returns:
            A tuple containing the string that was replaced (including the end
            char if applicable) and the HotstringDefinition that was triggered.
            None if there was no hotstring triggered.
        """
        self._update_char_state(char)

        typed = self._get_last_typed()
        if not typed:
            return None

        matched_results = self._match_results_end_char(typed)

        try:
            _, trigger, hotstring_definition, end_char = next(
                iter(matched_results))
            self.reset_char_state()
            trigger_forwards = ''.join(reversed(trigger))
            return self._prepare_action(
                trigger_forwards, hotstring_definition, end_char)
        except StopIteration:
            return None

    def _match_results_end_char(self, typed: str) \
            -> Iterable[tuple[int, str, HotstringDefinition, str]]:
        """Get hotstrings activated by the last typed string

        Returns: a tuple containing the hotstring's priority, the replaced
            text, the HotstringDefinition"""

        end_chars = self.global_hotstring_options.end_chars
        most_recent_char = typed[0]
        if most_recent_char in end_chars:
            for matched in self._match_results_case(typed[1:]):
                priority, trigger, hotstring_definition = matched
                yield (priority, most_recent_char + trigger,
                       hotstring_definition, most_recent_char)

        for matched in self._match_results_case(typed):
            hotstring_definition = matched[2]
            if HotstringFlags.NO_END_CHAR in hotstring_definition.flags:
                yield (*matched, '')

    def _match_results_case(self, typed: str) \
            -> Iterable[tuple[int, str, HotstringDefinition]]:

        yield from self._match_results(typed)

        for matched in self._match_results(typed.lower()):
            precedence, trigger_lower, hotstring_definition = matched
            if (HotstringFlags.CASE_SENSITIVE
                    not in hotstring_definition.flags):
                trigger = typed[:len(trigger_lower)]
                yield precedence, trigger, hotstring_definition

    def _match_results(self, to_match: str
                       ) -> Iterable[tuple[int, str, HotstringDefinition]]:
        for k, v in self._hotstring_map.prefixes(to_match):
            priority, hotstring_definition = v
            trigger_len = len(k)
            trigger = ''.join(to_match[:trigger_len])

            if (len(to_match) == trigger_len
                    or HotstringFlags.MATCH_SUFFIX in hotstring_definition.flags):  # noqa
                yield priority, trigger, hotstring_definition

    def _prepare_action(self, trigger_forwards: str,
                        hotstring_definition: HotstringDefinition,
                        end_char: str) -> tuple[str, Action]:

        transformers: list[Callable[[str], str]] = []

        if (HotstringFlags.OMIT_END_CHAR not in hotstring_definition.flags
                and HotstringFlags.NO_BACKSPACE
                not in hotstring_definition.flags):
            transformers.append(lambda x: x + end_char)

        if HotstringFlags.IGNORE_CASE not in hotstring_definition.flags:
            if trigger_forwards.isupper():
                transformers.append(lambda x: x.upper())
            elif trigger_forwards[0].isupper():
                transformers.append(lambda x: x.capitalize())

        action = functoolz.compose_left(
            hotstring_definition.action, *transformers)  # type: ignore

        replace_string = trigger_forwards
        if HotstringFlags.NO_BACKSPACE in hotstring_definition.flags:
            replace_string = ''
        return replace_string, action

    def _get_last_typed(self) -> str:
        """Returns the last "word" typed. This will include one end char (if
        it was the last character typed) and then all characters following
        until the next end char."""
        end_chars = self.global_hotstring_options.end_chars
        it = iter(self._char_stack)

        # yield most_recent character regardless of if it was an end_char
        try:
            most_recent = next(it)
        except StopIteration:
            return ''

        rest = ''.join(takewhile(lambda x: x not in end_chars, it))
        return most_recent + rest

    def _update_char_state(self, char: str):
        """Append or delete characters from buffer"""
        if char == '\b':
            try:
                self._char_stack.popleft()
            except IndexError:
                pass  # deque was empty
        else:
            self._char_stack.appendleft(char)
        logging.info(f'_char_stack: {self._char_stack}')

    def reset_char_state(self):
        """Resets the state of the buffer tha tracks recently typed
        characters."""
        self._char_stack.clear()
        logging.info(f'_char_stack: {self._char_stack}')


if __name__ == '__main__':
    main()
