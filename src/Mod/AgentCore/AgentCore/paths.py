# SPDX-License-Identifier: LGPL-2.1-or-later
"""Locate the FreeCAD source tree from either the source checkout or a build.

AgentCore runs from ``src/Mod/AgentCore`` during development and from
``<build>/Mod/AgentCore`` once built; upstream data files (``data/tests``,
``data/examples``) and the ``mcp-server`` packaging dir live only in the source
tree. Resolution order: ``$FC_SRC``, the checkout this file sits in, the build's
``CMakeCache.txt`` (``FreeCAD_SOURCE_DIR``).
"""

import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))


def _is_source_root(path):
    return os.path.isdir(os.path.join(path, "src", "Mod")) and os.path.isdir(os.path.join(path, "data"))


def _from_cmake_cache(start):
    d = start
    while True:
        cache = os.path.join(d, "CMakeCache.txt")
        if os.path.isfile(cache):
            with open(cache, errors="ignore") as f:
                m = re.search(r"^FreeCAD_SOURCE_DIR:\w+=(.*)$", f.read(), re.M)
            if m and _is_source_root(m.group(1).strip()):
                return m.group(1).strip()
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def source_root():
    """Absolute path of the FreeCAD source tree, or raise RuntimeError."""
    env = os.environ.get("FC_SRC")
    if env and _is_source_root(env):
        return os.path.abspath(env)
    checkout = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir, os.pardir, os.pardir))
    if _is_source_root(checkout):
        return checkout
    found = _from_cmake_cache(_HERE)
    if found:
        return found
    raise RuntimeError("cannot locate the FreeCAD source tree; set FC_SRC")


def agentcore_source_dir():
    """``src/Mod/AgentCore`` in the source tree (holds mcp-server/ packaging)."""
    return os.path.join(source_root(), "src", "Mod", "AgentCore")
