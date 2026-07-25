"""Pixkin application identity and release version.

Release tooling, runtime metadata, and UI labels must import VERSION from this
module instead of repeating a version literal.
"""

APP_NAME = "Pixkin"
VERSION = "1.3.0"


def portable_executable_name() -> str:
    return f"{APP_NAME}-Portable-{VERSION}"


def folder_archive_name() -> str:
    return f"{APP_NAME}-{VERSION}-win64"
