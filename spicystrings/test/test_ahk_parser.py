import unittest
from unittest import TestCase

from ..ahk_parser import Hotstring, parse_directive, parse_hotstring_line


class TestParseHotstringLine(TestCase):
    def test_parse_0(self):
        line = '::btw::by the way'
        actual = parse_hotstring_line(line)
        expected = Hotstring('', 'btw', 'by the way')

        self.assertEqual(actual, expected)

    def test_parse_1(self):
        line = ':*:j@::jsmith@somedomain.com'
        actual = parse_hotstring_line(line)
        expected = Hotstring('*', 'j@', 'jsmith@somedomain.com')

        self.assertEqual(actual, expected)


class TestParseDirective(TestCase):
    def test_parse_directive_0(self):
        line = r"""#Hotstring EndChars -()[]{}:;'"/\,.?!`n `t"""
        expected = '-()[]{}:;\'"/\\,.?!\n \t'
        actual = parse_directive(line)

        self.assertEqual(actual, expected)

if __name__ == '__main__':
    unittest.main()
