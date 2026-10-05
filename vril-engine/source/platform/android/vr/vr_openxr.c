/*
Copyright (C) 2025-2026 NZ:P Team

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
*/
// vr_openxr.c -- NZ:P Android (zurdo): capa VR OpenXR (PASO 2 del plan).
//
// Ciclo de vida OpenXR completo pero sin render estereo todavia:
//
//   VR_Init          xrInitializeLoaderKHR (JNI de SDL) -> xrCreateInstance
//                    -> xrGetSystem (HMD) -> espacios LOCAL/VIEW
//   VR_BeginFrame    xrCreateSession GLES (1a vez, con EGL ya actual) ->
//                    xrBeginSession -> xrWaitFrame -> xrBeginFrame ->
//                    xrLocateViews (poses de la cabeza)
//   VR_EndFrame      xrEndFrame (sin capas: HMD en negro; el render estereo
//                    real llega en el paso 3)
//
// Todo falla SUAVE: sin runtime OpenXR el juego arranca 2D exactamente igual.
// Referencias: third_party/OpenXR-SDK (headers + hello_xr) y
// third_party/quakevr (patrones de integracion VR en Quake).
#include "vr_openxr.h"

#ifdef NZP_VR_OPENXR

/* openxr_platform.h NO incluye por si solo los headers de plataforma: hay que
 * incluir jni.h y EGL ANTES, y definir estas macros para que declare los
 * tipos Android/GLES (patron de hello_xr). */
#include <jni.h>
#include <EGL/egl.h>

/* GLES 1.1 + OES_framebuffer_object: necesario para el render estereo (paso
 * 3). Cada imagen de swapchain OpenXR es una textura GL; para dibujar en ella
 * con el pipeline fijo hace falta un FBO por imagen. glext.h declara las
 * funciones *_OES; openxr_platform.h solo trae <GLES/gl.h>. */
#include <GLES/gl.h>
#include <GLES/glext.h>

#ifndef GL_DEPTH_COMPONENT24_OES
#define GL_DEPTH_COMPONENT24_OES 0x81A6
#endif

#define XR_USE_PLATFORM_ANDROID
#define XR_USE_GRAPHICS_API_OPENGL_ES

#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>
#include <android/log.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>

// Estado del SDL del host (sys_sdl.c / gl_vidsdl.c).
#include "../sdl_local.h"

// ---------------------------------------------------------------------------
// Estado del modulo
// ---------------------------------------------------------------------------
static qboolean vr_initialized = false;	// VR_Init ya llamado
static qboolean vr_available = false;	// loader + runtime + HMD OK
static qboolean vr_session_running = false;
static qboolean vr_session_focused = false;

static XrInstance	vr_instance = XR_NULL_HANDLE;
static XrSystemId	vr_system = XR_NULL_SYSTEM_ID;
static XrSession	vr_session = XR_NULL_HANDLE;
static XrSpace		vr_local_space = XR_NULL_HANDLE;
static XrSpace		vr_view_space = XR_NULL_HANDLE;
static XrSwapchain	vr_eye_swapchain[2] = {XR_NULL_HANDLE, XR_NULL_HANDLE};
static int32_t		vr_eye_width = 0;
static int32_t		vr_eye_height = 0;
static uint32_t		vr_eye_sample_count = 1;
static int64_t		vr_swapchain_format = 0;

/* ---------------------------------------------------------------------------
 * Paso 3: render estereo. Imagenes de swapchain + FBOs GLES.
 * Cada imagen del swapchain es una textura GL; para dibujar en ella con el
 * pipeline fijo hace falta un framebuffer por imagen (OES_framebuffer_object)
 * y un depth renderbuffer compartido por ojo.
 * ------------------------------------------------------------------------- */
#define VR_MAX_SWAPCHAIN_IMAGES 8
static XrSwapchainImageOpenGLESKHR	vr_eye_images[2][VR_MAX_SWAPCHAIN_IMAGES];
static uint32_t		vr_eye_image_count[2];
static GLuint		vr_eye_fbo[2][VR_MAX_SWAPCHAIN_IMAGES];
static GLuint		vr_eye_depth_rb[2];
static qboolean		vr_fbo_ready[2];
static uint32_t		vr_eye_acquired[2];	// indice adquirido este frame
static qboolean		vr_eye_acquiring[2];	// acquire pendiente de release
static qboolean		vr_eyes_done[2];		// ojo ya dibujado este frame
static int			vr_current_eye = -1;	// ojo en curso (R_SetupGL)

/* Vistas localizadas (pose + fov por ojo, en el espacio LOCAL: las mismas
 * coordenadas que iran a la capa de proyeccion) y estado del frame. */
static XrView		vr_eye_view[2];
static qboolean		vr_views_valid = false;
static qboolean		vr_rendering = false;	// true: SCR_UpdateScreen dibuja ojos

static XrSessionState vr_session_state = XR_SESSION_STATE_UNKNOWN;
static qboolean		vr_swapchains_ready = false;	// swapchains + acciones creados tras xrBeginSession

/* Contexto GLES3 auxiliar: el runtime de Meta crea las texturas de los
 * swapchains con el contexto del BINDING de la sesion y exige GLES >= 3.0
 * (GLES reqs: minApi=3.0). El motor renderiza con GLES 1.1, asi que creamos
 * un contexto ES3 que comparte objetos con el del juego (mismo share group)
 * y lo pasamos en el binding: las texturas del swapchain son visibles desde
 * el 1.1. Ver VR_CreateES3Context / VR_BindES3Context. */
static EGLDisplay	vr_es3_display = EGL_NO_DISPLAY;
static EGLContext	vr_es3_ctx = EGL_NO_CONTEXT;
static EGLSurface	vr_es3_surf = EGL_NO_SURFACE;

static XrFrameState	vr_frame_state;
static qboolean		vr_frame_waited = false;

/* Paso 4: head tracking. Delta de rotacion de la cabeza entre frames, en la
 * convencion del motor (grados; yaw horario, pitch positivo = abajo). */
static float		vr_head_dyaw = 0;
static float		vr_head_dpitch = 0;
static float		vr_prev_yaw = 0;
static float		vr_prev_pitch = 0;
static qboolean		vr_prev_valid = false;

/* Paso 5: controles. Acciones OpenXR de los mandos Quest (perfil Oculus
 * Touch). Los botones disparan los mismos comandos que los botones tactiles
 * (+attack, +use, ...) via Cbuf; el stick izquierdo mueve (VR_GetMoveStick) y
 * el derecho gira en juego / navega menus. */
static XrActionSet	vr_actionset = XR_NULL_HANDLE;
static XrAction		vr_act_stick[2];		// vector2f: mover (L) / girar (R)
static XrAction		vr_act_trigger[2];		// float: gatillo L/R
static XrAction		vr_act_grip[2];			// float: grip L/R
static XrAction		vr_act_pose[2];			// pose: aiming 6DoF futuro
static XrSpace		vr_pose_space[2];
static XrAction		vr_act_a, vr_act_b, vr_act_x, vr_act_y, vr_act_menu;
static XrAction		vr_act_haptic[2];
static XrPath		vr_hand_path[2];
static qboolean		vr_actions_ready = false;

static float		vr_move_x, vr_move_y;	// stick izquierdo (consumido por in_sdl)
static float		vr_look_x, vr_look_y;	// stick derecho en juego (consumido por in_sdl)
static qboolean		vr_prev_menu = false;
static qboolean		vr_prev_menu_up, vr_prev_menu_down, vr_prev_menu_left, vr_prev_menu_right;
static double		vr_menu_next_up, vr_menu_next_down, vr_menu_next_left, vr_menu_next_right;

cvar_t	vr_enabled = {"vr_enabled", "1", true};
cvar_t	vr_debug = {"vr_debug", "1", true};
cvar_t	vr_turn_speed = {"vr_turn_speed", "180", true};	// grados/segundo stick derecho

/* ---------------------------------------------------------------------------
 * Declaraciones adelantadas
 * ------------------------------------------------------------------------- */
static qboolean VR_CreateSwapchains (void);
static void VR_DestroySwapchains (void);
static qboolean VR_CreateSessionObjects (void);
static qboolean VR_CreateES3Context (EGLConfig config, EGLContext ctx_game);
static qboolean VR_BindES3Context (EGLSurface *out_draw, EGLSurface *out_read);
static void VR_RestoreGameContext (EGLSurface draw, EGLSurface read, EGLContext ctx_game);
static qboolean VR_CreateEyeFBOs (int eye);
static void VR_DestroySessionObjects (void);
static void VR_QuatToYawPitch (XrQuaternionf q, float *yaw_deg, float *pitch_deg);
static qboolean VR_InitActions (void);
static void VR_CreateActionSpaces (void);
static void VR_PollActions (void);

/* Log persistente en fichero: ademas de logcat, VR_LOG escribe en
 * <internal>/vr_log.txt. Se lee con:
 *   adb shell run-as com.nzpteam.nzportable cat files/vr_log.txt
 * Util cuando el cable USB se desconecta: la app sigue escribiendo en disco
 * y el log se recupera al reconectar. */
static FILE *vr_log_file = NULL;
static qboolean vr_log_opened = false;

static void VR_LogOpen (void)
{
	const char *internal;
	char path[512];

	if (vr_log_opened)
		return;
	vr_log_opened = true;

	internal = SDL_AndroidGetInternalStoragePath();
	if (!internal)
		return;

	snprintf(path, sizeof(path), "%s/vr_log.txt", internal);
	vr_log_file = fopen(path, "w");	// truncar en cada arranque del motor
}

static void VR_LogWrite (const char *fmt, ...)
{
	char buf[1024];
	va_list ap;

	va_start(ap, fmt);
	vsnprintf(buf, sizeof(buf), fmt, ap);
	va_end(ap);

	if (vr_debug.value > 0)
		__android_log_print(ANDROID_LOG_INFO, "nzp-vr", "%s", buf);

	VR_LogOpen();
	if (vr_log_file) {
		fprintf(vr_log_file, "%s\n", buf);
		fflush(vr_log_file);	// sin esto, un cable que se cae pierde el log
	}
}

#define VR_LOG(...) VR_LogWrite(__VA_ARGS__)

static const char *VR_ResultString (XrResult r)
{
	switch (r) {
		case XR_SUCCESS:						return "SUCCESS";
		case XR_ERROR_INSTANCE_LOST:			return "INSTANCE_LOST";
		case XR_ERROR_SESSION_LOST:				return "SESSION_LOST";
		case XR_ERROR_RUNTIME_FAILURE:			return "RUNTIME_FAILURE";
		case XR_ERROR_OUT_OF_MEMORY:			return "OUT_OF_MEMORY";
		case XR_ERROR_API_VERSION_UNSUPPORTED:	return "API_VERSION_UNSUPPORTED";
		case XR_ERROR_INITIALIZATION_FAILED:	return "INITIALIZATION_FAILED";
		case XR_ERROR_FUNCTION_UNSUPPORTED:		return "FUNCTION_UNSUPPORTED";
		case XR_ERROR_EXTENSION_NOT_PRESENT:	return "EXTENSION_NOT_PRESENT";
		case XR_ERROR_FEATURE_UNSUPPORTED:		return "FEATURE_UNSUPPORTED";
		case XR_ERROR_FORM_FACTOR_UNAVAILABLE:	return "FORM_FACTOR_UNAVAILABLE";
		case XR_ERROR_GRAPHICS_DEVICE_INVALID:	return "GRAPHICS_DEVICE_INVALID";
		case XR_ERROR_PERMISSION_INSUFFICIENT:	return "PERMISSION_INSUFFICIENT";
		case XR_ERROR_VALIDATION_FAILURE:		return "VALIDATION_FAILURE";
		case XR_ERROR_TIME_INVALID:				return "TIME_INVALID";
		case XR_ERROR_LAYER_LIMIT_EXCEEDED:		return "LAYER_LIMIT_EXCEEDED";
		case XR_ERROR_SWAPCHAIN_RECT_INVALID:	return "SWAPCHAIN_RECT_INVALID";
		case XR_ERROR_GRAPHICS_REQUIREMENTS_CALL_MISSING: return "GRAPHICS_REQUIREMENTS_CALL_MISSING";
		default:								return "OTHER";
	}
}

#define XR_CHECK(call) \
	do { \
		XrResult _r = (call); \
		if (XR_FAILED(_r)) { \
			VR_LOG("FALLO %s -> %s (%d)", #call, VR_ResultString(_r), (int)_r); \
			return false; \
		} \
	} while (0)

// ---------------------------------------------------------------------------
// Inicializacion
// ---------------------------------------------------------------------------

// El loader de OpenXR en Android necesita el JavaVM + Activity ANTES de
// xrCreateInstance (XR_KHR_loader_init_android). SDL ya lleva el JNI montado:
// SDL_AndroidGetJNIEnv/SDL_AndroidGetActivity nos dan VM y Activity.
extern void *SDL_AndroidGetJNIEnv (void);
extern void *SDL_AndroidGetActivity (void);

static qboolean VR_InitLoader (void)
{
	PFN_xrInitializeLoaderKHR pfnInitializeLoader = NULL;
	XrResult r = xrGetInstanceProcAddr(XR_NULL_HANDLE, "xrInitializeLoaderKHR",
		(PFN_xrVoidFunction *)&pfnInitializeLoader);

	if (r != XR_SUCCESS || pfnInitializeLoader == NULL) {
		VR_LOG("xrInitializeLoaderKHR no disponible (%d)", (int)r);
		return false;
	}

	JNIEnv *env = (JNIEnv *)SDL_AndroidGetJNIEnv();
	jobject activity = (jobject)SDL_AndroidGetActivity();

	if (env == NULL || activity == NULL) {
		VR_LOG("JNI no disponible (env=%p activity=%p)", (void *)env, (void *)activity);
		return false;
	}

	JavaVM *vm = NULL;
	/* En C, JNIEnv es 'const struct JNINativeInterface *', asi que la llamada
	 * es (*env)->Funcion(env, ...). */
	if ((*env)->GetJavaVM(env, &vm) != JNI_OK || vm == NULL) {
		VR_LOG("GetJavaVM fallo");
		return false;
	}

	XrLoaderInitInfoAndroidKHR loader_info;
	memset(&loader_info, 0, sizeof(loader_info));
	loader_info.type = XR_TYPE_LOADER_INIT_INFO_ANDROID_KHR;
	loader_info.applicationVM = (void *)vm;
	loader_info.applicationContext = (void *)activity;

	r = pfnInitializeLoader((const XrLoaderInitInfoBaseHeaderKHR *)&loader_info);
	if (XR_FAILED(r)) {
		VR_LOG("xrInitializeLoaderKHR fallo: %s", VR_ResultString(r));
		return false;
	}

	VR_LOG("xrInitializeLoaderKHR OK");
	return true;
}

qboolean VR_Init (void)
{
	if (vr_initialized)
		return vr_available;

	vr_initialized = true;
	vr_available = false;

	Cvar_RegisterVariable (&vr_enabled);
	Cvar_RegisterVariable (&vr_debug);
	Cvar_RegisterVariable (&vr_turn_speed);

	if (!VR_InitLoader())
		return false;

	// GLES es obligatoria para nuestro render. XR_KHR_loader_init_android es
	// una extension de PLATAFORMA: no se lista en enabledExtensionNames.
	const char *extensions[] = {
		XR_KHR_OPENGL_ES_ENABLE_EXTENSION_NAME,
	};

	XrInstanceCreateInfo ici;
	memset(&ici, 0, sizeof(ici));
	ici.type = XR_TYPE_INSTANCE_CREATE_INFO;
	Q_strncpy (ici.applicationInfo.applicationName, "NZP-Android-Zurdo", sizeof(ici.applicationInfo.applicationName));
	ici.applicationInfo.applicationVersion = 2;
	Q_strncpy (ici.applicationInfo.engineName, "Vril", sizeof(ici.applicationInfo.engineName));
	ici.applicationInfo.engineVersion = 1;
	ici.applicationInfo.apiVersion = XR_CURRENT_API_VERSION;
	ici.enabledExtensionCount = 1;
	ici.enabledExtensionNames = extensions;

	XR_CHECK(xrCreateInstance(&ici, &vr_instance));

	XrSystemGetInfo sgi;
	memset(&sgi, 0, sizeof(sgi));
	sgi.type = XR_TYPE_SYSTEM_GET_INFO;
	sgi.formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;

	XrResult r = xrGetSystem(vr_instance, &sgi, &vr_system);
	if (XR_FAILED(r)) {
		// Caso normal en un movil sin runtime VR: no es un error del juego.
		VR_LOG("Sin HMD OpenXR (%s). VR desactivada.", VR_ResultString(r));
		xrDestroyInstance(vr_instance);
		vr_instance = XR_NULL_HANDLE;
		return false;
	}

	XrSystemProperties props;
	memset(&props, 0, sizeof(props));
	props.type = XR_TYPE_SYSTEM_PROPERTIES;
	xrGetSystemProperties(vr_instance, vr_system, &props);
	VR_LOG("HMD: %s (maxLayers=%u)", props.systemName, props.graphicsProperties.maxLayerCount);

	vr_available = true;
	VR_LOG("VR_Init OK (system=%llu)", (unsigned long long)vr_system);
	return true;
}

// Crea la sesion GLES + espacios de referencia. Requiere el contexto EGL del
// juego ya actual (se llama desde VR_BeginFrame en el primer frame activo).
// Los swapchains van aparte (VR_CreateSwapchains) porque dependen de la
// resolucion recomendada, que solo se conoce con sesion creada.
static qboolean VR_CreateSession (void)
{
	// Si la sesion ya existe (p.ej. reintento tras un fallo posterior), NO
	// recrearla: xrCreateSession devolveria LIMIT_REACHED (-10).
	if (vr_session != XR_NULL_HANDLE)
		return true;

	// El binding GLES de OpenXR necesita el EGLDisplay y un EGLConfig
	// compatible con el contexto del juego. SDL no expone el config, asi que
	// pedimos la lista de configs del display y dejamos que el runtime
	// valide (hello_xr hace lo mismo con su EGL).
	EGLDisplay display = eglGetCurrentDisplay();
	EGLContext context = eglGetCurrentContext();

	if (display == EGL_NO_DISPLAY || context == EGL_NO_CONTEXT) {
		VR_LOG("Sin contexto EGL actual (display=%p ctx=%p)", (void *)display, (void *)context);
		return false;
	}

	EGLConfig config = NULL;
	{
		EGLint num_configs = 0;
		// Opcion 1 (ideal): usar el MISMO EGLConfig que SDL uso para el
		// contexto actual (via EGL_CONFIG_ID). El runtime de Meta valida la
		// compatibilidad config/contexto y rechaza configs distintos.
		EGLint config_id = 0;
		if (eglQueryContext(display, context, EGL_CONFIG_ID, &config_id) && config_id != 0) {
			const EGLint id_attribs[] = { EGL_CONFIG_ID, config_id, EGL_NONE };
			if (eglChooseConfig(display, id_attribs, &config, 1, &num_configs) && num_configs >= 1) {
				EGLint rend = 0, surf = 0;
				eglGetConfigAttrib(display, config, EGL_RENDERABLE_TYPE, &rend);
				eglGetConfigAttrib(display, config, EGL_SURFACE_TYPE, &surf);
				VR_LOG("EGLConfig del contexto: id=%d renderable=0x%x surface=0x%x",
					(int)config_id, (unsigned)rend, (unsigned)surf);
			} else {
				config = NULL;
			}
		}
		// Opcion 2 (fallback): pedir un config propio. IMPORTANTE: el runtime
		// de Meta exige EGL_OPENGL_ES3_BIT en EGL_RENDERABLE_TYPE (aunque el
		// juego use GLES 1.1); sin eso xrCreateSession devuelve OTHER (-50).
		if (config == NULL) {
			const EGLint attribs[] = {
				EGL_RENDERABLE_TYPE, (EGL_OPENGL_ES_BIT | EGL_OPENGL_ES2_BIT | EGL_OPENGL_ES3_BIT),
				EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
				EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8,
				EGL_DEPTH_SIZE, 24,
				EGL_NONE
			};
			if (!eglChooseConfig(display, attribs, &config, 1, &num_configs) || num_configs < 1) {
				VR_LOG("eglChooseConfig no encontro config GLES");
				return false;
			}
			VR_LOG("EGLConfig fallback elegido");
		}
	}

	/* El runtime de Meta crea las texturas de los swapchains con el contexto
	 * del binding y exige GLES >= 3.0: pasamos el ES3 auxiliar (comparte
	 * objetos con el 1.1 del juego, asi el motor puede dibujar en ellas). */
	if (!VR_CreateES3Context(config, context))
		return false;

	XrGraphicsBindingOpenGLESAndroidKHR gles_binding;
	memset(&gles_binding, 0, sizeof(gles_binding));
	gles_binding.type = XR_TYPE_GRAPHICS_BINDING_OPENGL_ES_ANDROID_KHR;
	gles_binding.display = display;
	gles_binding.config = config;
	gles_binding.context = vr_es3_ctx;

	/* OpenXR exige llamar a xrGetOpenGLSESKhrGraphicsRequirementsKHR antes de
	 * xrCreateSession; si no, devuelve GRAPHICS_REQUIREMENTS_CALL_MISSING
	 * (-50). Ademas valida que el contexto GLES sea >= 3.0... el runtime de
	 * Meta lo tolera con GLES 1.1 en la practica (el binding solo necesita
	 * display/config/context). */
	{
		PFN_xrGetOpenGLESGraphicsRequirementsKHR pGetGLESReqs = NULL;
		XrGraphicsRequirementsOpenGLESKHR reqs;
		XrResult rr;
		memset(&reqs, 0, sizeof(reqs));
		reqs.type = XR_TYPE_GRAPHICS_REQUIREMENTS_OPENGL_ES_KHR;
		rr = xrGetInstanceProcAddr(vr_instance, "xrGetOpenGLESGraphicsRequirementsKHR",
			(PFN_xrVoidFunction *)&pGetGLESReqs);
		if (XR_SUCCEEDED(rr) && pGetGLESReqs) {
			rr = pGetGLESReqs(vr_instance, vr_system, &reqs);
			if (XR_FAILED(rr)) {
				VR_LOG("xrGetOpenGLESGraphicsRequirementsKHR fallo: %s", VR_ResultString(rr));
				return false;
			}
			VR_LOG("GLES reqs: minApi=0x%lx maxApi=0x%lx",
				(unsigned long)reqs.minApiVersionSupported,
				(unsigned long)reqs.maxApiVersionSupported);
		} else {
			VR_LOG("proc xrGetOpenGLESGraphicsRequirementsKHR no disponible (%s)",
				VR_ResultString(rr));
		}
	}

	XrSessionCreateInfo sci;
	memset(&sci, 0, sizeof(sci));
	sci.type = XR_TYPE_SESSION_CREATE_INFO;
	sci.next = &gles_binding;
	sci.systemId = vr_system;

	/* Patron hello_xr: contexto GL actual al crear la sesion. Activamos el
	 * ES3 (el del binding) y restauramos el del juego justo despues. */
	{
		EGLSurface draw = EGL_NO_SURFACE, read = EGL_NO_SURFACE;
		qboolean es3_current = VR_BindES3Context(&draw, &read);
		XrResult sr = xrCreateSession(vr_instance, &sci, &vr_session);
		if (es3_current)
			VR_RestoreGameContext(draw, read, context);
		if (XR_FAILED(sr)) {
			VR_LOG("FALLO xrCreateSession -> %s (%d)", VR_ResultString(sr), (int)sr);
			return false;
		}
	}
	VR_LOG("Sesion GLES creada (display=%p, ctx ES3 compartido)", (void *)display);

	// Espacios de referencia. LOCAL: origen a la altura de la cabeza donde
	// arranco la sesion. VIEW: sigue a la cabeza (para xrLocateViews).
	XrReferenceSpaceCreateInfo rsi;
	memset(&rsi, 0, sizeof(rsi));
	rsi.type = XR_TYPE_REFERENCE_SPACE_CREATE_INFO;
	rsi.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_LOCAL;
	rsi.poseInReferenceSpace.orientation.w = 1.0f;
	XR_CHECK(xrCreateReferenceSpace(vr_session, &rsi, &vr_local_space));

	rsi.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_VIEW;
	XR_CHECK(xrCreateReferenceSpace(vr_session, &rsi, &vr_view_space));

	VR_LOG("Esperando estado READY del runtime...");
	return true;
}

// Resolucion recomendada, swapchains y acciones. Va DESPUES de xrBeginSession
// Y con la sesion ya en SYNCHRONIZED (hello_xr hace lo mismo): la maquina de
// estados exige IDLE -> READY -> begin -> SYNCHRONIZED, y el runtime de Meta
// rechaza xrCreateSwapchain antes de SYNCHRONIZED con RUNTIME_FAILURE (-2).
static qboolean VR_CreateSessionObjects (void)
{
	if (vr_swapchains_ready)
		return true;

	// Resolucion recomendada por ojo
	XrViewConfigurationView views[2];
	memset(views, 0, sizeof(views));
	views[0].type = XR_TYPE_VIEW_CONFIGURATION_VIEW;
	views[1].type = XR_TYPE_VIEW_CONFIGURATION_VIEW;
	uint32_t view_count = 0;
	XR_CHECK(xrEnumerateViewConfigurationViews(vr_instance, vr_system,
		XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO, 2, &view_count, views));

	vr_eye_width = views[0].recommendedImageRectWidth;
	vr_eye_height = views[0].recommendedImageRectHeight;
	vr_eye_sample_count = views[0].recommendedSwapchainSampleCount;
	VR_LOG("Resolucion por ojo: %dx%d (sampleCount=%u)",
		(int)vr_eye_width, (int)vr_eye_height, vr_eye_sample_count);

	if (!VR_CreateSwapchains())
		return false;

	if (!VR_InitActions())
		VR_LOG("Acciones de mandos no disponibles (seguira sin ellas)");

	vr_swapchains_ready = true;
	return true;
}

/* Activa el contexto ES3 auxiliar en el hilo actual, guardando las
 * superficies previas para restaurarlas con VR_RestoreGameContext. */
static qboolean VR_BindES3Context (EGLSurface *out_draw, EGLSurface *out_read)
{
	if (vr_es3_ctx == EGL_NO_CONTEXT)
		return false;

	*out_draw = eglGetCurrentSurface(EGL_DRAW);
	*out_read = eglGetCurrentSurface(EGL_READ);

	if (!eglMakeCurrent(vr_es3_display, vr_es3_surf, vr_es3_surf, vr_es3_ctx)) {
		VR_LOG("eglMakeCurrent ES3 fallo (0x%x)", (unsigned)eglGetError());
		return false;
	}
	return true;
}

static void VR_RestoreGameContext (EGLSurface draw, EGLSurface read, EGLContext ctx_game)
{
	if (vr_es3_display != EGL_NO_DISPLAY)
		eglMakeCurrent(vr_es3_display, draw, read, ctx_game);
}

// Crea (una vez) el contexto ES3 auxiliar compartiendo objetos con el
// contexto del juego (mismo share group). Es el contexto que va en el
// binding de la sesion: Meta crea con el las texturas de los swapchains y
// exige GLES >= 3.0; el motor sigue dibujando con su contexto 1.1.
static qboolean VR_CreateES3Context (EGLConfig config, EGLContext ctx_game)
{
	if (vr_es3_ctx != EGL_NO_CONTEXT)
		return true;

	vr_es3_display = eglGetCurrentDisplay();
	if (vr_es3_display == EGL_NO_DISPLAY)
		return false;

	// El config de la ventana del juego normalmente NO tiene EGL_PBUFFER_BIT,
	// y sin pbuffer no podemos ACTIVAR el contexto ES3 (eglMakeCurrent falla)
	// -> los swapchains se crearian con el contexto 1.1 actual y el runtime de
	// Meta responde RUNTIME_FAILURE (-2). Elegimos un config OFFSCREEN propio
	// (PBUFFER + ES3) solo para este contexto auxiliar.
	EGLConfig offscreen_config = NULL;
	{
		const EGLint attribs[] = {
			EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
			EGL_RENDERABLE_TYPE, (EGL_OPENGL_ES2_BIT | EGL_OPENGL_ES3_BIT),
			EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8,
			EGL_NONE
		};
		EGLint num = 0;
		if (eglChooseConfig(vr_es3_display, attribs, &offscreen_config, 1, &num) && num >= 1) {
			EGLint cid = 0;
			eglGetConfigAttrib(vr_es3_display, offscreen_config, EGL_CONFIG_ID, &cid);
			VR_LOG("Config offscreen ES3: id=%d", (int)cid);
		} else {
			offscreen_config = NULL;
			VR_LOG("Sin config offscreen PBUFFER+ES3, reutilizo el del juego (0x%x)",
				(unsigned)eglGetError());
		}
	}
	if (offscreen_config == NULL)
		offscreen_config = config;

	const EGLint ctx_attribs[] = { EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE };
	vr_es3_ctx = eglCreateContext(vr_es3_display, offscreen_config, ctx_game, ctx_attribs);
	if (vr_es3_ctx == EGL_NO_CONTEXT) {
		VR_LOG("eglCreateContext ES3 fallo (0x%x)", (unsigned)eglGetError());
		return false;
	}

	// Pbuffer 16x16 con el config offscreen para poder activarlo (algunos
	// drivers exigen superficie para hacer un contexto current).
	const EGLint pbuf_attribs[] = { EGL_WIDTH, 16, EGL_HEIGHT, 16, EGL_NONE };
	vr_es3_surf = eglCreatePbufferSurface(vr_es3_display, offscreen_config, pbuf_attribs);
	if (vr_es3_surf == EGL_NO_SURFACE)
		VR_LOG("AVISO: sin pbuffer ES3 (0x%x), eglMakeCurrent puede fallar",
			(unsigned)eglGetError());
	else
		VR_LOG("Pbuffer ES3 16x16 OK");

	VR_LOG("Contexto ES3 auxiliar creado (comparte con el 1.1 del juego)");
	return true;
}

// Crea los 2 swapchains (uno por ojo) y enumera sus imagenes GL.
static qboolean VR_CreateSwapchains (void)
{
	EGLContext ctx_game = eglGetCurrentContext();
	EGLSurface draw = EGL_NO_SURFACE, read = EGL_NO_SURFACE;

	// Crear las texturas de los swapchains desde el contexto ES3 del binding;
	// el contexto 1.1 del juego las ve por el share group.
	qboolean es3_bound = VR_BindES3Context(&draw, &read);
	if (!es3_bound)
		VR_LOG("AVISO: sin contexto ES3 auxiliar, usando el contexto del juego");
	else
		VR_LOG("ES3 current: GL_VERSION=\"%s\" RENDERER=\"%s\"",
			(const char *)glGetString(GL_VERSION), (const char *)glGetString(GL_RENDERER));

	// Formato de swapchain: preferir GL_RGBA8 (0x8058). El motor dibuja con
	// GLES 1.1 + OES_framebuffer_object y muchos drivers no aceptan texturas
	// sRGB (GL_SRGB8_ALPHA8) como attachment de FBO.
	{
		uint32_t fmt_count = 0;
		xrEnumerateSwapchainFormats(vr_session, 0, &fmt_count, NULL);
		if (fmt_count > 0) {
			int64_t *formats = (int64_t *)malloc(fmt_count * sizeof(int64_t));
			if (formats) {
				xrEnumerateSwapchainFormats(vr_session, fmt_count, &fmt_count, formats);
				{
					char buf[512];
					int used = 0;
					buf[0] = 0;
					for (uint32_t f = 0; f < fmt_count && used < (int)sizeof(buf) - 16; f++)
						used += snprintf(buf + used, sizeof(buf) - used, "%s0x%llx",
							f ? " " : "", (unsigned long long)formats[f]);
					VR_LOG("Formats swapchain (%u): %s", fmt_count, buf);
				}
				vr_swapchain_format = formats[0];
				for (uint32_t f = 0; f < fmt_count; f++) {
					if (formats[f] == 0x8058) { // GL_RGBA8
						vr_swapchain_format = formats[f];
						break;
					}
				}
				free(formats);
			}
		}
	}
	VR_LOG("Formato swapchain: 0x%llx", (unsigned long long)vr_swapchain_format);

	for (int eye = 0; eye < 2; eye++) {
		XrSwapchainCreateInfo scinfo;
		memset(&scinfo, 0, sizeof(scinfo));
		scinfo.type = XR_TYPE_SWAPCHAIN_CREATE_INFO;
		scinfo.usageFlags = XR_SWAPCHAIN_USAGE_SAMPLED_BIT | XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT;
		scinfo.format = vr_swapchain_format;
		scinfo.sampleCount = vr_eye_sample_count;
		scinfo.width = vr_eye_width;
		scinfo.height = vr_eye_height;
		scinfo.faceCount = 1;
		scinfo.arraySize = 1;
		scinfo.mipCount = 1;

		XrResult r = xrCreateSwapchain(vr_session, &scinfo, &vr_eye_swapchain[eye]);
		if (XR_FAILED(r)) {
			VR_LOG("FALLO xrCreateSwapchain(ojo %d) -> %s (%d)", eye, VR_ResultString(r), (int)r);
			goto fail;
		}

		uint32_t img_count = 0;
		if (XR_FAILED(xrEnumerateSwapchainImages(vr_eye_swapchain[eye], 0, &img_count, NULL))
			|| img_count == 0 || img_count > VR_MAX_SWAPCHAIN_IMAGES) {
			VR_LOG("Imagenes swapchain ojo %d invalidas (%u)", eye, img_count);
			goto fail;
		}
		memset(vr_eye_images[eye], 0, sizeof(vr_eye_images[eye]));
		for (uint32_t i = 0; i < img_count; i++)
			vr_eye_images[eye][i].type = XR_TYPE_SWAPCHAIN_IMAGE_OPENGL_ES_KHR;
		if (XR_FAILED(xrEnumerateSwapchainImages(vr_eye_swapchain[eye], img_count,
				&img_count, (XrSwapchainImageBaseHeader *)vr_eye_images[eye]))) {
			VR_LOG("xrEnumerateSwapchainImages fallo (ojo %d)", eye);
			goto fail;
		}
		vr_eye_image_count[eye] = img_count;
		vr_fbo_ready[eye] = false;
		VR_LOG("Swapchain ojo %d: %u imagenes (tex0=%u)", eye, img_count,
			(unsigned)vr_eye_images[eye][0].image);
	}

	// Volver al contexto GLES 1.1 del juego antes de tocar nada mas.
	if (es3_bound)
		VR_RestoreGameContext(draw, read, ctx_game);

	VR_LOG("Swapchains creados (%u/%u imagenes/ojo)", vr_eye_image_count[0], vr_eye_image_count[1]);
	return true;

fail:
	if (es3_bound)
		VR_RestoreGameContext(draw, read, ctx_game);
	// Destruir el swapchain parcialmente creado (ojo 0 si fallo el 1) para
	// que el reintento no filtre handles ni reciba LIMIT_REACHED.
	VR_DestroySwapchains();
	return false;
}

// ---------------------------------------------------------------------------
// FBOs por imagen de swapchain (paso 3)
// ---------------------------------------------------------------------------

// Crea (una vez) un FBO por imagen del swapchain del ojo + depth renderbuffer.
// El color de cada FBO es la textura GL de esa imagen de swapchain: dibujar en
// el FBO es dibujar en la imagen que xrEndFrame presentara en el HMD.
static qboolean VR_CreateEyeFBOs (int eye)
{
	GLuint prev_fbo = 0, prev_rb = 0, prev_tex = 0;

	glGetIntegerv(GL_FRAMEBUFFER_BINDING_OES, (GLint *)&prev_fbo);
	glGetIntegerv(GL_RENDERBUFFER_BINDING_OES, (GLint *)&prev_rb);
	glGetIntegerv(GL_TEXTURE_BINDING_2D, (GLint *)&prev_tex);

	// Depth compartido por todas las imagenes del ojo (solo se necesita uno
	// activo a la vez; el motor lo rellena cada frame).
	glGenRenderbuffersOES(1, &vr_eye_depth_rb[eye]);
	glBindRenderbufferOES(GL_RENDERBUFFER_OES, vr_eye_depth_rb[eye]);
	glRenderbufferStorageOES(GL_RENDERBUFFER_OES, GL_DEPTH_COMPONENT24_OES,
		vr_eye_width, vr_eye_height);

	for (uint32_t i = 0; i < vr_eye_image_count[eye]; i++) {
		GLuint tex = (GLuint)(uintptr_t)vr_eye_images[eye][i].image;
		GLuint fbo = 0;
		glGenFramebuffersOES(1, &fbo);
		glBindFramebufferOES(GL_FRAMEBUFFER_OES, fbo);
		glFramebufferTexture2DOES(GL_FRAMEBUFFER_OES, GL_COLOR_ATTACHMENT0_OES,
			GL_TEXTURE_2D, tex, 0);
		glFramebufferRenderbufferOES(GL_FRAMEBUFFER_OES, GL_DEPTH_ATTACHMENT_OES,
			GL_RENDERBUFFER_OES, vr_eye_depth_rb[eye]);
		if (glCheckFramebufferStatusOES(GL_FRAMEBUFFER_OES) != GL_FRAMEBUFFER_COMPLETE_OES) {
			VR_LOG("FBO incompleto (ojo %d imagen %u tex %u)", eye, i, tex);
			glBindFramebufferOES(GL_FRAMEBUFFER_OES, prev_fbo);
			return false;
		}
		vr_eye_fbo[eye][i] = fbo;
	}

	glBindTexture(GL_TEXTURE_2D, prev_tex);
	glBindRenderbufferOES(GL_RENDERBUFFER_OES, prev_rb);
	glBindFramebufferOES(GL_FRAMEBUFFER_OES, prev_fbo);

	vr_fbo_ready[eye] = true;
	VR_LOG("FBOs ojo %d listos (%u)", eye, vr_eye_image_count[eye]);
	return true;
}

// ---------------------------------------------------------------------------
// Paso 5: acciones de los mandos (perfil Oculus Touch)
// ---------------------------------------------------------------------------

// Helper: convierte una ruta de interaccion ("/user/hand/left") a XrPath.
// xrStringToPath escribe en un puntero de salida (no devuelve el path).
static XrPath VR_Path (const char *path)
{
	XrPath p = XR_NULL_PATH;
	xrStringToPath(vr_instance, path, &p);
	return p;
}

static qboolean VR_InitActions (void)
{
	XrActionSetCreateInfo asci;
	XrActionCreateInfo aci;
	XrResult r;

	memset(&asci, 0, sizeof(asci));
	asci.type = XR_TYPE_ACTION_SET_CREATE_INFO;
	Q_strncpy(asci.actionSetName, "nzp_controls", sizeof(asci.actionSetName));
	Q_strncpy(asci.localizedActionSetName, "NZP Controls", sizeof(asci.localizedActionSetName));
	r = xrCreateActionSet(vr_instance, &asci, &vr_actionset);
	if (XR_FAILED(r)) {
		VR_LOG("xrCreateActionSet fallo: %s", VR_ResultString(r));
		return false;
	}

	vr_hand_path[0] = VR_Path("/user/hand/left");
	vr_hand_path[1] = VR_Path("/user/hand/right");

	// Sticks (vector2f): izquierdo mueve, derecho gira/mira.
	for (int h = 0; h < 2; h++) {
		char name[32];
		snprintf(name, sizeof(name), "stick_%s", h ? "right" : "left");
		memset(&aci, 0, sizeof(aci));
		aci.type = XR_TYPE_ACTION_CREATE_INFO;
		Q_strncpy(aci.actionName, name, sizeof(aci.actionName));
		aci.actionType = XR_ACTION_TYPE_VECTOR2F_INPUT;
		aci.countSubactionPaths = 1;
		aci.subactionPaths = &vr_hand_path[h];
		if (XR_FAILED(xrCreateAction(vr_actionset, &aci, &vr_act_stick[h]))) {
			VR_LOG("xrCreateAction stick %d fallo", h);
			return false;
		}

		snprintf(name, sizeof(name), "trigger_%s", h ? "right" : "left");
		memset(&aci, 0, sizeof(aci));
		aci.type = XR_TYPE_ACTION_CREATE_INFO;
		Q_strncpy(aci.actionName, name, sizeof(aci.actionName));
		aci.actionType = XR_ACTION_TYPE_FLOAT_INPUT;
		aci.countSubactionPaths = 1;
		aci.subactionPaths = &vr_hand_path[h];
		if (XR_FAILED(xrCreateAction(vr_actionset, &aci, &vr_act_trigger[h]))) {
			VR_LOG("xrCreateAction trigger %d fallo", h);
			return false;
		}

		snprintf(name, sizeof(name), "grip_%s", h ? "right" : "left");
		memset(&aci, 0, sizeof(aci));
		aci.type = XR_TYPE_ACTION_CREATE_INFO;
		Q_strncpy(aci.actionName, name, sizeof(aci.actionName));
		aci.actionType = XR_ACTION_TYPE_FLOAT_INPUT;
		aci.countSubactionPaths = 1;
		aci.subactionPaths = &vr_hand_path[h];
		if (XR_FAILED(xrCreateAction(vr_actionset, &aci, &vr_act_grip[h]))) {
			VR_LOG("xrCreateAction grip %d fallo", h);
			return false;
		}

		snprintf(name, sizeof(name), "haptic_%s", h ? "right" : "left");
		memset(&aci, 0, sizeof(aci));
		aci.type = XR_TYPE_ACTION_CREATE_INFO;
		Q_strncpy(aci.actionName, name, sizeof(aci.actionName));
		aci.actionType = XR_ACTION_TYPE_VIBRATION_OUTPUT;
		aci.countSubactionPaths = 1;
		aci.subactionPaths = &vr_hand_path[h];
		if (XR_FAILED(xrCreateAction(vr_actionset, &aci, &vr_act_haptic[h]))) {
			VR_LOG("xrCreateAction haptic %d fallo", h);
			return false;
		}

		snprintf(name, sizeof(name), "pose_%s", h ? "right" : "left");
		memset(&aci, 0, sizeof(aci));
		aci.type = XR_TYPE_ACTION_CREATE_INFO;
		Q_strncpy(aci.actionName, name, sizeof(aci.actionName));
		aci.actionType = XR_ACTION_TYPE_POSE_INPUT;
		aci.countSubactionPaths = 1;
		aci.subactionPaths = &vr_hand_path[h];
		if (XR_FAILED(xrCreateAction(vr_actionset, &aci, &vr_act_pose[h]))) {
			VR_LOG("xrCreateAction pose %d fallo", h);
			return false;
		}
	}

	// Botones: A/B (derecha), X/Y (izquierda), menu (izquierda).
	struct { XrAction *act; const char *name; } buttons[] = {
		{ &vr_act_a, "a" }, { &vr_act_b, "b" },
		{ &vr_act_x, "x" }, { &vr_act_y, "y" },
		{ &vr_act_menu, "menu" },
	};
	for (size_t i = 0; i < sizeof(buttons) / sizeof(buttons[0]); i++) {
		memset(&aci, 0, sizeof(aci));
		aci.type = XR_TYPE_ACTION_CREATE_INFO;
		Q_strncpy(aci.actionName, buttons[i].name, sizeof(aci.actionName));
		aci.actionType = XR_ACTION_TYPE_BOOLEAN_INPUT;
		if (XR_FAILED(xrCreateAction(vr_actionset, &aci, buttons[i].act))) {
			VR_LOG("xrCreateAction %s fallo", buttons[i].name);
			return false;
		}
	}

	// Sugerir interaccion con el perfil Oculus Touch (Quest lo exige para
	// que los bindings por defecto del runtime funcionen).
	{
		XrPath touch_profile = VR_Path(
			"/interaction_profiles/oculus/touch_controller");
		XrActionSuggestedBinding sugg[] = {
			{ vr_act_stick[0], VR_Path("/user/hand/left/input/thumbstick") },
			{ vr_act_stick[1], VR_Path("/user/hand/right/input/thumbstick") },
			{ vr_act_trigger[0], VR_Path("/user/hand/left/input/trigger") },
			{ vr_act_trigger[1], VR_Path("/user/hand/right/input/trigger") },
			{ vr_act_grip[0], VR_Path("/user/hand/left/input/squeeze") },
			{ vr_act_grip[1], VR_Path("/user/hand/right/input/squeeze") },
			{ vr_act_a, VR_Path("/user/hand/right/input/a/click") },
			{ vr_act_b, VR_Path("/user/hand/right/input/b/click") },
			{ vr_act_x, VR_Path("/user/hand/left/input/x/click") },
			{ vr_act_y, VR_Path("/user/hand/left/input/y/click") },
			{ vr_act_menu, VR_Path("/user/hand/left/input/menu/click") },
			{ vr_act_pose[0], VR_Path("/user/hand/left/input/aim/pose") },
			{ vr_act_pose[1], VR_Path("/user/hand/right/input/aim/pose") },
			{ vr_act_haptic[0], VR_Path("/user/hand/left/output/haptic") },
			{ vr_act_haptic[1], VR_Path("/user/hand/right/output/haptic") },
		};
		XrInteractionProfileSuggestedBinding sib;
		memset(&sib, 0, sizeof(sib));
		sib.type = XR_TYPE_INTERACTION_PROFILE_SUGGESTED_BINDING;
		sib.interactionProfile = touch_profile;
		sib.countSuggestedBindings = sizeof(sugg) / sizeof(sugg[0]);
		sib.suggestedBindings = sugg;
		r = xrSuggestInteractionProfileBindings(vr_instance, &sib);
		if (XR_FAILED(r))
			VR_LOG("xrSuggestInteractionProfileBindings fallo (%d)", (int)r);
	}

	if (XR_FAILED(xrAttachSessionActionSets(vr_session, NULL))) {
		// Nota: se llama sin info (action set unico ya queda adjunto por
		// defecto en algunos runtimes); si falla, los mandos no funcionan
		// pero el juego sigue.
		VR_LOG("xrAttachSessionActionSets fallo");
		return false;
	}

	vr_actions_ready = true;
	VR_LOG("Acciones de mandos listas");
	return true;
}

// Espacios de las poses de los mandos (se crean con sesion ya creada).
static void VR_CreateActionSpaces (void)
{
	for (int h = 0; h < 2; h++) {
		XrActionSpaceCreateInfo asci;
		memset(&asci, 0, sizeof(asci));
		asci.type = XR_TYPE_ACTION_SPACE_CREATE_INFO;
		asci.action = vr_act_pose[h];
		asci.subactionPath = vr_hand_path[h];
		asci.poseInActionSpace.orientation.w = 1.0f;
		if (XR_FAILED(xrCreateActionSpace(vr_session, &asci, &vr_pose_space[h]))) {
			VR_LOG("xrCreateActionSpace mano %d fallo", h);
			vr_pose_space[h] = XR_NULL_HANDLE;
		}
	}
}

// ---------------------------------------------------------------------------
// Lectura de acciones (paso 5): mueve, gira, botones -> comandos
// ---------------------------------------------------------------------------

// Lee el estado float/bool de una accion para una mano. Devuelve false si la
// accion no esta disponible (mando sin sincronizar, etc).
static qboolean VR_GetFloatAction (XrAction act, int hand, float *out)
{
	XrActionStateFloat state;
	XrActionStateGetInfo info;

	memset(&state, 0, sizeof(state));
	state.type = XR_TYPE_ACTION_STATE_FLOAT;
	memset(&info, 0, sizeof(info));
	info.type = XR_TYPE_ACTION_STATE_GET_INFO;
	info.action = act;
	info.subactionPath = vr_hand_path[hand];
	if (XR_FAILED(xrGetActionStateFloat(vr_session, &info, &state)))
		return false;
	if (!state.isActive)
		return false;
	*out = state.currentState;
	return true;
}

static qboolean VR_GetBoolAction (XrAction act, int hand, qboolean *out)
{
	XrActionStateBoolean state;
	XrActionStateGetInfo info;

	memset(&state, 0, sizeof(state));
	state.type = XR_TYPE_ACTION_STATE_BOOLEAN;
	memset(&info, 0, sizeof(info));
	info.type = XR_TYPE_ACTION_STATE_GET_INFO;
	info.action = act;
	info.subactionPath = vr_hand_path[hand];
	if (XR_FAILED(xrGetActionStateBoolean(vr_session, &info, &state)))
		return false;
	if (!state.isActive)
		return false;
	*out = state.currentState != XR_FALSE;
	return true;
}

static qboolean VR_GetVector2Action (XrAction act, int hand, XrVector2f *out)
{
	XrActionStateVector2f state;
	XrActionStateGetInfo info;

	memset(&state, 0, sizeof(state));
	state.type = XR_TYPE_ACTION_STATE_VECTOR2F;
	memset(&info, 0, sizeof(info));
	info.type = XR_TYPE_ACTION_STATE_GET_INFO;
	info.action = act;
	info.subactionPath = vr_hand_path[hand];
	if (XR_FAILED(xrGetActionStateVector2f(vr_session, &info, &state)))
		return false;
	if (!state.isActive)
		return false;
	*out = state.currentState;
	return true;
}

// Repite un Key_Event de flecha mientras el stick derecho este desviado en
// menus (con auto-repeat cada 250 ms).
static void VR_MenuArrow (qboolean held, qboolean *prev, double *next, int key)
{
	double now = Sys_FloatTime();
	if (held) {
		if (!*prev || now >= *next) {
			Key_Event(key, true);
			Key_Event(key, false);
			*next = now + 0.25;
		}
		*prev = true;
	} else {
		*prev = false;
	}
}

static void VR_PollActions (void)
{
	float f;
	qboolean b;
	XrVector2f v2;

	if (!vr_actions_ready || vr_session == XR_NULL_HANDLE)
		return;

	// El action set activo solo importa cuando la sesion esta FOCUSED.
	if (vr_session_state == XR_SESSION_STATE_FOCUSED) {
		XrActionsSyncInfo si;
		memset(&si, 0, sizeof(si));
		si.type = XR_TYPE_ACTIONS_SYNC_INFO;
		if (XR_FAILED(xrSyncActions(vr_session, &si)))
			return;
	}

	// Stick izquierdo -> movimiento (lo consume NZP_TouchApplyMove via
	// VR_GetMoveStick). Y de OpenXR: arriba = +1; el motor: adelante = +1.
	vr_move_x = vr_move_y = 0;
	if (VR_GetVector2Action(vr_act_stick[0], 0, &v2)) {
		vr_move_x = v2.x;
		vr_move_y = v2.y;
	}

	// Stick derecho en juego -> mirar (lo consume IN_PlatformMouseMove via
	// VR_GetLookStick; en menus lo gestiona el bloque de navegacion de abajo).
	vr_look_x = vr_look_y = 0;
	if (key_dest == key_game && VR_GetVector2Action(vr_act_stick[1], 1, &v2)) {
		vr_look_x = v2.x;
		vr_look_y = v2.y;
	}

	// Gatillo derecho -> disparar (mismo comando que el boton FIRE tactil).
	if (VR_GetFloatAction(vr_act_trigger[1], 1, &f)) {
		static qboolean prev_fire = false;
		qboolean fire = f > 0.5f;
		if (fire != prev_fire) {
			Cbuf_AddText(fire ? "+attack\n" : "-attack\n");
			prev_fire = fire;
		}
	}

	// Gatillo izquierdo -> apuntar.
	if (VR_GetFloatAction(vr_act_trigger[0], 0, &f)) {
		static qboolean prev_aim = false;
		qboolean aim = f > 0.5f;
		if (aim != prev_aim) {
			Cbuf_AddText(aim ? "+aim\n" : "-aim\n");
			prev_aim = aim;
		}
	}

	// Grip derecho -> recargar; grip izquierdo -> usar.
	if (VR_GetFloatAction(vr_act_grip[1], 1, &f)) {
		static qboolean prev = false;
		qboolean now = f > 0.5f;
		if (now != prev) {
			Cbuf_AddText(now ? "+reload\n" : "-reload\n");
			prev = now;
		}
	}
	if (VR_GetFloatAction(vr_act_grip[0], 0, &f)) {
		static qboolean prev = false;
		qboolean now = f > 0.5f;
		if (now != prev) {
			Cbuf_AddText(now ? "+use\n" : "-use\n");
			prev = now;
		}
	}

	// A -> saltar; B -> cambiar arma; X -> granada; Y -> cuchillo.
	if (VR_GetBoolAction(vr_act_a, 1, &b)) {
		static qboolean prev = false;
		if (b != prev) { Cbuf_AddText(b ? "+jump\n" : "-jump\n"); prev = b; }
	}
	if (VR_GetBoolAction(vr_act_b, 1, &b)) {
		static qboolean prev = false;
		if (b != prev) { Cbuf_AddText(b ? "+switch\n" : "-switch\n"); prev = b; }
	}
	if (VR_GetBoolAction(vr_act_x, 0, &b)) {
		static qboolean prev = false;
		if (b != prev) { Cbuf_AddText(b ? "+grenade\n" : "-grenade\n"); prev = b; }
	}
	if (VR_GetBoolAction(vr_act_y, 0, &b)) {
		static qboolean prev = false;
		if (b != prev) { Cbuf_AddText(b ? "+knife\n" : "-knife\n"); prev = b; }
	}

	// Menu (boton izquierdo) -> togglemenu (pausa).
	if (VR_GetBoolAction(vr_act_menu, 0, &b)) {
		static qboolean prev = false;
		if (b && !prev)
			Cbuf_AddText("togglemenu\n");
		prev = b;
	}

	// Stick derecho en menus: navegar con flechas (auto-repeat).
	if (key_dest == key_menu || key_dest == key_menu_pause) {
		if (VR_GetVector2Action(vr_act_stick[1], 1, &v2)) {
			VR_MenuArrow(v2.y > 0.5f, &vr_prev_menu_up, &vr_menu_next_up, K_UPARROW);
			VR_MenuArrow(v2.y < -0.5f, &vr_prev_menu_down, &vr_menu_next_down, K_DOWNARROW);
			VR_MenuArrow(v2.x < -0.5f, &vr_prev_menu_left, &vr_menu_next_left, K_LEFTARROW);
			VR_MenuArrow(v2.x > 0.5f, &vr_prev_menu_right, &vr_menu_next_right, K_RIGHTARROW);
		}
		// Gatillo derecho en menus = aceptar (Enter).
		if (VR_GetFloatAction(vr_act_trigger[1], 1, &f)) {
			static qboolean prev = false;
			qboolean now = f > 0.5f;
			if (now && !prev) { Key_Event(K_ENTER, true); Key_Event(K_ENTER, false); }
			prev = now;
		}
		// B en menus = volver (Escape).
		if (VR_GetBoolAction(vr_act_b, 1, &b)) {
			static qboolean prev = false;
			if (b && !prev) { Key_Event(K_ESCAPE, true); Key_Event(K_ESCAPE, false); }
			prev = b;
		}
	}
}

// ---------------------------------------------------------------------------
// Eventos de sesion
// ---------------------------------------------------------------------------

static void VR_PumpEvents (void)
{
	for (;;) {
		XrEventDataBuffer event;
		memset(&event, 0, sizeof(event));
		event.type = XR_TYPE_EVENT_DATA_BUFFER;
		XrResult r = xrPollEvent(vr_instance, &event);
		if (r != XR_SUCCESS)
			break;	// XR_EVENT_UNAVAILABLE u error

		switch (event.type) {
			case XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED: {
				XrEventDataSessionStateChanged *e = (XrEventDataSessionStateChanged *)&event;
				VR_LOG("Estado de sesion: %d", (int)e->state);
				vr_session_state = e->state;
				switch (e->state) {
					case XR_SESSION_STATE_FOCUSED:
						vr_session_focused = true;
						// Los espacios de las poses de mandos necesitan sesion.
						if (vr_pose_space[0] == XR_NULL_HANDLE && vr_pose_space[1] == XR_NULL_HANDLE)
							VR_CreateActionSpaces();
						break;
					case XR_SESSION_STATE_STOPPING:
					case XR_SESSION_STATE_LOSS_PENDING:
					case XR_SESSION_STATE_EXITING:
						vr_session_focused = false;
						vr_session_running = false;
						break;
					default:
						break;
				}
				break;
			}
			case XR_TYPE_EVENT_DATA_INSTANCE_LOSS_PENDING:
				VR_LOG("Instancia OpenXR perdida");
				vr_available = false;
				return;
			default:
				break;
		}
	}
}

// ---------------------------------------------------------------------------
// Bucle de frames
// ---------------------------------------------------------------------------

qboolean VR_BeginFrame (void)
{
	static int vr_heartbeat_frames = 0;

	if (!vr_available)
		return false;

	// Heartbeat: si el bucle del motor esta congelado (app pausada por
	// pantalla apagada) no aparecera en vr_log.txt.
	if ((++vr_heartbeat_frames % 300) == 0)
		VR_LOG("HB frames=%d estado=%d running=%d sc=%d", vr_heartbeat_frames,
			(int)vr_session_state, (int)vr_session_running, (int)vr_swapchains_ready);

	VR_PumpEvents();

	// Fase 1: crear la sesion GLES en el primer frame, cuando el contexto
	// del juego ya esta actual (VID_Init ya paso). Los eventos de estado
	// (IDLE -> READY -> ...) llegan en frames siguientes.
	if (vr_session == XR_NULL_HANDLE) {
		(void)VR_CreateSession();	// si falla, se reintenta el proximo frame
		return false;
	}

	// Fase 2: con la sesion en READY (2), arrancarla y crear swapchains.
	if (!vr_session_running) {
		// Meta exige esperar a READY antes de xrBeginSession.
		if (vr_session_state != XR_SESSION_STATE_READY)
			return false;

		XrSessionBeginInfo bi;
		memset(&bi, 0, sizeof(bi));
		bi.type = XR_TYPE_SESSION_BEGIN_INFO;
		bi.primaryViewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;

		XrResult r = xrBeginSession(vr_session, &bi);
		if (XR_FAILED(r)) {
			VR_LOG("xrBeginSession fallo: %s", VR_ResultString(r));
			return false;
		}
		vr_session_running = true;
		VR_LOG("Sesion INICIADA (estereo)");
		// Los swapchains esperan a SYNCHRONIZED (fase 3, frames siguientes).
		return false;
	}

	// Swapchains + acciones: intentar desde READY (tras begin) hacia arriba.
	// El runtime de Meta NO manda SYNCHRONIZED si el casco esta sin poners,
	// y necesitamos poder probar/validar la creacion de swapchains tambien
	// sin casco. Reintento con backoff (1 vez cada 120 frames, max 40
	// intentos = ~70 s) para no inundar vr_log.txt si el runtime falla.
	{
		static int soi_attempts = 0;
		static int soi_frames = 0;
		static int soi_last_state = -1;
		if (!vr_swapchains_ready) {
			qboolean state_changed = ((int)vr_session_state != soi_last_state);
			soi_last_state = (int)vr_session_state;
			// Reintenta ya si el estado cambio (p.ej. casco puesto ->
			// SYNCHRONIZED), o periodicamente (cada 120 frames) si sigue
			// fallando. Max 40 intentos para no spamear el log.
			if (soi_attempts < 40 && (state_changed || (soi_frames % 120) == 0)) {
				soi_attempts++;
				VR_LOG("Intento de swapchains #%d (estado=%d)", soi_attempts,
					(int)vr_session_state);
				if (!VR_CreateSessionObjects())
					VR_LOG("Intento #%d FALLO", soi_attempts);
			}
			soi_frames++;
			if (!vr_swapchains_ready)
				return false;
		}
	}

	// Solo se puede presentar cuando la sesion esta sincronizada/visible/
	// focused (el compositor corre con el casco puesto).
	switch (vr_session_state) {
		case XR_SESSION_STATE_SYNCHRONIZED:
		case XR_SESSION_STATE_VISIBLE:
		case XR_SESSION_STATE_FOCUSED:
			break;
		default:
			return false;
	}

	// xrWaitFrame una vez por frame del motor
	if (!vr_frame_waited) {
		XrFrameWaitInfo fwi;
		memset(&fwi, 0, sizeof(fwi));
		memset(&vr_frame_state, 0, sizeof(vr_frame_state));
		vr_frame_state.type = XR_TYPE_FRAME_STATE;

		XR_CHECK(xrWaitFrame(vr_session, &fwi, &vr_frame_state));
		vr_frame_waited = true;
	}

	XrFrameBeginInfo fbi;
	memset(&fbi, 0, sizeof(fbi));
	fbi.type = XR_TYPE_FRAME_BEGIN_INFO;

	XR_CHECK(xrBeginFrame(vr_session, &fbi));

	// Poses de la cabeza + vistas por ojo (paso 3/4). Se localizan en el
	// espacio LOCAL: esas mismas poses son las que exige la capa de
	// proyeccion en xrEndFrame.
	{
		XrViewState view_state;
		memset(&view_state, 0, sizeof(view_state));
		view_state.type = XR_TYPE_VIEW_STATE;

		XrViewLocateInfo vli;
		memset(&vli, 0, sizeof(vli));
		vli.type = XR_TYPE_VIEW_LOCATE_INFO;
		vli.viewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
		vli.displayTime = vr_frame_state.predictedDisplayTime;
		vli.space = vr_local_space;

		memset(vr_eye_view, 0, sizeof(vr_eye_view));
		vr_eye_view[0].type = XR_TYPE_VIEW;
		vr_eye_view[1].type = XR_TYPE_VIEW;
		uint32_t located = 0;

		if (XR_SUCCEEDED(xrLocateViews(vr_session, &vli, &view_state, 2, &located, vr_eye_view)) && located > 0) {
			vr_views_valid = true;

			// Paso 4: delta de rotacion de la cabeza entre frames (solo yaw
			// y pitch; el roll del motor no se usa). La primera vez solo
			// guardamos la referencia (sin salto de camara).
			float yaw, pitch;
			VR_QuatToYawPitch(vr_eye_view[0].pose.orientation, &yaw, &pitch);
			if (vr_prev_valid) {
				vr_head_dyaw = yaw - vr_prev_yaw;
				if (vr_head_dyaw > 180.0f) vr_head_dyaw -= 360.0f;
				if (vr_head_dyaw < -180.0f) vr_head_dyaw += 360.0f;
				vr_head_dpitch = pitch - vr_prev_pitch;
			} else {
				vr_head_dyaw = 0;
				vr_head_dpitch = 0;
			}
			vr_prev_yaw = yaw;
			vr_prev_pitch = pitch;
			vr_prev_valid = true;

			static int pose_log_counter = 0;
			if (vr_debug.value > 0 && (pose_log_counter++ % 300) == 0) {
				VR_LOG("head pos=(%.2f,%.2f,%.2f) yaw=%.1f pitch=%.1f",
					(double)vr_eye_view[0].pose.position.x, (double)vr_eye_view[0].pose.position.y,
					(double)vr_eye_view[0].pose.position.z, (double)yaw, (double)pitch);
			}
		} else {
			vr_views_valid = false;
		}
	}

	// Lectura de mandos (paso 5): rellena vr_move_x/y y dispara comandos.
	VR_PollActions();

	// A partir de aqui SCR_UpdateScreen (llamado por Host_Frame) dibujara en
	// los FBOs de los ojos en vez de en la ventana 2D.
	vr_rendering = true;
	vr_eyes_done[0] = vr_eyes_done[1] = false;
	vr_current_eye = -1;

	return true;
}

qboolean VR_EndFrame (void)
{
	if (!vr_available || vr_session == XR_NULL_HANDLE || !vr_frame_waited)
		return false;

	vr_rendering = false;
	vr_current_eye = -1;

	// Paso 3: capa de proyeccion estereo con lo dibujado en los FBOs.
	// Solo se incluyen los ojos cuyo swapchain se adquirio y dibujo este
	// frame (una imagen no adquirida en la capa es un error de validacion).
	XrCompositionLayerProjectionView proj_views[2];
	int proj_count = 0;
	memset(proj_views, 0, sizeof(proj_views));
	proj_views[0].type = XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW;
	proj_views[1].type = XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW;

	for (int eye = 0; eye < 2; eye++) {
		if (!vr_eyes_done[eye])
			continue;
		proj_views[proj_count].pose = vr_eye_view[eye].pose;
		proj_views[proj_count].fov = vr_eye_view[eye].fov;
		proj_views[proj_count].subImage.swapchain = vr_eye_swapchain[eye];
		proj_views[proj_count].subImage.imageRect.offset.x = 0;
		proj_views[proj_count].subImage.imageRect.offset.y = 0;
		proj_views[proj_count].subImage.imageRect.extent.width = vr_eye_width;
		proj_views[proj_count].subImage.imageRect.extent.height = vr_eye_height;
		proj_count++;
	}

	XrCompositionLayerProjection layer;
	memset(&layer, 0, sizeof(layer));
	layer.type = XR_TYPE_COMPOSITION_LAYER_PROJECTION;
	layer.space = vr_local_space;
	layer.viewCount = (uint32_t)proj_count;
	layer.views = proj_views;

	XrFrameEndInfo fei;
	memset(&fei, 0, sizeof(fei));
	fei.type = XR_TYPE_FRAME_END_INFO;
	fei.displayTime = vr_frame_state.predictedDisplayTime;
	fei.environmentBlendMode = XR_ENVIRONMENT_BLEND_MODE_OPAQUE;
	if (vr_views_valid && proj_count > 0) {
		fei.layerCount = 1;
		fei.layers = (const XrCompositionLayerBaseHeader *const *)&layer;
	}
	// Sin vistas validas o sin ojos dibujados: frame sin capas (HMD en negro)
	// pero el ritmo de xrWaitFrame/xrBeginFrame/xrEndFrame se mantiene.

	XrResult r = xrEndFrame(vr_session, &fei);
	vr_frame_waited = false;

	if (XR_FAILED(r)) {
		VR_LOG("xrEndFrame fallo: %s", VR_ResultString(r));
		return false;
	}
	return true;
}

// Libera FBOs, depth renderbuffers y swapchains (sesion aun viva o no).
static void VR_DestroySwapchains (void)
{
	for (int eye = 0; eye < 2; eye++) {
		if (vr_fbo_ready[eye]) {
			glDeleteFramebuffersOES((GLsizei)vr_eye_image_count[eye], vr_eye_fbo[eye]);
			memset(vr_eye_fbo[eye], 0, sizeof(vr_eye_fbo[eye]));
			vr_fbo_ready[eye] = false;
		}
		if (vr_eye_depth_rb[eye]) {
			glDeleteRenderbuffersOES(1, &vr_eye_depth_rb[eye]);
			vr_eye_depth_rb[eye] = 0;
		}
		if (vr_eye_swapchain[eye] != XR_NULL_HANDLE) {
			xrDestroySwapchain(vr_eye_swapchain[eye]);
			vr_eye_swapchain[eye] = XR_NULL_HANDLE;
		}
		vr_eye_image_count[eye] = 0;
		vr_eye_acquiring[eye] = false;
	}
	vr_eye_width = vr_eye_height = 0;
	vr_views_valid = false;
	vr_rendering = false;
	vr_swapchains_ready = false;
}

// Libera todo lo que cuelga de la sesion (swapchains, espacios, acciones).
static void VR_DestroySessionObjects (void)
{
	VR_DestroySwapchains();
	for (int h = 0; h < 2; h++) {
		if (vr_pose_space[h] != XR_NULL_HANDLE) {
			xrDestroySpace(vr_pose_space[h]);
			vr_pose_space[h] = XR_NULL_HANDLE;
		}
	}
	if (vr_local_space != XR_NULL_HANDLE) { xrDestroySpace(vr_local_space); vr_local_space = XR_NULL_HANDLE; }
	if (vr_view_space != XR_NULL_HANDLE) { xrDestroySpace(vr_view_space); vr_view_space = XR_NULL_HANDLE; }
	if (vr_actionset != XR_NULL_HANDLE) { xrDestroyActionSet(vr_actionset); vr_actionset = XR_NULL_HANDLE; }
	vr_actions_ready = false;
	if (vr_session != XR_NULL_HANDLE) { xrDestroySession(vr_session); vr_session = XR_NULL_HANDLE; }
	vr_session_state = XR_SESSION_STATE_UNKNOWN;
}

void VR_Shutdown (void)
{
	VR_DestroySessionObjects();
	// Contexto ES3 auxiliar de los swapchains.
	if (vr_es3_surf != EGL_NO_SURFACE && vr_es3_display != EGL_NO_DISPLAY) {
		eglDestroySurface(vr_es3_display, vr_es3_surf);
		vr_es3_surf = EGL_NO_SURFACE;
	}
	if (vr_es3_ctx != EGL_NO_CONTEXT && vr_es3_display != EGL_NO_DISPLAY) {
		eglDestroyContext(vr_es3_display, vr_es3_ctx);
		vr_es3_ctx = EGL_NO_CONTEXT;
	}
	if (vr_instance != XR_NULL_HANDLE) { xrDestroyInstance(vr_instance); vr_instance = XR_NULL_HANDLE; }
	vr_session_running = false;
	vr_session_focused = false;
	vr_available = false;
	vr_initialized = false;
	if (vr_log_file) { fclose(vr_log_file); vr_log_file = NULL; }
	VR_LOG("VR_Shutdown");
}

// ---------------------------------------------------------------------------
// API para el motor
// ---------------------------------------------------------------------------

qboolean VR_IsAvailable (void)
{
	return vr_available;
}

qboolean VR_IsActive (void)
{
	// NOTA: no exigimos vr_session != NULL porque la sesion se crea DENTRO
	// de VR_BeginFrame (necesita el contexto EGL del juego ya actual).
	// Solo exigimos runtime disponible + cvar activado.
	return vr_available && vr_enabled.value > 0;
}

// true mientras haya un frame VR en curso: SCR_UpdateScreen debe dibujar en
// los FBOs de los ojos (VR_BeginEye/VR_EndEye) en vez de en la ventana 2D.
qboolean VR_IsRendering (void)
{
	return vr_rendering;
}

// Convierte un quaternion OpenXR (Y-up, mano derecha) a yaw/pitch en la
// convencion del motor (grados; yaw horario, pitch positivo = abajo).
static void VR_QuatToYawPitch (XrQuaternionf q, float *yaw_deg, float *pitch_deg)
{
	float yaw_out = atan2f(2.0f * (q.w * q.y + q.x * q.z), 1.0f - 2.0f * (q.y * q.y + q.x * q.x));
	float pitch_out = asinf(2.0f * (q.w * q.x - q.y * q.z));

	*yaw_deg = -yaw_out * (180.0f / M_PI);	// OpenXR antihorario -> motor horario
	*pitch_deg = -pitch_out * (180.0f / M_PI);
}

// Paso 4: cuanto ha girado la cabeza desde el frame anterior (grados).
void VR_GetHeadDelta (float *dyaw, float *dpitch)
{
	*dyaw = vr_head_dyaw;
	*dpitch = vr_head_dpitch;
}

// Paso 5: stick izquierdo de los mandos (-1..1, y positivo = adelante).
void VR_GetMoveStick (float *x, float *y)
{
	*x = vr_move_x;
	*y = vr_move_y;
}

// Paso 5: stick derecho en juego (-1..1) para girar la vista.
void VR_GetLookStick (float *x, float *y)
{
	*x = vr_look_x;
	*y = vr_look_y;
}

// Resolucion recomendada por ojo (para viewports y FBOs).
void VR_GetEyeSize (int *width, int *height)
{
	*width = vr_eye_width;
	*height = vr_eye_height;
}

// Angulos de fov del ojo actual (radianes positivos: left/right/up/down),
// para construir la proyeccion asimetrica de cada ojo. Si no hay vistas
// localizadas devuelve un fov simetrico aproximado (90x70).
void VR_GetEyeFov (float *left, float *right, float *up, float *down)
{
	int eye = vr_current_eye;

	if (eye < 0 || eye > 1 || !vr_views_valid) {
		*left = *right = *up = *down = 0.0f;
		*left = *right = 45.0f * (float)M_PI / 180.0f;
		*up = *down = 35.0f * (float)M_PI / 180.0f;
		return;
	}

	*left = vr_eye_view[eye].fov.angleLeft;
	*right = vr_eye_view[eye].fov.angleRight;
	*up = vr_eye_view[eye].fov.angleUp;
	*down = vr_eye_view[eye].fov.angleDown;
}

// ---------------------------------------------------------------------------
// Render por ojo (paso 3). SCR_UpdateScreen llama a esto en vez de dibujar a
// la ventana: cada llamada prepara UN ojo (acquire swapchain -> bind FBO ->
// viewport). Devuelve false cuando ya no quedan ojos por dibujar.
// ---------------------------------------------------------------------------
qboolean VR_BeginEye (int eye)
{
	if (!vr_rendering || eye < 0 || eye > 1)
		return false;
	if (vr_eye_swapchain[eye] == XR_NULL_HANDLE || vr_eye_image_count[eye] == 0)
		return false;
	// Ya dibujado este frame (SCR_UpdateScreen puede llamarse 2 veces por
	// frame desde la consola): no re-adquirir la imagen.
	if (vr_eyes_done[eye] || vr_eye_acquiring[eye])
		return false;
	if (!vr_fbo_ready[eye] && !VR_CreateEyeFBOs(eye))
		return false;

	XrSwapchainImageAcquireInfo ai;
	memset(&ai, 0, sizeof(ai));
	ai.type = XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO;
	uint32_t index = 0;
	if (XR_FAILED(xrAcquireSwapchainImage(vr_eye_swapchain[eye], &ai, &index))) {
		VR_LOG("xrAcquireSwapchainImage fallo (ojo %d)", eye);
		return false;
	}

	XrSwapchainImageWaitInfo wi;
	memset(&wi, 0, sizeof(wi));
	wi.type = XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO;
	wi.timeout = XR_INFINITE_DURATION;
	if (XR_FAILED(xrWaitSwapchainImage(vr_eye_swapchain[eye], &wi))) {
		VR_LOG("xrWaitSwapchainImage fallo (ojo %d)", eye);
		// liberar lo adquirido para no bloquear el siguiente frame
		XrSwapchainImageReleaseInfo ri;
		memset(&ri, 0, sizeof(ri));
		ri.type = XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO;
		xrReleaseSwapchainImage(vr_eye_swapchain[eye], &ri);
		return false;
	}

	vr_eye_acquired[eye] = index;
	vr_eye_acquiring[eye] = true;
	vr_current_eye = eye;

	glBindFramebufferOES(GL_FRAMEBUFFER_OES, vr_eye_fbo[eye][index]);
	glViewport(0, 0, vr_eye_width, vr_eye_height);
	return true;
}

// Termina el ojo actual: desvincula el FBO y libera la imagen de swapchain.
void VR_EndEye (int eye)
{
	if (eye < 0 || eye > 1 || !vr_eye_acquiring[eye])
		return;

	glBindFramebufferOES(GL_FRAMEBUFFER_OES, 0);

	XrSwapchainImageReleaseInfo ri;
	memset(&ri, 0, sizeof(ri));
	ri.type = XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO;
	xrReleaseSwapchainImage(vr_eye_swapchain[eye], &ri);

	vr_eye_acquiring[eye] = false;
	vr_eyes_done[eye] = true;
	vr_current_eye = -1;
}

#endif // NZP_VR_OPENXR
