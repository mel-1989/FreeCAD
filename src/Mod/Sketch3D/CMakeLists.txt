# SPDX-License-Identifier: LGPL-2.1-or-later
# Sketch3D: 3D sketch relations on the PlaneGCS system (cad-fork G10 slice 6a).

add_subdirectory(App)

set(Sketch3D_Scripts
    Init.py
    TestSketch3DApp.py
)

set(Sketch3D_TestScripts
    Sketch3DTests/__init__.py
    Sketch3DTests/Sketch3DCorpus.py
    Sketch3DTests/TestSketch3DSolver.py
    Sketch3DTests/Sketch3DBench.py
    Sketch3DTests/Sketch3DMutants.py
)

add_custom_target(Sketch3DScripts ALL
    SOURCES ${Sketch3D_Scripts} ${Sketch3D_TestScripts}
)

fc_target_copy_resource(Sketch3DScripts
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_BINARY_DIR}/Mod/Sketch3D
    ${Sketch3D_Scripts}
    ${Sketch3D_TestScripts}
)

INSTALL(
    FILES
        ${Sketch3D_Scripts}
    DESTINATION
        Mod/Sketch3D
)

INSTALL(
    FILES
        ${Sketch3D_TestScripts}
    DESTINATION
        Mod/Sketch3D/Sketch3DTests
)
