"""Bhasha Desk: the deterministic half of an agent team that localises an app.

Language models translate and talk to people. This package does everything
that has to be exactly right: finding what needs translating, checking every
string, recording glossary decisions and approvals, and the git work.
"""

__version__ = "0.1.0"


class BhashaError(Exception):
    """A problem the caller can fix, reported without a traceback."""
