# SPDX-License-Identifier: LGPL-2.1-or-later
# AgentCore: the cad-fork AI-agent layer (headless). Every .py and .json file
# under this directory is copied, so sub-packages can add files without
# editing this list.

file(GLOB_RECURSE AgentCore_SRCS RELATIVE "${CMAKE_CURRENT_SOURCE_DIR}" CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/*.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/*.json"
)
SOURCE_GROUP("" FILES ${AgentCore_SRCS})

ADD_CUSTOM_TARGET(AgentCore ALL
    SOURCES ${AgentCore_SRCS}
)

fc_copy_sources(AgentCore "${CMAKE_BINARY_DIR}/Mod/AgentCore" ${AgentCore_SRCS})

foreach(_f ${AgentCore_SRCS})
    get_filename_component(_d "${_f}" DIRECTORY)
    INSTALL(FILES "${_f}" DESTINATION "Mod/AgentCore/${_d}")
endforeach()
