# SPDX-License-Identifier: LGPL-2.1-or-later

# Sketch3D adds constraint classes on top of the Sketcher library's GCS::System. It links
# Sketcher for System, Constraint and the solvers only; nothing of the 2D sketch object is used.
set(Sketch3D_LIBS
    Sketcher
    FreeCADApp
)

SET(Sketch3D_SRCS
    AppSketch3D.cpp
    Relations.cpp
    Relations.h
    Solve3D.cpp
    Solve3D.h
    Sketch3DGlobal.h
    Sketch3D.module.pyi
)

add_library(Sketch3D SHARED ${Sketch3D_SRCS})

target_include_directories(
    Sketch3D
    PRIVATE
    ${CMAKE_BINARY_DIR}
    ${CMAKE_BINARY_DIR}/src
    ${CMAKE_SOURCE_DIR}/src
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_BINARY_DIR}
)

target_include_directories(
    Sketch3D
    SYSTEM
    PRIVATE
    ${EIGEN3_INCLUDE_DIR}
)

target_link_libraries(Sketch3D ${Sketch3D_LIBS})
target_compile_warn_error(Sketch3D)

SET_BIN_DIR(Sketch3D)
SET_PYTHON_PREFIX_SUFFIX(Sketch3D)

INSTALL(TARGETS Sketch3D DESTINATION ${CMAKE_INSTALL_LIBDIR})
