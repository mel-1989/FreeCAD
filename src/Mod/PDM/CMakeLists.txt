# SPDX-License-Identifier: LGPL-2.1-or-later
# PDM: the cad-fork local vault (headless, Python only). Every .py and .json file
# under this directory is copied, so sub-packages can add files without
# editing this list.

file(GLOB_RECURSE PDM_SRCS RELATIVE "${CMAKE_CURRENT_SOURCE_DIR}" CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/*.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/*.json"
)
SOURCE_GROUP("" FILES ${PDM_SRCS})

ADD_CUSTOM_TARGET(PDM ALL
    SOURCES ${PDM_SRCS}
)

fc_copy_sources(PDM "${CMAKE_BINARY_DIR}/Mod/PDM" ${PDM_SRCS})

foreach(_f ${PDM_SRCS})
    get_filename_component(_d "${_f}" DIRECTORY)
    INSTALL(FILES "${_f}" DESTINATION "Mod/PDM/${_d}")
endforeach()
