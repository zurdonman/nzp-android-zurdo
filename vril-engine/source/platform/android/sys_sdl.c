#include "../../nzportable_def.h"
#include "../../menu/menu_defs.h"
#include "sdl_local.h"
#ifdef NZP_VR_OPENXR
#include "vr/vr_openxr.h"
#endif

#include <errno.h>
#if defined(_WIN32)
#include <direct.h>
#endif
#include <sys/stat.h>
#include <unistd.h>
#include <pthread.h>
#include <android/log.h>

const char *NZPData_FindBasedir(void);

/* Hilo lector de la pipe: reenvia cada linea escrita por stdout/stderr a
 * logcat para que el arranque del motor sea visible con adb logcat. */
static void *NZP_LogcatThread(void *arg)
{
	int *cfg = (int *)arg;
	int fd = cfg[0];
	int is_stderr = cfg[1];
	const char *tag = is_stderr ? "nzportable-stderr" : "nzportable-stdout";
	char buf[1024];
	size_t pending = 0;
	ssize_t n;

	(void)tag;
	for (;;) {
		n = read(fd, buf + pending, sizeof(buf) - 1 - pending);
		if (n <= 0)
			break;
		pending += (size_t)n;
		buf[pending] = '\0';
		{
			char *cursor = buf;
			char *nl;
			while ((nl = strchr(cursor, '\n')) != NULL) {
				*nl = '\0';
				__android_log_print(
					is_stderr ? ANDROID_LOG_ERROR : ANDROID_LOG_INFO,
					tag, "%s", cursor);
				cursor = nl + 1;
			}
			pending = strlen(cursor);
			if (pending > 0)
				memmove(buf, cursor, pending + 1);
		}
	}
	/* Vuelca el resto sin salto de linea final. */
	if (pending > 0) {
		buf[pending] = '\0';
		__android_log_print(
			is_stderr ? ANDROID_LOG_ERROR : ANDROID_LOG_INFO,
			tag, "%s", buf);
	}
	free(cfg);
	return NULL;
}

#define MAX_HANDLES 32
#define DEFAULT_MEMORY_MB 128

static FILE *sys_handles[MAX_HANDLES];
qboolean isDedicated;
qboolean sdl_running = true;

static qboolean SDL_ArgumentsRequestHeadless(int argc, char **argv)
{
	int i;

	for (i = 1; i + 1 < argc; ++i)
		if (!strcmp(argv[i], "+vid_renderer") &&
			!Q_strcasecmp(argv[i + 1], "headless"))
			return true;
	return false;
}

static int Sys_FindHandle(void)
{
	int i;
	for (i = 1; i < MAX_HANDLES; ++i)
		if (!sys_handles[i]) return i;
	Sys_Error("out of handles");
	return -1;
}

static int Sys_FileLength(FILE *file)
{
	long position = ftell(file);
	long length;
	fseek(file, 0, SEEK_END);
	length = ftell(file);
	fseek(file, position, SEEK_SET);
	return (int)length;
}

int Sys_FileOpenRead(char *path, int *handle)
{
	FILE *file = fopen(path, "rb");
	int index;
	if (!file) { *handle = -1; return -1; }
	index = Sys_FindHandle();
	sys_handles[index] = file;
	*handle = index;
	return Sys_FileLength(file);
}

int Sys_FileOpenWrite(char *path)
{
	FILE *file = fopen(path, "wb");
	int index;
	if (!file) Sys_Error("Error opening %s: %s", path, strerror(errno));
	index = Sys_FindHandle();
	sys_handles[index] = file;
	return index;
}

void Sys_FileClose(int handle) { fclose(sys_handles[handle]); sys_handles[handle] = NULL; }
void Sys_FileSeek(int handle, int position) { fseek(sys_handles[handle], position, SEEK_SET); }
int Sys_FileRead(int handle, void *dest, int count) { return (int)fread(dest, 1, count, sys_handles[handle]); }
int Sys_FileWrite(int handle, void *data, int count) { return (int)fwrite(data, 1, count, sys_handles[handle]); }
int Sys_FileTime(char *path) {

	#if defined(_WIN32)
	struct _stat st;
	return _stat(path, &st) == 0 ? (int)st.st_mtime : -1;
	#else
	struct stat st;
	return stat(path, &st) == 0 ? (int)st.st_mtime : -1;
	#endif
}
void Sys_mkdir(char *path)
{ 
	#if defined(_WIN32)
	_mkdir(path);
	#else
	mkdir(path, 0777);
	#endif
}
void Sys_MakeCodeWriteable(unsigned long startaddr, unsigned long length) { (void)startaddr; (void)length; }

void Sys_PrintSystemInfo(void) { Con_Printf("Vril Engine SDL (%s)\n", SDL_GetPlatform()); }
void Sys_Printf(char *fmt, ...) { va_list args; va_start(args, fmt); vfprintf(stdout, fmt, args); va_end(args); }
void Sys_SystemError(char *error) { fprintf(stderr, "Vril Engine: %s\n", error); if (SDL_WasInit(SDL_INIT_VIDEO)) SDL_ShowSimpleMessageBox(SDL_MESSAGEBOX_ERROR, "Vril Engine", error, sdl_window); SDL_Quit(); exit(1); }
void Sys_Quit(void) { sdl_running = false; }
double Sys_FloatTime(void) { static Uint64 start; Uint64 now = SDL_GetPerformanceCounter(); if (!start) start = now; return (double)(now - start) / SDL_GetPerformanceFrequency(); }
char *Sys_ConsoleInput(void) { return NULL; }
void Sys_Sleep(void) { SDL_Delay(1); }
void Sys_HighFPPrecision(void) {}
void Sys_LowFPPrecision(void) {}
void Sys_SetFPCW(void) {}
void Sys_DebugLog(char *file, char *fmt, ...) { (void)file; (void)fmt; }
void Sys_CaptureScreenshot(void)
{
	SDL_Surface *screenshot;
	unsigned char *pixels;
	int width = vid.width;
	int height = vid.height;
	int row_size = width * 3;
	int y;

	pixels = malloc((size_t)row_size * height);
	if (!pixels) {
		Con_Printf("Could not allocate screenshot buffer.\n");
		return;
	}

	glReadBuffer(GL_BACK);
	glPixelStorei(GL_PACK_ALIGNMENT, 1);
	glReadPixels(0, 0, width, height, GL_RGB, GL_UNSIGNED_BYTE, pixels);

#if SDL_BYTEORDER == SDL_BIG_ENDIAN
	screenshot = SDL_CreateRGBSurface(0, width, height, 24,
		0xff0000, 0x00ff00, 0x0000ff, 0);
#else
	screenshot = SDL_CreateRGBSurface(0, width, height, 24,
		0x0000ff, 0x00ff00, 0xff0000, 0);
#endif
	if (!screenshot) {
		Con_Printf("Could not create screenshot surface: %s\n", SDL_GetError());
		free(pixels);
		return;
	}

	for (y = 0; y < height; ++y)
		memcpy((byte *)screenshot->pixels + y * screenshot->pitch,
			pixels + (height - y - 1) * row_size, row_size);

	if (SDL_SaveBMP(screenshot, "capture.bmp") != 0)
		Con_Printf("Could not save capture.bmp: %s\n", SDL_GetError());

	SDL_FreeSurface(screenshot);
	free(pixels);
}

void Sys_DefaultConfig(void)
{
	Cbuf_AddText("bind MOUSE1 +attack\n");
	Cbuf_AddText("bind MOUSE2 +aim\n");
	Cbuf_AddText("bind w +forward\n");
	Cbuf_AddText("bind s +back\n");
	Cbuf_AddText("bind a +moveleft\n");
	Cbuf_AddText("bind d +moveright\n");
	Cbuf_AddText("bind SPACE +jump\n");
	Cbuf_AddText("bind MWHEELUP +switch\n");
	Cbuf_AddText("bind MWHEELDOWN +switch\n");
	Cbuf_AddText("bind ` toggleconsole\n");
}


int mouse_dx;
int mouse_dy;

/* ======================================================================= */
/* Controles tactiles (Android)                                            */
/*                                                                         */
/* SDL en Android entrega SDL_FINGER* normalizados 0..1. Con               */
/* SDL_HINT_TOUCH_MOUSE_EVENTS=0 no llegan raton emulado, asi que el       */
/* finger ES la fuente de verdad. Zonas:                                   */
/*  - mitad izquierda inferior  -> stick de movimiento (vector al centro)  */
/*  - mitad derecha inferior    -> mirar (arrastre)                        */
/*  - botones visibles          -> comandos (+attack, +use, ...)           */
/*  - taps en menu/consola      -> Menu_* / toggleconsole                  */
/*                                                                         */
/* Modo edicion (touch_edit 1): los botones se pueden arrastrar a otra      */
/* posicion; touch_layout guarda "i:x,y;..." en fracciones y persiste.      */
/* El boton BACK fisico del movil emite "togglemenu" (menu opciones).       */
/* ======================================================================= */
#define NZP_TOUCH_SLOTS 10

static void SDL_MenuCoordinates(int window_x, int window_y, int *drawable_x, int *drawable_y);

cvar_t touch_ui = {"touch_ui", "1", true};
cvar_t touch_edit = {"touch_edit", "0", true};      /* modo edicion: arrastra botones */
cvar_t touch_layout = {"touch_layout", "", true};   /* posiciones guardadas, persistente */
cvar_t touch_aimtoggle = {"touch_aimtoggle", "0", true}; /* AIM alterna (tap) en vez de mantener */
cvar_t touch_scale = {"touch_scale", "1", true};   /* multiplicador de tamano de botones/stick */

/* Multiplicador de tamano con clamp de seguridad (0.5 - 2.0). */
static float NZP_TouchScale(void)
{
	float v = touch_scale.value;
	if (v < 0.5f) v = 0.5f;
	if (v > 2.0f) v = 2.0f;
	return v;
}

typedef struct {
	float cx, cy;              /* centro, fraccion de pantalla (x de ancho, y de alto) */
	float radius;              /* radio en fraccion del alto */
	const char *label;         /* texto sobre el boton (NULL = stick) */
	const char *cmd_down;      /* comando al pulsar ("+attack"); NULL para toggle */
	const char *cmd_up;        /* comando al soltar ("-attack") */
} nzp_touch_button_t;

/* Layout por defecto; las posiciones en runtime vienen de NZP_TouchRuntime[],
 * inicializadas desde aca y sobreescritas por touch_layout. */
static nzp_touch_button_t NZP_TouchButtons[] = {
	{ 0.880f, 0.620f, 0.050f, "FIRE", "+attack", "-attack" },
	{ 0.760f, 0.540f, 0.042f, "AIM",  "+aim",    "-aim"    },
	{ 0.950f, 0.470f, 0.040f, "RLD",  "+reload", "-reload" },
	{ 0.880f, 0.380f, 0.040f, "USE",  "+use",    "-use"    },
	{ 0.780f, 0.440f, 0.042f, "JMP",  "+jump",   "-jump"   },
	{ 0.670f, 0.400f, 0.034f, "SW",   "+switch", "-switch" },
	{ 0.960f, 0.360f, 0.034f, "GR",   "+grenade","-grenade"},
	{ 0.640f, 0.280f, 0.034f, "KNIFE", "+knife",  "-knife"  },
	{ 0.045f, 0.035f, 0.028f, "MENU", "togglemenu", NULL   },
	{ 0.115f, 0.035f, 0.028f, "CONS", "toggleconsole", NULL },
};
#define NZP_TOUCH_NUMBUTTONS ((int)(sizeof(NZP_TouchButtons) / sizeof(NZP_TouchButtons[0])))
#define NZP_TOUCH_STICK_CX 0.115f
#define NZP_TOUCH_STICK_CY 0.720f
#define NZP_TOUCH_STICK_R  0.115f

/* Posiciones runtime editables. touch_layout persiste "i:cx,cy;..." en
 * fracciones de pantalla; el radio no se edita (viene del default). */
typedef struct {
	float cx, cy;
} nzp_touch_pos_t;
static nzp_touch_pos_t NZP_TouchRuntime[NZP_TOUCH_NUMBUTTONS];
static float NZP_TouchStickCenter[2] = { NZP_TOUCH_STICK_CX, NZP_TOUCH_STICK_CY };
#define NZP_TOUCH_STICK_R_RUNTIME (NZP_TOUCH_STICK_R)

/* Carga touch_layout ("0:0.88,0.62;4:0.50,0.50") sobre NZP_TouchRuntime.
 * Los indices sin entrada quedan con su posicion por defecto. */
static void NZP_TouchLoadLayout(void)
{
	const char *p = touch_layout.string;
	int idx, parsed;
	float fx, fy;

	for (idx = 0; idx < NZP_TOUCH_NUMBUTTONS; ++idx) {
		NZP_TouchRuntime[idx].cx = NZP_TouchButtons[idx].cx;
		NZP_TouchRuntime[idx].cy = NZP_TouchButtons[idx].cy;
	}
	while (*p) {
		if (sscanf(p, " %d : %f , %f%n", &idx, &fx, &fy, &parsed) == 3 &&
			idx >= 0 && idx < NZP_TOUCH_NUMBUTTONS) {
			NZP_TouchRuntime[idx].cx = fx;
			NZP_TouchRuntime[idx].cy = fy;
		}
		while (*p && *p != ';') ++p;
		if (*p) ++p;
	}
}

/* Serializa NZP_TouchRuntime a touch_layout (persistido en config.cfg). */
static void NZP_TouchSaveLayout(void)
{
	char buf[512];
	int i, used = 0;
	size_t len = 0;

	buf[0] = '\0';
	for (i = 0; i < NZP_TOUCH_NUMBUTTONS; ++i) {
		used += snprintf(buf + len, sizeof(buf) - len, "%s%d:%.4f,%.4f",
			used ? ";" : "", i, NZP_TouchRuntime[i].cx, NZP_TouchRuntime[i].cy);
		len = strlen(buf);
		if (len >= sizeof(buf) - 16) break;
	}
	Cvar_Set("touch_layout", buf);
}

typedef struct {
	SDL_FingerID id;
	int button;                /* indice de boton, -1 si no es boton */
	qboolean is_look;
	qboolean is_move;
	qboolean is_edit_drag;     /* modo edicion: este dedo arrastra un boton */
	int edit_index;
	qboolean is_menu;          /* menu: este dedo hace tap/clic (fuera del stick) */
	qboolean is_menu_stick;    /* menu: este dedo maneja el stick del raton */
	qboolean menu_moved;       /* menu: se movio mas que el umbral de tap */
	float last_x, last_y;      /* ultima fraccion vista (look) */
} nzp_touch_slot_t;

static qboolean NZP_TouchInitialized = false;
static qboolean NZP_TouchLayoutApplied = false;
static int NZP_TouchPlaceIndex = -1;   /* >=0: siguiente toque coloca ese boton */
static qboolean NZP_TouchAimToggled = false;
static nzp_touch_slot_t NZP_TouchSlots[NZP_TOUCH_SLOTS];
static float NZP_TouchStickX, NZP_TouchStickY;      /* -1..1 (y: arriba=+1) */
static float NZP_TouchLookDx, NZP_TouchLookDy;      /* delta acumulado en px */
static float NZP_TouchStickPx, NZP_TouchStickPy;    /* knob para el dibujo, px */
static float NZP_MenuCursor[2] = { -1.0f, -1.0f };  /* raton emulado en menus (px drawable) */

/* Stick de menu (joystick derecho) para mover el raton en menus. */
#define NZP_TOUCH_MENUSTICK_CX 0.855f
#define NZP_TOUCH_MENUSTICK_CY 0.700f
#define NZP_TOUCH_MENUSTICK_R  0.115f
static float NZP_MenuStickX, NZP_MenuStickY;      /* -1..1 (y: arriba=+1) */
static float NZP_MenuStickPx, NZP_MenuStickPy;    /* knob para el dibujo, px */
static qboolean NZP_MenuStickActive = false;

/* sprint al llevar el stick de movimiento al tope (impulse 23/24, edge) */
static qboolean NZP_TouchSprinting = false;
#define NZP_TOUCH_SPRINT_ON  0.97f   /* deflexion que activa el sprint */
#define NZP_TOUCH_SPRINT_OFF 0.85f   /* desactiva (histeresis anti-flicker) */

/* Edge-trigger: QC lee self.impulse una vez y lo pone a 0, asi que cada
 * impulse se envia UNA vez por cruce de umbral. Solo sprinta hacia adelante
 * (defl_y > 0.4) para no activarse en diagonales/strafe. */
static void NZP_TouchUpdateSprint(float defl_x, float defl_y)
{
	float mag = sqrtf(defl_x * defl_x + defl_y * defl_y);
	if (!NZP_TouchSprinting) {
		if (mag >= NZP_TOUCH_SPRINT_ON && defl_y > 0.4f) {
			NZP_TouchSprinting = true;
			Cbuf_AddText("impulse 23\n");
		}
	} else {
		if (mag <= NZP_TOUCH_SPRINT_OFF || defl_y <= 0.0f) {
			NZP_TouchSprinting = false;
			Cbuf_AddText("impulse 24\n");
		}
	}
}

void NZP_TouchConsumeLook(int *dx, int *dy)
{
	*dx = (int)NZP_TouchLookDx;
	*dy = (int)NZP_TouchLookDy;
	NZP_TouchLookDx = 0;
	NZP_TouchLookDy = 0;
}

void NZP_TouchGetMove(float *mx, float *my)
{
	*mx = NZP_TouchStickX;
	*my = NZP_TouchStickY;
}

/* Comando de consola: activa/desactiva el modo edicion del layout. */
static void NZP_TouchEdit_f(void);
static void NZP_TouchReset_f(void);
static void NZP_TouchUpdateStickVisual(void);
void Menu_Touch_Set(void);
void Menu_Touch_Place_f(void);
static void NZP_TouchPlace0_f(void);
static void NZP_TouchPlace1_f(void);
static void NZP_TouchPlace2_f(void);
static void NZP_TouchPlace3_f(void);
static void NZP_TouchPlace4_f(void);
static void NZP_TouchPlace5_f(void);
static void NZP_TouchPlace6_f(void);
static void NZP_TouchPlace7_f(void);
static void NZP_TouchPlace8_f(void);
static void NZP_TouchPlace9_f(void);

/* Registro de cvars/comandos del touch. Se llama desde IN_PlatformInit()
 * (dentro de Host_Init), cuando Memory_Init ya inicializo el z_zone.
 * NO llamar antes de Host_Init: Cvar_RegisterVariable -> Cvar_SetQuick ->
 * Z_Strdup -> Z_Malloc crashea sin memoria inicializada (SIGSEGV). */
void NZP_TouchRegisterCvars(void)
{
	if (NZP_TouchInitialized) return;
	Cvar_RegisterVariable(&touch_ui);
	Cvar_RegisterVariable(&touch_edit);
	Cvar_RegisterVariable(&touch_layout);
	Cvar_RegisterVariable(&touch_aimtoggle);
	Cvar_RegisterVariable(&touch_scale);
	Cmd_AddCommand("touch_editmode", NZP_TouchEdit_f);
	Cmd_AddCommand("touch_resetlayout", NZP_TouchReset_f);
	Cmd_AddCommand("menu_touch", Menu_Touch_Set);
	Cmd_AddCommand("touch_place_0", NZP_TouchPlace0_f);
	Cmd_AddCommand("touch_place_1", NZP_TouchPlace1_f);
	Cmd_AddCommand("touch_place_2", NZP_TouchPlace2_f);
	Cmd_AddCommand("touch_place_3", NZP_TouchPlace3_f);
	Cmd_AddCommand("touch_place_4", NZP_TouchPlace4_f);
	Cmd_AddCommand("touch_place_5", NZP_TouchPlace5_f);
	Cmd_AddCommand("touch_place_6", NZP_TouchPlace6_f);
	Cmd_AddCommand("touch_place_7", NZP_TouchPlace7_f);
	Cmd_AddCommand("touch_place_8", NZP_TouchPlace8_f);
	Cmd_AddCommand("touch_place_9", NZP_TouchPlace9_f);
	memset(NZP_TouchSlots, 0, sizeof(NZP_TouchSlots));
	/* Posiciones por defecto hasta aplicar el layout guardado en config.cfg. */
	NZP_TouchLoadLayout();
	NZP_TouchLayoutApplied = false;
	NZP_TouchInitialized = true;
}

/* Lazy, por cada evento de dedo. Solo aplica el layout guardado UNA vez,
 * tras haber cargado config.cfg (host_initialized). */
static void NZP_TouchInit(void)
{
	if (NZP_TouchInitialized) {
		if (!NZP_TouchLayoutApplied && host_initialized) {
			NZP_TouchLoadLayout();
			NZP_TouchLayoutApplied = true;
		}
		return;
	}
	/* Evento antes del registro (no deberia ocurrir): no hay cvars aun. */
}

static nzp_touch_slot_t *NZP_TouchFindSlot(SDL_FingerID id)
{
	int i;
	for (i = 0; i < NZP_TOUCH_SLOTS; ++i)
		if (NZP_TouchSlots[i].id == id && (NZP_TouchSlots[i].button >= 0 ||
			NZP_TouchSlots[i].is_look || NZP_TouchSlots[i].is_move ||
			NZP_TouchSlots[i].is_menu || NZP_TouchSlots[i].is_menu_stick ||
			NZP_TouchSlots[i].is_edit_drag))
			return &NZP_TouchSlots[i];
	return NULL;
}

static nzp_touch_slot_t *NZP_TouchFreeSlot(void)
{
	int i;
	for (i = 0; i < NZP_TOUCH_SLOTS; ++i)
		if (NZP_TouchSlots[i].button < 0 && !NZP_TouchSlots[i].is_look &&
			!NZP_TouchSlots[i].is_move && !NZP_TouchSlots[i].is_menu &&
			!NZP_TouchSlots[i].is_menu_stick && !NZP_TouchSlots[i].is_edit_drag)
			return &NZP_TouchSlots[i];
	return NULL;
}

static qboolean NZP_TouchButtonIsPressed(int index)
{
	int i;
	for (i = 0; i < NZP_TOUCH_SLOTS; ++i)
		if (NZP_TouchSlots[i].button == index) return true;
	return false;
}

static int NZP_TouchHitButton(float fx, float fy)
{
	int i;
	float dx, dy, dist2, r;
	for (i = 0; i < NZP_TOUCH_NUMBUTTONS; ++i) {
		dx = (fx - NZP_TouchRuntime[i].cx) * 1.0f;          /* fracciones x */
		dy = (fy - NZP_TouchRuntime[i].cy) * 2.0f;          /* pantalla alta: y mide el doble */
		r = NZP_TouchButtons[i].radius * 1.35f * NZP_TouchScale(); /* hit generoso */
		dist2 = dx * dx + dy * dy;
		if (dist2 <= r * r)
			return i;
	}
	return -1;
}

static void NZP_TouchPressButton(int index)
{
	if (!NZP_TouchButtons[index].cmd_down) return;
	/* Disparar o cuchillo cancelan el sprint (el QC hace W_SprintStop al
	 * atacar); desarmamos el edge para poder reactivarlo al soltar. */
	if ((index == 0 || index == 9) && NZP_TouchSprinting) {
		NZP_TouchSprinting = false;
		Cbuf_AddText("impulse 24\n");
	}
	Cbuf_AddText((char *)NZP_TouchButtons[index].cmd_down);
	Cbuf_AddText("\n");
}

static void NZP_TouchReleaseButton(int index)
{
	if (!NZP_TouchButtons[index].cmd_up) return;
	/* Al soltar disparo/cuchillo con el stick seguia a tope adelante:
	 * reactiva el sprint sin mover el dedo. */
	if ((index == 0 || index == 9) && !NZP_TouchSprinting &&
		NZP_TouchStickY > 0.4f) {
		float mag = sqrtf(NZP_TouchStickX * NZP_TouchStickX +
			NZP_TouchStickY * NZP_TouchStickY);
		if (mag >= NZP_TOUCH_SPRINT_ON) {
			NZP_TouchSprinting = true;
			Cbuf_AddText("impulse 23\n");
		}
	}
	Cbuf_AddText((char *)NZP_TouchButtons[index].cmd_up);
	Cbuf_AddText("\n");
}

/* Stick de menu: deflexion del knob respecto al centro (clamp al radio). */
static void NZP_TouchMenuStickUpdate(float fx, float fy)
{
	float dx = (fx - NZP_TOUCH_MENUSTICK_CX) * (float)vid.width;
	float dy = (fy - NZP_TOUCH_MENUSTICK_CY) * (float)vid.height;
	float r = NZP_TOUCH_MENUSTICK_R * (float)vid.height * NZP_TouchScale();
	float len = sqrtf(dx * dx + dy * dy);
	float scale = (len > r && len > 0.0f) ? r / len : 1.0f;
	NZP_MenuStickX = dx * scale / r;
	NZP_MenuStickY = -dy * scale / r;
	NZP_MenuStickPx = NZP_TOUCH_MENUSTICK_CX * (float)vid.width + dx * scale;
	NZP_MenuStickPy = NZP_TOUCH_MENUSTICK_CY * (float)vid.height + dy * scale;
}

static void NZP_TouchFingerDown(float fx, float fy, SDL_FingerID id)
{
	nzp_touch_slot_t *slot;
	int b;

	/* --- modo edicion: tocar un boton y arrastrarlo --- */
	if (touch_edit.value && key_dest == key_game) {
		b = NZP_TouchHitButton(fx, fy);
		slot = NZP_TouchFreeSlot();
		if (slot) {
			slot->id = id;
			slot->button = -1;
			slot->is_look = slot->is_move = false;
			slot->is_edit_drag = true;
			slot->edit_index = b;
			slot->is_menu = false;
			slot->is_menu_stick = false;
			if (b >= 0) {
				NZP_TouchRuntime[b].cx = fx;
				NZP_TouchRuntime[b].cy = fy;
			}
		}
		return;
	}

		if (key_dest == key_console) {
		if (touch_edit.value) Cvar_SetValue("touch_edit", 0.0f); /* salir de edicion */
		Con_ToggleConsole_f();
		return;
	}
	if (key_dest == key_menu || key_dest == key_menu_pause) {
		/* Raton emulado con JOYSTICK (zona derecha): el stick desplaza el
		 * cursor con velocidad; fuera del stick, doble tap rapido = clic. */
		nzp_touch_slot_t *mslot = NZP_TouchFreeSlot();
		float mdx, mdy, mlen, mr;
		if (NZP_MenuCursor[0] < 0.0f) {
			/* primera vez: cursor en el centro */
			NZP_MenuCursor[0] = (float)vid.width * 0.5f;
			NZP_MenuCursor[1] = (float)vid.height * 0.5f;
			Menu_MouseMove((int)NZP_MenuCursor[0], (int)NZP_MenuCursor[1]);
		}
		mdx = (fx - NZP_TOUCH_MENUSTICK_CX) * (float)vid.width;
		mdy = (fy - NZP_TOUCH_MENUSTICK_CY) * (float)vid.height;
		mlen = sqrtf(mdx * mdx + mdy * mdy);
		mr = NZP_TOUCH_MENUSTICK_R * (float)vid.height * NZP_TouchScale();
		if (mslot && mlen <= mr * 1.5f) {
			/* dedo sobre el stick de menu: maneja el raton */
			mslot->id = id;
			mslot->button = -1;
			mslot->is_look = mslot->is_move = mslot->is_edit_drag = false;
			mslot->edit_index = -1;
			mslot->is_menu = false;
			mslot->is_menu_stick = true;
			mslot->menu_moved = true;    /* el stick nunca genera clic */
			NZP_MenuStickActive = true;
			NZP_TouchMenuStickUpdate(fx, fy);
		} else if (mslot) {
			/* fuera del stick: tap (doble tap rapido = clic) */
			mslot->id = id;
			mslot->button = -1;
			mslot->is_look = mslot->is_move = mslot->is_edit_drag = false;
			mslot->edit_index = -1;
			mslot->is_menu = true;
			mslot->is_menu_stick = false;
			mslot->menu_moved = false;
			mslot->last_x = fx;
			mslot->last_y = fy;
		}
		return;
	}
	/* pantalla de carga esperando confirmacion: cualquier toque = ENTER */
	if (key_dest == key_game && LoadingScreen_IsWaiting()) {
		Key_Event(K_ENTER, true);
		Key_Event(K_ENTER, false);
		return;
	}
	if (key_dest != key_game) return;

	/* Colocar boton pendiente desde el menu TOUCH OPTIONS. */
	if (NZP_TouchPlaceIndex >= 0) {
		NZP_TouchRuntime[NZP_TouchPlaceIndex].cx = fx;
		NZP_TouchRuntime[NZP_TouchPlaceIndex].cy = fy;
		NZP_TouchSaveLayout();
		Con_Printf("touch: '%s' colocado en %.3f,%.3f (guardado)\n",
			NZP_TouchButtons[NZP_TouchPlaceIndex].label, fx, fy);
		NZP_TouchPlaceIndex = -1;
		return;
	}

	b = NZP_TouchHitButton(fx, fy);
	if (b >= 0) {
		/* AIM en modo toggle: tap alterna, no mantener. */
		if (touch_aimtoggle.value && NZP_TouchButtons[b].cmd_down &&
			!strcmp(NZP_TouchButtons[b].cmd_down, "+aim")) {
			NZP_TouchAimToggled = !NZP_TouchAimToggled;
			Cbuf_AddText(NZP_TouchAimToggled ? "+aim\n" : "-aim\n");
			return;
		}
		slot = NZP_TouchFreeSlot();
		if (slot) {
			slot->id = id; slot->button = b;
			slot->is_look = slot->is_move = false;
			slot->is_edit_drag = false;
			slot->edit_index = -1;
			slot->is_menu = false;
			slot->is_menu_stick = false;
			NZP_TouchPressButton(b);
		}
		return;
	}

	slot = NZP_TouchFreeSlot();
	if (!slot) return;
	slot->id = id;
	slot->button = -1;
	slot->is_look = (fx >= 0.5f);
	slot->is_move = !slot->is_look;
	slot->is_edit_drag = false;
	slot->edit_index = -1;
	slot->is_menu = false;
	slot->is_menu_stick = false;
	slot->last_x = fx;
	slot->last_y = fy;
	if (slot->is_move) {
		/* vector en pixeles respecto al centro del stick, normalizado al radio.
		 * El aspecto importa: x esta en fraccion de ancho, y en de alto. */
		float dx = (fx - NZP_TOUCH_STICK_CX) * (float)vid.width;
		float dy = (fy - NZP_TOUCH_STICK_CY) * (float)vid.height;
		float r = NZP_TOUCH_STICK_R_RUNTIME * (float)vid.height * NZP_TouchScale();
		float len = sqrtf(dx * dx + dy * dy);
		float scale = (len > r && len > 0.0f) ? r / len : 1.0f;
		NZP_TouchStickX = dx * scale / r;
		NZP_TouchStickY = -dy * scale / r;
		NZP_TouchUpdateSprint(NZP_TouchStickX, NZP_TouchStickY);
	}
}

static void NZP_TouchFingerMotion(float fx, float fy, SDL_FingerID id)
{
	nzp_touch_slot_t *slot = NZP_TouchFindSlot(id);

	if (!slot) return;
	if (slot->is_menu_stick) {
		NZP_TouchMenuStickUpdate(fx, fy);
		return;
	}
	if (slot->is_menu) {
		/* raton emulado touchpad: desplazamiento relativo al dedo.
		 * x2.0 para recorrer la pantalla comodo. */
		float dx, dy;
		if (fabsf(fx - slot->last_x) * (float)vid.width +
			fabsf(fy - slot->last_y) * (float)vid.height > 12.0f)
			slot->menu_moved = true;   /* ya no es un tap */
		dx = (fx - slot->last_x) * (float)vid.width * 2.0f;
		dy = (fy - slot->last_y) * (float)vid.height * 2.0f;
		slot->last_x = fx;
		slot->last_y = fy;
		NZP_MenuCursor[0] += dx;
		NZP_MenuCursor[1] += dy;
		if (NZP_MenuCursor[0] < 0.0f) NZP_MenuCursor[0] = 0.0f;
		if (NZP_MenuCursor[1] < 0.0f) NZP_MenuCursor[1] = 0.0f;
		if (NZP_MenuCursor[0] > (float)vid.width - 1.0f)
			NZP_MenuCursor[0] = (float)vid.width - 1.0f;
		if (NZP_MenuCursor[1] > (float)vid.height - 1.0f)
			NZP_MenuCursor[1] = (float)vid.height - 1.0f;
		Menu_MouseMove((int)NZP_MenuCursor[0], (int)NZP_MenuCursor[1]);
		return;
	}
	if (slot->is_edit_drag) {
		if (slot->edit_index >= 0) {
			NZP_TouchRuntime[slot->edit_index].cx = fx;
			NZP_TouchRuntime[slot->edit_index].cy = fy;
		}
		return;
	}
	if (slot->button >= 0) return;
	if (slot->is_look) {
		NZP_TouchLookDx += (fx - slot->last_x) * (float)vid.width;
		NZP_TouchLookDy += (fy - slot->last_y) * (float)vid.height;
		slot->last_x = fx;
		slot->last_y = fy;
	} else if (slot->is_move) {
		float dx = (fx - NZP_TOUCH_STICK_CX) * (float)vid.width;
		float dy = (fy - NZP_TOUCH_STICK_CY) * (float)vid.height;
		float r = NZP_TOUCH_STICK_R_RUNTIME * (float)vid.height * NZP_TouchScale();
		float len = sqrtf(dx * dx + dy * dy);
		float scale = (len > r && len > 0.0f) ? r / len : 1.0f;
		NZP_TouchStickX = dx * scale / r;
		NZP_TouchStickY = -dy * scale / r;
		NZP_TouchUpdateSprint(NZP_TouchStickX, NZP_TouchStickY);
	}
}

static void NZP_TouchFingerUp(float fx, float fy, SDL_FingerID id)
{
	nzp_touch_slot_t *slot = NZP_TouchFindSlot(id);
	(void)fx; (void)fy;
	if (!slot) return;
	if (slot->is_menu_stick) {
		/* soltar el stick del raton: reset */
		NZP_MenuStickActive = false;
		NZP_MenuStickX = 0.0f;
		NZP_MenuStickY = 0.0f;
		slot->id = 0; slot->button = -1;
		slot->is_look = slot->is_move = false;
		slot->is_edit_drag = false; slot->edit_index = -1;
		slot->is_menu = false; slot->menu_moved = false;
		slot->is_menu_stick = false;
		return;
	}
	if (slot->is_menu) {
		/* tap corto (sin arrastre) = clic bajo el cursor.
		 * Debounce 350ms: un doble tap fisico no activa dos veces. */
		static unsigned menu_last_click_ms = 0;
		unsigned now = SDL_GetTicks();
		if (!slot->menu_moved && NZP_MenuCursor[0] >= 0.0f &&
			now - menu_last_click_ms > 350) {
			int cx = (int)NZP_MenuCursor[0];
			int cy = (int)NZP_MenuCursor[1];
			Menu_MouseMove(cx, cy);
			if (Menu_MouseButton(cx, cy, true))
				Menu_MouseButton(cx, cy, false);   /* slider: fija valor */
			else
				Menu_ButtonPress();                /* activar boton */
			menu_last_click_ms = now;
		}
		slot->id = 0; slot->button = -1;
		slot->is_look = slot->is_move = false;
		slot->is_edit_drag = false; slot->edit_index = -1;
		slot->is_menu = false; slot->menu_moved = false;
		slot->is_menu_stick = false;
		return;
	}
	if (slot->is_edit_drag) {
		if (slot->edit_index >= 0) NZP_TouchSaveLayout();
		slot->id = 0; slot->button = -1;
		slot->is_look = slot->is_move = false;
		slot->is_edit_drag = false; slot->edit_index = -1;
		slot->is_menu = false;
		slot->is_menu_stick = false;
		return;
	}
	if (slot->button >= 0) NZP_TouchReleaseButton(slot->button);
	if (slot->is_move) {
		NZP_TouchStickX = 0; NZP_TouchStickY = 0;
		if (NZP_TouchSprinting) {
			NZP_TouchSprinting = false;
			Cbuf_AddText("impulse 24\n");
		}
	}
	slot->id = 0; slot->button = -1;
	slot->is_look = slot->is_move = false;
	slot->is_edit_drag = false; slot->edit_index = -1;
	slot->is_menu = false;
	slot->is_menu_stick = false;
}

/* --------------------------- overlay en pantalla ------------------------- */

static void NZP_TouchDrawCircle(float cx, float cy, float radius, float r, float g, float b, float a)
{
	float angle;
	int i;

	glDisable(GL_TEXTURE_2D);
	glEnable(GL_BLEND);
	glDisable(GL_ALPHA_TEST);
	glColor4f(r, g, b, a);
	glBegin(GL_POLYGON);
	glVertex2f(cx, cy);
	for (i = 0; i <= 20; ++i) {
		angle = (float)i * 6.2831853f / 20.0f;
		glVertex2f(cx + cosf(angle) * radius, cy + sinf(angle) * radius);
	}
	glEnd();
	glColor4f(1, 1, 1, 1);
	glEnable(GL_ALPHA_TEST);
	glDisable(GL_BLEND);
	glEnable(GL_TEXTURE_2D);
}

void NZP_DrawTouchControls(void)
{
	float h = (float)vid.height;
	float w = (float)vid.width;
	float s = h / 400.0f;
	int i;
	qboolean edit_mode = touch_edit.value && key_dest == key_game;

	/* menus: joystick derecho mueve el raton (velocidad por frame) y cursor.
	 * Va antes de los early-return: se dibuja siempre que haya menu. */
	if (key_dest == key_menu || key_dest == key_menu_pause) {
		/* reset defensivo: si el stick quedo activo sin dedo, centrarlo */
		if (!NZP_MenuStickActive &&
			(NZP_MenuStickX != 0.0f || NZP_MenuStickY != 0.0f)) {
			NZP_MenuStickX = 0.0f;
			NZP_MenuStickY = 0.0f;
		}
		if (NZP_MenuCursor[0] >= 0.0f) {
			static double menu_last_t = 0.0;
			double now = Sys_FloatTime();
			float dt = (menu_last_t > 0.0 && now > menu_last_t)
				? (float)(now - menu_last_t) : 0.0f;
			menu_last_t = now;
			if (dt > 0.1f) dt = 0.1f;
			if (NZP_MenuStickActive &&
				(NZP_MenuStickX != 0.0f || NZP_MenuStickY != 0.0f)) {
				/* stick de menu: velocidad proporcional a la deflexion */
				NZP_MenuCursor[0] += NZP_MenuStickX * (float)vid.width * 1.4f * dt;
				NZP_MenuCursor[1] -= NZP_MenuStickY * (float)vid.height * 1.4f * dt;
				if (NZP_MenuCursor[0] < 0.0f) NZP_MenuCursor[0] = 0.0f;
				if (NZP_MenuCursor[1] < 0.0f) NZP_MenuCursor[1] = 0.0f;
				if (NZP_MenuCursor[0] > (float)vid.width - 1.0f)
					NZP_MenuCursor[0] = (float)vid.width - 1.0f;
				if (NZP_MenuCursor[1] > (float)vid.height - 1.0f)
					NZP_MenuCursor[1] = (float)vid.height - 1.0f;
				Menu_MouseMove((int)NZP_MenuCursor[0], (int)NZP_MenuCursor[1]);
			}
		}
		/* stick de menu dibujado (abajo a la derecha) */
		{
			float mbx = NZP_TOUCH_MENUSTICK_CX * w;
			float mby = NZP_TOUCH_MENUSTICK_CY * h;
			float mbr = NZP_TOUCH_MENUSTICK_R * h * NZP_TouchScale();
			NZP_TouchDrawCircle(mbx, mby, mbr, 1, 1, 1, 0.10f);
			NZP_TouchDrawCircle(mbx + NZP_MenuStickX * mbr,
				mby - NZP_MenuStickY * mbr, mbr * 0.45f,
				1, 1, 1, NZP_MenuStickActive ? 0.40f : 0.22f);
		}
		if (NZP_MenuCursor[0] >= 0.0f) {
			NZP_TouchDrawCircle(NZP_MenuCursor[0], NZP_MenuCursor[1], 9.0f * s, 1, 1, 1, 0.15f);
			NZP_TouchDrawCircle(NZP_MenuCursor[0], NZP_MenuCursor[1], 3.5f * s, 1, 1, 1, 0.85f);
		}
	}

	if (!touch_ui.value) return;
	if (key_dest != key_game) return;
	NZP_TouchUpdateStickVisual();

	/* stick de movimiento */
	{
		float bx = NZP_TouchStickCenter[0] * w;
		float by = NZP_TouchStickCenter[1] * h;
		float br = NZP_TOUCH_STICK_R_RUNTIME * h * NZP_TouchScale();
		NZP_TouchDrawCircle(bx, by, br, 1, 1, 1, 0.10f);
		NZP_TouchDrawCircle(NZP_TouchStickPx, NZP_TouchStickPy, br * 0.45f, 1, 1, 1, 0.30f);
	}

	/* botones */
	for (i = 0; i < NZP_TOUCH_NUMBUTTONS; ++i) {
		float bx = NZP_TouchRuntime[i].cx * w;
		float by = NZP_TouchRuntime[i].cy * h;
		float br = NZP_TouchButtons[i].radius * h * NZP_TouchScale();
		float ls = s * NZP_TouchScale();              /* escala del texto del boton */
		qboolean pressed = NZP_TouchButtonIsPressed(i);
		/* AIM en modo toggle: se ve activo mientras la mira esta puesta. */
		if (touch_aimtoggle.value && NZP_TouchButtons[i].cmd_down &&
			!strcmp(NZP_TouchButtons[i].cmd_down, "+aim"))
			pressed = NZP_TouchAimToggled;
		NZP_TouchDrawCircle(bx, by, br,
			edit_mode ? 0.3f : (pressed ? 1.0f : 0.6f),
			edit_mode ? 0.9f : (pressed ? 0.8f : 0.6f),
			edit_mode ? 1.0f : (pressed ? 0.2f : 0.6f),
			edit_mode ? 0.30f : (pressed ? 0.45f : 0.18f));
		if (NZP_TouchButtons[i].label) {
			Draw_ColoredString((int)(bx - getTextWidth((char *)NZP_TouchButtons[i].label, ls) / 2), (int)(by - 4 * s),
				(char *)NZP_TouchButtons[i].label, 1, 1, 1, pressed ? 1.0f : 0.6f, ls);
		}
	}

	/* banner: colocar boton pendiente */
	if (NZP_TouchPlaceIndex >= 0 && NZP_TouchPlaceIndex < NZP_TOUCH_NUMBUTTONS) {
		Draw_ColoredString(8, (int)(8 * s),
			va("PLACE: toca para poner '%s'", NZP_TouchButtons[NZP_TouchPlaceIndex].label),
			1, 1, 0, 1, s);
	}

	/* banner de modo edicion */
	if (edit_mode) {
		Draw_ColoredString(8, (int)(8 * s), "EDIT: arrastra botones", 1, 1, 0, 1, s);
		Draw_ColoredString(8, (int)(24 * s), "CONS para salir", 1, 1, 0, 1, s);
	}
}

/* Comando de consola: activa/desactiva el modo edicion del layout. */
static void NZP_TouchEdit_f(void)
{
	if (key_dest != key_game) return;
	Cvar_SetValue("touch_edit", touch_edit.value ? 0.0f : 1.0f);
	if (!touch_edit.value) NZP_TouchSaveLayout();
	Con_Printf("touch_edit %s\n", touch_edit.value ? "ON (arrastra los botones)" : "OFF (guardado)");
}

/* Comando de consola: restaura el layout por defecto. */
static void NZP_TouchReset_f(void)
{
	Cvar_Set("touch_layout", "");
	NZP_TouchLoadLayout();
	Con_Printf("touch layout restaurado\n");
}

/* --- Colocar un boton tocando la pantalla (desde menu TOUCH OPTIONS) --- */
void Menu_Touch_Place_f(void)
{
	if (NZP_TouchPlaceIndex >= 0 && NZP_TouchPlaceIndex < NZP_TOUCH_NUMBUTTONS)
		Con_Printf("touch: coloca '%s' tocando la pantalla\n", NZP_TouchButtons[NZP_TouchPlaceIndex].label);
}

/* Genera los comandos touch_place_N de consola (menu TOUCH OPTIONS). */
#define NZP_TOUCH_PLACE_FN(n) \
	static void NZP_TouchPlace##n##_f(void) \
	{ \
		NZP_TouchPlaceIndex = n; \
		key_dest = key_game; \
		m_state = m_none; \
		Menu_Touch_Place_f(); \
	}

NZP_TOUCH_PLACE_FN(0)
NZP_TOUCH_PLACE_FN(1)
NZP_TOUCH_PLACE_FN(2)
NZP_TOUCH_PLACE_FN(3)
NZP_TOUCH_PLACE_FN(4)
NZP_TOUCH_PLACE_FN(5)
NZP_TOUCH_PLACE_FN(6)
NZP_TOUCH_PLACE_FN(7)
NZP_TOUCH_PLACE_FN(8)
NZP_TOUCH_PLACE_FN(9)

/* knob del stick: posición visual derivada del vector actual */
static void NZP_TouchUpdateStickVisual(void)
{
	float w = (float)vid.width;
	float h = (float)vid.height;
	float br = NZP_TOUCH_STICK_R_RUNTIME * h * NZP_TouchScale();
	NZP_TouchStickPx = NZP_TouchStickCenter[0] * w + NZP_TouchStickX * br * 0.6f;
	NZP_TouchStickPy = NZP_TouchStickCenter[1] * h - NZP_TouchStickY * br * 0.6f;
}
static int SDL_KeyToQuake(SDL_Keycode key)
{
	switch (key) {
	case SDLK_UP: return K_UPARROW; case SDLK_DOWN: return K_DOWNARROW;
	case SDLK_LEFT: return K_LEFTARROW; case SDLK_RIGHT: return K_RIGHTARROW;
	case SDLK_ESCAPE: return K_ESCAPE; case SDLK_RETURN: case SDLK_KP_ENTER: return K_ENTER;
	case SDLK_AC_BACK: return K_ESCAPE;   /* BACK fisico Android = ESC (menu/atras) */
	case SDLK_TAB: return K_TAB; case SDLK_BACKSPACE: return '\b';
	case SDLK_DELETE: return K_DELETE;
	case SDLK_F1: return K_AUX1; case SDLK_F2: return K_AUX2; case SDLK_F3: return K_AUX3;
	case SDLK_F4: return K_AUX4; case SDLK_F5: return K_AUX5; case SDLK_F6: return K_AUX6;
	case SDLK_F7: return K_AUX7; case SDLK_F8: return K_AUX8; case SDLK_F9: return K_AUX9;
	case SDLK_F10: return K_AUX10; case SDLK_F11: return K_AUX11; case SDLK_F12: return K_AUX12;
	case SDLK_F13: return K_AUX13; case SDLK_F14: return K_AUX14; case SDLK_F15: return K_AUX15;
	case SDLK_F16: return K_AUX16; case SDLK_F17: return K_AUX17; case SDLK_F18: return K_AUX18;
	case SDLK_F19: return K_AUX19; case SDLK_F20: return K_AUX20; case SDLK_F21: return K_AUX21;
	case SDLK_F22: return K_AUX22; case SDLK_F23: return K_AUX23; case SDLK_F24: return K_AUX24;
	case SDLK_LSHIFT: case SDLK_RSHIFT: return K_SHIFT;
	case SDLK_LCTRL: case SDLK_RCTRL: return K_CTRL;
	case SDLK_LALT: case SDLK_RALT: return K_ALT;
	case SDLK_HOME: return K_HOME; case SDLK_END: return K_END;
	case SDLK_PAGEUP: return K_PGUP; case SDLK_PAGEDOWN: return K_PGDN;
	case SDLK_INSERT: return K_INSERT; case SDLK_PAUSE: return K_PAUSE;
	case SDLK_CAPSLOCK: return K_CAPSLOCK; case SDLK_NUMLOCKCLEAR: return K_NUMLOCK;
	case SDLK_SCROLLLOCK: return K_SCROLLLOCK; case SDLK_PRINTSCREEN: return K_PRINTSCREEN;
	case SDLK_KP_9: return K_KP_9; case SDLK_KP_PERIOD: return K_KP_PERIOD;
	case SDLK_KP_DIVIDE: return K_KP_DIVIDE; case SDLK_KP_MULTIPLY: return K_KP_MULTIPLY;
	case SDLK_KP_MINUS: return K_KP_MINUS; case SDLK_KP_PLUS: return K_KP_PLUS;
	case SDLK_KP_EQUALS: return K_KP_EQUALS;
	case SDLK_APPLICATION: return K_APPLICATION; case SDLK_POWER: return K_POWER;
	case SDLK_HELP: return K_HELP; case SDLK_MENU: return K_MENU; case SDLK_SELECT: return K_SELECT_KEY;
	case SDLK_STOP: return K_STOP; case SDLK_AGAIN: return K_AGAIN; case SDLK_UNDO: return K_UNDO;
	case SDLK_CUT: return K_CUT; case SDLK_COPY: return K_COPY; case SDLK_PASTE: return K_PASTE;
	case SDLK_FIND: return K_FIND; case SDLK_MUTE: return K_MUTE; case SDLK_VOLUMEUP: return K_VOLUMEUP;
	case SDLK_VOLUMEDOWN: return K_VOLUMEDOWN; case SDLK_SYSREQ: return K_SYSREQ; case SDLK_CLEAR: return K_CLEAR;
	case SDLK_SPACE: return K_SPACE;
	default:
		if (key >= 32 && key < 127) return (int)key;
		{
			SDL_Scancode scancode = SDL_GetScancodeFromKey(key);
			return scancode != SDL_SCANCODE_UNKNOWN ? K_SDL_SCANCODE_BASE + scancode : 0;
		}
	}
}

static int SDL_StringToKeynum(const char *name)
{
	SDL_Scancode scancode = SDL_GetScancodeFromName(name);
	return scancode != SDL_SCANCODE_UNKNOWN ? K_SDL_SCANCODE_BASE + scancode : -1;
}

static const char *SDL_KeynumToString(int keynum)
{
	if (keynum < K_SDL_SCANCODE_BASE || keynum >= MAX_KEYS)
		return NULL;
	return SDL_GetScancodeName((SDL_Scancode)(keynum - K_SDL_SCANCODE_BASE));
}

static int SDL_MouseToQuake(Uint8 button)
{
	switch (button) { case SDL_BUTTON_LEFT: return K_MOUSE1; case SDL_BUTTON_RIGHT: return K_MOUSE2; case SDL_BUTTON_MIDDLE: return K_MOUSE3; case SDL_BUTTON_X1: return K_MOUSE4; case SDL_BUTTON_X2: return K_MOUSE5; default: return 0; }
}

static int SDL_ControllerToQuake(SDL_GameControllerButton button)
{
	switch (button) {
	case SDL_CONTROLLER_BUTTON_A: return K_BOTTOMFACE;
	case SDL_CONTROLLER_BUTTON_B: return K_RIGHTFACE;
	case SDL_CONTROLLER_BUTTON_X: return K_LEFTFACE;
	case SDL_CONTROLLER_BUTTON_Y: return K_TOPFACE;
	case SDL_CONTROLLER_BUTTON_BACK: return K_SELECT;
	case SDL_CONTROLLER_BUTTON_START: return K_START;
	case SDL_CONTROLLER_BUTTON_LEFTSTICK: return K_LTHUMB;
	case SDL_CONTROLLER_BUTTON_RIGHTSTICK: return K_RTHUMB;
	case SDL_CONTROLLER_BUTTON_LEFTSHOULDER: return K_LTRIGGER;
	case SDL_CONTROLLER_BUTTON_RIGHTSHOULDER: return K_RTRIGGER;
	case SDL_CONTROLLER_BUTTON_DPAD_UP: return K_DPAD_UP;
	case SDL_CONTROLLER_BUTTON_DPAD_DOWN: return K_DPAD_DOWN;
	case SDL_CONTROLLER_BUTTON_DPAD_LEFT: return K_DPAD_LEFT;
	case SDL_CONTROLLER_BUTTON_DPAD_RIGHT: return K_DPAD_RIGHT;
	default: return 0;
	}
}

static void SDL_MenuCoordinates(int window_x, int window_y, int *drawable_x, int *drawable_y)
{
	int window_width, window_height;
	SDL_GetWindowSize(sdl_window, &window_width, &window_height);
	*drawable_x = window_width > 0 ? window_x * (int)vid.width / window_width : window_x;
	*drawable_y = window_height > 0 ? window_y * (int)vid.height / window_height : window_y;
}

void Sys_SendKeyEvents(void)
{
	SDL_Event event;
	static qboolean trigger_down[2];
	if (!SDL_WasInit(SDL_INIT_EVENTS))
		return;
	while (SDL_PollEvent(&event)) {
		int key;
		switch (event.type) {
		case SDL_QUIT: sdl_running = false; break;
		case SDL_KEYDOWN: case SDL_KEYUP:
			if (event.type == SDL_KEYDOWN) { IN_SetActiveDevice(IN_DEVICE_KEYBOARD_MOUSE); Menu_SetInputDevice(IN_DEVICE_KEYBOARD_MOUSE); }
			if (event.type == SDL_KEYDOWN && !event.key.repeat &&
				event.key.keysym.sym == SDLK_BACKQUOTE && key_dest == key_menu) {
				Con_ToggleConsole_f();
				break;
			}
			key = SDL_KeyToQuake(event.key.keysym.sym);
			if (key && !event.key.repeat) Key_Event(key, event.type == SDL_KEYDOWN);
			break;
		case SDL_MOUSEBUTTONDOWN: case SDL_MOUSEBUTTONUP:
			if (event.type == SDL_MOUSEBUTTONDOWN) { IN_SetActiveDevice(IN_DEVICE_KEYBOARD_MOUSE); Menu_SetInputDevice(IN_DEVICE_KEYBOARD_MOUSE); }
			key = SDL_MouseToQuake(event.button.button);
			if (key && !in_bind && (key_dest == key_menu || key_dest == key_menu_pause)) {
				int x, y;
				qboolean slider_handled = false;
				SDL_MenuCoordinates(event.button.x, event.button.y, &x, &y);
				Menu_MouseMove(x, y);
				if (key == K_MOUSE1)
					slider_handled = Menu_MouseButton(x, y, event.type == SDL_MOUSEBUTTONDOWN);
				if (event.type == SDL_MOUSEBUTTONDOWN && key == K_MOUSE1 && !slider_handled) Menu_ButtonPress();
			} else if (key) Key_Event(key, event.type == SDL_MOUSEBUTTONDOWN);
			break;
		case SDL_MOUSEMOTION:
			if (event.motion.xrel || event.motion.yrel) { IN_SetActiveDevice(IN_DEVICE_KEYBOARD_MOUSE); Menu_SetInputDevice(IN_DEVICE_KEYBOARD_MOUSE); }
			if (key_dest == key_menu || key_dest == key_menu_pause) {
				int x, y;
				SDL_MenuCoordinates(event.motion.x, event.motion.y, &x, &y);
				Menu_MouseMove(x, y);
			}
			else { mouse_dx += event.motion.xrel; mouse_dy += event.motion.yrel; }
			break;
		case SDL_MOUSEWHEEL:
			if (in_bind || (key_dest != key_menu && key_dest != key_menu_pause)) {
				key = event.wheel.y > 0 ? K_MWHEELUP : event.wheel.y < 0 ? K_MWHEELDOWN : 0;
				if (key) { Key_Event(key, true); Key_Event(key, false); }
			} else {
				if (event.wheel.y > 0) Menu_IncreaseCursor();
				if (event.wheel.y < 0) Menu_DecreaseCursor();
			}
			break;
		case SDL_CONTROLLERBUTTONDOWN: case SDL_CONTROLLERBUTTONUP:
			IN_SDLControllerActivated(event.cbutton.which);
			if (event.type == SDL_CONTROLLERBUTTONDOWN) { IN_SetActiveDevice(IN_DEVICE_GAMEPAD); Menu_SetInputDevice(IN_DEVICE_GAMEPAD); }
			key = SDL_ControllerToQuake(event.cbutton.button);
			if (key) Key_Event(key, event.type == SDL_CONTROLLERBUTTONDOWN);
			break;
		case SDL_CONTROLLERAXISMOTION:
			IN_SDLControllerActivated(event.caxis.which);
			if (event.caxis.value > 8192 || event.caxis.value < -8192) { IN_SetActiveDevice(IN_DEVICE_GAMEPAD); Menu_SetInputDevice(IN_DEVICE_GAMEPAD); }
			if (event.caxis.axis == SDL_CONTROLLER_AXIS_TRIGGERLEFT ||
				event.caxis.axis == SDL_CONTROLLER_AXIS_TRIGGERRIGHT) {
				int trigger = event.caxis.axis == SDL_CONTROLLER_AXIS_TRIGGERLEFT ? 0 : 1;
				qboolean down = event.caxis.value > 16384;
				if (down != trigger_down[trigger]) {
					trigger_down[trigger] = down;
					Key_Event(trigger ? K_ZRTRIGGER : K_ZLTRIGGER, down);
				}
			}
			break;
		case SDL_CONTROLLERDEVICEADDED: IN_SDLControllerAdded(event.cdevice.which); break;
		case SDL_CONTROLLERDEVICEREMOVED: IN_SDLControllerRemoved(event.cdevice.which); break;
		case SDL_FINGERDOWN:
			NZP_TouchInit();
			NZP_TouchFingerDown(event.tfinger.x, event.tfinger.y, event.tfinger.fingerId);
			break;
		case SDL_FINGERMOTION:
			NZP_TouchInit();
			NZP_TouchFingerMotion(event.tfinger.x, event.tfinger.y, event.tfinger.fingerId);
			break;
		case SDL_FINGERUP:
			NZP_TouchInit();
			NZP_TouchFingerUp(event.tfinger.x, event.tfinger.y, event.tfinger.fingerId);
			break;
		case SDL_WINDOWEVENT:
			if (event.window.event == SDL_WINDOWEVENT_SIZE_CHANGED || event.window.event == SDL_WINDOWEVENT_RESIZED)
				VID_SDLResize();
			break;
		}
	}
	SDL_SetRelativeMouseMode((key_dest == key_game && SDL_GetKeyboardFocus() == sdl_window) ? SDL_TRUE : SDL_FALSE);
}

int main(int argc, char **argv)
{
	quakeparms_t parms;
	startup_arguments_t startup;
	const char *base_directory;
	char startup_error[256];
	size_t heap_size;
	double oldtime;
	qboolean headless_test;
	const char *android_basedir = NULL;

#ifdef __ANDROID__
	/* Redirige stdout/stderr a logcat (tag "nzportable-stdout"). Sin esto, TODO
	 * el texto del motor (Con_Printf, Sys_Error, banners de arranque) se pierde
	 * y no se ve por que Host_Init aborta. */
	{
		int pfd[2];
		if (pipe(pfd) == 0) {
			int pfd_err[2];
			if (pipe(pfd_err) == 0) {
				int rd_out = dup(pfd[0]);      /* lectura stdout para hilo */
				int rd_err = dup(pfd_err[0]);  /* lectura stderr para hilo */
				dup2(pfd[0], STDIN_FILENO);
				dup2(pfd[1], STDOUT_FILENO);
				dup2(pfd_err[1], STDERR_FILENO);
				close(pfd[0]); close(pfd[1]);
				close(pfd_err[0]); close(pfd_err[1]);
				/* Sin buffer: si el proceso aborta (Sys_Error/exit), el texto
				 * ya esta en el pipe y no se pierde en el buffer de stdio. */
				setvbuf(stdout, NULL, _IONBF, 0);
				setvbuf(stderr, NULL, _IONBF, 0);
				/* Hilos de lectura: vuelcan lo escrito a logcat. */
				{
					pthread_t t_out, t_err;
					int *a_out = (int *)malloc(2 * sizeof(int));
					int *a_err = (int *)malloc(2 * sizeof(int));
					a_out[0] = rd_out; a_out[1] = 0;
					a_err[0] = rd_err; a_err[1] = 1;
					if (pthread_create(&t_out, NULL, NZP_LogcatThread, a_out) == 0)
						pthread_detach(t_out);
					if (pthread_create(&t_err, NULL, NZP_LogcatThread, a_err) == 0)
						pthread_detach(t_err);
				}
			}
		}
	}
	/* En Android "." es "/", de solo lectura, asi que el motor necesita una
	 * ruta absoluta real. NZPData_FindBasedir() decide entre el almacenamiento
	 * externo (si el usuario ya volco los datos) y la extraccion de los assets
	 * del APK a almacenamiento interno. Ver sys_android_data.c. */
	android_basedir = NZPData_FindBasedir();
	if (!android_basedir) {
		fprintf(stderr, "NZ:P: no se encontraron los datos del juego\n");
		return 1;
	}
	base_directory = android_basedir;
#endif

	memset(&parms, 0, sizeof(parms));
	if (!Startup_LoadArguments(&startup, argc, argv, "setup.ini",
		startup_error, sizeof(startup_error))) {
		fprintf(stderr, "Startup: %s\n", startup_error);
		return 1;
	}
	if (!Startup_GetBaseDirectory(&startup, ".",
		&base_directory, startup_error, sizeof(startup_error))) {
		fprintf(stderr, "Startup: %s\n", startup_error);
		Startup_FreeArguments(&startup);
		return 1;
	}
#ifdef __ANDROID__
	/* Si el usuario no paso -basedir (ni en setup.ini ni en los argumentos de
	 * la activity) se usa el directorio resuelto a partir de los assets o del
	 * almacenamiento externo. */
	if (base_directory[0] == '.' && base_directory[1] == '\0')
		base_directory = android_basedir;
#endif
	headless_test = TestHandler_ArgumentsAllowHeadless(startup.argc, startup.argv) ||
		SDL_ArgumentsRequestHeadless(startup.argc, startup.argv);
#ifdef __ANDROID__
	/* Touch limpio: sin raton emulado (evita eventos duplicados) y con
	 * multitouch (stick + mirar + botones a la vez). */
	SDL_SetHint(SDL_HINT_TOUCH_MOUSE_EVENTS, "0");
	SDL_SetHint(SDL_HINT_MOUSE_TOUCH_EVENTS, "0");
	/* Paisaje fijo: SDL elige orientacion de la Activity segun el hint
	 * (SDLActivity.setOrientation). Sin esto, una ventana RESIZABLE en
	 * portrait inicial dejaba el juego en vertical. */
	SDL_SetHint(SDL_HINT_ORIENTATIONS, "LandscapeLeft LandscapeRight");
#endif
	if (SDL_Init(headless_test ? SDL_INIT_TIMER :
		(SDL_INIT_VIDEO | SDL_INIT_AUDIO | SDL_INIT_EVENTS | SDL_INIT_GAMECONTROLLER)) != 0) {
		fprintf(stderr, "SDL_Init: %s\n", SDL_GetError());
		Startup_FreeArguments(&startup);
		return 1;
	}
	Key_SetPlatformKeyConversion(SDL_StringToKeynum, SDL_KeynumToString);
	parms.membase = Startup_AllocateHeap(&startup, DEFAULT_MEMORY_MB * 1024 * 1024,
		&heap_size, startup_error, sizeof(startup_error));
	if (!parms.membase) {
		fprintf(stderr, "Startup: %s\n", startup_error);
		Startup_FreeArguments(&startup);
		SDL_Quit();
		return 1;
	}
	parms.memsize = (int)heap_size;
	parms.basedir = (char *)base_directory;
	COM_InitArgv(startup.argc, startup.argv);
	parms.argc = com_argc;
	parms.argv = com_argv;
	/* NOTA: no registrar cvars de touch aqui. Cvar_RegisterVariable usa
	 * Z_Malloc y antes de Host_Init el z_zone no existe (SIGSEGV en Z_Malloc).
	 * El registro ocurre en NZP_TouchRegisterCvars() <- IN_PlatformInit(). */
	Host_Init(&parms);
#ifdef NZP_VR_OPENXR
	/* VR: despues de Host_Init porque registra cvars (necesita la zona de
	 * memoria ya creada). Si no hay runtime OpenXR en el dispositivo queda
	 * desactivada y el juego sigue 2D igual. */
	VR_Init();
#endif
	oldtime = Sys_FloatTime();
#ifdef NZP_VR_OPENXR
	int vr_diag_i = 0;
#endif
	while (sdl_running) {
		double now = Sys_FloatTime();
#ifdef NZP_VR_OPENXR
		/* VR: xrWaitFrame/xrBeginFrame antes de simular+renderizar, y
		 * xrEndFrame despues. Sin capas todavia (paso 3: render estereo). */
		qboolean vr_frame = VR_IsActive() && VR_BeginFrame();
		/* Diagnostico: primeras iteraciones del bucle para ver si el loop
		 * sigue vivo tras xrBeginSession y donde se atasca si no. */
		if (vr_diag_i < 40) {
			vr_diag_i++;
			VR_DiagLog("LOOP %d active=%d frame=%d started=%d", vr_diag_i,
				(int)VR_IsActive(), (int)vr_frame, (int)VR_IsSessionStarted());
		}
#endif
		Host_Frame(now - oldtime);
#ifdef NZP_VR_OPENXR
		if (vr_frame)
			VR_EndFrame();
		else if (VR_IsSessionStarted())
			SDL_Delay(8);	/* sesion VR abierta pero sin compositor (estado
							 * READY/SYNC aun): no quemar CPU ni llenar el log */
		if (vr_diag_i == 10 || vr_diag_i == 20 || vr_diag_i == 40)
			VR_DiagLog("LOOP %d post-Host_Frame", vr_diag_i);
#endif
		music_update();
		oldtime = now;
	}
	if (host_initialized)
		Host_Shutdown();
#ifdef NZP_VR_OPENXR
	VR_Shutdown();
#endif
	free(parms.membase);
	Startup_FreeArguments(&startup);
	SDL_Quit();
	return 0;
}
