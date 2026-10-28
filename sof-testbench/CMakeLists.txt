# SPDX-License-Identifier: BSD-3-Clause

cmake_minimum_required(VERSION 3.19)

project(SOF_TESTBENCH C ASM)

option(TESTBENCH_XTENSA_SIM "Build single-threaded testbench for Xtensa simulator" OFF)
if(CMAKE_C_COMPILER MATCHES "xt-|xtensa-" OR DEFINED ENV{XTENSA_CORE})
	set(TESTBENCH_XTENSA_SIM ON CACHE BOOL "Build single-threaded testbench for Xtensa simulator" FORCE)
endif()

# Locate SOF firmware source tree (${SOF_SRC_DIR}) via:
# 1. -DSOF_SRC_DIR=/path/to/sof
# 2. $SOF_SRC_DIR or $SOF_ROOT environment variables
# 3. `west list -f "{abspath}" sof` in the current or parent west workspace
# 4. Standard relative sibling paths (../sof, ../../sof)
if(NOT SOF_SRC_DIR)
	if(DEFINED ENV{SOF_SRC_DIR} AND EXISTS "$ENV{SOF_SRC_DIR}/Kconfig")
		set(SOF_SRC_DIR "$ENV{SOF_SRC_DIR}")
	elseif(DEFINED ENV{SOF_ROOT} AND EXISTS "$ENV{SOF_ROOT}/Kconfig")
		set(SOF_SRC_DIR "$ENV{SOF_ROOT}")
	else()
		execute_process(
			COMMAND west list -f "{abspath}" sof
			WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
			OUTPUT_VARIABLE _west_sof_dir
			OUTPUT_STRIP_TRAILING_WHITESPACE
			ERROR_QUIET
		)
		if(_west_sof_dir AND EXISTS "${_west_sof_dir}/Kconfig")
			set(SOF_SRC_DIR "${_west_sof_dir}")
		else()
			foreach(_cand
				"${CMAKE_CURRENT_SOURCE_DIR}/../../sof"
				"${CMAKE_CURRENT_SOURCE_DIR}/../sof"
				"${CMAKE_CURRENT_SOURCE_DIR}/../../sof-arl/sof-testbench-upstream"
				"${CMAKE_CURRENT_SOURCE_DIR}/../../sof-arl/sof"
			)
				if(EXISTS "${_cand}/Kconfig")
					set(SOF_SRC_DIR "${_cand}")
					break()
				endif()
			endforeach()
		endif()
	endif()
endif()

if(NOT SOF_SRC_DIR OR NOT EXISTS "${SOF_SRC_DIR}/Kconfig")
	message(FATAL_ERROR
		"SOF source directory not found. Pass -DSOF_SRC_DIR=/path/to/sof, "
		"set SOF_SRC_DIR in the environment, or run inside a west workspace."
	)
endif()

get_filename_component(SOF_SRC_DIR "${SOF_SRC_DIR}" ABSOLUTE)
set(SOF_SRC_DIR "${SOF_SRC_DIR}" CACHE PATH "Path to SOF firmware source repository" FORCE)
message(STATUS "Building testbench against SOF_SRC_DIR: ${SOF_SRC_DIR}")

set(SOF_UTILS_TESTBENCH_DIR "${CMAKE_CURRENT_SOURCE_DIR}")

include("${SOF_SRC_DIR}/scripts/cmake/misc.cmake")
include(CheckCCompilerFlag)

if(EXISTS "/usr/include/alsa/sound/uapi/asoc.h")
	set(default_asoc_h "/usr/include/alsa/sound/uapi/asoc.h")
else()
	set(default_asoc_h "/usr/include/sound/asoc.h")
endif()

file(READ "${CMAKE_CURRENT_SOURCE_DIR}/arch/host/configs/library_defconfig" library_defconfig)

if(library_defconfig MATCHES "CONFIG_IPC_MAJOR_4=y")
	message(STATUS "Selected IPC4 build for testbench")
	set(testbench "sof-testbench4")
else()
	message(STATUS "Selected IPC3 build for testbench")
	set(testbench "sof-testbench3")
endif()

# Build libsof.a (out-of-tree from ${SOF_SRC_DIR}/src + local host platform)
# and libsof_tplg_parser.a directly in our local build directory
add_subdirectory(sof_lib)
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../sof-tplg-parser ${CMAKE_CURRENT_BINARY_DIR}/sof-tplg-parser)

set(config_h "${CMAKE_CURRENT_BINARY_DIR}/sof_lib/generated/include/autoconfig.h")

add_executable(${testbench}
	testbench.c
	file.c
	utils.c
	utils_ipc3.c
	utils_ipc4.c
	topology_ipc3.c
	topology_ipc4.c
)
if(NOT TESTBENCH_XTENSA_SIM)
	target_sources(${testbench} PRIVATE remote_server.c)
endif()

sof_append_relative_path_definitions(${testbench})

# Ensure generated headers (autoconfig.h, sof_versions.h, uuid-registry.h) exist before compiling testbench sources
add_dependencies(${testbench} genconfig check_version_h uuid_reg_h)

target_include_directories(${testbench} PRIVATE
	${CMAKE_CURRENT_SOURCE_DIR}/include
	${CMAKE_CURRENT_SOURCE_DIR}/arch/host/include
	${CMAKE_CURRENT_SOURCE_DIR}/platform/library/include
	${CMAKE_CURRENT_SOURCE_DIR}/arch/host/posix/include
	${SOF_SRC_DIR}/src/audio
)

# Configuration time, make copy
configure_file(${default_asoc_h} ${CMAKE_CURRENT_BINARY_DIR}/include/alsa/sound/asoc.h)

# Build time
target_include_directories(${testbench} PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/include")

check_c_compiler_flag(-Wimplicit-fallthrough supports_implicit_fallthrough)
if(supports_implicit_fallthrough)
	set(implicit_fallthrough -Wimplicit-fallthrough)
endif()

target_compile_options(${testbench} PRIVATE -g -O3 -Wall -Werror -Wmissing-prototypes
	${implicit_fallthrough} -DCONFIG_LIBRARY "-imacros${config_h}")
if(TESTBENCH_XTENSA_SIM)
	target_compile_definitions(${testbench} PRIVATE CONFIG_XT_SIMULATOR=1)
endif()
if(CMAKE_C_COMPILER MATCHES "xt-|xtensa-")
	target_compile_options(${testbench} PRIVATE
		-U__INT32_TYPE__ -D__INT32_TYPE__=int
		-U__UINT32_TYPE__ "-D__UINT32_TYPE__=unsigned int"
		-Wno-format)
endif()

target_link_libraries(${testbench} PRIVATE
	sof
	sof_tplg_parser
	sof_public_headers
	m
)
if(NOT TESTBENCH_XTENSA_SIM)
	target_link_libraries(${testbench} PRIVATE pthread)
endif()

install(TARGETS ${testbench} DESTINATION bin)

if(NOT TESTBENCH_XTENSA_SIM)
	add_executable(sof-tb-ctl tb_ctl.c)
	target_include_directories(sof-tb-ctl PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
	target_compile_options(sof-tb-ctl PRIVATE -g -O3 -Wall -Werror -Wmissing-prototypes
		${implicit_fallthrough})
	install(TARGETS sof-tb-ctl DESTINATION bin)
endif()

if(NOT TESTBENCH_XTENSA_SIM)
	enable_testing()
	add_test(NAME testbench_tier1_modules
		 COMMAND ${CMAKE_COMMAND} -E env
			 SOF_SRC_DIR=${SOF_SRC_DIR}
			 TB_BIN=$<TARGET_FILE:${testbench}>
			 TB_CTL_BIN=$<TARGET_FILE:sof-tb-ctl>
			 ${CMAKE_CURRENT_SOURCE_DIR}/run_test_suite.sh --tier 1)
	add_test(NAME testbench_tier2_complex_dags
		 COMMAND ${CMAKE_COMMAND} -E env
			 SOF_SRC_DIR=${SOF_SRC_DIR}
			 TB_BIN=$<TARGET_FILE:${testbench}>
			 TB_CTL_BIN=$<TARGET_FILE:sof-tb-ctl>
			 ${CMAKE_CURRENT_SOURCE_DIR}/run_test_suite.sh --tier 2)
	add_test(NAME testbench_tier3_ll_dp
		 COMMAND ${CMAKE_COMMAND} -E env
			 SOF_SRC_DIR=${SOF_SRC_DIR}
			 TB_BIN=$<TARGET_FILE:${testbench}>
			 TB_CTL_BIN=$<TARGET_FILE:sof-tb-ctl>
			 ${CMAKE_CURRENT_SOURCE_DIR}/run_test_suite.sh --tier 3)
	add_test(NAME testbench_tier4_multicore
		 COMMAND ${CMAKE_COMMAND} -E env
			 SOF_SRC_DIR=${SOF_SRC_DIR}
			 TB_BIN=$<TARGET_FILE:${testbench}>
			 TB_CTL_BIN=$<TARGET_FILE:sof-tb-ctl>
			 ${CMAKE_CURRENT_SOURCE_DIR}/run_test_suite.sh --tier 4)
	add_test(NAME testbench_tier5_controls_remote
		 COMMAND ${CMAKE_COMMAND} -E env
			 SOF_SRC_DIR=${SOF_SRC_DIR}
			 TB_BIN=$<TARGET_FILE:${testbench}>
			 TB_CTL_BIN=$<TARGET_FILE:sof-tb-ctl>
			 ${CMAKE_CURRENT_SOURCE_DIR}/run_test_suite.sh --tier 5)
endif()
