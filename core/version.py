"""Pixkin application identity and release version.

Release tooling, runtime metadata, and UI labels must import VERSION from this
module instead of repeating a version literal.
"""

APP_NAME = "Pixkin"
VERSION = "1.5.0"
MINIMUM_UPDATE_VERSION = "1.3.0"
# Public releases stop at the 1.5 line; this internal marker records the
# capability milestone represented by that release for planning and support.
CAPABILITY_MILESTONE = "2.0.0"


def portable_executable_name() -> str:
    return f"{APP_NAME}-Portable-{VERSION}"


def folder_archive_name() -> str:
    return f"{APP_NAME}-{VERSION}-win64"
