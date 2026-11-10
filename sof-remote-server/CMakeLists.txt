# SPDX-License-Identifier: (GPL-2.0-only OR BSD-3-Clause)
cmake_minimum_required(VERSION 3.19)

add_executable(sof-remote sof_remote_cli.c)
target_include_directories(sof-remote PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(sof-remote PRIVATE pthread m)
install(TARGETS sof-remote DESTINATION bin)

find_library(ASOUND_LIB NAMES asound libasound.so.2)
get_filename_component(SOF_WORK_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
set(ALSA_INC "${SOF_WORK_DIR}/alsa-lib/include" CACHE PATH "Path to alsa-lib include directory")
set(TINYALSA_INC "${SOF_WORK_DIR}/tinyalsa/include" CACHE PATH "Path to tinyalsa include directory")
set(TINYALSA_LIB "${SOF_WORK_DIR}/tinyalsa/src/libtinyalsa.a" CACHE FILEPATH "Path to libtinyalsa.a")
set(TINYCOMPR_INC "${SOF_WORK_DIR}/tinycompress/include" CACHE PATH "Path to tinycompress include directory")
set(TINYCOMPR_OBJ1 "${SOF_WORK_DIR}/tinycompress/src/lib/.libs/libtinycompress_la-compress.o" CACHE FILEPATH "Path to tinycompress compress.o")
set(TINYCOMPR_OBJ2 "${SOF_WORK_DIR}/tinycompress/src/lib/.libs/libtinycompress_la-compress_hw.o" CACHE FILEPATH "Path to tinycompress compress_hw.o")

if(ASOUND_LIB OR ANDROID)
	add_executable(sof_remote_server
		sof_remote_server.c
		audio_backend_alsa.c
		audio_backend_tinyalsa.c
		audio_backend_compress.c
		dapm_inspector.c
	)
	target_include_directories(sof_remote_server PRIVATE
		${CMAKE_CURRENT_SOURCE_DIR}
	)
	if(ANDROID)
		target_compile_definitions(sof_remote_server PRIVATE NO_ALSA)
		target_link_libraries(sof_remote_server PRIVATE log dl m)
	else()
		if(EXISTS "${ALSA_INC}/alsa/asoundlib.h")
			target_include_directories(sof_remote_server PRIVATE ${ALSA_INC})
		endif()
		target_link_libraries(sof_remote_server PRIVATE
			${ASOUND_LIB}
			dl
			pthread
			m
		)
	endif()
	if(EXISTS "${TINYALSA_LIB}" AND (EXISTS "${TINYALSA_INC}/tinyalsa/pcm.h" OR EXISTS "${TINYALSA_INC}/tinyalsa/asoundlib.h"))
		target_compile_definitions(sof_remote_server PRIVATE HAVE_TINYALSA)
		target_include_directories(sof_remote_server PRIVATE ${TINYALSA_INC})
		target_link_libraries(sof_remote_server PRIVATE ${TINYALSA_LIB})
	endif()
	if(EXISTS "${TINYCOMPR_OBJ1}" AND EXISTS "${TINYCOMPR_INC}/tinycompress/tinycompress.h")
		target_compile_definitions(sof_remote_server PRIVATE HAVE_TINYCOMPRESS)
		target_include_directories(sof_remote_server PRIVATE ${TINYCOMPR_INC})
		target_sources(sof_remote_server PRIVATE ${TINYCOMPR_OBJ1} ${TINYCOMPR_OBJ2})
	endif()
	install(TARGETS sof_remote_server DESTINATION bin)
endif()
