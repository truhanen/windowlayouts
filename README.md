# windowlayouts

Utility for automatically storing & restoring window layouts for specific screen
configurations on the X Window System.

## Installation

Run `pip install truhanen.windowlayouts` to install the script `windowlayouts`
to be used from the command line.

### Requirements

- Python 3.7+ & pip
- wmctrl command line tool

### Install from source

Clone the repository & run `pip install .` in the project root directory.

## Usage

```
~$ windowlayouts --help
usage: windowlayouts [-h] [--verbose] {store,restore,switch} ...

positional arguments:
  {store,restore,switch}
    store               Store the current window layout for the current screen
                        layout. Possibly replace values in previously stored
                        layouts.
    restore             Restore a window layout if one with the current
                        screen layout has previously been stored.
    switch              Run store, then switch to a screen layout configured
                        in ~/.config/windowlayouts/config.ini, and
                        then run restore. Screen layout values in the
                        configuration file should be valid input for xrandr
                        that apply a specific screen layout. See
                        examples/config_example.ini for example.

optional arguments:
  -h, --help            show this help message and exit
  --verbose, -v         Increase verbosity.
```
