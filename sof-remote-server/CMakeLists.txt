# SPDX-License-Identifier: (GPL-2.0-only OR BSD-3-Clause)
cmake_minimum_required(VERSION 3.19)

add_executable(sof-remote sof_remote_cli.c)
target_include_directories(sof-remote PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(sof-remote PRIVATE pthread m)

find_library(ASOUND_LIB NAMES asound libasound.so.2)
get_filename_component(SOF_WORK_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
set(ALSA_INC "${SOF_WORK_DIR}/alsa-lib/include" CACHE PATH "Path to alsa-lib include directory")
set(TINYALSA_INC "${SOF_WORK_DIR}/tinyalsa/include" CACHE PATH "Path to tinyalsa include directory")
set(TINYALSA_LIB "${SOF_WORK_DIR}/tinyalsa/src/libtinyalsa.a" CACHE FILEPATH "Path to libtinyalsa.a")
set(TINYCOMPR_INC "${SOF_WORK_DIR}/tinycompress/include" CACHE PATH "Path to tinycompress include directory")
set(TINYCOMPR_OBJ1 "${SOF_WORK_DIR}/tinycompress/src/lib/.libs/libtinycompress_la-compress.o" CACHE FILEPATH "Path to tinycompress compress.o")
set(TINYCOMPR_OBJ2 "${SOF_WORK_DIR}/tinycompress/src/lib/.libs/libtinycompress_la-compress_hw.o" CACHE FILEPATH "Path to tinycompress compress_hw.o")

if(ASOUND_LIB AND EXISTS "${TINYALSA_LIB}" AND EXISTS "${TINYCOMPR_OBJ1}")
	add_executable(sof_remote_server
		sof_remote_server.c
		audio_backend_alsa.c
		audio_backend_tinyalsa.c
		audio_backend_compress.c
		dapm_inspector.c
		${TINYCOMPR_OBJ1}
		${TINYCOMPR_OBJ2}
	)
	target_include_directories(sof_remote_server PRIVATE
		${CMAKE_CURRENT_SOURCE_DIR}
		${ALSA_INC}
		${TINYALSA_INC}
		${TINYCOMPR_INC}
	)
	target_link_libraries(sof_remote_server PRIVATE
		${TINYALSA_LIB}
		${ASOUND_LIB}
		dl
		pthread
		m
	)
endif()
