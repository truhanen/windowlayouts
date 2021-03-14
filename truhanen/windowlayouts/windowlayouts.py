import argparse
import asyncio
import configparser
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

import appdirs
from dataclasses_json import dataclass_json

try:
    # If pandas is available, use it for extra logging.
    import pandas as pd
except ImportError:
    pd = None

__all__ = ["main"]


LOG = logging.getLogger(__file__)

# Serialization paths
JSON_PATH = Path(appdirs.user_cache_dir("windowlayouts")) / "windowlayouts.json"

# Configuration file
CONFIG_DIR = Path(appdirs.user_config_dir("windowlayouts"))
CONFIG_PATH = CONFIG_DIR / "config.ini"
CONFIG_SECTION_SCREENLAYOUTS = "screenlayouts"

# Regex pattern for `wmctrl -lpG` output
REGEX_WMCTRL_WINDOW = re.compile(
    r"(?P<window_id>0x[a-z0-9]+) +"
    r"(?P<desktop_number>-?[0-9]+) +"
    r"(?P<process_id>[0-9]+) +"
    r"(?P<x>-?[0-9]+) +"
    r"(?P<y>-?[0-9]+) +"
    r"(?P<width>[0-9]+) +"
    r"(?P<height>[0-9]+) +"
    r"[a-zA-Z0-9/]* +"
    r"(?P<window_name>.+)"
)

# Regex pattern for xrandr output.
REGEX_XRANDR_SCREEN = re.compile(
    r"(?P<name>[a-zA-Z0-9-]+) .+ "
    r"(?P<width>[0-9]+)x"
    r"(?P<height>[0-9]+)"
    r"(?P<x>[+-][0-9]*)"
    r"(?P<y>[+-][0-9]*)"
)

# Desktop number used for sticky windows
DESKTOP_NUMBER_STICKY = -1


@dataclass_json
@dataclass
class Position:
    x: int
    y: int


@dataclass_json
@dataclass
class Size:
    width: int
    height: int


@dataclass_json
@dataclass
class Screen:
    name: str
    size: Size
    position: Position


@dataclass_json
@dataclass
class Window:
    # The title of the window
    name: str
    # The class of the window
    window_class: str
    # The window identifier used by the backend
    window_id: str
    # The process id of the window
    process_id: Optional[str]
    # The number of the virtual desktop of the window
    desktop_number: int
    # The position of the window
    # Note that position determines the screen
    # in which the window is displayed.
    position: Position
    # The size of the window
    size: Size
    # Horizontal & vertical maximized state
    maximized_horizontal: bool
    maximized_vertical: bool


@dataclass_json
@dataclass
class WindowLayout:
    """Window layout associated with a specific screen layout."""

    screen_layout: List[Screen]
    windows: List[Window]


def log_dataclass_list(dc_list: List[dataclass]):
    if pd is not None:
        with pd.option_context(
            "display.max_columns", 10, "display.max_rows", 1000, "display.width", 1000
        ):
            message = "\n" + str(pd.DataFrame([asdict(dc) for dc in dc_list]))
    else:
        message = dc_list
    LOG.debug(message)


def log_window_layout(window_layout: WindowLayout, postfix: Optional[str] = None):
    window_layout_name = "window layout" + f" {postfix}" if postfix else ""
    LOG.debug(f"Screen layout of {window_layout_name}:")
    log_dataclass_list(window_layout.screen_layout)
    LOG.debug(f"Windows of {window_layout_name}:")
    log_dataclass_list(window_layout.windows)


def log_window_layouts(window_layouts: List[WindowLayout]):
    """Log debug data about a set of window layouts."""
    for i, layout in enumerate(window_layouts):
        log_window_layout(layout, postfix=f"{i}")


def get_config_screen_layouts() -> Dict[str, str]:
    """Read screen layout configurations from the configuration file."""
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH)

    screen_layouts = {}
    if CONFIG_SECTION_SCREENLAYOUTS in config:
        for screen_layout_name, screen_layout_value in config[
            CONFIG_SECTION_SCREENLAYOUTS
        ].items():
            screen_layouts[screen_layout_name] = screen_layout_value

    if not screen_layouts:
        LOG.warning(f"Couldn't read screen layout configurations from '{CONFIG_PATH}'.")

    return screen_layouts


async def run_command(command: str) -> str:
    """Run a shell command & return its output."""
    LOG.debug(f"Run shell command '{command}'.")
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    return stdout.decode()


def parse_screen(xrandr_row: str) -> Optional[Screen]:
    match = re.search(REGEX_XRANDR_SCREEN, xrandr_row)

    if not match:
        return None

    return Screen(
        name=match.group("name"),
        size=Size(
            width=int(match.group("width")),
            height=int(match.group("height")),
        ),
        position=Position(
            x=int(match.group("x")),
            y=int(match.group("y")),
        ),
    )


async def parse_window(wmctrl_row: str) -> Optional[Window]:
    """Create a Window from wmctrl output.

    Parameters
    ----------
    wmctrl_row
        Output row from `wmctrl -lpG`. None will be returned if this doesn't
        match the expected format `REGEX_WMCTRL_WINDOW`.
    """
    match = REGEX_WMCTRL_WINDOW.match(wmctrl_row)

    if not match:
        return None

    window_id = match.group("window_id")

    position = Position(
        x=int(match.group("x")),
        y=int(match.group("y")),
    )

    size = Size(
        width=int(match.group("width")),
        height=int(match.group("height")),
    )

    xprop_state = await run_command(f"xprop -id {window_id} _NET_WM_STATE")
    maximized_horizontal = "_NET_WM_STATE_MAXIMIZED_HORZ" in xprop_state
    maximized_vertical = "_NET_WM_STATE_MAXIMIZED_VERT" in xprop_state

    return Window(
        name=match.group("window_name"),
        window_class="",
        window_id=window_id,
        process_id=match.group("process_id"),
        desktop_number=int(match.group("desktop_number")),
        position=position,
        size=size,
        maximized_horizontal=maximized_horizontal,
        maximized_vertical=maximized_vertical,
    )


async def restore_window(window: Window):
    """Restore the position & size of one window. After calling this function
    the values in window may not be up-to-date.
    """
    window_id = window.window_id

    # Guard for changes in the number of desktops.
    current_desktop_count = len((await run_command("wmctrl -d")).split("\n"))
    desktop_number = min(window.desktop_number, current_desktop_count)

    # Unmaximize before moving.
    await run_command(
        f"wmctrl -i -r {window_id} -b remove,maximized_vert,maximized_horz"
    )

    # Set desktop number or sticky value.
    if desktop_number == DESKTOP_NUMBER_STICKY:
        await run_command(f"wmctrl -i -r {window_id} -b add,sticky")
    else:
        await run_command(f"wmctrl -i -r {window_id} -b remove,sticky")
        await run_command(f"wmctrl -i -r {window_id} -t {desktop_number}")

    # Set size & position.
    await run_command(
        f"wmctrl -i -r {window_id} -e "
        f"0,"
        f"{window.position.x},"
        f"{window.position.y},"
        f"{window.size.width},"
        f"{window.size.height}"
    )

    # Set maximized state.
    for maximized_value, property_name in zip(
        (window.maximized_vertical, window.maximized_horizontal),
        ("maximized_vert", "maximized_horz"),
    ):
        if maximized_value:
            await run_command(f"wmctrl -i -r {window_id} -b add,{property_name}")


async def get_current_window_layout() -> WindowLayout:
    """Get the current screen & window layout."""
    LOG.debug("Get current window layout.")

    # Get the current screen layout.
    xrandr_rows = (await run_command("xrandr")).split("\n")
    screen_layout = []
    for xrandr_row in xrandr_rows:
        screen = parse_screen(xrandr_row)
        if isinstance(screen, Screen):
            screen_layout.append(screen)

    # Get the current windows.
    wmctrl_rows = (await run_command("wmctrl -lpG")).split("\n")
    get_window_tasks = [asyncio.create_task(parse_window(row)) for row in wmctrl_rows]
    windows = [
        window
        for window in await asyncio.gather(*get_window_tasks)
        if isinstance(window, Window)
    ]

    window_layout = WindowLayout(
        screen_layout=screen_layout,
        windows=windows,
    )

    log_window_layout(window_layout, postfix="(current)")

    return window_layout


def open_stored_window_layouts() -> List[WindowLayout]:
    """Open previously stored window layouts."""
    if not JSON_PATH.exists():
        return []

    LOG.info(f"Open stored window layouts from '{JSON_PATH}'.")
    with open(JSON_PATH) as f:
        json_data = json.load(f)
    window_layouts = [WindowLayout.from_dict(layout_data) for layout_data in json_data]

    LOG.debug("Currently stored window layouts:")
    log_window_layouts(window_layouts)

    return window_layouts


async def store_current_window_layout(**_):
    """Store the current window layout for the current screen layout. Replace
    a previously stored window layout if such a window layout exists for the
    current screen layout.
    """
    window_layout_current = await get_current_window_layout()

    # Get stored window layouts, if any.
    window_layouts_stored: List[WindowLayout] = open_stored_window_layouts()

    # Add the current WindowLayout to the stored WindowLayouts, possibly replacing a
    # former WindowLayout with identical screen layout.
    for i in range(len(window_layouts_stored)):
        window_layout_stored = window_layouts_stored[i]
        if window_layout_stored.screen_layout == window_layout_current.screen_layout:
            LOG.info(f"Replace stored window layout {i}.")
            window_layouts_stored[i] = window_layout_current
            break
    else:
        window_layouts_stored.append(window_layout_current)

    LOG.debug("Window layouts to be stored:")
    log_window_layouts(window_layouts_stored)

    # Store WindowLayouts as json.
    JSON_PATH.parent.mkdir(exist_ok=True, parents=True)
    LOG.info(f"Store window layouts to '{JSON_PATH}'.")
    data = [layout.to_dict() for layout in window_layouts_stored]
    with open(JSON_PATH, "w") as f:
        json.dump(data, f, indent=2)


async def restore_window_layout(**_):
    """Restore a window layout if one with the current screen layout has
    previously been stored.
    """
    # Load previously stored layouts.
    window_layouts_stored = open_stored_window_layouts()

    if not window_layouts_stored:
        # Nothing to do if no layouts have been stored.
        return

    # Get the current window layout.
    window_layout_current = await get_current_window_layout()

    # Restore window positions if ones with a matching screen layout have been stored.
    restore_tasks = []
    for i, window_layout_stored in enumerate(window_layouts_stored):
        if window_layout_stored.screen_layout == window_layout_current.screen_layout:
            LOG.info(f"Restore window layout {i}.")
            window_map_current = {
                window.window_id: window for window in window_layout_current.windows
            }
            for window_stored in window_layout_stored.windows:
                if window_stored.window_id in window_map_current:
                    restore_tasks.append(
                        asyncio.create_task(restore_window(window_stored))
                    )
            break
    await asyncio.gather(*restore_tasks)

    if LOG.level == logging.DEBUG:
        await get_current_window_layout()


async def switch_screen_layout(screen_layout_name: str, **kwargs):
    """Run store, then switch to a screen layout configured in CONFIG_DIR/config.ini,
    and then run restore. Screen layout values in the configuration file must be
    valid input for xrandr that apply a specific screen layout. See
    examples/config.ini for example.
    """
    # Store the current window layout.
    await store_current_window_layout(**kwargs)

    # Apply the screen layout.
    LOG.info(f"Apply screen layout '{screen_layout_name}'.")
    config_screen_layouts = get_config_screen_layouts()
    xrandr_args = config_screen_layouts[screen_layout_name].replace("\n", " ")
    await run_command(f"xrandr {xrandr_args}")

    # Wait for the desktop environment to stabilize.
    await asyncio.sleep(10)

    # Restore a previously stored window layout.
    await restore_window_layout(**kwargs)

    # Notify user that everythin is ready.
    await run_command("notify-send -t 1000 'Layout ready'")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verbose",
        "-v",
        action="count",
        help="Increase verbosity.",
        default=0,
    )

    # Handle commands as subparsers.
    subparsers = parser.add_subparsers()

    store = subparsers.add_parser("store", help=store_current_window_layout.__doc__)
    store.set_defaults(func=store_current_window_layout)

    restore = subparsers.add_parser("restore", help=restore_window_layout.__doc__)
    restore.set_defaults(func=restore_window_layout)

    switch = subparsers.add_parser(
        "switch",
        help=switch_screen_layout.__doc__.replace(
            f"CONFIG_DIR/{CONFIG_PATH.name}", str(CONFIG_PATH)
        ),
    )
    switch.add_argument(
        "screen_layout_name",
        choices=list(get_config_screen_layouts().keys()),
        help=f"The name of a screen layout configured in {CONFIG_PATH}.",
    )
    switch.set_defaults(func=switch_screen_layout)

    return parser.parse_args()


def main():
    args = parse_args()

    log_level = {0: logging.WARNING, 1: logging.INFO}.get(args.verbose, logging.DEBUG)
    logging.basicConfig(level=log_level)

    asyncio.run(args.func(**vars(args)))


if __name__ == "__main__":
    main()
