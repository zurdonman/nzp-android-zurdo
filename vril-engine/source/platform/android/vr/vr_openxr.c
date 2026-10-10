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
static qboolean		vr_menu_mode = false;	// true: sin mundo -> capa quad anclada lejos

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
static EGLSurface	vr_es3_surf = EGL_NO_SURFACE;	// superficie activa del ES3
static EGLSurface	vr_es3_window = EGL_NO_SURFACE;	// ventana SDL (paridad hello_xr)
static EGLSurface	vr_es3_pbuffer = EGL_NO_SURFACE;	// fallback si la ventana muere

static XrFrameState	vr_frame_state;
static qboolean		vr_frame_waited = false;

/* Paso 4: head tracking. Delta de rotacion de la cabeza entre frames, en la
 * convencion del motor (grados; yaw horario, pitch positivo = abajo). */
static float		vr_head_dyaw = 0;
static float		vr_head_yaw_motor = 0;
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
static XrAction		vr_act_click_l, vr_act_click_r;	// click de los thumbsticks
static XrAction		vr_act_haptic[2];
static XrPath		vr_hand_path[2];
static qboolean		vr_actions_ready = false;
static qboolean		vr_actions_built = false;	// set+acciones+bindings creados (una vez por instancia)
static qboolean		vr_actions_attached = false; // adjuntados a la SESION actual (una vez)

static float		vr_move_x, vr_move_y;	// stick izquierdo (consumido por in_sdl)
static float		vr_look_x, vr_look_y;	// stick derecho en juego (consumido por in_sdl)
static qboolean		vr_prev_menu = false;
static qboolean		vr_prev_menu_up, vr_prev_menu_down, vr_prev_menu_left, vr_prev_menu_right;
static double		vr_menu_next_up, vr_menu_next_down, vr_menu_next_left, vr_menu_next_right;

/* Paso 6: puntero tipo raton. Raycast del aim pose del mando contra el plano
 * del quad del menu. u/v normalizados (0..1, origen arriba-izquierda como el
 * espacio 2D del menu) en el punto de interseccion. */
static qboolean		vr_pointer_on = false;
static float		vr_pointer_u = 0.5f;
static float		vr_pointer_v = 0.5f;
/* Origen del mando proyectado en perspectiva sobre el plano del panel (para
 * dibujar el laser rojo y la marca del mando en r_screen.c). */
static float		vr_ptr_o0 = 0.5f, vr_ptr_v0 = 0.9f;
static qboolean		vr_ptr_origin_on = false;

cvar_t	vr_enabled = {"vr_enabled", "1", true};
cvar_t	vr_debug = {"vr_debug", "1", true};
cvar_t	vr_turn_speed = {"vr_turn_speed", "180", true};	// grados/segundo stick derecho
cvar_t	vr_fps = {"vr_fps", "0", true};	// 0 = sin tope (sigue al HMD); >0 = limita
cvar_t	vr_menu_distance = {"vr_menu_distance", "3.0", true};	// m: pantalla del menu (quad anclada)
cvar_t	vr_world_scale = {"vr_world_scale", "18", true};	// unidades Quake por metro (IPD/altura). >16 = jugador mas grande -> mundo se ve mas lejos, SIN deformar
cvar_t	vr_pointer = {"vr_pointer", "1", true};	// 1 = puntero de mando tipo raton en menus
cvar_t	vr_fov_mult = {"vr_fov_mult", "1.0", true};	// 1.0 = proyeccion exacta del HMD (rigido). >1 ensancha el render pero el compositor lo recorta -> efecto goma/chicle al girar (NO usar)
cvar_t	vr_vm_scale = {"vr_vm_scale", "0.65", true};	// escala del viewmodel en VR (manos/arma)
cvar_t	vr_vm_gain = {"vr_vm_gain", "5.0", true};	// holgura: multiplica mucho el desplazamiento del mando alrededor de su postura media
cvar_t	vr_hud_scale = {"vr_hud_scale", "0.65", true};	// HUD mas pequeno = mas lejos visualmente en VR
cvar_t	vr_camera_right = {"vr_camera_right", "0.75", true};	// desplazamiento lateral de la primera persona, unidades Quake
cvar_t	vr_hand_right = {"vr_hand_right", "-0.75", true};	// mano/arma a izquierda, unidades Quake
cvar_t	vr_hand_up = {"vr_hand_up", "0", true};	// mano/arma arriba/abajo, unidades Quake
cvar_t	vr_hand_forward = {"vr_hand_forward", "0", true};	// mano/arma cerca/lejos, unidades Quake
cvar_t	vr_hand_yaw = {"vr_hand_yaw", "0", true};	// giro horizontal extra del arma, grados
cvar_t	vr_hand_pitch = {"vr_hand_pitch", "0", true};	// giro vertical extra del arma, grados
cvar_t	vr_laser_muzzle = {"vr_laser_muzzle", "40", true};	// distancia desde el origen del modelo hasta la boca del canon

// Limitador del motor (host.c). En VR lo igualamos a vr_fps para que el bucle
// renderice al ritmo del compositor (72/90 Hz); si se queda en 30, Host_FilterTime
// rechaza la mitad de los frames y el HMD muestra negro intercalado = parpadeo.
extern cvar_t	cl_maxfps;

/* Navegacion del menu 2D (menu_sys.c): el puntero del mando actua como raton;
 * mueve el cursor bajo la interseccion del rayo y activa el boton pulsado. */
qboolean LoadingScreen_IsWaiting (void);
void Menu_MouseMove (int x, int y);
void Menu_ButtonPress (void);
qboolean Menu_MouseButton (int x, int y, qboolean down);

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
static void VR_ReleaseAcquiredImages (void);
static void VR_DestroySessionObjects (void);
static void VR_QuatToYawPitch (XrQuaternionf q, float *yaw_deg, float *pitch_deg);
static qboolean VR_InitActions (void);
static void VR_CreateActionSpaces (void);
static void VR_PollActions (void);
static void VR_ControllerRaycast (void);

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

/* Log de diagnostico accesible desde el bucle principal (sys_sdl.c):
 * escribe en vr_log.txt igual que VR_LOG. */
void VR_DiagLog (const char *fmt, ...)
{
	char buf[1024];
	va_list ap;

	va_start(ap, fmt);
	vsnprintf(buf, sizeof(buf), fmt, ap);
	va_end(ap);
	VR_LogWrite("%s", buf);
}

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
	Cvar_RegisterVariable (&vr_fps);
	Cvar_RegisterVariable (&vr_menu_distance);
	Cvar_RegisterVariable (&vr_world_scale);
	Cvar_RegisterVariable (&vr_pointer);
	Cvar_RegisterVariable (&vr_fov_mult);
	Cvar_RegisterVariable (&vr_vm_scale);
	Cvar_RegisterVariable (&vr_vm_gain);
	Cvar_RegisterVariable (&vr_hud_scale);
	Cvar_RegisterVariable (&vr_camera_right);
	Cvar_RegisterVariable (&vr_hand_right);
	Cvar_RegisterVariable (&vr_hand_up);
	Cvar_RegisterVariable (&vr_hand_forward);
	Cvar_RegisterVariable (&vr_hand_yaw);
	Cvar_RegisterVariable (&vr_hand_pitch);
	Cvar_RegisterVariable (&vr_laser_muzzle);

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
	// CRITICO (patrón hello_xr Android): config = (EGLConfig)0, NO la config
	// actual del juego. El runtime de Meta valida los swapchains contra la
	// config declarada en el binding; como vr_es3_ctx se creo con la config
	// offscreen id=9 (PBUFFER) y la del juego es id=7, pasar la del juego
	// provocaba un mismatch config/contexto -> "[OpenXR] swapchain validation
	// failure" en xrEndFrame (XR_ERROR_LAYER_INVALID -23). Con 0 el runtime
	// ignora la config y usa solo el contexto.
	gles_binding.config = (EGLConfig)0;
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

// Resolucion recomendada, swapchains y acciones. VERIFICADO en Quest 3: el
// runtime Meta SOLO acepta xrCreateSwapchain ANTES de xrBeginSession (estado
// READY). Crearlos despues (WaitFrame o ventana de frame) da -2 con
// "ImportTextureResourcesGLES: external memory object extensions are not
// found" en logcat.
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

	// Preferir la ventana SDL viva (paridad hello_xr); si murio, pbuffer.
	EGLSurface surf = (vr_es3_window != EGL_NO_SURFACE)
		? vr_es3_window : vr_es3_pbuffer;
	if (!eglMakeCurrent(vr_es3_display, surf, surf, vr_es3_ctx)) {
		if (surf == vr_es3_window && vr_es3_pbuffer != EGL_NO_SURFACE)
			surf = vr_es3_pbuffer;
		if (!eglMakeCurrent(vr_es3_display, surf, surf, vr_es3_ctx)) {
			VR_LOG("eglMakeCurrent ES3 fallo (0x%x)", (unsigned)eglGetError());
			return false;
		}
	}
	vr_es3_surf = surf;
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

	// PARIDAD HELLO_XR (intento decisivo contra el -23): el contexto ES3 se
	// crea con la MISMA config de la ventana del juego (id=7: renderable
	// incluye ES3 y surface incluye WINDOW+PBUFFER) y se activa sobre la
	// SUPERFICIE DE VENTANA real, no sobre un pbuffer offscreen. El
	// importador DMA-BUF/external-memory de Meta valida las extensiones
	// contra el config del contexto current: con el config offscreen id=9
	// decia "external memory object extensions are not found" -> capa -23 en
	// xrEndFrame (pre-begin) y -2 en creacion (post-begin).
	vr_es3_window = eglGetCurrentSurface(EGL_DRAW);

	const EGLint ctx_attribs[] = { EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE };
	vr_es3_ctx = eglCreateContext(vr_es3_display, config, ctx_game, ctx_attribs);
	if (vr_es3_ctx == EGL_NO_CONTEXT) {
		VR_LOG("eglCreateContext ES3 con config de ventana fallo (0x%x)",
			(unsigned)eglGetError());
		return false;
	}
	VR_LOG("Contexto ES3 con config de ventana OK (ventana=%p)",
		(void *)vr_es3_window);

	// Fallback: si la superficie de ventana muere (pause de SDL), activamos
	// el ES3 sobre un pbuffer con la MISMA config de ventana (soporta
	// PBUFFER, surface=0x15a5 lo confirma).
	const EGLint pbuf_attribs[] = { EGL_WIDTH, 16, EGL_HEIGHT, 16, EGL_NONE };
	vr_es3_pbuffer = eglCreatePbufferSurface(vr_es3_display, config, pbuf_attribs);

	vr_es3_surf = (vr_es3_window != EGL_NO_SURFACE) ? vr_es3_window : vr_es3_pbuffer;
	if (vr_es3_surf == EGL_NO_SURFACE)
		VR_LOG("AVISO: sin superficie ES3 (0x%x), eglMakeCurrent puede fallar",
			(unsigned)eglGetError());

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
				// A/B: GL_RGBA8 (0x8058) con COLOR-only + pre-begin.
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
		// A/B FINAL: COLOR-only (0x1) como hace hello_xr para el color, con
		// manifest ES3 + pre-begin. Con ES2 esta combo dio -23; las extensiones
		// de importacion activadas por el manifest ES3 pueden cambiar el resultado.
		scinfo.usageFlags = XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT;
		scinfo.format = vr_swapchain_format;
		scinfo.sampleCount = vr_eye_sample_count;
		scinfo.width = vr_eye_width;
		scinfo.height = vr_eye_height;
		scinfo.faceCount = 1;
		scinfo.arraySize = 1;
		scinfo.mipCount = 1;

		XrResult r = xrCreateSwapchain(vr_session, &scinfo, &vr_eye_swapchain[eye]);
		VR_LOG("SC ojo %d: usage=%llu fmt=0x%llx %ux%u samples=%u -> r=%d",
			eye, (unsigned long long)scinfo.usageFlags,
			(unsigned long long)scinfo.format, (unsigned)scinfo.width,
			(unsigned)scinfo.height, (unsigned)scinfo.sampleCount, (int)r);
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

	/* CAUSA RAIZ de "gatillo muerto tras dormir el casco" (apk18/19/20): la
	 * sesion OpenXR NO se destruye en los ciclos suenyo/vigilia (STOPPING->
	 * READY->FOCUSED复用 la MISMA sesion). Al volver a READY, esta funcion
	 * se llamaba otra vez y xrAttachSessionActionSets sobre una sesion que
	 * YA tenia el set adjunto responde OTHER -> vr_actions_ready=false ->
	 * sincronizacion/botones/puntero TODOS muertos. Juegos VR comerciales:
	 * action set + bindings se crean UNA vez por instancia; attach UNA vez
	 * por sesion; nunca se recrea ni se re-adjunta. */
	if (vr_actions_built && vr_actionset != XR_NULL_HANDLE) {
		if (!vr_actions_attached) {
			XrSessionActionSetsAttachInfo attach;
			memset(&attach, 0, sizeof(attach));
			attach.type = XR_TYPE_SESSION_ACTION_SETS_ATTACH_INFO;
			attach.countActionSets = 1;
			attach.actionSets = &vr_actionset;
			r = xrAttachSessionActionSets(vr_session, &attach);
			if (XR_FAILED(r)) {
				VR_LOG("re-attach acciones fallo: %s", VR_ResultString(r));
				return false;
			}
			vr_actions_attached = true;
		}
		vr_actions_ready = true;
		return true;
	}

	if (vr_actionset == XR_NULL_HANDLE) {
		memset(&asci, 0, sizeof(asci));
		asci.type = XR_TYPE_ACTION_SET_CREATE_INFO;
		Q_strncpy(asci.actionSetName, "nzp_ctl", sizeof(asci.actionSetName));
		Q_strncpy(asci.localizedActionSetName, "NZP Controls", sizeof(asci.localizedActionSetName));
		r = xrCreateActionSet(vr_instance, &asci, &vr_actionset);
		if (XR_FAILED(r)) {
			VR_LOG("xrCreateActionSet fallo: %s", VR_ResultString(r));
			return false;
		}
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
		Q_strncpy(aci.localizedActionName, name, sizeof(aci.localizedActionName));
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
		Q_strncpy(aci.localizedActionName, name, sizeof(aci.localizedActionName));
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
		Q_strncpy(aci.localizedActionName, name, sizeof(aci.localizedActionName));
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
		Q_strncpy(aci.localizedActionName, name, sizeof(aci.localizedActionName));
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
		Q_strncpy(aci.localizedActionName, name, sizeof(aci.localizedActionName));
		Q_strncpy(aci.actionName, name, sizeof(aci.actionName));
		aci.actionType = XR_ACTION_TYPE_POSE_INPUT;
		aci.countSubactionPaths = 1;
		aci.subactionPaths = &vr_hand_path[h];
		if (XR_FAILED(xrCreateAction(vr_actionset, &aci, &vr_act_pose[h]))) {
			VR_LOG("xrCreateAction pose %d fallo", h);
			return false;
		}
	}

	// Botones: A/B (derecha), X/Y (izquierda), menu (izquierda), clicks stick.
	// Cada accion lleva su subaction path: luego se consulta con la mano
	// correspondiente en VR_GetBoolAction().
	struct { XrAction *act; const char *name; int hand; } buttons[] = {
		{ &vr_act_a, "a", 1 }, { &vr_act_b, "b", 1 },
		{ &vr_act_x, "x", 0 }, { &vr_act_y, "y", 0 },
		{ &vr_act_menu, "menu", 0 },
		{ &vr_act_click_l, "click_l", 0 }, { &vr_act_click_r, "click_r", 1 },
	};
	for (size_t i = 0; i < sizeof(buttons) / sizeof(buttons[0]); i++) {
		memset(&aci, 0, sizeof(aci));
		Q_strncpy(aci.localizedActionName, buttons[i].name, sizeof(aci.localizedActionName));
		aci.type = XR_TYPE_ACTION_CREATE_INFO;
		Q_strncpy(aci.actionName, buttons[i].name, sizeof(aci.actionName));
		aci.actionType = XR_ACTION_TYPE_BOOLEAN_INPUT;
		aci.countSubactionPaths = 1;
		aci.subactionPaths = &vr_hand_path[buttons[i].hand];
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
			{ vr_act_trigger[0], VR_Path("/user/hand/left/input/trigger/value") },
			{ vr_act_trigger[1], VR_Path("/user/hand/right/input/trigger/value") },
			{ vr_act_grip[0], VR_Path("/user/hand/left/input/squeeze/value") },
			{ vr_act_grip[1], VR_Path("/user/hand/right/input/squeeze/value") },
			{ vr_act_a, VR_Path("/user/hand/right/input/a/click") },
			{ vr_act_b, VR_Path("/user/hand/right/input/b/click") },
			{ vr_act_x, VR_Path("/user/hand/left/input/x/click") },
			{ vr_act_y, VR_Path("/user/hand/left/input/y/click") },
			{ vr_act_menu, VR_Path("/user/hand/left/input/menu/click") },
			{ vr_act_click_l, VR_Path("/user/hand/left/input/thumbstick/click") },
			{ vr_act_click_r, VR_Path("/user/hand/right/input/thumbstick/click") },
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

	// xrAttachSessionActionSets: UNA sola vez por sesion (exige info explicita;
	// NULL es invalido). Si ya estaba adjunto, el bloque del principio salta.
	{
		XrSessionActionSetsAttachInfo attach;
		memset(&attach, 0, sizeof(attach));
		attach.type = XR_TYPE_SESSION_ACTION_SETS_ATTACH_INFO;
		attach.countActionSets = 1;
		attach.actionSets = &vr_actionset;
		r = xrAttachSessionActionSets(vr_session, &attach);
	}
	if (XR_FAILED(r)) {
		VR_LOG("xrAttachSessionActionSets fallo: %s", VR_ResultString(r));
		return false;
	}
	vr_actions_attached = true;

	vr_actions_ready = true;
	vr_actions_built = true;
	VR_LOG("Acciones de mandos listas (set+attach)" );
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

// ---------------------------------------------------------------------------
// Puntero tipo raton (paso 6): raycast del mando contra el panel del menu
// ---------------------------------------------------------------------------

// Rota v por el cuaternione q (v' = q * v * q_conj).
static void VR_RotateQuat (XrQuaternionf q, const float v[3], float out[3])
{
	float tx = 2.0f * (q.y * v[2] - q.z * v[1]);
	float ty = 2.0f * (q.z * v[0] - q.x * v[2]);
	float tz = 2.0f * (q.x * v[1] - q.y * v[0]);

	out[0] = v[0] + q.w * tx + (q.y * tz - q.z * ty);
	out[1] = v[1] + q.w * ty + (q.z * tx - q.x * tz);
	out[2] = v[2] + q.w * tz + (q.x * ty - q.y * tx);
}

// Interseca el rayo del mando (pose "aim": origen en el mando, direccion -Z
// local = hacia donde apunta) con el plano del quad del menu (espacio LOCAL,
// z = -dist, centrado en (0,0), tamano qw x qh = 16:10, igual que VR_EndFrame).
// Si el rayo acierta dentro del rect, guarda la posicion normalizada del panel
// (u: 0=izq 1=der, v: 0=arriba 1=abajo, como la ortho 2D del menu) y activa
// vr_pointer_on. Prioridad: mando derecho, luego izquierdo.
static void VR_ControllerRaycast (void)
{
	XrSpaceLocation loc;
	const float dir[3] = {0.0f, 0.0f, -1.0f};
	float o[3], d[3];
	float dist, qh, qw;
	qboolean found = false;
	float u = 0.5f, v = 0.5f;

	if (!vr_menu_mode || vr_pointer.value < 1.0f ||
		vr_local_space == XR_NULL_HANDLE ||
		(vr_pose_space[0] == XR_NULL_HANDLE && vr_pose_space[1] == XR_NULL_HANDLE)) {
		vr_pointer_on = false;
		vr_ptr_origin_on = false;
		return;
	}

	vr_ptr_origin_on = false;

	dist = vr_menu_distance.value;
	if (dist < 0.5f) dist = 0.5f;
	qh = 2.0f * dist * tanf(18.0f * (float)M_PI / 180.0f);
	qw = qh * 1.6f;

	for (int h = 1; h >= 0 && !found; h--) {	// derecho primero
		float t, px, py;
		if (vr_pose_space[h] == XR_NULL_HANDLE)
			continue;
		memset(&loc, 0, sizeof(loc));
		loc.type = XR_TYPE_SPACE_LOCATION;
		if (XR_FAILED(xrLocateSpace(vr_pose_space[h], vr_local_space,
				vr_frame_state.predictedDisplayTime, &loc)))
			continue;
		if (!(loc.locationFlags & (XR_SPACE_LOCATION_POSITION_VALID_BIT |
				XR_SPACE_LOCATION_ORIENTATION_VALID_BIT)))
			continue;
		o[0] = loc.pose.position.x;
		o[1] = loc.pose.position.y;
		o[2] = loc.pose.position.z;
		// Proyecta el mando en perspectiva (cabeza en z=0, panel en z=-dist)
		// sobre el plano del panel -> origen del laser + marca del mando.
		{
			float denom = -o[2];
			float s;
			if (denom < 0.05f) denom = 0.05f;
			s = dist / denom;
			vr_ptr_o0 = 0.5f + (o[0] * s) / qw;
			vr_ptr_v0 = 0.5f - (o[1] * s) / qh;
			vr_ptr_origin_on = true;
		}
		VR_RotateQuat(loc.pose.orientation, dir, d);
		if (fabsf(d[2]) < 1e-4f)
			continue;	// rayo paralelo al panel
		t = (-dist - o[2]) / d[2];
		if (t <= 0.0f)
			continue;	// el panel esta detras del mando
		px = o[0] + d[0] * t;
		py = o[1] + d[1] * t;
		if (fabsf(px) > qw * 0.5f || fabsf(py) > qh * 0.5f)
			continue;	// fuera del rect del panel
		u = 0.5f + px / qw;
		v = 0.5f - py / qh;
		found = true;
	}

	vr_pointer_on = found;
	if (found) {
		vr_pointer_u = u;
		vr_pointer_v = v;
	}
}

// Dimensiones del espacio 2D del menu en VR: SCR_UpdateScreenVR hace override
// de vid.width/height (altura = resolucion del ojo, ancho = eh*1.6 para el
// panel 16:10). Pero este codigo corre en VR_BeginFrame, ANTES de ese
// override, asi que vid.width/height aqui son los de la ventana y no valen
// para hit-test del menu. Reproducimos el override para mapear u/v a pixels.
static void VR_MenuDims (int *w, int *h)
{
	int eh = (int)vr_eye_height;
	if (eh > 0) {
		*h = eh;
		*w = (int)(eh * 1.6f);
	} else {
		*h = vid.height;
		*w = vid.width;
	}
}

static void VR_PollActions (void)
{
	float f;
	qboolean b;
	XrVector2f v2;

	if (!vr_actions_ready || vr_session == XR_NULL_HANDLE)
		return;

	{
		XrActionsSyncInfo si;
		memset(&si, 0, sizeof(si));
		si.type = XR_TYPE_ACTIONS_SYNC_INFO;
		si.countActiveActionSets = 1;
		si.activeActionSets = &(XrActiveActionSet){ vr_actionset, XR_NULL_PATH };
		(void)xrSyncActions(vr_session, &si);
	}

	// Paso 6: puntero tipo raton. El rayo del mando mueve el cursor del menu
	// a la posicion del panel que apunta (como un raton sobre la pantalla).
	// FUERA del gate de FOCUSED: xrLocateActionSpace no exige foco, y tras
	// dormir/despertar el casco hay frames VISIBLE-no-FOCUSED en los que el
	// puntero debe seguir vivo (sino parece "muerto" hasta el re-foco).
	VR_ControllerRaycast();
	if (vr_pointer_on && (key_dest == key_menu || key_dest == key_menu_pause)) {
		int mw, mh;
		VR_MenuDims(&mw, &mh);
		Menu_MouseMove((int)(vr_pointer_u * (float)mw),
			(int)(vr_pointer_v * (float)mh));
	}

	// El resto (sticks/gatillo/botones) solo importa cuando la sesion esta
	// FOCUSED: sin foco xrSyncActions da NOT_ACTIVE y las lecturas son basura.
	if (vr_session_state != XR_SESSION_STATE_FOCUSED)
		return;

	// Pantalla de carga esperando confirmacion (el "Press ENTER/SPACE to
	// skip"): key_dest ya es key_game aqui, asi que el bloque de menus de
	// abajo no corre y el gatillo se perderia como +attack. Igual que el
	// tactil (sys_sdl.c: cualquier toque = ENTER), gatillo/grip/A saltan
	// al mapa. Sin esto el usuario se queda clavado en la loading screen.
	if (LoadingScreen_IsWaiting()) {
		static qboolean prev_ld = false;
		qboolean now_ld = false;
		if (VR_GetFloatAction(vr_act_trigger[1], 1, &f) && f > 0.55f)
			now_ld = true;
		if (!now_ld && VR_GetFloatAction(vr_act_grip[1], 1, &f) && f > 0.55f)
			now_ld = true;
		if (!now_ld && VR_GetBoolAction(vr_act_a, 1, &b) && b)
			now_ld = true;
		if (now_ld && !prev_ld) {
			Key_Event(K_ENTER, true);
			Key_Event(K_ENTER, false);
		}
		prev_ld = now_ld;
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

	// Gest: els 4 gatillos (2 triggers + 2 grips) a la vegada >= 1 segon
	// obren/tancen el menu d'opcions (togglemenu). Cal soltar-los tots per
	// poder tornar a activar el gest.
	{
		qboolean g_all, g_t0 = false, g_t1 = false, g_g0 = false, g_g1 = false;
		static double g_hold_start = 0.0;
		static qboolean g_fired = false;
		float gf;
		if (VR_GetFloatAction(vr_act_trigger[0], 0, &gf)) g_t0 = gf > 0.5f;
		if (VR_GetFloatAction(vr_act_trigger[1], 1, &gf)) g_t1 = gf > 0.5f;
		if (VR_GetFloatAction(vr_act_grip[0], 0, &gf)) g_g0 = gf > 0.5f;
		if (VR_GetFloatAction(vr_act_grip[1], 1, &gf)) g_g1 = gf > 0.5f;
		g_all = g_t0 && g_t1 && g_g0 && g_g1;
		if (g_all) {
			if (g_hold_start == 0.0)
				g_hold_start = Sys_FloatTime();
			else if (!g_fired && (Sys_FloatTime() - g_hold_start) >= 1.0) {
				g_fired = true;
				Cbuf_AddText("togglemenu\n");
			}
		} else {
			g_hold_start = 0.0;
			g_fired = false;
		}
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

	/* Reservar el par gatillo+grip izquierdo antes de emitir +aim/+use. */
	qboolean aim_use_chord = false;
	{
		float chord_trigger = 0.0f, chord_grip = 0.0f;
		(void)VR_GetFloatAction(vr_act_trigger[0], 0, &chord_trigger);
		(void)VR_GetFloatAction(vr_act_grip[0], 0, &chord_grip);
		aim_use_chord = chord_trigger > 0.5f && chord_grip > 0.5f;
	}

	// Gatillo izquierdo -> apuntar.
	if (!aim_use_chord && VR_GetFloatAction(vr_act_trigger[0], 0, &f)) {
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
	if (!aim_use_chord && VR_GetFloatAction(vr_act_grip[0], 0, &f)) {
		static qboolean prev = false;
		qboolean now = f > 0.5f;
		if (now != prev) {
			Cbuf_AddText(now ? "+use\n" : "-use\n");
			prev = now;
		}
	}

	// A -> saltar; B -> cambiar arma; X -> granada; Y -> cuchillo.
	/* Combinaciones para acciones que el juego tiene pero que no caben como
	 * botones independientes en Quest Touch. Se leen antes de los botones
	 * normales y, mientras estan activas, esos botones quedan reservados para
	 * la combinacion. */
	qboolean combo_l3 = false, combo_r3 = false;
	qboolean combo_x = false, combo_y = false, combo_b = false;
	qboolean combo_aim = false, combo_use = false;
	static double chord_l3_since = 0.0, chord_x_since = 0.0;
	static double chord_aim_since = 0.0;
	const double chord_now = Sys_FloatTime();
	(void)VR_GetBoolAction(vr_act_click_l, 0, &combo_l3);
	(void)VR_GetBoolAction(vr_act_click_r, 1, &combo_r3);
	(void)VR_GetBoolAction(vr_act_x, 0, &combo_x);
	(void)VR_GetBoolAction(vr_act_y, 0, &combo_y);
	(void)VR_GetBoolAction(vr_act_b, 1, &combo_b);
	(void)VR_GetFloatAction(vr_act_trigger[0], 0, &f);
	combo_aim = f > 0.5f;
	(void)VR_GetFloatAction(vr_act_grip[0], 0, &f);
	combo_use = f > 0.5f;
	if (combo_l3 && !combo_r3) {
		if (chord_l3_since == 0.0) chord_l3_since = chord_now;
	} else if (!combo_l3) {
		chord_l3_since = 0.0;
	}
	if (combo_x && !combo_y && !combo_b) {
		if (chord_x_since == 0.0) chord_x_since = chord_now;
	} else if (!combo_x) {
		chord_x_since = 0.0;
	}
	if (combo_aim && !combo_use) {
		if (chord_aim_since == 0.0) chord_aim_since = chord_now;
	} else if (!combo_aim) {
		chord_aim_since = 0.0;
	}
	/* Give a second button 180 ms to join a chord. This prevents the first
	 * button from firing its standalone action when the user presses the pair
	 * naturally, in either order. */
	const qboolean chord_l3_pending = combo_l3 && !combo_r3 &&
		(chord_now - chord_l3_since < 0.18);
	const qboolean chord_x_pending = combo_x && !combo_y && !combo_b &&
		(chord_now - chord_x_since < 0.18);
	const qboolean chord_aim_pending = combo_aim && !combo_use &&
		(chord_now - chord_aim_since < 0.18);

	/* L3 + R3: tirarse al suelo. */
	{
		static qboolean prev = false;
		qboolean now = combo_l3 && combo_r3;
		if (now && !prev)
			Cbuf_AddText("impulse 32\n");
		prev = now;
	}
	/* X + B: cambiar entre frag y Bouncing Betty. */
	{
		static qboolean prev = false;
		qboolean now = combo_x && combo_b;
		if (now && !prev)
			Cbuf_AddText("impulse 25\n");
		prev = now;
	}
	/* Gatillo izquierdo + grip izquierdo: alternar ADS hold/toggle. */
	{
		static qboolean prev = false;
		qboolean now = combo_aim && combo_use;
		if (now && !prev)
			Cbuf_AddText("impulse 26\n");
		prev = now;
	}
	/* X + Y: preparar Bouncing Betty. */
	{
		static qboolean prev = false;
		qboolean now = combo_x && combo_y;
		if (now && !prev)
			Cbuf_AddText("impulse 33\n");
		prev = now;
	}

	if (VR_GetBoolAction(vr_act_a, 1, &b)) {
		static qboolean prev = false;
		if (b != prev) { Cbuf_AddText(b ? "+jump\n" : "-jump\n"); prev = b; }
	}
	if (VR_GetBoolAction(vr_act_b, 1, &b) && !combo_x && !combo_y &&
		!chord_x_pending) {
		static qboolean prev = false;
		if (b != prev) { Cbuf_AddText(b ? "+switch\n" : "-switch\n"); prev = b; }
	}
	if (VR_GetBoolAction(vr_act_x, 0, &b) && !combo_b && !combo_y &&
		!chord_x_pending) {
		static qboolean prev = false;
		if (b != prev) { Cbuf_AddText(b ? "+grenade\n" : "-grenade\n"); prev = b; }
	}
	if (VR_GetBoolAction(vr_act_y, 0, &b) && !combo_x && !chord_x_pending) {
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

	// Click stick IZQUIERDO -> esprintar mientras se mantiene.
	if (VR_GetBoolAction(vr_act_click_l, 0, &b) && !combo_r3 &&
		!chord_l3_pending) {
		static qboolean prev = false;
		if (b != prev)
			Cbuf_AddText(b ? "impulse 23\n" : "impulse 24\n");
		prev = b;
	}
	// Click stick DERECHO -> agacharse/levantarse (impulso 31).
	if (VR_GetBoolAction(vr_act_click_r, 1, &b) && !combo_l3 &&
		!chord_l3_pending) {
		static qboolean prev = false;
		if (b && !prev)
			Cbuf_AddText("impulse 31\n");
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
		// Gatillo derecho en menus: si el rayo del mando acierta en el panel,
		// es CLIC de raton en esa posicion (activa el boton apuntado; si es
		// slider, inicia/termina el arrastre). Si el rayo no acierta, mantiene
		// Enter (aceptar el item del cursor del stick) como respaldo.
		// Un unico detector de flanco evita doble-fire y sliders "clavados"
		// cuando el puntero sale del panel a mitad de arrastre.
		{
			static qboolean prev_trig = false;
			qboolean now_trig = false;
			int px = 0, py = 0;

			if (vr_pointer_on) {
				int mw, mh;
				VR_MenuDims(&mw, &mh);
				px = (int)(vr_pointer_u * (float)mw);
				py = (int)(vr_pointer_v * (float)mh);
			}
			// En menus, TODO boton tipo gatillo hace clic: trigger derecho,
			// squeeze/grip derecho (el "gatillo de arriba" del mando Quest),
			// trigger izquierdo o A. El usuario no tiene que recordar cual es.
			if (VR_GetFloatAction(vr_act_trigger[1], 1, &f) && f > 0.55f)
				now_trig = true;
			if (!now_trig && VR_GetFloatAction(vr_act_grip[1], 1, &f) && f > 0.55f)
				now_trig = true;
			if (!now_trig && VR_GetFloatAction(vr_act_trigger[0], 0, &f) && f > 0.55f)
				now_trig = true;
			if (!now_trig && VR_GetBoolAction(vr_act_a, 1, &b) && b)
				now_trig = true;
			if (now_trig && !prev_trig) {
				if (vr_pointer_on) {
					if (!Menu_MouseButton(px, py, true))
						Menu_ButtonPress();
				} else {
					Key_Event(K_ENTER, true);
					Key_Event(K_ENTER, false);
				}
			}
			if (!now_trig && prev_trig)
				Menu_MouseButton(0, 0, false);	// suelta el slider si habia
			prev_trig = now_trig;
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
					case XR_SESSION_STATE_EXITING:
						// Cierre definitivo (usuario saca la app del rincon VR o
						// la cierra desde ajustes). El proceso SDL queda vivo con
						// la sesion muerta y al reabrirla Android lo reutiliza ->
						// pantalla negra / no arranca (reportado por el usuario).
						// Las apps Oculus correctas se terminan a si mismas aqui.
						VR_LOG("Sesion EXITING: cierre limpio del proceso");
						fflush(vr_log_file);
						exit(0);
						break;
					case XR_SESSION_STATE_STOPPING:
					case XR_SESSION_STATE_LOSS_PENDING:
						vr_session_focused = false;
						// Fin de sesion: la spec exige destruir los
						// swapchains ANTES de xrEndSession. Se recrean
						// cuando vuelva READY (VR_BeginFrame reintenta el
						// ciclo completo pre-begin).
						VR_DestroySwapchains();
						// NO tocar acciones/espacios/attach: verificado en
						// un juego VR comercial (gta-sa-vr-quest) — el
						// handle de sesion SOBREVIVE los ciclos sueno/despier-
						// tacion (STOPPING->READY->FOCUSED) y el action set
						// queda adjuntado PARA SIEMPRE. xrAttachSessionAction-
						// Sets solo es legal ANTES del primer xrBeginSession:
						// re-adjuntar tras el restart responde OTHER y mataba
						// los mandos (bug apk18-20). Los pose spaces tambien
						// sobreviven: xrLocateSpace simplemente falla hasta
						// el proximo FOCUSED.
						if (vr_session_running && e->state == XR_SESSION_STATE_STOPPING)
							xrEndSession(vr_session);
						vr_session_running = false;
						vr_frame_waited = false;
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
	// pantalla apagada) no aparecera en vr_log.txt. Cada 1800 frames (~30 s
	// a 60 fps) para no inundar el log.
	if ((++vr_heartbeat_frames % 1800) == 0)
		VR_LOG("HB frames=%d estado=%d running=%d sc=%d", vr_heartbeat_frames,
			(int)vr_session_state, (int)vr_session_running, (int)vr_swapchains_ready);

	VR_PumpEvents();

	// Limitador de FPS (vr_fps): mientras la sesion VR corre, el ritmo lo
	// marca xrWaitFrame (refresh del HMD, 72/90 Hz). Si Host_FilterTime sigue
	// con cl_maxfps=30, rechaza ~la mitad de los frames y el compositor pinta
	// negro en los huecos = parpadeo. Iguala cl_maxfps a vr_fps en VR y lo
	// restaura al salir. cl_maxfps<1 => sin limite (deja correr a la frecuencia
	// del visor, lo ideal en VR).
	{
		static float vr_saved_maxfps = -1.0f;
		if (vr_session_running) {
			if (vr_saved_maxfps < 0.0f)
				vr_saved_maxfps = cl_maxfps.value;
			/* PARPADEO (caiz): en VR el ritmo lo marca xrWaitFrame (refresh
			 * real del HMD: 72/80/90/120 Hz). Si Host_FilterTime mantiene un
			 * tope (cl_maxfps) por debajo de ese refresh, rechaza frames por
			 * jitter y esos frames se envian a xrEndFrame SIN capa => negro
			 * intercalado = parpadeo. vr_fps=0 (default) => sin tope: el limite
			 * real es el visor y NUNCA se rechaza un frame. vr_fps>0 => tope
			 * explicito (solo sirve si se pone AL refresh del HMD o mas alto). */
			float want = vr_fps.value;
			if (want < 1.0f) want = 999.0f;
			if (fabsf(cl_maxfps.value - want) > 0.5f)
				Cvar_SetValue("cl_maxfps", want);
		} else if (vr_saved_maxfps >= 0.0f) {
			Cvar_SetValue("cl_maxfps", vr_saved_maxfps);
			vr_saved_maxfps = -1.0f;
		}
	}

	// Fase 1: crear la sesion GLES en el primer frame, cuando el contexto
	// del juego ya esta actual (VID_Init ya paso). Los eventos de estado
	// (IDLE -> READY -> ...) llegan en frames siguientes.
	if (vr_session == XR_NULL_HANDLE) {
		(void)VR_CreateSession();	// si falla, se reintenta el proximo frame
		return false;
	}

	// Fase 2: con la sesion en READY y ANTES de iniciarla, crear swapchains.
	// VERIFICADO en Quest 3 (runtime Meta): post-BeginFrame da -2 SIEMPRE
	// (logcat "ImportTextureResourcesGLES: no import extensions found" en el
	// HILO DEL RUNTIME, donde eglGetCurrentContext()=0x0; el manifiesto ES3 no
	// lo arregla). Pre-begin SI crea (r=0, texturas asignadas server-side).
	if (!vr_swapchains_ready && vr_session_state == XR_SESSION_STATE_READY) {
		VR_LOG("Creando swapchains PRE-begin (estado=READY)");
		if (!VR_CreateSessionObjects())
			return false;	// reintenta el proximo frame
	}

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
		VR_LOG("Sesion INICIADA (estereo); swapchains se crean en ventana de frame");
		return false;
	}

	// xrWaitFrame SIEMPRE que la sesion esta iniciada, sea cual sea el
	// estado. El runtime de Meta solo emite SYNCHRONIZED/VISIBLE/FOCUSED
	// DESPUES de ver el primer xrWaitFrame; si esperamos el estado para
	// llamarlo, la sesion queda clavada en READY (HMD en negro). Orden
	// canonico hello_xr: Wait -> Begin -> (render) -> End cada frame.
	if (!vr_frame_waited) {
		XrFrameWaitInfo fwi;
		memset(&fwi, 0, sizeof(fwi));
		fwi.type = XR_TYPE_FRAME_WAIT_INFO;
		memset(&vr_frame_state, 0, sizeof(vr_frame_state));
		vr_frame_state.type = XR_TYPE_FRAME_STATE;

		XrResult rw = xrWaitFrame(vr_session, &fwi, &vr_frame_state);
		if (XR_FAILED(rw)) {
			// SessionEventInvalid/session cerrada: el proximo PumpEvents
			// pondra el estado correcto y se reintentara el ciclo.
			VR_LOG("xrWaitFrame fallo: %s", VR_ResultString(rw));
			return false;
		}
		vr_frame_waited = true;
	}

	XrFrameBeginInfo fbi;
	memset(&fbi, 0, sizeof(fbi));
	fbi.type = XR_TYPE_FRAME_BEGIN_INFO;

	if (XR_FAILED(xrBeginFrame(vr_session, &fbi))) {
		vr_frame_waited = false;
		return false;
	}

	// Solo dibujamos cuando el compositor lo pide (shouldRender) y la sesion
	// esta sincronizada/visible/focused. En READY u otros estados hacemos el
	// ciclo Wait/Begin/End VACIO (sin capas) para que el runtime avance.
	vr_rendering = vr_frame_state.shouldRender == XR_TRUE &&
		(vr_session_state == XR_SESSION_STATE_SYNCHRONIZED ||
		 vr_session_state == XR_SESSION_STATE_VISIBLE ||
		 vr_session_state == XR_SESSION_STATE_FOCUSED);

	if (vr_rendering) {
		// Poses de la cabeza + vistas por ojo (paso 3/4). Se localizan en el
		// espacio LOCAL: esas mismas poses son las que exige la capa de
		// proyeccion en xrEndFrame.
		XrViewState view_state;
		memset(&view_state, 0, sizeof(view_state));
		view_state.type = XR_TYPE_VIEW_STATE;

		XrViewLocateInfo vli;
		memset(&vli, 0, sizeof(vli));
		vli.type = XR_TYPE_VIEW_LOCATE_INFO;
		vli.viewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
		vli.displayTime = vr_frame_state.predictedDisplayTime;
		vli.space = vr_local_space;

		/* ANTI-PARPANDEO: localizar en un buffer temporal. Solo si xrLocateViews
		 * tiene exito copiamos a vr_eye_view; si falla UN frame conservamos la
		 * ultima pose valida (el memset anterior a la llamada borraba las poses
		 * y el frame se enviaba SIN capa a xrEndFrame = destello negro). */
		XrView tmp_views[2];
		memset(tmp_views, 0, sizeof(tmp_views));
		tmp_views[0].type = XR_TYPE_VIEW;
		tmp_views[1].type = XR_TYPE_VIEW;
		uint32_t located = 0;

		if (XR_SUCCEEDED(xrLocateViews(vr_session, &vli, &view_state, 2, &located, tmp_views)) && located > 0) {
			memcpy(vr_eye_view, tmp_views, sizeof(vr_eye_view));
			vr_views_valid = true;

			// Paso 4: delta de rotacion de la cabeza entre frames (solo yaw
			// y pitch; el roll del motor no se usa). La primera vez solo
			// guardamos la referencia (sin salto de camara).
			float yaw, pitch;
			VR_QuatToYawPitch(vr_eye_view[0].pose.orientation, &yaw, &pitch);
			// yaw ya en convencion del motor (la misma que BodyRot usa para
			// componer la vista por ojo): mundo = yaw_cuerpo + yaw_cabeza.
			vr_head_yaw_motor = yaw;
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
	} else {
		vr_views_valid = false;
	}

	// Lectura de mandos (paso 5): rellena vr_move_x/y y dispara comandos.
	VR_PollActions();

	// A partir de aqui SCR_UpdateScreen (llamado por Host_Frame) dibujara en
	// los FBOs de los ojos (solo si vr_rendering) en vez de la ventana 2D.
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
		// OpenXR (header de este SDK): XrSwapchainSubImage NO tiene
		// imageIndex; el compositor presenta implicitamente LA imagen
		// adquirida este frame (xrAcquireSwapchainImage en VR_BeginEye).
		proj_views[proj_count].subImage.imageArrayIndex = 0;
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

	// DIAGNOSTICO crash xrEndFrame: volcar el contenido EXACTO de la capa los
	// primeros frames (espacio, swapchains, indices adquiridos, rects, fov)
	// para ver que campo esta corrupto/nulo cuando Meta derefencia 0x23.
	{
		static int layer_dump = 0;
		if (layer_dump < 6) {
			layer_dump++;
			VR_LOG("LAYERDUMP n=%d space=%p sc0=%p sc1=%p acq=[%u,%u] done=[%d,%d] w=%u h=%u",
				proj_count, (void *)(uintptr_t)vr_local_space,
				(void *)(uintptr_t)vr_eye_swapchain[0], (void *)(uintptr_t)vr_eye_swapchain[1],
				(unsigned)vr_eye_acquired[0], (unsigned)vr_eye_acquired[1],
				(int)vr_eyes_done[0], (int)vr_eyes_done[1],
				(unsigned)vr_eye_width, (unsigned)vr_eye_height);
			for (int v = 0; v < proj_count; v++) {
				VR_LOG("LAYERDUMP view[%d] swap=%p rect=(%d,%d %ux%u) arr=%u pose.p=(%.2f,%.2f,%.2f) fov=(%.2f,%.2f,%.2f,%.2f)",
					v, (void *)(uintptr_t)proj_views[v].subImage.swapchain,
					(int)proj_views[v].subImage.imageRect.offset.x,
					(int)proj_views[v].subImage.imageRect.offset.y,
					(unsigned)proj_views[v].subImage.imageRect.extent.width,
					(unsigned)proj_views[v].subImage.imageRect.extent.height,
					(unsigned)proj_views[v].subImage.imageArrayIndex,
					(double)proj_views[v].pose.position.x, (double)proj_views[v].pose.position.y,
					(double)proj_views[v].pose.position.z,
					(double)proj_views[v].fov.angleLeft, (double)proj_views[v].fov.angleRight,
					(double)proj_views[v].fov.angleUp, (double)proj_views[v].fov.angleDown);
			}
		}
	}

	// MODO MENU (sin mundo cargado): presentamos el 2D como una capa QUAD
	// ANCLADA al espacio LOCAL a vr_menu_distance metros al frente. Al estar
	// anclada, giras la cabeza y el menu SE QUEDA EN SU SITIO (no te sigue), y
	// a 3 m se ve entero y comodo en un entorno negro. Con mapa cargado usamos
	// la capa de PROYECCION estereo (camara fija en el mundo, la cabeza mira).
	XrCompositionLayerQuad quad;
	memset(&quad, 0, sizeof(quad));
	qboolean quad_layer = (vr_menu_mode && proj_count > 0 && vr_eyes_done[0]);
	if (quad_layer) {
		float dist = vr_menu_distance.value;
		float qh, qw;
		if (dist < 0.5f) dist = 0.5f;
		// fov vertical ~36 grados: alto = 2*d*tan(18). Ancho 16:10 (igual que
		// la ortho del menu en r_screen.c): con el aspect del ojo (0.95, casi
		// cuadrado) el panel quedaba "corto" y las letras apretadas.
		qh = 2.0f * dist * tanf(18.0f * (float)M_PI / 180.0f);
		qw = qh * 1.6f;
		quad.type = XR_TYPE_COMPOSITION_LAYER_QUAD;
		quad.space = vr_local_space;
		quad.pose.position.x = 0.0f;
		quad.pose.position.y = 0.0f;
		quad.pose.position.z = -dist;
		quad.pose.orientation.w = 1.0f;
		quad.eyeVisibility = XR_EYE_VISIBILITY_BOTH;
		quad.subImage.swapchain = vr_eye_swapchain[0];
		quad.subImage.imageRect.offset.x = 0;
		quad.subImage.imageRect.offset.y = 0;
		quad.subImage.imageRect.extent.width = vr_eye_width;
		quad.subImage.imageRect.extent.height = vr_eye_height;
		quad.size.width = qw;
		quad.size.height = qh;
	}

	XrFrameEndInfo fei;
	memset(&fei, 0, sizeof(fei));
	fei.type = XR_TYPE_FRAME_END_INFO;
	fei.displayTime = vr_frame_state.predictedDisplayTime;
	fei.environmentBlendMode = XR_ENVIRONMENT_BLEND_MODE_OPAQUE;
	if (vr_views_valid && proj_count > 0) {
		// El compositor Meta va a muestrear las texturas del swapchain en su
		// propio contexto GL: hay que haber sometido los comandos de dibujo
		// (los samples GLES de Meta/hello_xr hacen glFinish aqui).
		glFinish();

		// CRITICO Meta/GLES: xrEndFrame procesa la capa usando el contexto
		// PASADO en el graphics binding de la sesion (nuestro ES3 auxiliar),
		// no el contexto 1.1 del juego que esta current ahora. Si el runtime
		// no lo encuentra, dereferencia un objeto interno nulo -> SIGSEGV
		// (fault addr 0x23) dentro de libvrapiimpl. hello_xr mantiene el
		// contexto de binding current durante todo el ciclo de frame.
		EGLContext ctx_game = eglGetCurrentContext();
		EGLSurface es3_draw = EGL_NO_SURFACE, es3_read = EGL_NO_SURFACE;
		qboolean es3_bound = VR_BindES3Context(&es3_draw, &es3_read);

		// ===== FIX RAIZ DEL -23 (XR_ERROR_LAYER_INVALID) =====
		// La spec OpenXR y hello_xr exigen que la imagen del swapchain este en
		// estado READY cuando xrEndFrame procesa la capa. READY significa:
		// adquirida -> esperada -> DIBUJADA -> LIBERADA. Nosotros liberabamos
		// DESPUES de xrEndFrame (VR_ReleaseAcquiredImages al final), asi la capa
		// referenciaba una imagen aun "acquired" y el compositor de Meta la
		// rechazaba con -23 en TODAS las combinaciones de usage/formato/orden/
		// manifest/tipo-de-capa (por eso el quad fallaba igual). El SIGSEGV que
		// temiamos por liberar antes era en realidad el bug de fei.layers (ya
		// corregido), no el orden de release. Se libera aqui con ES3 current.
		for (int eye = 0; eye < 2; eye++) {
			if (!vr_eye_acquiring[eye] || vr_eye_swapchain[eye] == XR_NULL_HANDLE)
				continue;
			XrSwapchainImageReleaseInfo ri;
			memset(&ri, 0, sizeof(ri));
			ri.type = XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO;
			if (XR_FAILED(xrReleaseSwapchainImage(vr_eye_swapchain[eye], &ri)))
				VR_LOG("xrReleaseSwapchainImage fallo (ojo %d)", eye);
			vr_eye_acquiring[eye] = false;
		}

		fei.layerCount = 1;
		// CRITICO: XrFrameEndInfo::layers es un PUNTERO A UN ARRAY DE PUNTEROS
		// a capas (const XrCompositionLayerBaseHeader* const*), NO la direccion
		// de la estructura de capa. hello_xr hace:
		//   std::array<const XrCompositionLayerBaseHeader*,1> layers{};
		//   layers[0] = (const XrCompositionLayerBaseHeader*)&projLayer;
		//   frameEndInfo.layers = layers.data();
		// Pasar &layer (la struct) hacia que el runtime leyera los primeros 8
		// bytes (el campo type=69) como si fueran un puntero -> dereferencia
		// una direccion basura -> SIGSEGV determinista en libvrapiimpl dentro
		// de xrEndFrame (fault addr 0x23, offset 0x7d4568). ESA era la causa
		// real del crash, no el orden de los swapchains.
		const XrCompositionLayerBaseHeader *layer_ptrs[1];
		layer_ptrs[0] = quad_layer
			? (const XrCompositionLayerBaseHeader *)&quad
			: (const XrCompositionLayerBaseHeader *)&layer;
		fei.layers = layer_ptrs;

		// Diagnostico periodico: que capa real se envia (quad anclada = menu,
		// proyeccion = mundo estereo). Cada ~300 frames para no inundar.
		{
			static int layer_kind_counter = 0;
			if ((layer_kind_counter++ % 300) == 0) {
				if (quad_layer)
					VR_LOG("CAPA=QUAD anclada dist=%.2f menu=%d ojos=%d",
						(double)quad.pose.position.z * -1.0, (int)vr_menu_mode, proj_count);
				else
					VR_LOG("CAPA=PROJ estereo menu=%d ojos=%d eye0.p.x=%.3f eye1.p.x=%.3f",
						(int)vr_menu_mode, proj_count,
						(double)proj_views[0].pose.position.x,
						(double)(proj_count > 1 ? proj_views[1].pose.position.x : 0.0));
			}
		}

		XrResult r2 = xrEndFrame(vr_session, &fei);
		if (es3_bound)
			VR_RestoreGameContext(es3_draw, es3_read, ctx_game);
		vr_frame_waited = false;
		// NO liberar aqui: las imagenes ya se liberaron ANTES de xrEndFrame
		// (requisito de estado READY de la capa). Doble release daria error.
		if (XR_FAILED(r2)) {
			VR_LOG("xrEndFrame fallo: %s (%d)", VR_ResultString(r2), (int)r2);
			return false;
		}
		return true;
	}
	// Sin vistas validas o sin ojos dibujados: frame sin capas (HMD en negro)
	// pero el ritmo de xrWaitFrame/xrBeginFrame/xrEndFrame se mantiene.
	XrResult r = xrEndFrame(vr_session, &fei);
	vr_frame_waited = false;
	VR_ReleaseAcquiredImages();
	if (XR_FAILED(r)) {
		VR_LOG("xrEndFrame (sin capas) fallo: %s (%d)", VR_ResultString(r), (int)r);
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
	/* El action set NO se destruye aqui: VR_InitActions destruye las acciones
	 * y el set viejo antes de crear el nuevo (xrDestroyActionSet con acciones
	 * vivas falla silenciosamente y deja el nombre ocupado -> OTHER). */
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
	if (vr_actionset != XR_NULL_HANDLE) { xrDestroyActionSet(vr_actionset); vr_actionset = XR_NULL_HANDLE; }
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

// true una vez que xrBeginSession tuvo exito: el compositor VR reclama el
// display y el swap de la ventana 2D (SDL_GL_SwapWindow) haria spin-wait de
// un VBlank que ya no llega, congelando el bucle. GL_EndRendering debe
// saltarse el swap 2D en cuanto esto sea true.
qboolean VR_IsSessionStarted (void)
{
	return vr_available && vr_enabled.value > 0 && vr_session_running;
}

// true mientras haya un frame VR en curso: SCR_UpdateScreen debe dibujar en
// los FBOs de los ojos (VR_BeginEye/VR_EndEye) en vez de en la ventana 2D.
qboolean VR_IsRendering (void)
{
	return vr_rendering;
}

// Modo menu (lo fija SCR_UpdateScreenVR cada frame): true = no hay mundo
// cargado (menu/consola forzada). VR_EndFrame presenta entonces una capa QUAD
// ANCLADA al espacio LOCAL a vr_menu_distance metros, en vez de la capa de
// proyeccion (que es una ventana pegada a la cabeza -> seguiria a las gafas).
void VR_SetMenuMode (qboolean menu_mode)
{
	vr_menu_mode = menu_mode;
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

// Yaw absoluto de la cabeza relativo al ancla del cuerpo (grados, convencion
// del motor: + = anti-horario). El movimiento en VR debe seguir a las gafas.
void VR_GetHeadYawMotor (float *yaw)
{
	*yaw = vr_head_yaw_motor;
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

// Paso 6: puntero tipo raton. Devuelve si el rayo del mando intersecta el
// panel del menu este frame y la posicion normalizada (u/v 0..1, origen
// arriba-izquierda como la ortho 2D) del punto de interseccion.
void VR_GetPointer (qboolean *on, float *u, float *v)
{
	*on = vr_pointer_on;
	*u = vr_pointer_u;
	*v = vr_pointer_v;
}

// Paso 6: origen del mando proyectado sobre el panel (para el laser). True si
// hay un mando con pose valida este frame.
void VR_GetPointerOrigin (qboolean *on, float *u, float *v)
{
	*on = vr_ptr_origin_on;
	*u = vr_ptr_o0;
	*v = vr_ptr_v0;
}

// Resolucion recomendada por ojo (para viewports y FBOs).
void VR_GetEyeSize (int *width, int *height)
{
	*width = vr_eye_width;
	*height = vr_eye_height;
}

// Angulos de fov del ojo actual (radianes POSITIVOS: left/right/up/down),
// para construir la proyeccion asimetrica de cada ojo. OJO: OpenXR entrega
// angleLeft y angleDown NEGATIVOS (angulo con signo: el log muestra
// fov=(-0.94,0.70,0.77,-0.96)). Si se pasan tal cual a VR_SetupEyeProjection
// (que hace -tan(left)), el frustum queda invertido (left>right) -> matriz
// degenerada -> el mundo NO se dibuja (pantalla negra). Normalizamos signos.
void VR_GetEyeFov (float *left, float *right, float *up, float *down)
{
	int eye = vr_current_eye;

	if (eye < 0 || eye > 1 || !vr_views_valid) {
		*left = *right = *up = *down = 0.0f;
		*left = *right = 45.0f * (float)M_PI / 180.0f;
		*up = *down = 35.0f * (float)M_PI / 180.0f;
		return;
	}

	*left = fabsf(vr_eye_view[eye].fov.angleLeft);
	*right = fabsf(vr_eye_view[eye].fov.angleRight);
	*up = fabsf(vr_eye_view[eye].fov.angleUp);
	*down = fabsf(vr_eye_view[eye].fov.angleDown);
}

// ---------------------------------------------------------------------------
// Helpers de pose de mando (manos virtuales).
// ---------------------------------------------------------------------------

// Rotation matrix Rz(yaw)*Ry(pitch)*Rx(roll) row-major (Quake convention).
static void VR_BodyRot (float yaw_deg, float pitch_deg, float roll_deg, float rb[3][3])
{
	float cy = cosf(yaw_deg * (float)M_PI / 180.0f), sy = sinf(yaw_deg * (float)M_PI / 180.0f);
	float cp = cosf(pitch_deg * (float)M_PI / 180.0f), sp = sinf(pitch_deg * (float)M_PI / 180.0f);
	float cr = cosf(roll_deg * (float)M_PI / 180.0f), sr = sinf(roll_deg * (float)M_PI / 180.0f);
	/* tmp = Ry(pitch)*Rx(roll) (fila-major). Multiplicacion explicita:
	 *   fila0 = [cp, sp*sr, sp*cr]   (antes estaba [cp, sp*cr, sp*sr]:
	 *   columnas 1 y 2 intercambiadas => matriz NO ortogonal => el mundo
	 *   se veia torcido/roto dentro del mapa, reporte apk23) */
	float tmp[3][3] = {
		{ cp, sp*sr, sp*cr },
		{ 0,  cr,   -sr },
		{ -sp, cp*sr, cp*cr }
	};
	rb[0][0] = cy*tmp[0][0] - sy*tmp[1][0];
	rb[0][1] = cy*tmp[0][1] - sy*tmp[1][1];
	rb[0][2] = cy*tmp[0][2] - sy*tmp[1][2];
	rb[1][0] = sy*tmp[0][0] + cy*tmp[1][0];
	rb[1][1] = sy*tmp[0][1] + cy*tmp[1][1];
	rb[1][2] = sy*tmp[0][2] + cy*tmp[1][2];
	rb[2][0] = tmp[2][0];
	rb[2][1] = tmp[2][1];
	rb[2][2] = tmp[2][2];
}

// ---------------------------------------------------------------------------
// Matriz de vista por ojo (3D estereo ANCLADO al mundo).
//
// Clave: la capa de proyeccion pasa al compositor la pose de cada ojo en el
// espacio LOCAL (giro de cabeza + IPD incluidos). Para que el mundo quede FIJO
// (no seguir a la cabeza), la camara del juego debe ser EXACTAMENTE esa pose
// compuesta con el transform del cuerpo del jugador:
//
//   view_gl = Basis · inverse( M_cuerpo · LocalAQuake( pose_ojo ) )
//
// El compositor re-proyecta con la pose del ojo; al coincidir con la camara,
// girar la cabeza mueve la camara DENTRO del mundo (no la imagen). Y como la
// pose ya trae el offset IPD por ojo, las dos vistas difieren lateralmente =>
// relieve 3D real. (Patrón quakevr vr.cpp: la pose de cabeza va a la CAMARA,
// NO a cl.viewangles; por eso antes la pantalla seguia a la cabeza.)
//
// origin/yaw/pitch/roll = cuerpo del jugador (r_refdef) en coords Quake
// (Z-up, unidades). Devuelve la modelview 4x4 COLUMN-MAJOR para glMultMatrixf.
// False si no hay vistas validas (el caller usa la ruta vieja).
qboolean VR_GetEyeViewMatrix (const float *origin, float yaw_deg, float pitch_deg,
	float roll_deg, float *mv16)
{
	int eye = vr_current_eye;
	const float scale = vr_world_scale.value > 0.0f ? vr_world_scale.value : 16.0f;
	float rxr[3][3], rq[3][3];
	float oq[3];
	float ml[4][4];	// pose del ojo en base Quake (row-major)
	float rb[3][3];	// rotacion del cuerpo: Rz(yaw)*Ry(pitch)*Rx(roll)
	float rw[3][3], tw[3];
	float ri[3][3], ti[3];
	float B[3][3] = { { 0, 0, -1 }, { -1, 0, 0 }, { 0, 1, 0 } };	// xr(Y-up) -> Quake(Z-up)
	float Basis[3][3] = { { 0, -1, 0 }, { 0, 0, 1 }, { -1, 0, 0 } };	// Quake -> GL
	XrQuaternionf q;
	XrVector3f p;
	int i, j, k;

	if (eye < 0 || eye > 1 || !vr_views_valid)
		return false;

	q = vr_eye_view[eye].pose.orientation;
	p = vr_eye_view[eye].pose.position;

	// quat -> matriz de rotacion OpenXR (v' = R*v)
	rxr[0][0] = 1 - 2*(q.y*q.y + q.z*q.z);
	rxr[0][1] = 2*(q.x*q.y - q.z*q.w);
	rxr[0][2] = 2*(q.x*q.z + q.y*q.w);
	rxr[1][0] = 2*(q.x*q.y + q.z*q.w);
	rxr[1][1] = 1 - 2*(q.x*q.x + q.z*q.z);
	rxr[1][2] = 2*(q.y*q.z - q.x*q.w);
	rxr[2][0] = 2*(q.x*q.z - q.y*q.w);
	rxr[2][1] = 2*(q.y*q.z + q.x*q.w);
	rxr[2][2] = 1 - 2*(q.x*q.x + q.y*q.y);

	// Rq = B*Rxr*B^T ; oq = B*p*scale
	for (i = 0; i < 3; i++) {
		for (j = 0; j < 3; j++) {
			rq[i][j] = 0;
			for (k = 0; k < 3; k++)
				rq[i][j] += B[i][k] * (rxr[k][0]*B[j][0] + rxr[k][1]*B[j][1] + rxr[k][2]*B[j][2]);
		}
		oq[i] = (B[i][0]*p.x + B[i][1]*p.y + B[i][2]*p.z) * scale;
	}

	ml[0][0]=rq[0][0]; ml[0][1]=rq[0][1]; ml[0][2]=rq[0][2]; ml[0][3]=oq[0];
	ml[1][0]=rq[1][0]; ml[1][1]=rq[1][1]; ml[1][2]=rq[1][2]; ml[1][3]=oq[1];
	ml[2][0]=rq[2][0]; ml[2][1]=rq[2][1]; ml[2][2]=rq[2][2]; ml[2][3]=oq[2];
	ml[3][0]=0; ml[3][1]=0; ml[3][2]=0; ml[3][3]=1;

	// rotacion del cuerpo (Quake: pitch/yaw/roll grados; forward = +X)
	VR_BodyRot(yaw_deg, pitch_deg, roll_deg, rb);

	// M_eye_world = M_body * M_local
	for (i = 0; i < 3; i++) {
		rw[i][0] = rb[i][0]*ml[0][0] + rb[i][1]*ml[1][0] + rb[i][2]*ml[2][0];
		rw[i][1] = rb[i][0]*ml[0][1] + rb[i][1]*ml[1][1] + rb[i][2]*ml[2][1];
		rw[i][2] = rb[i][0]*ml[0][2] + rb[i][1]*ml[1][2] + rb[i][2]*ml[2][2];
		tw[i] = rb[i][0]*ml[0][3] + rb[i][1]*ml[1][3] + rb[i][2]*ml[2][3] + origin[i];
			/* Pequeño offset lateral de la cámara: coloca el ojo de primera
			 * persona más alineado con el arma extendida. */
			tw[i] += rb[i][1] * vr_camera_right.value;
	}

	// inversa rigida: Ri = R^T, ti = -Ri*t
	ri[0][0] = rw[0][0]; ri[0][1] = rw[1][0]; ri[0][2] = rw[2][0];
	ri[1][0] = rw[0][1]; ri[1][1] = rw[1][1]; ri[1][2] = rw[2][1];
	ri[2][0] = rw[0][2]; ri[2][1] = rw[1][2]; ri[2][2] = rw[2][2];
	ti[0] = -(ri[0][0]*tw[0] + ri[0][1]*tw[1] + ri[0][2]*tw[2]);
	ti[1] = -(ri[1][0]*tw[0] + ri[1][1]*tw[1] + ri[1][2]*tw[2]);
	ti[2] = -(ri[2][0]*tw[0] + ri[2][1]*tw[1] + ri[2][2]*tw[2]);

	// mv = Basis * inv, empacado COLUMN-MAJOR para glMultMatrixf
	for (j = 0; j < 4; j++) {
		for (i = 0; i < 3; i++) {
			if (j == 3)
				mv16[j*4 + i] = Basis[i][0]*ti[0] + Basis[i][1]*ti[1] + Basis[i][2]*ti[2];
			else
				mv16[j*4 + i] = Basis[i][0]*ri[0][j] + Basis[i][1]*ri[1][j] + Basis[i][2]*ri[2][j];
		}
		mv16[j*4 + 3] = (j == 3) ? 1.0f : 0.0f;
	}

	return true;
}

// ---------------------------------------------------------------------------
// MANOS / ARMAS: pose del mando derecho convertida al mundo del juego.
//
// VR_LocateHand: pose LOCAL del mando 'hand' (0 izq, 1 der) en vr_local_space
// (mismo espacio que las poses de ojo -> misma base de conversion).
// VR_PoseToWorldQuake: dada una pose LOCAL (ojo o mando), devuelve M_body *
// LocalAQuake(pose) = transform del objeto en coords Quake del mundo (rw,tw).
// De ahi salen: la modelview para dibujar (eyeView*handWorld), la matriz de
// mundo del arma (para anclar el modelo al mando) y el vector de apuntado.
// ---------------------------------------------------------------------------
static qboolean VR_LocateHand (int hand, XrPosef *outpose)
{
	XrSpaceLocation loc;

	if (hand < 0 || hand > 1 || vr_pose_space[hand] == XR_NULL_HANDLE ||
		vr_local_space == XR_NULL_HANDLE)
		return false;
	memset(&loc, 0, sizeof(loc));
	loc.type = XR_TYPE_SPACE_LOCATION;
	if (XR_FAILED(xrLocateSpace(vr_pose_space[hand], vr_local_space,
			vr_frame_state.predictedDisplayTime, &loc)))
		return false;
	if (!(loc.locationFlags & (XR_SPACE_LOCATION_POSITION_VALID_BIT |
			XR_SPACE_LOCATION_ORIENTATION_VALID_BIT)))
		return false;
	*outpose = loc.pose;
	/* HOLGURA (vr_vm_gain): el arma se desplaza alrededor de la POSTURA MEDIA
	 * de la mano, amplificado x gain. Con gain 1.6, mover la mano 10 cm mueve
	 * el arma 16 cm -> mas recorrido, menos rigidez. La media sigue a la mano
	 * con un resorte lento (quieto, el arma acaba exactamente en la mano). */
	{
		static XrVector3f avg_pos[2];
		static double avg_time[2];
		static qboolean avg_valid[2];
		float gain = vr_vm_gain.value;
		double now;
		XrVector3f *p = &outpose->position;
		if (gain < 1.0f) gain = 1.0f;
		if (gain > 8.0f) gain = 8.0f;
		if (gain > 1.01f) {
			now = Sys_FloatTime();
			if (!avg_valid[hand] || now - avg_time[hand] > 0.5) {
				avg_pos[hand] = *p;
				avg_valid[hand] = true;
			} else {
				double dt = now - avg_time[hand];
				float a;
				if (dt > 0.1) dt = 0.1;
				a = (float)(1.0 - exp(-dt * 2.5));
				avg_pos[hand].x += (p->x - avg_pos[hand].x) * a;
				avg_pos[hand].y += (p->y - avg_pos[hand].y) * a;
				avg_pos[hand].z += (p->z - avg_pos[hand].z) * a;
			}
			avg_time[hand] = now;
			p->x = avg_pos[hand].x + (p->x - avg_pos[hand].x) * gain;
			p->y = avg_pos[hand].y + (p->y - avg_pos[hand].y) * gain;
			p->z = avg_pos[hand].z + (p->z - avg_pos[hand].z) * gain;
		}
	}
	return true;
}

// Pose LOCAL (XrPosef) -> transform en mundo Quake (rw row-major, tw).
static void VR_PoseToWorldQuake (const XrPosef *pose, const float *origin,
	float yaw_deg, float pitch_deg, float roll_deg, float rw[3][3], float tw[3])
{
	const float scale = vr_world_scale.value > 0.0f ? vr_world_scale.value : 16.0f;
	float rxr[3][3], rq[3][3], oq[3], ml[4][4], rb[3][3];
	float B[3][3] = { { 0, 0, -1 }, { -1, 0, 0 }, { 0, 1, 0 } };	// xr(Y-up) -> Quake(Z-up)
	const XrQuaternionf q = pose->orientation;
	const XrVector3f p = pose->position;
	int i, j, k;

	rxr[0][0] = 1 - 2*(q.y*q.y + q.z*q.z);
	rxr[0][1] = 2*(q.x*q.y - q.z*q.w);
	rxr[0][2] = 2*(q.x*q.z + q.y*q.w);
	rxr[1][0] = 2*(q.x*q.y + q.z*q.w);
	rxr[1][1] = 1 - 2*(q.x*q.x + q.z*q.z);
	rxr[1][2] = 2*(q.y*q.z - q.x*q.w);
	rxr[2][0] = 2*(q.x*q.z - q.y*q.w);
	rxr[2][1] = 2*(q.y*q.z + q.x*q.w);
	rxr[2][2] = 1 - 2*(q.x*q.x + q.y*q.y);

	for (i = 0; i < 3; i++) {
		for (j = 0; j < 3; j++) {
			rq[i][j] = 0;
			for (k = 0; k < 3; k++)
				rq[i][j] += B[i][k] * (rxr[k][0]*B[j][0] + rxr[k][1]*B[j][1] + rxr[k][2]*B[j][2]);
		}
		oq[i] = (B[i][0]*p.x + B[i][1]*p.y + B[i][2]*p.z) * scale;
	}

	ml[0][0]=rq[0][0]; ml[0][1]=rq[0][1]; ml[0][2]=rq[0][2]; ml[0][3]=oq[0];
	ml[1][0]=rq[1][0]; ml[1][1]=rq[1][1]; ml[1][2]=rq[1][2]; ml[1][3]=oq[1];
	ml[2][0]=rq[2][0]; ml[2][1]=rq[2][1]; ml[2][2]=rq[2][2]; ml[2][3]=oq[2];
	ml[3][0]=0; ml[3][1]=0; ml[3][2]=0; ml[3][3]=1;

	VR_BodyRot(yaw_deg, pitch_deg, roll_deg, rb);

	for (i = 0; i < 3; i++) {
		rw[i][0] = rb[i][0]*ml[0][0] + rb[i][1]*ml[1][0] + rb[i][2]*ml[2][0];
		rw[i][1] = rb[i][0]*ml[0][1] + rb[i][1]*ml[1][1] + rb[i][2]*ml[2][1];
		rw[i][2] = rb[i][0]*ml[0][2] + rb[i][1]*ml[1][2] + rb[i][2]*ml[2][2];
		tw[i] = rb[i][0]*ml[0][3] + rb[i][1]*ml[1][3] + rb[i][2]*ml[2][3] + origin[i];
	}
	/* Ajustes visuales compartidos por modelo y disparo: desplazar en los
	 * ejes locales del mando evita que el arma visible y la mirilla se separen.
	 * vr_hand_right negativo coloca el arma a la izquierda. */
	tw[0] += rw[0][0] * vr_hand_right.value + rw[0][2] * vr_hand_up.value + rw[0][1] * vr_hand_forward.value;
	tw[1] += rw[1][0] * vr_hand_right.value + rw[1][2] * vr_hand_up.value + rw[1][1] * vr_hand_forward.value;
	tw[2] += rw[2][0] * vr_hand_right.value + rw[2][2] * vr_hand_up.value + rw[2][1] * vr_hand_forward.value;
	/* El giro extra se aplica al cuerpo antes de convertir la pose del arma;
	 * mantiene el mismo desplazamiento en todos los ojos. */
	if (vr_hand_yaw.value != 0.0f || vr_hand_pitch.value != 0.0f) {
		float extra[3][3];
		VR_BodyRot(vr_hand_yaw.value, vr_hand_pitch.value, 0.0f, extra);
		float old[3][3];
		memcpy(old, rw, sizeof(old));
		for (i = 0; i < 3; i++) {
			rw[i][0] = extra[i][0]*old[0][0] + extra[i][1]*old[1][0] + extra[i][2]*old[2][0];
			rw[i][1] = extra[i][0]*old[0][1] + extra[i][1]*old[1][1] + extra[i][2]*old[2][1];
			rw[i][2] = extra[i][0]*old[0][2] + extra[i][1]*old[1][2] + extra[i][2]*old[2][2];
		}
	}
}

// rw,tw (mundo Quake) -> matriz 4x4 COLUMN-MAJOR en coords Quake (SIN Basis ni
// inversa). Se multiplica DESPUES de la eyeView (que ya trae Basis), de modo
// que eyeView * handWorld lleva vertices del modelo local del arma al mundo.
static void VR_WorldQuakeToColumn (float rw[3][3], float tw[3], float *m16)
{
	int i, j;
	for (j = 0; j < 4; j++) {
		for (i = 0; i < 3; i++)
			m16[j*4 + i] = (j == 3) ? tw[i] : rw[i][j];
		m16[j*4 + 3] = (j == 3) ? 1.0f : 0.0f;
	}
}

// Matriz de mundo del arma anclada al mando 'hand' (column-major, coords
// Quake). origin/yaw/pitch/roll = cuerpo del jugador (r_refdef). False si no
// hay pose valida (el caller dibuja el arma en la cabeza como siempre).
qboolean VR_GetHandWorldMatrix (int hand, const float *origin, float yaw_deg,
	float pitch_deg, float roll_deg, float *m16)
{
	XrPosef pose;
	float rw[3][3], tw[3];
	if (!VR_LocateHand(hand, &pose))
		return false;
	VR_PoseToWorldQuake(&pose, origin, yaw_deg, pitch_deg, roll_deg, rw, tw);
	VR_WorldQuakeToColumn(rw, tw, m16);
	return true;
}

// Yaw horizontal ESTABLE de un vector de direccion (f) con su vector arriba
// (u), ambos en mundo Quake. El yaw normal = atan2(fy,fx) de la proyeccion
// horizontal del forward; pero cuando el arma/cabeza apunta casi VERTICAL
// (al suelo o al techo), esa proyeccion es minuscula y atan2 salta 180 grados
// entre frames -> el movimiento se vuelve loco (bug "arma abajo"). En esa
// zona usamos el vector UP de la vista: mirando ABAJO el up apunta al frente
// (yaw directo); mirando ARRIBA apunta atras (hay que sumarle 180).
static float VR_StableYaw (const float *f, const float *u)
{
	float h = sqrtf(f[0]*f[0] + f[1]*f[1]);
	if (h > 0.20f)
		return atan2f(f[1], f[0]) * (180.0f / M_PI);
	{
		float yaw = atan2f(u[1], u[0]) * (180.0f / M_PI);
		if (f[2] > 0)	// Quake: forward[2] POSITIVO = mirando ARRIBA
			yaw += 180.0f;
		return anglemod(yaw);
	}
}

// Apuntado del arma: posicion del mando en el mundo (out_origin) y angulos de
// disparo (out_angles[0]=pitch,[1]=yaw,[2]=roll) en convencion del motor. El
// forward del arma = eje +X Quake del mando (el -Z de OpenXR mapea a +X).
qboolean VR_GetHandAim (int hand, const float *origin, float yaw_deg,
	float pitch_deg, float roll_deg, float *out_origin, float *out_angles)
{
	XrPosef pose;
	float rw[3][3], tw[3];
	vec3_t fwd, ang;
	int i;
	if (!VR_LocateHand(hand, &pose))
		return false;
	VR_PoseToWorldQuake(&pose, origin, yaw_deg, pitch_deg, roll_deg, rw, tw);
	fwd[0] = rw[0][0]; fwd[1] = rw[1][0]; fwd[2] = rw[2][0];
	VectorNormalize(fwd);
	vectoangles(fwd, ang);
	for (i = 0; i < 3; i++) { out_origin[i] = tw[i]; }
	// Quake: pitch POSITIVO = mirar ABAJO; vectoangles da pitch matematico
	// (positivo = arriba). Sin el menos, apuntar abajo dispara arriba.
	out_angles[0] = -ang[0];
	// YAW estable (VR_StableYaw): vectoangles usa atan2 de la proyeccion
	// horizontal y salta 180 deg cuando el arma apunta casi al suelo -> el
	// desplazamiento se invertia con el arma abajo (bug apk37).
	{
		float upv[3];
		upv[0] = rw[0][2]; upv[1] = rw[1][2]; upv[2] = rw[2][2];
		out_angles[1] = VR_StableYaw(fwd, upv);
	}
	out_angles[2] = 0;
	return true;
}

// Yaw absoluto de la CABEZA en el mundo del juego, calculado con el MISMO
// pipeline (VR_PoseToWorldQuake + columna 0) que VR_GetHandAim, asi que las
// dos cifras son directamente comparables. Sirve para que el desplazamiento
// siga a las gafas aunque el arma apunte a otro lado.
qboolean VR_GetHeadWorldYaw (const float *origin, float yaw_deg,
	float pitch_deg, float roll_deg, float *out_yaw)
{
	XrPosef pose;
	float rw[3][3], tw[3];
	if (!vr_views_valid)
		return false;
	pose = vr_eye_view[0].pose;
	VR_PoseToWorldQuake(&pose, origin, yaw_deg, pitch_deg, roll_deg, rw, tw);
	{
		float f[3], u[3];
		f[0] = rw[0][0]; f[1] = rw[1][0]; f[2] = rw[2][0];
		u[0] = rw[0][2]; u[1] = rw[1][2]; u[2] = rw[2][2];
		*out_yaw = VR_StableYaw(f, u);
	}
	return true;
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

	// El runtime de Meta IMPORTA/VALIDA las texturas GLES del swapchain con el
	// contexto del binding (ES3 auxiliar) current. Si acquire/wait se ejecutan
	// con el contexto 1.1 del juego, la importacion falla: "[OpenXR] swapchain
	// validation failure" y la capa queda rechazada despues en xrEndFrame con
	// XR_ERROR_LAYER_INVALID (-23). hello_xr mantiene el contexto del binding
	// current durante TODO el ciclo de frame; aqui imitamos eso en las llamadas
	// que tocan el swapchain, y volvemos al contexto del juego para dibujar.
	EGLContext ctx_game = eglGetCurrentContext();
	EGLSurface es3_draw = EGL_NO_SURFACE, es3_read = EGL_NO_SURFACE;
	qboolean es3_bound = VR_BindES3Context(&es3_draw, &es3_read);

	XrSwapchainImageAcquireInfo ai;
	memset(&ai, 0, sizeof(ai));
	ai.type = XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO;
	uint32_t index = 0;
	if (XR_FAILED(xrAcquireSwapchainImage(vr_eye_swapchain[eye], &ai, &index))) {
		VR_LOG("xrAcquireSwapchainImage fallo (ojo %d)", eye);
		if (es3_bound)
			VR_RestoreGameContext(es3_draw, es3_read, ctx_game);
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
		if (es3_bound)
			VR_RestoreGameContext(es3_draw, es3_read, ctx_game);
		return false;
	}

	if (es3_bound)
		VR_RestoreGameContext(es3_draw, es3_read, ctx_game);

	vr_eye_acquired[eye] = index;
	vr_eye_acquiring[eye] = true;
	vr_current_eye = eye;

	glBindFramebufferOES(GL_FRAMEBUFFER_OES, vr_eye_fbo[eye][index]);
	glViewport(0, 0, vr_eye_width, vr_eye_height);
	return true;
}

// Termina el ojo actual: desvincula el FBO. La imagen NO se libera aqui:
// se libera en VR_EndFrame justo ANTES de xrEndFrame (estado READY que exige
// la capa). El SIGSEGV historico era el bug de fei.layers, no el release.
void VR_EndEye (int eye)
{
	if (eye < 0 || eye > 1 || !vr_eye_acquiring[eye])
		return;

	glBindFramebufferOES(GL_FRAMEBUFFER_OES, 0);

	vr_eyes_done[eye] = true;
	vr_current_eye = -1;
}

// Libera las imagenes adquiridas del frame (llamar tras xrEndFrame). Con el
// contexto ES3 del binding current: el release entrega la textura al
// compositor y Meta la re-valida en el contexto de importacion (mismo motivo
// que acquire en VR_BeginEye).
static void VR_ReleaseAcquiredImages (void)
{
	EGLContext ctx_game = eglGetCurrentContext();
	EGLSurface es3_draw = EGL_NO_SURFACE, es3_read = EGL_NO_SURFACE;
	qboolean es3_bound = VR_BindES3Context(&es3_draw, &es3_read);

	for (int eye = 0; eye < 2; eye++) {
		if (!vr_eye_acquiring[eye] || vr_eye_swapchain[eye] == XR_NULL_HANDLE)
			continue;
		XrSwapchainImageReleaseInfo ri;
		memset(&ri, 0, sizeof(ri));
		ri.type = XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO;
		if (XR_FAILED(xrReleaseSwapchainImage(vr_eye_swapchain[eye], &ri)))
			VR_LOG("xrReleaseSwapchainImage fallo (ojo %d)", eye);
		vr_eye_acquiring[eye] = false;
	}

	if (es3_bound)
		VR_RestoreGameContext(es3_draw, es3_read, ctx_game);
}

#endif // NZP_VR_OPENXR
