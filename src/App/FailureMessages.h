// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Readable recompute-failure messages (cad-fork G08a, slice 1).         *
 *                                                                         *
 *   Maps the raw text a failed recompute stores on a feature (a Python    *
 *   exception message, an OCCT message, a Part/PartDesign/Sketcher text)  *
 *   to one sentence that names the feature and says what to change.       *
 *   Display only: the stored error text, the object state and saved       *
 *   documents are never changed by this.                                  *
 ***************************************************************************/

#pragma once

#include <string>

#include <FCGlobal.h>

namespace App
{

class DocumentObject;

/// A readable failure message and the catalogue key that produced it.
struct FailureMessage
{
    /// Catalogue key such as "fillet.radius"; "generic" for text with no entry.
    std::string key;
    /// One sentence naming the feature label and what to change.
    std::string sentence;
};

/**
 * Describe the failure `raw` of `obj` (raw is `Document::getErrorDescription(obj)`).
 * Known texts go through the built-in catalogue (a pure C++ table, so it works without Python);
 * unknown texts fall back to the first line of the raw text plus the feature label.
 * Never throws, never returns an empty sentence.
 */
AppExport FailureMessage describeFailure(const DocumentObject* obj, const std::string& raw);

/// Same, for a label and TypeId name instead of an object (used by tests and tools).
AppExport FailureMessage describeFailure(
    const std::string& label,
    const std::string& typeName,
    const std::string& raw
);

}  // namespace App
