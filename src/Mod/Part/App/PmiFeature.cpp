// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2026 FreeCAD fork contributors                          *
 *                                                                         *
 *   This file is part of FreeCAD.                                         *
 *                                                                         *
 *   FreeCAD is free software: you can redistribute it and/or modify it    *
 *   under the terms of the GNU Lesser General Public License as           *
 *   published by the Free Software Foundation, either version 2.1 of the  *
 *   License, or (at your option) any later version.                       *
 *                                                                         *
 *   FreeCAD is distributed in the hope that it will be useful, but        *
 *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
 *   Lesser General Public License for more details.                       *
 *                                                                         *
 *   You should have received a copy of the GNU Lesser General Public      *
 *   License along with FreeCAD. If not, see                               *
 *   <https://www.gnu.org/licenses/>.                                      *
 *                                                                         *
 **************************************************************************/

#include "PreCompiled.h"

#include "PmiFeature.h"

using namespace Part;

PROPERTY_SOURCE(Part::PmiItem, App::DocumentObject)

PmiItem::PmiItem()
{
    ADD_PROPERTY_TYPE(PmiType, (""), "PMI", App::Prop_None, "XCAF type name of the item");
    ADD_PROPERTY_TYPE(SemanticName, (""), "PMI", App::Prop_None, "Name given by the source file");
    ADD_PROPERTY_TYPE(Value, (0.0), "PMI", App::Prop_None, "Nominal value (mm or degrees) or tolerance magnitude");
    ADD_PROPERTY_TYPE(HasBounds, (false), "PMI", App::Prop_None, "UpperBound and LowerBound are set");
    ADD_PROPERTY_TYPE(BoundsKind, (""), "PMI", App::Prop_None, "range, plus_minus or class");
    ADD_PROPERTY_TYPE(UpperBound, (0.0), "PMI", App::Prop_None, "Upper limit or deviation");
    ADD_PROPERTY_TYPE(LowerBound, (0.0), "PMI", App::Prop_None, "Lower limit or deviation");
    ADD_PROPERTY_TYPE(Qualifier, (""), "PMI", App::Prop_None, "Item qualifier");
    ADD_PROPERTY_TYPE(Modifiers, (), "PMI", App::Prop_None, "Modifiers in source order");
    ADD_PROPERTY_TYPE(DatumReferences, (), "PMI", App::Prop_None, "Datum references in compartment order");
    ADD_PROPERTY_TYPE(References, (nullptr), "PMI", App::Prop_None, "Governed faces and edges");
    ADD_PROPERTY_TYPE(StepFaceIds, (0), "PMI", App::Prop_None, "Part 21 ids of the governed faces at import");
    ADD_PROPERTY_TYPE(RawAttributes, (), "PMI", App::Prop_None, "All source attributes as text");
    ADD_PROPERTY_TYPE(Unmapped, (false), "PMI", App::Prop_None, "Type or modifier not recognised");
    ADD_PROPERTY_TYPE(Unattached, (false), "PMI", App::Prop_None, "No face or edge could be found");
    StepFaceIds.setValues({});
}

PROPERTY_SOURCE(Part::PmiDimension, Part::PmiItem)

PmiDimension::PmiDimension()
{
    ADD_PROPERTY_TYPE(ToleranceClass, (""), "PMI", App::Prop_None, "ISO 286 class of tolerance");
}

PROPERTY_SOURCE(Part::PmiTolerance, Part::PmiItem)

PmiTolerance::PmiTolerance()
{
    ADD_PROPERTY_TYPE(ZoneShape, (""), "PMI", App::Prop_None, "Diameter or SphericalDiameter zone");
    ADD_PROPERTY_TYPE(ZoneModifier, (""), "PMI", App::Prop_None, "Projected, Runout or NonUniform");
    ADD_PROPERTY_TYPE(ZoneModifierValue, (0.0), "PMI", App::Prop_None, "Value of the zone modifier");
    ADD_PROPERTY_TYPE(Datums, (nullptr), "PMI", App::Prop_None, "Datum objects in reference order");
}

PROPERTY_SOURCE(Part::PmiDatum, Part::PmiItem)

PmiDatum::PmiDatum()
{
    ADD_PROPERTY_TYPE(Letter, (""), "PMI", App::Prop_None, "Datum letter");
    ADD_PROPERTY_TYPE(Position, (0), "PMI", App::Prop_None, "Precedence in the datum reference frame");
    ADD_PROPERTY_TYPE(IsTarget, (false), "PMI", App::Prop_None, "Datum target");
}

PROPERTY_SOURCE(Part::PmiGroup, App::DocumentObjectGroup)
