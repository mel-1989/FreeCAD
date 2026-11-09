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

#ifndef PART_PMIFEATURE_H
#define PART_PMIFEATURE_H

#include <App/DocumentObject.h>
#include <App/DocumentObjectGroup.h>
#include <App/PropertyLinks.h>
#include <App/PropertyGeo.h>
#include <App/PropertyStandard.h>
#include <Mod/Part/PartGlobal.h>

#include "PropertyTopoShape.h"

// Semantic product and manufacturing information (PMI): the dimensions, geometric tolerances and
// datums of a 3D model. The vocabulary follows the CAx-IF recommended practices for the
// representation and presentation of PMI (AP242), ASME Y14.5 and ISO 1101; the type names are the
// ones of OCCT's XCAFDimTolObjects enumerations, so an item can be rebuilt as an XCAF object.

namespace Part
{

/**
 * Common data of one PMI item. Not a Part::Feature: it has no shape, only values and links to the
 * faces or edges it governs.
 */
class PartExport PmiItem: public App::DocumentObject
{
    PROPERTY_HEADER_WITH_OVERRIDE(Part::PmiItem);

public:
    PmiItem();

    /// Type name from the XCAF enumeration, e.g. Size_Diameter, Location_LinearDistance, Position, Flatness, Datum
    App::PropertyString PmiType;
    /// Semantic name the source file gave the item (for example "diameter" or "Position.1")
    App::PropertyString SemanticName;
    /// Nominal value (millimetre, or degree for angles); the tolerance magnitude for a tolerance
    App::PropertyFloat Value;
    /// False for a dimension the author gave no stated value (Value is then 0.0 and means nothing; the writer writes no nominal)
    App::PropertyBool HasValue;
    /// True when UpperBound and LowerBound are set
    App::PropertyBool HasBounds;
    /// "range" (absolute limits), "plus_minus" (deviations from the nominal) or "class" (limits and fits), empty without bounds
    App::PropertyString BoundsKind;
    App::PropertyFloat UpperBound;
    App::PropertyFloat LowerBound;
    /// Qualifier of the item (Min, Max, Avg; Small, Large, Equal for angles); empty if none
    App::PropertyString Qualifier;
    /// Modifiers in the source order, upper case names such as MAXIMUM_MATERIAL_REQUIREMENT
    App::PropertyStringList Modifiers;
    /// Datum references in compartment order, one per use, e.g. "A", "B(MAXIMUM_MATERIAL_REQUIREMENT)"
    App::PropertyStringList DatumReferences;
    /// The imported shape objects and the Face/Edge names this item governs
    App::PropertyXLinkSubList References;
    /// "Object.FaceN" names, a subset of References: the faces on the second side of a location dimension (the other
    /// faces of References are its first side). Empty for an imported item (its sides are kept in RawAttributes) and for
    /// every item that has one side
    App::PropertyStringList SecondSide;
    /// Standards rule checks of the authoring tool (PmiAuthoring.checkPmi): one entry per broken rule, "<rule id>: <message>";
    /// empty when none was found. A warning only, nothing in the export depends on it
    App::PropertyStringList RuleWarnings;
    /// Part 21 entity ids (#n) of the faces governed at import, ascending
    App::PropertyIntegerList StepFaceIds;
    /// Every field of the source object as text; enough to rebuild the XCAF object
    App::PropertyMap RawAttributes;
    /// True when the type or a modifier was not recognised
    App::PropertyBool Unmapped;
    /// True when the item could not be attached to any face or edge of the document
    App::PropertyBool Unattached;
    /// What the kernel (OCCT's STEP reader) lost or altered, found by comparing it with the STEP model, one reason per entry
    /// and each starting "lost:" (the file states it, the item lacks it) or "corrected:" (the item was repaired from the file); empty when nothing was found
    App::PropertyStringList KernelLoss;
    /// Name the STEP file gives the dimension or datum entity (for example "diameter"), empty when it could not be found
    App::PropertyString StepEntityName;
    /// The graphic presentation the source file carries for the item (the kernel's presentation compound: leader and
    /// extension lines, text outlines, in the frame of the shape the item annotates); null if the file has none.
    /// Written back on export (CAx-IF PMI recommended practice, "Representation and Presentation of PMI", tessellated annotation)
    Part::PropertyPartShape Presentation;

    /// Global placement of the governed object (the first of References) when the item was imported. The presentation
    /// is stored in the coordinates of the source file, so the display moves it by the object's current global
    /// placement times the inverse of this one; identity when the item governs nothing or the document predates it
    App::PropertyPlacement ReferencePlacement;

    /// A document saved before ReferencePlacement existed has no such property: on restore it is set to the governed
    /// object's current global placement, so the presentation draws in place
    void onDocumentRestored() override;

    const char* getViewProviderName() const override
    {
        return "PartGui::ViewProviderPmiItem";
    }

protected:
    void onChanged(const App::Property* prop) override;

private:
    bool referencePlacementRestored {false};
};

/// A size or location dimension
class PartExport PmiDimension: public PmiItem
{
    PROPERTY_HEADER_WITH_OVERRIDE(Part::PmiDimension);

public:
    PmiDimension();
    /// ISO 286 class of tolerance such as "H7" (empty if none); hole is capital, shaft is lower case
    App::PropertyString ToleranceClass;
};

/// A geometric tolerance with its datum references
class PartExport PmiTolerance: public PmiItem
{
    PROPERTY_HEADER_WITH_OVERRIDE(Part::PmiTolerance);

public:
    PmiTolerance();
    /// "", "Diameter" or "SphericalDiameter": the shape of the tolerance zone value
    App::PropertyString ZoneShape;
    /// "", "Projected", "Runout" or "NonUniform"
    App::PropertyString ZoneModifier;
    /// The value that goes with the zone modifier (projected zone height, for example)
    App::PropertyFloat ZoneModifierValue;
    /// One datum object per datum reference, in the order of DatumReferences
    App::PropertyLinkList Datums;
};

/// A datum, or a datum target; one object per datum reference use, as OCCT stores them
class PartExport PmiDatum: public PmiItem
{
    PROPERTY_HEADER_WITH_OVERRIDE(Part::PmiDatum);

public:
    PmiDatum();
    /// The datum letter
    App::PropertyString Letter;
    /// Place of the datum in the reference frame of its tolerance (1 is primary)
    App::PropertyInteger Position;
    App::PropertyBool IsTarget;
};

/// A graphic-only annotation: a presentation (callout) the file carries with no semantic dimension, tolerance or
/// datum behind it (XCAF types CommonLabel and DimensionPresentation). It governs nothing and is written back as it was read
class PartExport PmiAnnotation: public PmiItem
{
    PROPERTY_HEADER_WITH_OVERRIDE(Part::PmiAnnotation);

public:
    PmiAnnotation();
};

/// The group that holds all PMI items of one import
class PartExport PmiGroup: public App::DocumentObjectGroup
{
    PROPERTY_HEADER_WITH_OVERRIDE(Part::PmiGroup);

public:
    PmiGroup() = default;

    const char* getViewProviderName() const override
    {
        return "PartGui::ViewProviderPmiGroup";
    }
};

}  // namespace Part

#endif  // PART_PMIFEATURE_H
