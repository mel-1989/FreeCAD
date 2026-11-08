# SPDX-License-Identifier: LGPL-2.1-or-later

# -*- coding: utf8 -*-
## @package importDWG
#  \ingroup DRAFT
#  \brief DWG file importer & exporter
"""
@package importDWG
ingroup DRAFT
\brief DWG file importer & exporter

This module provides support for importing and exporting Autodesk DWG files.
This module is only a thin layer that uses an outside converter program
(LibreDWG's dwg2dxf and dxf2dwg, the ODA File Converter or QCAD's dwg2dwg)
to convert to/from DXF. Then the real work is done by importDXF. The
programs are only ever run as separate processes.

A conversion succeeds only when the program returns 0 within the
"DWGConversionTimeout" preference (seconds, 0 means no limit) and writes a
non-empty output file. Anything else raises RuntimeError from open(), insert()
and export(), naming the converter, the return code or the timeout and the
last line the program printed on stderr. convertToDxf() and convertToDwg()
keep returning None on failure.

Test files
https://knowledge.autodesk.com/support/autocad/downloads/
    caas/downloads/content/autocad-sample-files.html
"""

# Check code quality with
# flake8 --ignore=E226,E266,E401,W503

# ***************************************************************************
# *   Copyright (c) 2009 Yorik van Havre <yorik@uncreated.net>              *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************

import os
import platform
import shutil
import signal
import subprocess
import tempfile

import FreeCAD
from FreeCAD import Console as FCC
from draftutils import params

if FreeCAD.GuiUp:
    from draftutils.translate import translate
else:

    def translate(context, txt):
        return txt


# LibreDWG's installation places that a Finder-launched application does not
# have on its PATH. Source: the LibreDWG README (it installs to the prefix of
# the package manager), Homebrew and MacPorts prefixes.
def _libredwg_fixed_dirs():
    system = platform.system()
    if system == "Darwin":
        return ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"]
    if system == "Linux":
        return ["/usr/bin", "/usr/local/bin"]
    return []


def _no_converter_message(notes=()):
    msg = translate(
        "draft",
        """No suitable external DWG converter has been found.
Please set one manually under menu Edit → Preferences → Import/Export → DWG
For more information see:
https://wiki.freecad.org/Import_Export_Preferences""",
    )
    if notes:
        msg += "\n" + "\n".join(notes)
    return msg


def _timeout():
    """Return the conversion timeout in seconds, or None for no limit."""
    value = params.get_param("DWGConversionTimeout")
    if not value or value <= 0:
        return None
    return value


def _last_line(*texts):
    for text in texts:
        if not text:
            continue
        for line in reversed(text.splitlines()):
            if line.strip():
                return line.strip()
    return ""


def _kill(proc):
    """Kill the program and anything it started."""
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass


def _run_converter(name, cmdline, result, cwd=None):
    """Run an outside converter and apply the success rule.

    A conversion succeeds only when the program returns 0 within the timeout
    and `result` exists and is not empty.

    Returns
    -------
    str or None
        None on success, otherwise the failure text.
    """
    timeout = _timeout()
    FCC.PrintMessage(translate("draft", "Converting:") + " " + str(cmdline) + "\n")
    kwargs = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True  # so a timeout can kill the whole group
    try:
        proc = subprocess.Popen(
            cmdline,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            errors="replace",
            **kwargs,
        )
    except OSError as exc:
        return f"{name} could not be started: {exc}"
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill(proc)
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        msg = f"{name} timed out after {timeout} s and was stopped"
        last = _last_line(err, out)
        return msg + (f": {last}" if last else "")
    last = _last_line(err, out)
    if proc.returncode != 0:
        return f"{name} returned {proc.returncode}" + (f": {last}" if last else "")
    if not os.path.isfile(result):
        return f"{name} returned 0 but wrote no output file" + (f": {last}" if last else "")
    if os.path.getsize(result) == 0:
        return f"{name} returned 0 but wrote an empty output file" + (f": {last}" if last else "")
    return None


def _clean(path):
    shutil.rmtree(path, ignore_errors=True)


def open(filename):
    """Open filename and parse using importDXF.open().

    Parameters
    ----------
    filename : str
        The path to the filename to be opened.

    Returns
    -------
    App::Document
        The new FreeCAD document object created, with the parsed information.

    Raises
    ------
    RuntimeError
        If the file cannot be converted to DXF.
    """
    dxf, outdir = _dwg_to_dxf(filename)
    try:
        import importDXF

        return importDXF.open(dxf)
    finally:
        _clean(outdir)


def insert(filename, docname):
    """Imports a file using importDXF.insert().

    If no document exist, it is created.

    Parameters
    ----------
    filename : str
        The path to the filename to be opened.
    docname : str
        The name of the active App::Document if one exists, or
        of the new one created.

    Returns
    -------
    App::Document
        The active FreeCAD document, or the document created if none exists,
        with the parsed information.

    Raises
    ------
    RuntimeError
        If the file cannot be converted to DXF.
    """
    dxf, outdir = _dwg_to_dxf(filename)
    try:
        import importDXF

        return importDXF.insert(dxf, docname)
    finally:
        _clean(outdir)


def export(objectslist, filename):
    """Export the DWG file with a given list of objects.

    The objects are exported with importDXF.export().
    Then the result is converted to DWG.

    Parameters
    ----------
    exportList : list
        List of document objects to export.
    filename : str
        Path to the new file.

    Returns
    -------
    str
        The same `filename` input, a file that exists and is not empty.

    Raises
    ------
    RuntimeError
        If the DWG file cannot be written.
    """
    import importDXF

    outdir = tempfile.mkdtemp()
    try:
        _basename = os.path.splitext(os.path.basename(filename))[0]
        dxf = outdir + os.sep + _basename + ".dxf"
        importDXF.export(objectslist, dxf)
        if not os.path.isfile(dxf) or os.path.getsize(dxf) == 0:
            raise RuntimeError(translate("draft", "The DXF writer produced no file to convert."))
        _dxf_to_dwg(dxf, filename, outdir)
        return filename
    finally:
        _clean(outdir)


def get_libredwg_converter(typ):
    """Find the LibreDWG converter.

    Order: the "TeighaFileConverter" preference when it names an existing
    LibreDWG program (a missing file gives one warning and falls through),
    then the OS search path, then the usual install places of the package
    managers, which a Finder-launched macOS application does not have on its
    search path.

    `typ` is required because LibreDWG uses two converters and we store only one.

    Parameters
    ----------
    typ : str
        "dwg2dxf" or "dxf2dwg".

    Returns
    -------
    str
        The full path of the converter.
    """
    windows = platform.system() == "Windows"

    def usable(path):
        if not (os.path.exists(path) and os.path.isfile(path)):
            return False
        return windows or os.access(path, os.X_OK)

    path = params.get_param("TeighaFileConverter") or ""

    if "dwg2dxf" in path or "dxf2dwg" in path:  # path set manually
        if typ not in os.path.basename(path):
            path = os.path.dirname(path) + "/" + typ + os.path.splitext(path)[1]
        if usable(path):
            return path
        FCC.PrintWarning(
            translate("draft", "The DWG converter set in the preferences was not found:")
            + " "
            + path
            + "\n"
        )

    dirs = os.getenv("PATH", "").split(os.pathsep) + _libredwg_fixed_dirs()
    for sub in dirs:
        if not sub:
            continue
        if windows:
            path = sub.replace("\\", "/") + "/" + typ + ".exe"
        else:
            path = sub + "/" + typ
        if usable(path):
            return path

    return None


def get_oda_converter():
    """Find the ODA converter.

    It searches the FreeCAD parameters database, then searches for common paths.

    Parameters
    ----------
    None

    Returns
    -------
    str
        The full path of the converter.
    """
    path = params.get_param("TeighaFileConverter")

    if "ODAFileConverter" in path:  # path set manually
        if os.path.exists(path) and os.path.isfile(path):
            return path
    elif platform.system() == "Windows":
        odadir = os.path.expandvars("%ProgramFiles%\\ODA").replace("\\", "/")
        if os.path.exists(odadir):
            for sub in os.listdir(odadir):
                path = odadir + "/" + sub + "/" + "ODAFileConverter.exe"
                if os.path.exists(path) and os.path.isfile(path):
                    return path
    elif platform.system() == "Linux":
        path = "/usr/bin/ODAFileConverter"
        if os.path.exists(path) and os.path.isfile(path):
            return path
    else:  # for macOS
        path = "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"
        if os.path.exists(path) and os.path.isfile(path):
            return path

    return None


def get_qcad_converter():
    """Find the QCAD converter.

    It searches the FreeCAD parameters database, then searches for common paths.

    Parameters
    ----------
    None

    Returns
    -------
    str
        The full path of the converter.
    """
    path = params.get_param("TeighaFileConverter")

    if "dwg2dwg" in path:  # path set manually
        pass
    elif platform.system() == "Windows":
        path = os.path.expandvars("%ProgramFiles%\\QCAD\\dwg2dwg.bat").replace("\\", "/")
    elif platform.system() == "Linux":
        # /home/$USER/opt/qcad-3.28.1-trial-linux-qt5.14-x86_64/dwg2dwg
        path = os.path.expandvars("/home/$USER/opt")
        if os.path.exists(path) and os.path.isdir(path):
            for sub in os.listdir(path):
                if "qcad" in sub:
                    path = path + "/" + sub + "/" + "dwg2dwg"
                    break
    else:  # for macOS
        path = "/Applications/QCAD.app/Contents/Resources/dwg2dwg"

    if os.path.exists(path) and os.path.isfile(path):
        return path
    return None


def _dwg_to_dxf(dwgfilename):
    """Convert a DWG file to a DXF file in a new temporary directory.

    Returns
    -------
    tuple of str
        The DXF file and the temporary directory that holds it. The caller
        removes the directory when it has finished reading the file.

    Raises
    ------
    RuntimeError
        If no converter is found or none of the ones found succeeds.
    """
    dwgfilename = dwgfilename.replace("\\", "/")
    conv = params.get_param("DWGConversion")
    basename = os.path.basename(dwgfilename)
    stem = os.path.splitext(basename)[0]
    failures = []
    notes = []

    def attempt(name, program, make_cmdline, cwd=None):
        outdir = tempfile.mkdtemp().replace("\\", "/")
        result = outdir + "/" + stem + ".dxf"
        err = _run_converter(name, make_cmdline(outdir, result), result, cwd)
        if err is None:
            FCC.PrintMessage(translate("draft", "Conversion successful") + "\n")
            return result, outdir
        _clean(outdir)
        failures.append(err)
        return None

    if conv in [0, 1]:  # LibreDWG
        libredwg = get_libredwg_converter("dwg2dxf")
        if libredwg is not None:
            found = attempt(
                "dwg2dxf", libredwg, lambda outdir, result: [libredwg, dwgfilename, "-o", result]
            )
            if found:
                return found
        elif conv != 0:
            notes.append(translate("draft", "LibreDWG converter not found"))

    if conv in [0, 2]:  # ODA
        oda = get_oda_converter()
        if oda is not None:
            indir = os.path.dirname(dwgfilename)
            found = attempt(
                "ODAFileConverter",
                oda,
                lambda outdir, result: [oda, indir, outdir, "ACAD2000", "DXF", "0", "1", basename],
            )
            if found:
                return found
        elif conv != 0:
            notes.append(translate("draft", "ODA converter not found"))

    if conv in [0, 3]:  # QCAD
        qcad = get_qcad_converter()
        if qcad is not None:
            found = attempt(
                "dwg2dwg",
                qcad,
                lambda outdir, result: [qcad, "-f", "-o", result, dwgfilename],
                cwd=os.path.dirname(qcad),  # cwd required for Windows
            )
            if found:
                return found
        elif conv != 0:
            notes.append(translate("draft", "QCAD converter not found"))

    if failures:
        raise RuntimeError(
            translate("draft", "DWG conversion failed:") + " " + failures[-1] + "\n" + "\n".join(notes)
        )
    raise RuntimeError(_no_converter_message(notes))


def convertToDxf(dwgfilename):
    """Convert a DWG file to a DXF file.

    If the converter is found it is used, otherwise the conversion fails.
    The DXF file lives in a new temporary directory that the caller removes.

    Parameters
    ----------
    dwgfilename : str
        The input filename.

    Returns
    -------
    str
        The new file produced, or None on failure (the reason is printed).
    """
    try:
        return _dwg_to_dxf(dwgfilename)[0]
    except RuntimeError as exc:
        FCC.PrintError(str(exc).rstrip() + "\n")
        return None


def _dxf_to_dwg(dxffilename, dwgfilename, workdir=None):
    """Convert a DXF file to a DWG file.

    The converter writes into a temporary directory and the result is moved
    to `dwgfilename` only on success, so a failure leaves nothing behind and
    does not touch a file that was already there.

    Raises
    ------
    RuntimeError
        If no converter is found or none of the ones found succeeds.
    """
    dxffilename = dxffilename.replace("\\", "/")
    dwgfilename = dwgfilename.replace("\\", "/")
    conv = params.get_param("DWGConversion")
    stem = os.path.splitext(os.path.basename(dxffilename))[0]
    failures = []
    notes = []

    def attempt(name, make_cmdline, cwd=None):
        outdir = tempfile.mkdtemp().replace("\\", "/")
        try:
            result = outdir + "/" + stem + ".dwg"
            err = _run_converter(name, make_cmdline(outdir, result), result, cwd)
            if err is None:
                try:
                    shutil.move(result, dwgfilename)
                except OSError as exc:
                    err = f"{name} wrote the file but it could not be moved to {dwgfilename}: {exc}"
            if err is None:
                FCC.PrintMessage(translate("draft", "Conversion successful") + "\n")
                return True
            failures.append(err)
            return False
        finally:
            _clean(outdir)

    if conv in [0, 1]:  # LibreDWG
        libredwg = get_libredwg_converter("dxf2dwg")
        if libredwg is not None:
            if attempt(
                "dxf2dwg", lambda outdir, result: [libredwg, dxffilename, "-y", "-o", result]
            ):
                return dwgfilename
        elif conv != 0:
            notes.append(translate("draft", "LibreDWG converter not found"))

    if conv in [0, 2]:  # ODA
        oda = get_oda_converter()
        if oda is not None:
            indir = os.path.dirname(dxffilename)
            basename = os.path.basename(dxffilename)
            if attempt(
                "ODAFileConverter",
                lambda outdir, result: [oda, indir, outdir, "ACAD2000", "DWG", "0", "1", basename],
            ):
                return dwgfilename
        elif conv != 0:
            notes.append(translate("draft", "ODA converter not found"))

    if conv in [0, 3]:  # QCAD
        qcad = get_qcad_converter()
        if qcad is not None:
            if attempt(
                "dwg2dwg",
                lambda outdir, result: [qcad, "-f", "-o", result, dxffilename],
                cwd=os.path.dirname(qcad),  # cwd required for Windows
            ):
                return dwgfilename
        elif conv != 0:
            notes.append(translate("draft", "QCAD converter not found"))

    if failures:
        raise RuntimeError(
            translate("draft", "DWG conversion failed:") + " " + failures[-1] + "\n" + "\n".join(notes)
        )
    raise RuntimeError(_no_converter_message(notes))


def convertToDwg(dxffilename, dwgfilename):
    """Convert a DXF file to a DWG file.

    If the converter is found it is used, otherwise the conversion fails.

    Parameters
    ----------
    dxffilename : str
        The input DXF file
    dwgfilename : str
        The output DWG file

    Returns
    -------
    str
        The same `dwgfilename` file path, or None on failure (the reason is
        printed).
    """
    try:
        return _dxf_to_dwg(dxffilename, dwgfilename)
    except RuntimeError as exc:
        FCC.PrintError(str(exc).rstrip() + "\n")
        return None
