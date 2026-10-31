# SPDX-License-Identifier: LGPL-2.1-or-later
# SheetMetal: LGPL subset of shaise/FreeCAD_SheetMetal (cad-fork G07 slice 1). Python, data and
# resources only. Every .py, .md, .csv, .ui, .svg and .dxf file under this directory is copied, so
# sub-packages can add files without editing this list.

file(GLOB_RECURSE SHEETMETAL_SRCS RELATIVE "${CMAKE_CURRENT_SOURCE_DIR}" CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/*.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/*.csv"
    "${CMAKE_CURRENT_SOURCE_DIR}/*.ui"
    "${CMAKE_CURRENT_SOURCE_DIR}/*.svg"
    "${CMAKE_CURRENT_SOURCE_DIR}/*.dxf"
    "${CMAKE_CURRENT_SOURCE_DIR}/*.json"
)
SOURCE_GROUP("" FILES ${SHEETMETAL_SRCS})

ADD_CUSTOM_TARGET(SheetMetal ALL
    SOURCES ${SHEETMETAL_SRCS}
)

fc_copy_sources(SheetMetal "${CMAKE_BINARY_DIR}/Mod/SheetMetal" ${SHEETMETAL_SRCS})

foreach(_f ${SHEETMETAL_SRCS})
    get_filename_component(_d "${_f}" DIRECTORY)
    INSTALL(FILES "${_f}" DESTINATION "Mod/SheetMetal/${_d}")
endforeach()
