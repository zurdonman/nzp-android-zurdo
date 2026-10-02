# ============================================================================
#  Android.mk -- Nazi Zombies: Portable (VRIL engine) para Android / arm64-v8a
# ============================================================================
#
#  Construye libmain.so, que contiene TODO el motor:
#
#      - nucleo del motor      : source/*.c, menu/, qcvm/, render/, tests/
#      - plataforma Android    : source/platform/android/*.c        (copia de sdl)
#      - renderer GLQUAKE      : source/platform/android/gl/*.c     (+ gl_gles.c)
#      - SDL2_mixer 2.8.2      : estatico, solo WAV + MP3 (minimp3 embebido)
#
#  SDL2 NO se recompila aqui: se enlaza contra la libreria COMPARTIDA
#  libSDL2.so ya construida con ndk-build en sdl2-src/SDL-release-2.32.10 y
#  copiada a source/platform/android/prebuilt/arm64-v8a/.
#
#  GLES: GLES 1.1 no tiene modo inmediato. source/platform/android/gl/gl_gles.c
#  implementa ese subconjunto (18 funciones) sobre vertex arrays. Ver gl_gles.h.
#
#  main(): SDL_main.h redefine main -> SDL_main cuando __ANDROID__ esta definido,
#  y SDLActivity.java invoca "SDL_main" dentro de libmain.so mediante
#  nativeRunMain(). Por eso NO hace falta el modulo SDL2_main.
# ============================================================================

LOCAL_PATH := $(call my-dir)

# ---------------------------------------------------------------------------
# Rutas del proyecto
#
# OJO: con NDK_PROJECT_PATH=. la macro my-dir devuelve una ruta RELATIVA ("jni").
# ndk-build resuelve despues LOCAL_SRC_FILES como $(LOCAL_PATH)/$1, lo que
# duplicaria el prefijo ("jni/jni/...") y make no encontraria los fuentes.
# Forzamos abspath para que todo el arbol use rutas absolutas.
# ---------------------------------------------------------------------------
LOCAL_PATH := $(abspath $(call my-dir))

PLATFORM_DIR := $(LOCAL_PATH)/..
VRIL_ROOT    := $(PLATFORM_DIR)/../../..
NZP_ROOT     := $(VRIL_ROOT)/..
ENGINE_BUILD := $(VRIL_ROOT)/build/android
SDL2_ROOT    := $(NZP_ROOT)/sdl2-src/SDL-release-2.32.10
SDL_MIXER    := $(NZP_ROOT)/sdl2-src/SDL_mixer-release-2.8.2

# ---------------------------------------------------------------------------
# 1) SDL2 -- prebuilt compartido (libSDL2.so, 1.62 MB, arm64-v8a)
#    ndk-build resuelve LOCAL_SRC_FILES relativo a LOCAL_PATH (jni/), por eso
#    la ruta va con "../prebuilt/..." y NO con la variable absoluta
#    PLATFORM_DIR (produce "jni/jni/../prebuilt/..." y aborta).
# ---------------------------------------------------------------------------
include $(CLEAR_VARS)

LOCAL_MODULE := SDL2
LOCAL_SRC_FILES := ../prebuilt/arm64-v8a/libSDL2.so
LOCAL_EXPORT_C_INCLUDES := $(SDL2_ROOT)/include

include $(PREBUILT_SHARED_LIBRARY)

# ---------------------------------------------------------------------------
# 2) SDL2_mixer 2.8.2 -- estatico
# ---------------------------------------------------------------------------
include $(CLEAR_VARS)

LOCAL_MODULE := SDL2_mixer
LOCAL_MODULE_FILENAME := libSDL2_mixer

LOCAL_C_INCLUDES := \
	$(SDL_MIXER)/include \
	$(SDL_MIXER)/src \
	$(SDL_MIXER)/src/codecs \
	$(SDL2_ROOT)/include

# Decodificadores activos. Sin estas macros music_wav.c/music_minimp3.c
# compilan VACIOS (#ifdef) y Mix_Init(MIX_INIT_MP3) falla en runtime con
# "MP3 support not available" -> Sys_Error en Music_Init. minimp3 es
# header-only: no hace falta ningun external/.
LOCAL_CFLAGS += -DMUSIC_WAV -DMUSIC_MP3_MINIMP3

# Solo los decodificadores que el juego usa. El resto de music_*.c estan
# envueltos en #ifdef MUSIC_* y se quedan fuera (ver Application.mk).
LOCAL_SRC_FILES := \
	$(SDL_MIXER)/src/mixer.c \
	$(SDL_MIXER)/src/music.c \
	$(SDL_MIXER)/src/utils.c \
	$(SDL_MIXER)/src/effects_internal.c \
	$(SDL_MIXER)/src/effect_position.c \
	$(SDL_MIXER)/src/effect_stereoreverse.c \
	$(SDL_MIXER)/src/codecs/load_aiff.c \
	$(SDL_MIXER)/src/codecs/load_voc.c \
	$(SDL_MIXER)/src/codecs/mp3utils.c \
	$(SDL_MIXER)/src/codecs/music_wav.c \
	$(SDL_MIXER)/src/codecs/music_minimp3.c \
	$(SDL_MIXER)/src/codecs/remap_channels.c

include $(BUILD_STATIC_LIBRARY)

# ---------------------------------------------------------------------------
# 3) Motor NZ:P  ->  libmain.so
# ---------------------------------------------------------------------------
include $(CLEAR_VARS)

LOCAL_MODULE := main
LOCAL_MODULE_FILENAME := libmain

# ---- Nucleo del motor (misma lista que Makefile.sdl) ----------------------
CORE_SRC := \
	$(wildcard $(VRIL_ROOT)/source/*.c) \
	$(wildcard $(VRIL_ROOT)/source/menu/*.c) \
	$(wildcard $(VRIL_ROOT)/source/qcvm/*.c) \
	$(wildcard $(VRIL_ROOT)/source/render/*.c) \
	$(wildcard $(VRIL_ROOT)/source/tests/*.c)

# ---- Plataforma Android + renderer GLQUAKE + VR OpenXR --------------------
PLATFORM_SRC := \
	$(wildcard $(PLATFORM_DIR)/*.c) \
	$(wildcard $(PLATFORM_DIR)/gl/*.c) \
	$(wildcard $(PLATFORM_DIR)/vr/*.c)

LOCAL_SRC_FILES := $(CORE_SRC) $(PLATFORM_SRC)

LOCAL_C_INCLUDES := \
	$(VRIL_ROOT)/source \
	$(PLATFORM_DIR) \
	$(PLATFORM_DIR)/gl \
	$(PLATFORM_DIR)/vr \
	$(NZP_ROOT)/third_party/OpenXR-SDK/include \
	$(NZP_ROOT)/third_party/OpenXR-SDK/build-android/include \
	$(ENGINE_BUILD) \
	$(SDL2_ROOT)/include \
	$(SDL_MIXER)/include

LOCAL_SHARED_LIBRARIES := SDL2 openxr_loader
LOCAL_STATIC_LIBRARIES := SDL2_mixer

# GLES 1.1 (Common profile) + audio nativo + log + headers JNI + EGL (VR).
LOCAL_LDLIBS := -lGLESv1_CM -lGLESv2 -lEGL -lOpenSLES -llog -landroid -ldl -lm

include $(BUILD_SHARED_LIBRARY)

# ---------------------------------------------------------------------------
# 4) libopenxr_loader.so prebuilt (third_party/OpenXR-SDK/build-android).
#    Se enlaza contra libmain.so (LOCAL_SHARED_LIBRARIES de arriba) y ndk-build
#    lo copia a libs/arm64-v8a/ para que el APK lo empaquete.
# ---------------------------------------------------------------------------
include $(CLEAR_VARS)
LOCAL_MODULE := openxr_loader
LOCAL_SRC_FILES := $(NZP_ROOT)/third_party/OpenXR-SDK/build-android/src/loader/libopenxr_loader.so
include $(PREBUILT_SHARED_LIBRARY)
