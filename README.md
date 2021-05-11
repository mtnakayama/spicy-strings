# spicy-strings

**This program is under development. Expect breaking changes. This readme file may not be up to date.**

This repository is based on the `hotstrings` script from
[cryzed/bin](https://github.com/cryzed/bin).

There might be better ways to do some of the things I wrote these scripts for, or
even already existing solutions I simply didn't know about; if you think you
know of either one please let me know.

## Usage

```txt
$ hotstrings --help
usage: hotstrings [-h] [path]

positional arguments:
  path        Path to JSON file containing hotstring definitions

optional arguments:
  -h, --help  show this help message and exit
```

This script is a much lighter version of AutoKey, specifically the
[AutoKey-py3](https://aur.archlinux.org/packages/autokey-py3/) fork. Since there was a recent update to the official
[python-xlib](https://github.com/python-xlib/python-xlib) which breaks compatibility with the fork, and the fork also
doesn't seem to be actively maintained anymore, I wanted to preemptively find another solution before AutoKey-py3
eventually stops working entirely on my system.

This pretty much implements only the functionality I actually use: replacing hotstrings and running commands; basically
you type text and it is either replaced by the given replacement string or a specified command is run (optionally
replacing the hotstring with its stdout output). Dependencies are Python 3 and the the official python-xlib or
[LiuLang's fork](https://github.com/LiuLang/python3-xlib) which is used by AutoKey-py3.

An example configuration file might look like this:

```txt
$ cat ~/.config/hotstrings.json
{
    "first": ["replace", "replacement 1"],
    "second": ["replace", "replacement 2"],
    "third": ["run", "sh", "-c", "touch ~/Desktop/hello_world.txt"],
    "fourth": ["run-replace", "date"],

    // doesn't strip whitespace at the beginning and end of the output
    "five": ["run-replace-raw", "date"]
}
```
