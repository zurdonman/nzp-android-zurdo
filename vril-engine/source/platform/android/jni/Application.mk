# ============================================================================
#  Application.mk -- configuracion ndk-build de Nazi Zombies: Portable
# ============================================================================
#
#  Uso (desde source/platform/android/):
#
#     %ANDROID_NDK%\ndk-build.cmd NDK_PROJECT_PATH=. \
#            APP_BUILD_SCRIPT=jni/Android.mk NDK_APPLICATION_MK=jni/Application.mk
#
#  Salida: source/platform/android/libs/arm64-v8a/libmain.so
# ============================================================================

# Solo arm64-v8a (dispositivos modernos y Meta Quest al completo).
APP_ABI := arm64-v8a

# minSdk 21 = Android 5.0, el minimo que soporta OpenGL ES 2 y SDL2.
APP_PLATFORM := android-21

APP_OPTIM := release

# Necesario porque SDL2 y SDL2_mixer son C++ en parte (hid.cpp, etc.).
APP_STL := c++_static

# ---------------------------------------------------------------------------
# Definiciones equivalentes a las de Makefile.sdl, adaptadas a Android.
# ---------------------------------------------------------------------------
APP_CFLAGS := \
	-std=gnu99 \
	-O2 \
	-fno-strict-aliasing \
	-DGLQUAKE \
	-DPLATFORM_SDL \
	-DPLATFORM_CONFIRM_IS_ENTER \
	-DPLATFORM_DIRECTORY=android \
	-DPLATFORM_RENDERER=gl \
	-DMAX_AI_COUNT=24 \
	-DPLATFORM_USES_GENERIC_GLYPHS \
	-DPLATFORM_SUPPORTS_HIGH_FRAMERATES \
	-DPLATFORM_SUPPORTS_VIDEO_OPTIONS \
	-DPLATFORM_SUPPORTS_GYRO \
	-DPLATFORM_SUPPORTS_RUMBLE \
	-DPLATFORM_SUPPORTS_LIGHTBAR \
	-DNZP_MENU_COOP \
	-DNZP_VR_OPENXR

# ---------------------------------------------------------------------------
# SDL2_mixer: activar SOLO los decodificadores que se compilan.
# Cualquier music_*.c que se compile sin su MUSIC_* queda vacio, asi que
# definir de mas es inofensivo; definir de menos haria que el codigo no exista.
# minizip/opus/flac/etc. quedan fuera porque sus fuentes no estan en el build.
# ---------------------------------------------------------------------------
APP_CPPFLAGS := \
	-DMUSIC_WAV \
	-DMUSIC_MP3_MINIMP3

# Silenciar los miles de avisos del motor (codigo Quake de 1997 + clang nuevo).
APP_CFLAGS += -Wno-everything

# Los .so del motor se generan ya stripped por ndk-build en release.
APP_STRIP_MODE := --strip-unneeded
