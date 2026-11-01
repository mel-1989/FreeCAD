# SPDX-License-Identifier: LGPL-2.1-or-later
# Weldment: structural members, corner treatment and trim, gussets, end caps, cut list (cad-fork G07 slices 5a, 5b).
# Python and data only. Every .py, .csv and .md file under this directory is copied, which covers
# Profiles.py, WTests/testProfiles.py and profiles/*.csv without listing them.

file(GLOB_RECURSE WELDMENT_SRCS RELATIVE "${CMAKE_CURRENT_SOURCE_DIR}" CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/*.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/*.csv"
    "${CMAKE_CURRENT_SOURCE_DIR}/*.md"
    "${CMAKE_CURRENT_SOURCE_DIR}/*.svg"
)
SOURCE_GROUP("" FILES ${WELDMENT_SRCS})

ADD_CUSTOM_TARGET(Weldment ALL
    SOURCES ${WELDMENT_SRCS}
)

fc_copy_sources(Weldment "${CMAKE_BINARY_DIR}/Mod/Weldment" ${WELDMENT_SRCS})

foreach(_f ${WELDMENT_SRCS})
    get_filename_component(_d "${_f}" DIRECTORY)
    INSTALL(FILES "${_f}" DESTINATION "Mod/Weldment/${_d}")
endforeach()
