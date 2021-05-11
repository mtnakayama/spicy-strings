#!/usr/bin/env python
from __future__ import annotations

import argparse
from collections import deque
import json
import logging
import os
import signal
import sys
from typing import Any

from pygtrie import CharTrie

import Xlib
import Xlib.X
import Xlib.XK
import Xlib.display
import Xlib.ext.record
import Xlib.protocol

from .actions import Action

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
        hotstrings_json = json.load(file)

    if not hotstrings_json:
        argument_parser.exit(EXIT_FAILURE, 'No hotstrings defined.\n')

    hotstring_mapping = hotstring_lookup_from_json(hotstrings_json)

    record_context = record_connection.record_create_context(
        *RECORD_CONTEXT_ARGUMENTS)

    # Only keep at maximum the amount of characters of the longest hotstring
    # in the HotstringProcessor queue
    hotstring_processor = HotstringProcessor(
        hotstring_mapping,
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

    def __init__(self, hotstring_lookup: CharTrie[str, Action], connection):
        self.hotstring_mapping = hotstring_lookup
        self.connection = connection

        maxlen = max(len(k) for k in hotstring_lookup.keys())
        self.char_stack: deque[str] = deque(maxlen=maxlen)

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

    def make_key_press_event(self, detail, state, window, **kwargs):
        arguments = self._default_key_press_event_arguments.copy()
        arguments.update(kwargs)
        return Xlib.protocol.event.KeyPress(detail=detail, state=state, window=window, **arguments)

    def make_key_release_event(self, detail, state, window, **kwargs):
        arguments = self._default_key_release_event_arguments.copy()
        arguments.update(kwargs)
        return Xlib.protocol.event.KeyRelease(detail=detail, state=state, window=window, **arguments)

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

    def type_keycode(self, keycode, window):
        detail, state = keycode
        window.send_event(self.make_key_press_event(detail, state, window))
        window.send_event(self.make_key_release_event(detail, state, window))

    def type_keycodes(self, keycodes, window):
        for keycode in keycodes:
            self.type_keycode(keycode, window)

        self.connection.flush()

    def __call__(self, character):
        self.update_char_stack(character)

        window = self.connection.get_input_focus().focus

        hotstring, action = self.hotstring_mapping.longest_prefix(
            ''.join(self.char_stack))

        if hotstring:
            replacement = action.replacement()

            self.type_backspaces(len(hotstring), window)

            # Linefeeds don't seem to be sent by Xlib, so replace them with
            # carriage returns: normalize \r\n to \r
            # first, then replace all remaining \n with \r
            replacement = replacement.replace('\r\n', '\r').replace('\n', '\r')
            self.type_keycodes(self.string_to_keycodes(replacement), window)

            self.char_stack.clear()

    def update_char_stack(self, character):
        """Append or delete characters from buffer"""
        if character == self.BACKSPACE_CHARACTER and self.char_stack:
            self.char_stack.popleft()
        else:
            self.char_stack.appendleft(character)

    def type_backspaces(self, num_times, window):
        backspace = tuple(
            self.string_to_keycodes(self.BACKSPACE_CHARACTER)
        )
        self.type_keycodes(backspace * num_times, window)


def hotstring_lookup_from_json(hotstrings: Any) -> CharTrie[str, Action]:
    """Returns a CharTrie mapping a reversed hotstring to an Action."""
    return CharTrie((reversed(hotstring), Action.from_list(action_code))
                    for hotstring, action_code in hotstrings.items())


if __name__ == '__main__':
    main()
