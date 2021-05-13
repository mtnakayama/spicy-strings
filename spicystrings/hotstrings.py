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
    """Contains the state of recently typed characters and determines when an
    Action should be triggered."""

    MIN_BUFFER_SIZE = 128

    def __init__(self, hotstring_definitions: Iterable[HotstringDefinition],
                 global_hotstring_options: GlobalHotstringOptions):

        self.global_hotstring_options = global_hotstring_options

        # a mapping of reversed hotstring to HotstringDefinition
        self._end_char_hotstrings: dict[str, HotstringDefinition] = {}
        # mapping for the hotstings that trigger without an end char
        self._no_end_char_hotstrings: CharTrie[HotstringDefinition] = CharTrie()  # noqa
        # mapping for hotstrings with the MATCH_SUFFIX flag set
        self._suffix_hotstrings: CharTrie[HotstringDefinition] = CharTrie()

        maxlen = self._calc_maxlen(hotstring_definitions)
        self._char_stack: deque[str] = deque(maxlen=maxlen)

        for hotstring_def in hotstring_definitions:
            self._add_hotstring(hotstring_def)

    def _calc_maxlen(self, hotstring_definitions):
        longest = max(len(x.hotstring) for x in hotstring_definitions)
        return max(self.MIN_BUFFER_SIZE, longest * 2)

    def _add_hotstring(self, hotstring_definition: HotstringDefinition):
        match_str = ''.join(reversed(hotstring_definition.hotstring))

        if HotstringFlags.NO_END_CHAR in hotstring_definition.flags:
            self._no_end_char_hotstrings[match_str] = hotstring_definition
        else:
            print('end char')
            self._end_char_hotstrings[match_str] = hotstring_definition

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
        if char in self.global_hotstring_options.end_chars:
            match_result = self._match_last_word()
            if match_result:
                self.reset_char_state()
                matched_word, hotstring_definition = match_result
                return self._prepare_replacement(matched_word,
                                                 hotstring_definition, char)

        self._update_char_state(char)

        match_result = self._match_end_of_string(self._no_end_char_hotstrings)
        if match_result:
            self.reset_char_state()
            matched_word, hotstring_definition = match_result
            return self._prepare_replacement(matched_word,
                                             hotstring_definition)

        return None

    def _match_last_word(self) -> Optional[tuple[str, HotstringDefinition]]:
        """Check for a hotstring only in the characters typed after an end char
        """
        end_chars = self.global_hotstring_options.end_chars
        last_word = ''.join(takewhile(lambda x: x not in end_chars,
                                      self._char_stack))
        try:
            hotstring_definition = self._end_char_hotstrings[last_word.lower()]
            return ''.join(reversed(last_word)), hotstring_definition
        except KeyError:
            return None

    def _match_end_of_string(self, mapping: CharTrie[HotstringDefinition]
                             ) -> Optional[tuple[str, HotstringDefinition]]:
        """Check if the recently typed characters match a hotstring"""

        recently_typed = ''.join(self._char_stack)
        step = mapping.longest_prefix(recently_typed.lower())
        if step:
            matched, hotstring_definition = step
            matched_typed = ''.join(reversed(recently_typed[:len(matched)]))
            return matched_typed, hotstring_definition
        return None

    def _prepare_replacement(self, matched: str,
                             hotstring_definition: HotstringDefinition,
                             end_char: str = ''
                             ) -> Optional[tuple[str, Action]]:

        processors: list[Callable[[str], str]] = []

        if end_char:
            processors.append(lambda x: x + end_char)

        if matched.isupper():
            processors.append(lambda x: x.upper())
        elif matched[0].isupper():
            processors.append(lambda x: x.capitalize())

        action = functoolz.compose_left(hotstring_definition.action,  # type: ignore # noqa
                                        *processors)

        return (matched + end_char, action)

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
