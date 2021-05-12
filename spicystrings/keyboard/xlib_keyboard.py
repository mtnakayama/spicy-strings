import logging

import Xlib.display
import Xlib.X

from .keyboard import BaseTyper


class XlibTyper(BaseTyper):
    def __init__(self, connection: Xlib.display.Display):
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
        self._default_key_release_event_arguments = self._default_key_press_event_arguments  # noqa

    def _get_window(self):
        return self.connection.get_input_focus().focus

    def make_key_press_event(self, detail, state, window, **kwargs):
        arguments = self._default_key_press_event_arguments.copy()
        arguments.update(kwargs)
        return Xlib.protocol.event.KeyPress(detail=detail, state=state,
                                            window=window, **arguments)

    def make_key_release_event(self, detail, state, window, **kwargs):
        arguments = self._default_key_release_event_arguments.copy()
        arguments.update(kwargs)
        return Xlib.protocol.event.KeyRelease(detail=detail, state=state,
                                              window=window, **arguments)

    def type_backspaces(self, num: int, window=None):
        if not window:
            window = self._get_window()

        self.type_string('\b' * num, window)

    def type_string(self, string: str, window=None):
        if not window:
            window = self._get_window()

        window = self.connection.get_input_focus().focus
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
                logging.error(f'No keycode found for: {character}.')
                continue

            yield keycode
