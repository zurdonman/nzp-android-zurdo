#include "../../nzportable_def.h"
#include "sdl_local.h"

extern int mouse_dx;
extern int mouse_dy;
void NZP_TouchConsumeLook(int *dx, int *dy);
void NZP_TouchGetMove(float *mx, float *my);
void NZP_TouchRegisterCvars(void);

qboolean IN_PlatformHasMouse(void) { return true; }
qboolean IN_PlatformHasGamepad(void) { return true; }

void IN_SetMouseToRelative(bool relative)
{
	if (relative)
		SDL_SetRelativeMouseMode(SDL_TRUE);
	else
		SDL_SetRelativeMouseMode(SDL_FALSE);
}

void IN_PlatformClearPendingInput(void)
{
	mouse_dx = 0;
	mouse_dy = 0;
}

void IN_PlatformMouseMove(usercmd_t *cmd)
{
	int touch_dx, touch_dy;

	(void)cmd;

	V_StopPitchDrift();

	/* mirada tactil: arrastre 1:1, medio arrastre de pantalla = ~90 grados.
	 * forward[2] = -sin(pitch) => pitch POSITIVO mira ABAJO. Dedo hacia abajo
	 * (dy>0) => pitch positivo => mirar abajo, como se espera en un FPS movil.
	 * Fijo a proposito: NO multiplica por m_pitch (puede ser negativo en el
	 * config y invertiria la vista tactil). */
	NZP_TouchConsumeLook(&touch_dx, &touch_dy);
	if (touch_dx || touch_dy) {
		cl.viewangles[YAW] -= (float)touch_dx * 0.12f;
		cl.viewangles[PITCH] += (float)touch_dy * 0.12f;
		if (cl.viewangles[PITCH] > 80) cl.viewangles[PITCH] = 80;
		if (cl.viewangles[PITCH] < -70) cl.viewangles[PITCH] = -70;
	}

	if (mouse_dx || mouse_dy) {
		float pitch_direction = m_pitch.value > 0 ? -1.0f : 1.0f;
		cl.viewangles[YAW] -= mouse_dx * sensitivity.value * 0.022f;
		cl.viewangles[PITCH] += mouse_dy * sensitivity.value * 0.022f * pitch_direction;
		if (cl.viewangles[PITCH] > 80) cl.viewangles[PITCH] = 80;
		if (cl.viewangles[PITCH] < -70) cl.viewangles[PITCH] = -70;
		mouse_dx = mouse_dy = 0;
	}
}

/* stick tactil -> avance + lateral. input.c solo sobreescribe cmd->sidemove/
 * forwardmove si hay gamepad con stick movido; sin el, lo nuestro persiste. */
static void NZP_TouchApplyMove(usercmd_t *cmd)
{
	float mx, my;

	NZP_TouchGetMove(&mx, &my);
	if ((mx == 0.0f && my == 0.0f) || !sv_player)
		return;
	cmd->sidemove += sv_player->v.maxspeed * 0.8f * mx;
	cmd->forwardmove += (my >= 0.0f ? sv_player->v.maxspeed : sv_player->v.maxspeed * 0.7f) * my;
}

#define MAX_SDL_CONTROLLERS 4
static SDL_GameController *sdl_controllers[MAX_SDL_CONTROLLERS];
static SDL_GameController *sdl_controller;

/* --- Giroscopio del telefono (SDL_Sensor API, android) ---
 * SDL entrega el gyro del movil en rad/s. En landscape:
 *   data[0] = rotacion alrededor del eje corto del movil (arriba/abajo) -> YAW
 *   data[1] = rotacion alrededor del eje largo (izquierda/derecha)      -> PITCH
 * El signo depende de con que lado queda vertical el movil (ROTATION_90 o 270);
 * el acelerometro dice donde esta la gravedad (a[0] > 0 => eje X hacia abajo). */
static SDL_Sensor *phone_gyro;
static SDL_Sensor *phone_accel;
static float phone_gyro_sign = 1.0f;   /* ajustado con la gravedad en cada lectura */

static void IN_PhoneOpenSensors(void)
{
	int i;
	if (SDL_InitSubSystem(SDL_INIT_SENSOR) != 0) {
		Con_Printf("gyro: SDL_INIT_SENSOR fallo: %s\n", SDL_GetError());
		return;
	}
	for (i = 0; i < SDL_NumSensors(); ++i) {
		SDL_SensorType t = SDL_SensorGetDeviceType(i);
		if (t == SDL_SENSOR_GYRO && !phone_gyro)
			phone_gyro = SDL_SensorOpen(i);
		else if (t == SDL_SENSOR_ACCEL && !phone_accel)
			phone_accel = SDL_SensorOpen(i);
	}
	Con_Printf("gyro: telefono %s, accel %s\n",
		phone_gyro ? "DETECTADO" : "NO disponible",
		phone_accel ? "detectado" : "no disponible");
}

void IN_PlatformSetLightbar(byte red, byte green, byte blue)
{
	if (sdl_controller && SDL_GameControllerHasLED(sdl_controller))
		SDL_GameControllerSetLED(sdl_controller, red, green, blue);
}

qboolean IN_PlatformGetGyro(float *x, float *y)
{
	float data[3];
	float s;
	*x = *y = 0.0f;
	/* preferencia: gyro del telefono; si no hay, mando con sensor */
	if (phone_gyro) {
		if (SDL_SensorGetData(phone_gyro, data, 3) != 0)
			return false;
		s = phone_gyro_sign;
		if (phone_accel) {
			float a[3];
			if (SDL_SensorGetData(phone_accel, a, 3) == 0 &&
				(a[0] > 4.0f || a[0] < -4.0f))
				s = phone_gyro_sign = (a[0] > 0.0f) ? 1.0f : -1.0f;
		}
		/* Mapeo pedido por el usuario (v7.2), "el movil es la camara":
		 * - tirar del borde IZQUIERDO hacia ti -> camara a la IZQUIERDA
		 *   (esa rotacion es alrededor del eje corto vertical del movil,
		 *   data[0] en landscape; el accel ajusta el signo ROTATION_90/270).
		 * - tirar del borde SUPERIOR hacia ti -> mirar ARRIBA
		 *   (rotacion alrededor del eje largo horizontal, data[1]).
		 * input.c (Android): yaw += gyro_y*k ; pitch += gyro_x*k. */
		*y = data[0] * s;   /* eje vertical del movil -> yaw */
		*x = data[1] * s;   /* eje horizontal del movil -> pitch */
		return true;
	}
	if (!sdl_controller || !SDL_GameControllerHasSensor(sdl_controller, SDL_SENSOR_GYRO))
		return false;
	if (SDL_GameControllerGetSensorData(sdl_controller, SDL_SENSOR_GYRO, data, 3) != 0)
		return false;
	*x = data[0];
	*y = data[1];
	return true;
}

void IN_PlatformRumble(unsigned short low_frequency, unsigned short high_frequency, unsigned int duration)
{
	if (sdl_controller)
		SDL_GameControllerRumble(sdl_controller, low_frequency, high_frequency, duration);
}

static void IN_SDLOpenController(int device_index)
{
	int i;
	SDL_GameController *controller;

	if (!SDL_IsGameController(device_index)) return;
	controller = SDL_GameControllerOpen(device_index);
	if (!controller) return;
	for (i = 0; i < MAX_SDL_CONTROLLERS; ++i) {
		if (!sdl_controllers[i]) {
			sdl_controllers[i] = controller;
			if (SDL_GameControllerHasSensor(controller, SDL_SENSOR_GYRO))
				SDL_GameControllerSetSensorEnabled(controller, SDL_SENSOR_GYRO, SDL_TRUE);
			if (!sdl_controller) sdl_controller = controller;
			return;
		}
	}
	SDL_GameControllerClose(controller);
}

void IN_PlatformInit(void)
{
	int i;
	/* Registra touch_ui/touch_edit/touch_layout + comandos. Seguro aqui:
	 * Memory_Init ya corrio (dentro de Host_Init). */
	NZP_TouchRegisterCvars();
	Cvar_SetValue("in_anub_mode", 1);
	IN_PhoneOpenSensors();   /* gyro + accel del telefono */
	for (i = 0; i < SDL_NumJoysticks(); ++i)
		IN_SDLOpenController(i);
}

void IN_PlatformShutdown(void)
{
	int i;
	for (i = 0; i < MAX_SDL_CONTROLLERS; ++i) {
		if (sdl_controllers[i]) SDL_GameControllerClose(sdl_controllers[i]);
		sdl_controllers[i] = NULL;
	}
	sdl_controller = NULL;
}

void IN_PlatformCommands(void) {}
void IN_PlatformMove(usercmd_t *cmd) { NZP_TouchApplyMove(cmd); }

void IN_GetAnalogStick(in_analog_stick_id_t stick, in_analog_stick_t *value)
{
	SDL_GameControllerAxis xaxis = stick == IN_STICK_LEFT ? SDL_CONTROLLER_AXIS_LEFTX : SDL_CONTROLLER_AXIS_RIGHTX;
	SDL_GameControllerAxis yaxis = stick == IN_STICK_LEFT ? SDL_CONTROLLER_AXIS_LEFTY : SDL_CONTROLLER_AXIS_RIGHTY;
	value->x = value->y = 0.0f;
	if (!sdl_controller) return;
	value->x = SDL_GameControllerGetAxis(sdl_controller, xaxis) / 32767.0f;
	value->y = -SDL_GameControllerGetAxis(sdl_controller, yaxis) / 32767.0f;
}

void IN_SDLControllerAdded(int device_index)
{
	IN_SDLOpenController(device_index);
}

void IN_SDLControllerActivated(SDL_JoystickID instance_id)
{
	int i;
	for (i = 0; i < MAX_SDL_CONTROLLERS; ++i) {
		if (sdl_controllers[i] &&
			SDL_JoystickInstanceID(SDL_GameControllerGetJoystick(sdl_controllers[i])) == instance_id) {
			sdl_controller = sdl_controllers[i];
			return;
		}
	}
}

void IN_SDLControllerRemoved(SDL_JoystickID instance_id)
{
	int i;
	for (i = 0; i < MAX_SDL_CONTROLLERS; ++i) {
		if (sdl_controllers[i] &&
			SDL_JoystickInstanceID(SDL_GameControllerGetJoystick(sdl_controllers[i])) == instance_id) {
			if (sdl_controller == sdl_controllers[i]) sdl_controller = NULL;
			SDL_GameControllerClose(sdl_controllers[i]);
			sdl_controllers[i] = NULL;
			break;
		}
	}
	if (!sdl_controller)
		for (i = 0; i < MAX_SDL_CONTROLLERS; ++i)
			if (sdl_controllers[i]) { sdl_controller = sdl_controllers[i]; break; }
}
