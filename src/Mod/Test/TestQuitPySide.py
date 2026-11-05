# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exit-time regression test: Python wrapping the main thread's event dispatcher must not abort quit.

PySide tags every QObject it wraps with a dynamic property that, on macOS, was still alive when libc
exit() ran static destructors after Shiboken had gone, aborting the process (SIGABRT). A test cannot
see its own exit status, so this suite's single test only does the wrapping; the pass or fail is the
exit status of the FreeCAD process running it (0 expected, -6 on the bug), which a harness that runs
this suite in its own GUI process (harness/gui/suites.py under run_gui.py) reads.
"""

import unittest

from PySide6 import QtCore


class TestQuitPySide(unittest.TestCase):
    def test_wrap_event_dispatcher(self):
        dispatcher = QtCore.QAbstractEventDispatcher.instance()
        self.assertIsNotNone(dispatcher)
        # Python now owns a wrapper of the C++ dispatcher; leave it alive until process exit.
        type(self)._keep = dispatcher
