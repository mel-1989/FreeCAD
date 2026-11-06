// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <FCGlobal.h>

#ifndef Sketch3DExport
# ifdef Sketch3D_EXPORTS
#  define Sketch3DExport FREECAD_DECL_EXPORT
# else
#  define Sketch3DExport FREECAD_DECL_IMPORT
# endif
#endif
