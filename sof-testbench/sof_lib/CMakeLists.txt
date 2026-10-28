# SPDX-License-Identifier: BSD-3-Clause
#
# Local out-of-tree CMake build for libsof.a inside sof-utils.
# Compiles sof-utils's local host platform runtime (arch/host, platform/library)
# together with SOF firmware modules, pipeline, and IPC from ${SOF_SRC_DIR}/src/
# into the local sof-utils build directory without modifying ${SOF_SRC_DIR}.

cmake_minimum_required(VERSION 3.19)

find_package(Python3 COMPONENTS Interpreter)
set(PYTHON3 "${Python3_EXECUTABLE}")

set(ARCH host)
set(CONFIG_LIBRARY ON)

project(SOF C ASM)

# Point SOF CMake scripts and sub-CMakeLists at the external SOF source repository
# while keeping all generated files and object files in ${PROJECT_BINARY_DIR}.
set(PROJECT_SOURCE_DIR "${SOF_SRC_DIR}")
set(CMAKE_SOURCE_DIR "${SOF_SRC_DIR}")
set(SOF_ROOT_SOURCE_DIRECTORY "${SOF_SRC_DIR}")
set(SOF_ROOT_BINARY_DIRECTORY "${PROJECT_BINARY_DIR}")
set(RIMAGE_TOP "${SOF_SRC_DIR}/tools/rimage")

include("${SOF_SRC_DIR}/scripts/cmake/misc.cmake")

set(CMAKE_ASM_FLAGS -DASSEMBLY)

add_library(sof_public_headers INTERFACE)
target_include_directories(sof_public_headers INTERFACE ${SOF_SRC_DIR}/src/include)

add_library(sof_options INTERFACE)
target_link_libraries(sof_options INTERFACE sof_public_headers)
target_compile_definitions(sof_options INTERFACE BLD_COUNTERS=0)

add_library(sof_static_libraries INTERFACE)

execute_process(
	COMMAND ${CMAKE_C_COMPILER} --version
	OUTPUT_VARIABLE cc_version_output
	OUTPUT_STRIP_TRAILING_WHITESPACE
	ERROR_QUIET
)

string(REGEX MATCH "^[^\r\n]+" CC_VERSION_TEXT "${cc_version_output}")
if(NOT CC_VERSION_TEXT)
	set(CC_VERSION_TEXT 0)
endif()

set(GENERATED_DIRECTORY ${PROJECT_BINARY_DIR}/generated)
file(MAKE_DIRECTORY ${GENERATED_DIRECTORY}/include)

set(DOT_CONFIG_PATH ${GENERATED_DIRECTORY}/.config)
set(CONFIG_H_PATH ${GENERATED_DIRECTORY}/include/autoconfig.h CACHE
	PATH "Path to kconfig's .h output file" FORCE)
set(VERSION_H_PATH ${GENERATED_DIRECTORY}/include/sof_versions.h)

# Use sof-utils's local library_defconfig by default
if(NOT INIT_CONFIG_found)
	set(INIT_CONFIG "../arch/host/configs/library_defconfig" CACHE STRING "Initial .config file" FORCE)
endif()

include("${SOF_SRC_DIR}/scripts/cmake/version.cmake")
include("${SOF_SRC_DIR}/scripts/cmake/kconfig.cmake")
read_kconfig_config(${DOT_CONFIG_PATH})

set_property(DIRECTORY APPEND
	PROPERTY CMAKE_CONFIGURE_DEPENDS ${DOT_CONFIG_PATH})

add_dependencies(sof_public_headers genconfig check_version_h)
target_include_directories(sof_public_headers INTERFACE ${GENERATED_DIRECTORY}/include)

include("${SOF_SRC_DIR}/scripts/cmake/uuid-registry.cmake")

add_library(sof STATIC "")
target_link_libraries(sof PRIVATE sof_options)
target_link_libraries(sof PRIVATE sof_static_libraries)

# Local host architecture & platform runtime in sof-utils/testbench/
add_subdirectory("${SOF_UTILS_TESTBENCH_DIR}/arch/host" "${PROJECT_BINARY_DIR}/src/arch/host")
add_subdirectory("${SOF_UTILS_TESTBENCH_DIR}/platform/library" "${PROJECT_BINARY_DIR}/src/platform/library")

# Firmware modules, pipeline, buffers, IPC, and math from ${SOF_SRC_DIR}/src/
add_subdirectory("${SOF_SRC_DIR}/src/ipc" "${PROJECT_BINARY_DIR}/src/ipc")
add_subdirectory("${SOF_SRC_DIR}/src/audio" "${PROJECT_BINARY_DIR}/src/audio")
add_subdirectory("${SOF_SRC_DIR}/src/lib" "${PROJECT_BINARY_DIR}/src/lib")
add_subdirectory("${SOF_SRC_DIR}/src/math" "${PROJECT_BINARY_DIR}/src/math")
add_subdirectory("${SOF_SRC_DIR}/src/module" "${PROJECT_BINARY_DIR}/src/module")
if(CONFIG_SAMPLES)
	add_subdirectory("${SOF_SRC_DIR}/src/samples" "${PROJECT_BINARY_DIR}/src/samples")
endif()
add_subdirectory("${SOF_SRC_DIR}/src/drivers" "${PROJECT_BINARY_DIR}/src/drivers")
if(CONFIG_TRACE)
	add_subdirectory("${SOF_SRC_DIR}/src/trace" "${PROJECT_BINARY_DIR}/src/trace")
endif()

sof_append_relative_path_definitions(sof)
