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

#define XR_USE_PLATFORM_ANDROID
#define XR_USE_GRAPHICS_API_OPENGL_ES

#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>
#include <android/log.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

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
static uint32_t		vr_eye_image_count = 0;
static int32_t		vr_eye_width = 0;
static int32_t		vr_eye_height = 0;
static int64_t		vr_swapchain_format = 0;

static XrFrameState	vr_frame_state;
static qboolean		vr_frame_waited = false;

static XrPosef		vr_head_pose;
static qboolean		vr_head_pose_valid = false;

cvar_t	vr_enabled = {"vr_enabled", "0", true};
cvar_t	vr_debug = {"vr_debug", "1", true};

#define VR_LOG(...) do { if (vr_debug.value > 0) __android_log_print(ANDROID_LOG_INFO, "nzp-vr", __VA_ARGS__); } while (0)

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

// Crea la sesion GLES + swapchains. Requiere el contexto EGL del juego ya
// actual (se llama desde VR_BeginFrame en el primer frame activo).
static qboolean VR_CreateSession (void)
{
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
		const EGLint attribs[] = {
			EGL_RENDERABLE_TYPE, EGL_OPENGL_ES_BIT,
			EGL_SURFACE_TYPE, 0,
			EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8,
			EGL_DEPTH_SIZE, 24,
			EGL_NONE
		};
		if (!eglChooseConfig(display, attribs, &config, 1, &num_configs) || num_configs < 1) {
			VR_LOG("eglChooseConfig no encontro config GLES");
			return false;
		}
	}

	XrGraphicsBindingOpenGLESAndroidKHR gles_binding;
	memset(&gles_binding, 0, sizeof(gles_binding));
	gles_binding.type = XR_TYPE_GRAPHICS_BINDING_OPENGL_ES_ANDROID_KHR;
	gles_binding.display = display;
	gles_binding.config = config;
	gles_binding.context = context;

	XrSessionCreateInfo sci;
	memset(&sci, 0, sizeof(sci));
	sci.type = XR_TYPE_SESSION_CREATE_INFO;
	sci.next = &gles_binding;
	sci.systemId = vr_system;

	XR_CHECK(xrCreateSession(vr_instance, &sci, &vr_session));
	VR_LOG("Sesion GLES creada (display=%p)", (void *)display);

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
	VR_LOG("Resolucion por ojo: %dx%d (sampleCount=%u)",
		(int)vr_eye_width, (int)vr_eye_height, views[0].recommendedSwapchainSampleCount);

	// Formato de swapchain: preferir SRGB8_ALPHA8, si no el primero
	{
		uint32_t fmt_count = 0;
		xrEnumerateSwapchainFormats(vr_session, 0, &fmt_count, NULL);
		if (fmt_count > 0) {
			int64_t *formats = (int64_t *)malloc(fmt_count * sizeof(int64_t));
			if (formats) {
				xrEnumerateSwapchainFormats(vr_session, fmt_count, &fmt_count, formats);
				vr_swapchain_format = formats[0];
				for (uint32_t f = 0; f < fmt_count; f++) {
					if (formats[f] == 0x8C43) { // GL_SRGB8_ALPHA8
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
		scinfo.sampleCount = views[0].recommendedSwapchainSampleCount;
		scinfo.width = vr_eye_width;
		scinfo.height = vr_eye_height;
		scinfo.faceCount = 1;
		scinfo.arraySize = 1;
		scinfo.mipCount = 1;

		XR_CHECK(xrCreateSwapchain(vr_session, &scinfo, &vr_eye_swapchain[eye]));

		uint32_t img_count = 0;
		xrEnumerateSwapchainImages(vr_eye_swapchain[eye], 0, &img_count, NULL);
		vr_eye_image_count = img_count;
	}

	VR_LOG("Swapchains creados (%u imagenes/ojo)", vr_eye_image_count);
	return true;
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
				switch (e->state) {
					case XR_SESSION_STATE_FOCUSED:
						vr_session_focused = true;
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
	if (!vr_available || vr_session == XR_NULL_HANDLE)
		return false;

	VR_PumpEvents();

	if (!vr_session_running) {
		// La sesion GLES se crea en el primer frame, cuando el contexto del
		// juego ya esta actual (VID_Init ya paso).
		if (!VR_CreateSession())
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

	// Poses de la cabeza (paso 4: head tracking -> viewangles)
	{
		XrViewState view_state;
		memset(&view_state, 0, sizeof(view_state));
		view_state.type = XR_TYPE_VIEW_STATE;

		XrViewLocateInfo vli;
		memset(&vli, 0, sizeof(vli));
		vli.type = XR_TYPE_VIEW_LOCATE_INFO;
		vli.viewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
		vli.displayTime = vr_frame_state.predictedDisplayTime;
		vli.space = vr_view_space;

		XrView views[2];
		memset(views, 0, sizeof(views));
		views[0].type = XR_TYPE_VIEW;
		views[1].type = XR_TYPE_VIEW;
		uint32_t located = 0;

		if (XR_SUCCEEDED(xrLocateViews(vr_session, &vli, &view_state, 2, &located, views)) && located > 0) {
			vr_head_pose = views[0].pose;
			vr_head_pose_valid = true;

			static int pose_log_counter = 0;
			if (vr_debug.value > 0 && (pose_log_counter++ % 300) == 0) {
				VR_LOG("head pos=(%.2f,%.2f,%.2f) quat=(%.2f,%.2f,%.2f,%.2f)",
					(double)views[0].pose.position.x, (double)views[0].pose.position.y, (double)views[0].pose.position.z,
					(double)views[0].pose.orientation.x, (double)views[0].pose.orientation.y,
					(double)views[0].pose.orientation.z, (double)views[0].pose.orientation.w);
			}
		}
	}

	return true;
}

qboolean VR_EndFrame (void)
{
	if (!vr_available || vr_session == XR_NULL_HANDLE || !vr_frame_waited)
		return false;

	// PASO 2: sin capas de proyeccion todavia (HMD en negro). El paso 3
	// anadira el XrCompositionLayerProjection con los 2 ojos.
	XrFrameEndInfo fei;
	memset(&fei, 0, sizeof(fei));
	fei.type = XR_TYPE_FRAME_END_INFO;
	fei.displayTime = vr_frame_state.predictedDisplayTime;
	fei.environmentBlendMode = XR_ENVIRONMENT_BLEND_MODE_OPAQUE;

	XrResult r = xrEndFrame(vr_session, &fei);
	vr_frame_waited = false;

	if (XR_FAILED(r)) {
		VR_LOG("xrEndFrame fallo: %s", VR_ResultString(r));
		return false;
	}
	return true;
}

void VR_Shutdown (void)
{
	for (int eye = 0; eye < 2; eye++) {
		if (vr_eye_swapchain[eye] != XR_NULL_HANDLE) {
			xrDestroySwapchain(vr_eye_swapchain[eye]);
			vr_eye_swapchain[eye] = XR_NULL_HANDLE;
		}
	}
	if (vr_local_space != XR_NULL_HANDLE) { xrDestroySpace(vr_local_space); vr_local_space = XR_NULL_HANDLE; }
	if (vr_view_space != XR_NULL_HANDLE) { xrDestroySpace(vr_view_space); vr_view_space = XR_NULL_HANDLE; }
	if (vr_session != XR_NULL_HANDLE) { xrDestroySession(vr_session); vr_session = XR_NULL_HANDLE; }
	if (vr_instance != XR_NULL_HANDLE) { xrDestroyInstance(vr_instance); vr_instance = XR_NULL_HANDLE; }
	vr_session_running = false;
	vr_session_focused = false;
	vr_available = false;
	vr_initialized = false;
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
	return vr_available && vr_enabled.value > 0 && vr_session != XR_NULL_HANDLE;
}

void VR_GetHeadYawPitch (float *yaw, float *pitch)
{
	if (!vr_head_pose_valid) {
		*yaw = 0;
		*pitch = 0;
		return;
	}

	// Quaternion OpenXR (Y-up, mano derecha) -> yaw/pitch del motor.
	// Yaw alrededor de +Y, pitch alrededor de +X.
	XrQuaternionf q = vr_head_pose.orientation;
	float yaw_out = atan2f(2.0f * (q.w * q.y + q.x * q.z), 1.0f - 2.0f * (q.y * q.y + q.x * q.x));
	float pitch_out = asinf(2.0f * (q.w * q.x - q.y * q.z));

	*yaw = -yaw_out * (180.0f / M_PI);	// OpenXR antihorario -> motor horario
	*pitch = -pitch_out * (180.0f / M_PI);
}

#endif // NZP_VR_OPENXR
