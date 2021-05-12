#!/usr/bin/env python
from __future__ import annotations

import argparse
from collections import deque
from collections.abc import Iterable
import logging
import os
import re
import signal
import sys
from typing import Optional, Tuple

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
        connection
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
    BACKSPACE_CHARACTER = '\x08'

    def __init__(self, hotstring_detector: HotstringDetector, connection):
        self.hotstring_detector = hotstring_detector
        self.connection = connection

        self.root_window = self.connection.screen().root

        # These stay the same for all requests, so just keep a local copy
        self._default_key_press_event_arguments = dict(
            time=Xlib.X.CurrentTime,
            root=self.root_window,
            child=Xlib.X.NONE,
            root_x=0, root_y=0, event_x=0, event_y=0,
            same_screen=1
        )
        self._default_key_release_event_arguments = self._default_key_press_event_arguments  # noqa: E501

    def __call__(self, char):
        matched = self.hotstring_detector.next_typed_char(char)
        if matched:
            window = self.connection.get_input_focus().focus
            trigger_str, action = matched

            self.type_string('\b' * len(trigger_str), window)

            replacement = action()
            # Linefeeds don't seem to be sent by Xlib, so replace them with
            # carriage returns
            replacement = replace_newlines_with_cr(replacement)

            self.type_string(replacement, window)



    def make_key_press_event(self, detail, state, window, **kwargs):
        arguments = self._default_key_press_event_arguments.copy()
        arguments.update(kwargs)
        return Xlib.protocol.event.KeyPress(detail=detail, state=state, window=window, **arguments)

    def make_key_release_event(self, detail, state, window, **kwargs):
        arguments = self._default_key_release_event_arguments.copy()
        arguments.update(kwargs)
        return Xlib.protocol.event.KeyRelease(detail=detail, state=state, window=window, **arguments)

    def type_backspaces(self, num: int, window):
        self.type_string('\b' * num, window)

    def type_string(self, string: str, window):
        self.type_keycodes(self.string_to_keycodes(string), window)

    def type_keycodes(self, keycodes, window):
        for keycode in keycodes:
            self.type_keycode(keycode, window)

        self.connection.flush()

    def type_keycode(self, keycode, window):
        detail, state = keycode
        window.send_event(self.make_key_press_event(detail, state, window))
        window.send_event(self.make_key_release_event(detail, state, window))

    # TODO: Figure out a way to find keycodes not assigned in the current keyboard mapping
    def string_to_keycodes(self, string_):
        for character in string_:
            code_point = ord(character)

            # TODO: Take a look at other projects using python-xlib to improve this
            # See Xlib.XK.keysym_to_string
            keycodes = tuple(self.connection.keysym_to_keycodes(code_point) or
                             self.connection.keysym_to_keycodes(0xFF00 | code_point))
            keycode = keycodes[0] if keycodes else None

            # TODO: Remap missing characters to available keycodes
            if not keycode:
                logging.info('No keycode found for: %r.' % character, file=sys.stderr)
                continue

            yield keycode


MATCH_NEWLINE = re.compile(r'\r?\n')


def replace_newlines_with_cr(string: str):
    """Replace linefeeds with carriage returns"""
    return MATCH_NEWLINE.sub('\r', string)


class HotstringDetector:
    """Contains the state of recently typed characters and determines when an
    Action should be triggered."""
    def __init__(self, hotstring_definitions: Iterable[HotstringDefinition],
                 global_hotstring_options: GlobalHotstringOptions):

        self.global_hotstring_options = global_hotstring_options

        # a mapping of reversed hotstring to HotstringDefinition
        self._end_char_hotstrings: CharTrie[HotstringDefinition] = CharTrie()
        # mapping for the hotstings that trigger without an end char
        self._no_end_char_hotstrings: CharTrie[HotstringDefinition] = CharTrie()  # noqa

        maxlen = self._calc_maxlen(hotstring_definitions)
        self._char_stack: deque[str] = deque(maxlen=maxlen)

        for hotstring_def in hotstring_definitions:
            self._add_hotstring(hotstring_def)

    def _calc_maxlen(self, hotstring_definitions):
        return max(len(x.hotstring) for x in hotstring_definitions)

    def _add_hotstring(self, hotstring_definition: HotstringDefinition):
        match_str = reversed(hotstring_definition.hotstring)

        if HotstringFlags.NO_END_CHAR in hotstring_definition.flags:
            self._no_end_char_hotstrings[match_str] = hotstring_definition
        else:
            print('end char')
            self._end_char_hotstrings[match_str] = hotstring_definition

    def next_typed_char(self, char: str) -> \
            Optional[Tuple[str, Action]]:
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
            hotstring_match = self._match_hotstring(
                self._end_char_hotstrings, char)
            if hotstring_match:
                self.reset_char_state()
                return hotstring_match

        self._update_char_state(char)

        hotstring_match = self._match_hotstring(
            self._no_end_char_hotstrings, '')
        if hotstring_match:
            self.reset_char_state()
            return hotstring_match

        return None

    def _match_hotstring(self,
                         hotstring_mapping: CharTrie[HotstringDefinition],
                         end_char: str) -> Optional[Tuple[str, Action]]:
        """Check if the recently typed characters match a hotstring"""

        recently_typed = ''.join(self._char_stack)
        step = hotstring_mapping.longest_prefix(recently_typed)
        if step:
            _, hotstring_definition = step
            trigger_str = hotstring_definition.hotstring + end_char

            return (trigger_str,
                    lambda: hotstring_definition.action() + end_char)
        return None

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
