import unittest
from unittest import TestCase

from ..actions import GlobalHotstringOptions, HotstringFlags, Replace
from ..hotstrings import HotstringDefinition, HotstringDetector


class TestHotstringDetector(TestCase):
    def test_hotstring(self):
        hotstring_definitions = [HotstringDefinition('yl', Replace('yield'))]
        hotstring_detector = self.get_basic_hotstring_detector(
            hotstring_definitions)

        typed = 'yl '
        expected = [None, None, ('yl ', 'yield ')]
        self.check_hotstring_output(hotstring_detector, typed, expected)

    def test_hotstring_empty_backspace(self):
        hotstring_detector = self.get_basic_hotstring_detector([])

        typed = '\b'
        expected = [None]
        self.check_hotstring_output(hotstring_detector, typed, expected)

    def test_hotstring_back_to_back(self):
        """Make sure that the buffer is cleared when a match is found."""
        hotstring_definitions = [HotstringDefinition('ab', Replace('abc'))]
        hotstring_detector = self.get_basic_hotstring_detector(
            hotstring_definitions)

        typed = 'ab \b\bb '
        expected = [None, None, ('ab ', 'abc ')] + [None] * 4
        self.check_hotstring_output(hotstring_detector, typed, expected)

    def test_hotstring_case_insensitive_definition(self):
        """A case-insensitive replacement can be defined upper case or lower
        case"""
        hotstring_definitions = [HotstringDefinition('YL', Replace('yield'))]
        hotstring_detector = self.get_basic_hotstring_detector(
            hotstring_definitions)

        typed = 'yl '
        expected = [None, None, ('yl ', 'yield ')]
        self.check_hotstring_output(hotstring_detector, typed, expected)

    def test_hotstring_capitalized(self):
        hotstring_definitions = [HotstringDefinition('yl', Replace('yield'))]
        hotstring_detector = self.get_basic_hotstring_detector(
            hotstring_definitions)

        typed = 'Yl '
        expected = [None, None, ('Yl ', 'Yield ')]
        self.check_hotstring_output(hotstring_detector, typed, expected)

    def test_hotstring_upper(self):
        hotstring_definitions = [HotstringDefinition('yl', Replace('yield'))]
        hotstring_detector = self.get_basic_hotstring_detector(
            hotstring_definitions)

        typed = 'YL '
        expected = [None, None, ('YL ', 'YIELD ')]
        self.check_hotstring_output(hotstring_detector, typed, expected)

    def test_hotstring_as_string_end(self):
        """a normal hotstring should not trigger if it is only a subsring."""
        hotstring_definitions = [HotstringDefinition('yl', Replace('yield'))]
        hotstring_detector = self.get_basic_hotstring_detector(
            hotstring_definitions)

        typed = 'ayl '
        expected = [None] * 4
        self.check_hotstring_output(hotstring_detector, typed, expected)

    def test_hotstring_as_string_beginning(self):
        """a normal hotstring should not trigger if it is only a subsring."""
        hotstring_definitions = [HotstringDefinition('yl', Replace('yield'))]
        hotstring_detector = self.get_basic_hotstring_detector(
            hotstring_definitions)

        typed = 'yla '
        expected = [None] * 4
        self.check_hotstring_output(hotstring_detector, typed, expected)

    def test_hotstring_no_end_char(self):
        hotstring_definitions = [HotstringDefinition(
            'yl',
            Replace('yield'),
            {HotstringFlags.NO_END_CHAR}
            )]
        hotstring_detector = self.get_basic_hotstring_detector(
            hotstring_definitions)

        typed = 'yl'
        expected = [None, ('yl', 'yield')]
        self.check_hotstring_output(hotstring_detector, typed, expected)

    def test_no_end_char_back_to_back(self):
        """Verified behavior with AHK 1.1.33.09"""
        hotstring_definitions = [
            HotstringDefinition(
                'a.',
                Replace('abracadabra'),
                {HotstringFlags.NO_END_CHAR}
                )
            ]
        hotstring_detector = self.get_basic_hotstring_detector(
            hotstring_definitions)

        typed = 'a.a.'
        expected = [None, ('a.', 'abracadabra'), None, ('a.', 'abracadabra')]
        self.check_hotstring_output(hotstring_detector, typed, expected)

    def test_no_end_char_as_substring(self):
        """This should not trigger."""
        hotstring_definitions = [
            HotstringDefinition(
                'a',
                Replace('abracadabra'),
                {HotstringFlags.NO_END_CHAR}
                )
            ]
        hotstring_detector = self.get_basic_hotstring_detector(
            hotstring_definitions)

        typed = 'Za'
        expected = [None, None]
        self.check_hotstring_output(hotstring_detector, typed, expected)

    def test_no_end_char_backspace(self):
        """Verified behavior with AHK 1.1.33.09"""
        hotstring_definitions = [
            HotstringDefinition(
                'a.',
                Replace('abracadabra'),
                {HotstringFlags.NO_END_CHAR}
                )
            ]
        hotstring_detector = self.get_basic_hotstring_detector(
            hotstring_definitions)

        typed = 'a \b.'
        expected = [None] * 3 + [('a.', 'abracadabra')]
        self.check_hotstring_output(hotstring_detector, typed, expected)

    def test_match_suffix(self):
        hotstring_definitions = [
            HotstringDefinition(
                'al',
                Replace('airline'),
                {HotstringFlags.MATCH_SUFFIX}
                )
            ]

        hotstring_detector = self.get_basic_hotstring_detector(
            hotstring_definitions)

        typed = 'practical '
        expected = [None] * 9 + [('al ', 'airline ')]
        self.check_hotstring_output(hotstring_detector, typed, expected)

    def test_hotstring_no_backspace(self):
        hotstring_definitions = [HotstringDefinition(
            'yl',
            Replace('yield'),
            {HotstringFlags.NO_BACKSPACE}
        )]
        hotstring_detector = self.get_basic_hotstring_detector(
            hotstring_definitions)

        typed = 'yl.'
        expected = [None, None, ('', 'yield')]
        self.check_hotstring_output(hotstring_detector, typed, expected)

    def test_hotstring_omit_end_char(self):
        hotstring_definitions = [HotstringDefinition(
            'yl',
            Replace('yield'),
            {HotstringFlags.OMIT_END_CHAR}
        )]
        hotstring_detector = self.get_basic_hotstring_detector(
            hotstring_definitions)

        typed = 'yl.'
        expected = [None, None, ('yl.', 'yield')]
        self.check_hotstring_output(hotstring_detector, typed, expected)

    def get_basic_hotstring_detector(self, hotstring_definitions):
        global_hotstring_options = GlobalHotstringOptions()
        return HotstringDetector(hotstring_definitions,
                                 global_hotstring_options)

    def check_hotstring_output(self, hotstring_detector, typed,
                               expected):
        def hotstring_output(result):
            if result:
                trigger, action = result
                replacement = action()
                return trigger, replacement
            else:
                return result

        actual = list((hotstring_output(hotstring_detector.next_typed_char(x))
                       for x in typed))
        self.assertEqual(expected, actual)


if __name__ == '__main__':
    unittest.main()
