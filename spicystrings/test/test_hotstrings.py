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
