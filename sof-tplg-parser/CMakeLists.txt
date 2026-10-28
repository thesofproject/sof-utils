# SPDX-License-Identifier: BSD-3-Clause

cmake_minimum_required(VERSION 3.13)

project(SOF_TPLG_PARSER C)

if(NOT SOF_SRC_DIR)
	if(DEFINED ENV{SOF_SRC_DIR} AND EXISTS "$ENV{SOF_SRC_DIR}/CMakeLists.txt")
		set(SOF_SRC_DIR "$ENV{SOF_SRC_DIR}")
	elseif(DEFINED ENV{SOF_ROOT} AND EXISTS "$ENV{SOF_ROOT}/CMakeLists.txt")
		set(SOF_SRC_DIR "$ENV{SOF_ROOT}")
	else()
		execute_process(
			COMMAND west list -f "{abspath}" sof
			WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
			OUTPUT_VARIABLE _west_sof_dir
			OUTPUT_STRIP_TRAILING_WHITESPACE
			ERROR_QUIET
		)
		if(_west_sof_dir AND EXISTS "${_west_sof_dir}/CMakeLists.txt")
			set(SOF_SRC_DIR "${_west_sof_dir}")
		endif()
	endif()
endif()

include("${SOF_SRC_DIR}/scripts/cmake/misc.cmake")
include(CheckCCompilerFlag)

if(EXISTS "/usr/include/alsa/sound/uapi/asoc.h")
	set(default_asoc_h "/usr/include/alsa/sound/uapi/asoc.h")
else()
	set(default_asoc_h "/usr/include/sound/asoc.h")
endif()

add_library(sof_tplg_parser STATIC "")

if(CONFIG_IPC4)
	set(tplg_ipc CONFIG_IPC_MAJOR_4)
else()
	set(tplg_ipc CONFIG_IPC_MAJOR_3)
endif()

target_sources(sof_tplg_parser PRIVATE
	tokens.c
	process.c
	control.c
	pcm.c
	pga.c
	mixer.c
	pipeline.c
	host.c
	dai.c
	asrc.c
	src.c
	buffer.c
	graph.c
	object.c
	audio_formats.c
)

sof_append_relative_path_definitions(sof_tplg_parser)

target_include_directories(sof_tplg_parser PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_include_directories(sof_tplg_parser PRIVATE ${SOF_SRC_DIR}/src/include)
target_include_directories(sof_tplg_parser PRIVATE ${SOF_SRC_DIR}/src/audio)
target_include_directories(sof_tplg_parser PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../sof-testbench/arch/host/include)
target_include_directories(sof_tplg_parser PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../sof-testbench/platform/library/include)
target_include_directories(sof_tplg_parser PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../sof-testbench/arch/host/posix/include)
target_include_directories(sof_tplg_parser PRIVATE ${SOF_SRC_DIR}/posix/include)

# Configuration time, make copy
configure_file(${default_asoc_h} ${CMAKE_CURRENT_BINARY_DIR}/include/alsa/sound/asoc.h)

# Build time
target_include_directories(sof_tplg_parser PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/include")

check_c_compiler_flag(-Wimplicit-fallthrough supports_implicit_fallthrough)
if(supports_implicit_fallthrough)
	set(implicit_fallthrough -Wimplicit-fallthrough)
endif()

target_compile_options(sof_tplg_parser PRIVATE
	-g -O -Wall -Werror -fPIC -DPIC
	-Wmissing-prototypes ${implicit_fallthrough}
	-DCONFIG_LIBRARY -D${tplg_ipc})
if(CMAKE_C_COMPILER MATCHES "xt-|xtensa-")
	target_compile_options(sof_tplg_parser PRIVATE
		-U__INT32_TYPE__ -D__INT32_TYPE__=int
		-U__UINT32_TYPE__ "-D__UINT32_TYPE__=unsigned int"
		-Wno-format)
endif()

target_link_libraries(sof_tplg_parser PRIVATE -lm)

if(TARGET sof_public_headers)
	target_link_libraries(sof_tplg_parser PRIVATE sof_public_headers)
else()
	set(UUID_REG_H "${PROJECT_BINARY_DIR}/include/uuid-registry.h")
	file(MAKE_DIRECTORY "${PROJECT_BINARY_DIR}/include")
	add_custom_command(
		OUTPUT ${UUID_REG_H}
		COMMAND ${SOF_SRC_DIR}/scripts/gen-uuid-reg.py
			${SOF_SRC_DIR}/uuid-registry.txt
			${UUID_REG_H}
	)
	add_custom_target(tplg_uuid_reg_h DEPENDS ${UUID_REG_H})
	add_dependencies(sof_tplg_parser tplg_uuid_reg_h)
endif()

install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/include/tplg_parser
	DESTINATION include
	PATTERN "*.h"
)

install(TARGETS sof_tplg_parser DESTINATION lib)
