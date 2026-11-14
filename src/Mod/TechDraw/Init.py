# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2002 Juergen Riegel <juergen.riegel@web.de>             *
# *                                                                         *
# *   This file is part of the FreeCAD CAx development system.              *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful,            *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Lesser General Public License for more details.                   *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with FreeCAD; if not, write to the Free Software        *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************/

# FreeCAD init script of the TechDraw module

FreeCAD.__unit_test__ += ["TestTechDrawApp", "TestTechDrawPmi"]


# G04 slice 5a: a dimension-set driver (DimChain.py) that stable FreeCAD re-saved comes back without its Proxy; give it back when
# the document becomes active after opening, or before its next recompute.  Document-level slots only: a per-property observer
# (slotChangedObject) ran Python on every property change in every process and crashed AgentCore's close-race test.  The module is
# imported only when such a feature is met.
class _DimChainRehome:
    def _scan(self, doc):
        try:
            for obj in doc.Objects:
                if obj.TypeId == "App::FeaturePython" and obj.Proxy is None and "Vertices" in obj.PropertiesList and obj.getGroupOfProperty("Vertices") == "DimChain":
                    import DimChain

                    DimChain.reattach(obj)
        except Exception:
            pass

    def slotActivateDocument(self, doc):
        self._scan(doc)

    def slotBeforeRecomputeDocument(self, doc):
        self._scan(doc)


FreeCAD.addDocumentObserver(_DimChainRehome())
