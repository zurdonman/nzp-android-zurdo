/*
================================================================================
sys_android_data.c -- resolucion del directorio de datos en Android
================================================================================

Problema
--------
En Android el proceso no arranca con un directorio de trabajo util: "." es "/"
y ademas es de solo lectura. El motor busca los datos con

    nzp/progs.dat, nzp/config.cfg, nzp/maps/*.bsp ...

usando fopen()/stat() relativos a <basedir>, asi que hay que darle una ruta
absoluta real. Ademas "progs.dat" NO puede ir comprimido dentro del APK porque
se abre con file descriptor (Sys_FileOpenRead).

Estrategia (dos niveles)
------------------------
1. ALMACENAMIENTO EXTERNO EFIMERO (preferido si ya existe)
       /sdcard/Android/data/<pkg>/files/nzp/progs.dat
   Si el usuario ha volcado ahi los datos (adb push, o un instalador posterior),
   se usa ese directorio tal cual. Permite mods y actualizaciones de datos sin
   recompilar el APK.

2. ASSETS DEL APK (primer arranque)
       assets/base/nzp/**  ->  <interno>/nzpdata/nzp/**
   Se copia el arbol completo de assets a almacenamiento interno la primera vez.
   El marcador <interno>/nzpdata/.complete evita repetir la copia.

La extraccion se hace con la API nativa AAssetManager (libandroid.so) en vez de
usar SDL_RWops, porque hace falta fopen() real despues. Los ficheros muy grandes
no se pueden abrir como asset (limite ~1 MB de AAsset_openFileDescriptor), pero
los .bsp y .mp3 grandes del juego NO se leen con fopen: van por COM_FOpenFile.
Para los que si se leen asi (.dat, .txt, .lmp, .cfg) el tamano es pequeno.
Todos los ficheros se leen en el modo "buffer completo" de AAsset para no
depender de ese limite.

Ver tambien: source/platform/android/sys_sdl.c (main)
================================================================================
*/

#include "../../nzportable_def.h"

#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/types.h>

#include <android/log.h>
#include <android/asset_manager.h>
#include <android/asset_manager_jni.h>
#include <jni.h>
#include <SDL.h>

#define NZP_DATA_TAG     "nzportable"
#define NZP_ASSET_ROOT   "nzp"           /* carpeta dentro de assets/base */
#define NZP_EXTRACT_DIR  "nzpdata"       /* carpeta de destino en almacen interno */
#define NZP_MARKER       ".complete"

static void NZPData_Log(const char *fmt, ...)
{
	va_list ap;
	va_start(ap, fmt);
	__android_log_vprint(ANDROID_LOG_INFO, NZP_DATA_TAG, fmt, ap);
	va_end(ap);
}

/* Crea todos los directorios intermedios de una ruta de fichero. */
static void NZPData_NormalizeSlashes(char *path)
{
	char *cursor;
	for (cursor = path; *cursor; ++cursor) {
		if (*cursor == '\\')
			*cursor = '/';
		if (*cursor == '\r') {
			*cursor = '\0';
			break;
		}
	}
}

static void NZPData_NormalizeAssetRelPath(char *path)
{
	char *cursor;
	char *start = path;
	NZPData_NormalizeSlashes(path);
	while (*start == '.' && start[1] == '/')
		start += 2;
	if (start != path) {
		memmove(path, start, strlen(start) + 1);
	}
	for (cursor = path; *cursor; ++cursor) {
		if (cursor[0] == '/' && cursor[1] == '/')
			memmove(cursor, cursor + 1, strlen(cursor + 1) + 1);
	}
}

static int NZPData_MakeDirs(const char *file_path)
{
	char buffer[MAX_OSPATH];
	char *cursor;
	int len;

	len = (int)strlen(file_path);
	if (len <= 0 || len >= (int)sizeof(buffer))
		return 0;

	memcpy(buffer, file_path, (size_t)len + 1);
	NZPData_NormalizeSlashes(buffer);

	/* Recorre la copia y crea cada nivel al encontrar un separador. */
	for (cursor = buffer + 1; *cursor; ++cursor) {
		if (*cursor != '/')
			continue;
		*cursor = '\0';
		if (mkdir(buffer, 0777) != 0 && errno != EEXIST)
			return 0;
		*cursor = '/';
	}

	return 1;
}

static int NZPData_FileExists(const char *path)
{
	struct stat st;
	return stat(path, &st) == 0 && S_ISREG(st.st_mode);
}

/* Comprueba que un directorio de datos es utilizable de verdad. */
static int NZPData_LooksUsable(const char *basedir)
{
	char probe[MAX_OSPATH];
	snprintf(probe, sizeof(probe), "%s/" GAMENAME "/progs.dat", basedir);
	return NZPData_FileExists(probe);
}

/*
================
NZPData_CopyAsset

Copia un unico asset a disco. Devuelve 1 si el fichero quedo en disco.
================
*/
static int NZPData_CopyAsset(AAssetManager *manager, const char *asset_path,
	const char *dest_path, size_t *written_out)
{
	AAsset *asset;
	FILE *out;
	unsigned char buffer[16384];
	int read;

	*written_out = 0;

	if (!NZPData_MakeDirs(dest_path)) {
		NZPData_Log("no pude crear directorios para %s", dest_path);
		return 0;
	}

	asset = AAssetManager_open(manager, asset_path, AASSET_MODE_BUFFER);
	if (!asset) {
		NZPData_Log("asset no encontrado: %s", asset_path);
		return 0;
	}

	out = fopen(dest_path, "wb");
	if (!out) {
		NZPData_Log("no pude escribir %s (%s)", dest_path, strerror(errno));
		AAsset_close(asset);
		return 0;
	}

	while ((read = AAsset_read(asset, buffer, sizeof(buffer))) > 0) {
		if (fwrite(buffer, 1, (size_t)read, out) != (size_t)read) {
			NZPData_Log("escritura incompleta en %s", dest_path);
			fclose(out);
			AAsset_close(asset);
			return 0;
		}
		*written_out += (size_t)read;
	}

	fclose(out);
	AAsset_close(asset);

	if (read < 0) {
		NZPData_Log("lectura fallida de %s", asset_path);
		return 0;
	}

	return 1;
}

/*
================
NZPData_ExtractDir

Recorre un directorio de assets con AAssetDir. Se usa SOLO como plan B: en
Android AAssetDir_getNextFileName() NO devuelve los subdirectorios, asi que no
puede descubrir el arbol por si solo.
================
*/
static void NZPData_ExtractDir(AAssetManager *manager, const char *asset_dir,
	const char *dest_dir, int depth, int *files_out, size_t *bytes_out)
{
	AAssetDir *dir;
	const char *name;

	if (depth > 16)
		return;

	dir = AAssetManager_openDir(manager, asset_dir);
	if (!dir)
		return;

	while ((name = AAssetDir_getNextFileName(dir)) != NULL) {
		char asset_path[MAX_OSPATH];
		char dest_path[MAX_OSPATH];
		size_t written = 0;

		if (name[0] == '\0' || name[0] == '.')
			continue;

		snprintf(asset_path, sizeof(asset_path), "%s/%s", asset_dir, name);
		snprintf(dest_path, sizeof(dest_path), "%s/%s", dest_dir, name);

		if (NZPData_CopyAsset(manager, asset_path, dest_path, &written)) {
			(*files_out)++;
			*bytes_out += written;
		}
	}

	AAssetDir_close(dir);
}

/*
================
NZPData_ExtractViaFilelist

Extrae el arbol usando el indice <asset_root>/filelist.txt, que genera
scripts/gen_asset_filelist.ps1 antes de compilar.

Por que un indice y no recursion nativa: AAssetDir_getNextFileName() solo
devuelve los ficheros de un nivel (no los subdirectorios), de modo que no hay
forma de descubrir gfx/, maps/, sounds/... desde la API de assets. El indice
tiene un nombre NORMAL (nada de ".filelist"): Android descarta en silencio los
ficheros de assets que empiezan por punto.
================
*/
static int NZPData_ExtractViaFilelist(AAssetManager *manager,
	const char *asset_root, const char *dest_root, int *files_out,
	size_t *bytes_out)
{
	char list_path[MAX_QPATH];
	AAsset *list_asset;
	size_t total;
	char *blob;
	char *cursor;
	int got;
	int files = 0;
	size_t bytes = 0;

	snprintf(list_path, sizeof(list_path), "%s/filelist.txt", asset_root);
	list_asset = AAssetManager_open(manager, list_path, AASSET_MODE_BUFFER);
	if (!list_asset)
		return 0;

	total = (size_t)AAsset_getLength(list_asset);
	blob = (char *)malloc(total + 1);
	if (!blob) {
		AAsset_close(list_asset);
		return 0;
	}

	got = (int)AAsset_read(list_asset, blob, total);
	AAsset_close(list_asset);
	if (got <= 0) {
		free(blob);
		return 0;
	}
	blob[got] = '\0';

	cursor = blob;
	while (*cursor) {
		char *eol = strchr(cursor, '\n');
		char rel_path[MAX_OSPATH];
		char asset_path[MAX_OSPATH];
		char dest_path[MAX_OSPATH];
		size_t written = 0;

		if (eol)
			*eol = '\0';

		snprintf(rel_path, sizeof(rel_path), "%s", cursor);
		NZPData_NormalizeAssetRelPath(rel_path);

		if (rel_path[0] != '\0') {
			snprintf(asset_path, sizeof(asset_path), "%s/%s",
				asset_root, rel_path);
			snprintf(dest_path, sizeof(dest_path), "%s/%s",
				dest_root, rel_path);
			if (NZPData_CopyAsset(manager, asset_path, dest_path, &written)) {
				files++;
				bytes += written;
			} else {
				NZPData_Log("aviso: fallo copiando %s", asset_path);
			}
		}

		if (!eol)
			break;
		cursor = eol + 1;
	}

	free(blob);
	*files_out = files;
	*bytes_out = bytes;
	return files > 0;
}

/*
================
NZPData_ExtractTree

Replica assets/<root>/** en <dest_root>. Primero intenta el indice filelist.txt
y, si no existe, cae al recorrido con AAssetDir (que solo cubre el primer
nivel pero al menos deja el juego arrancar con los ficheros de la raiz).
================
*/
static int NZPData_ExtractTree(AAssetManager *manager,
	const char *asset_root, const char *dest_root)
{
	int files = 0;
	size_t bytes = 0;
	int ok;

	ok = NZPData_ExtractViaFilelist(manager, asset_root, dest_root, &files, &bytes);
	if (!ok) {
		NZPData_Log("sin indice %s/filelist.txt; probando AAssetDir (solo nivel 1)",
			asset_root);
		files = 0;
		bytes = 0;
		NZPData_ExtractDir(manager, asset_root, dest_root, 0, &files, &bytes);
		ok = files > 0;
	}

	NZPData_Log("extraidos %d ficheros, %.1f MB (ok=%d)", files,
		(double)bytes / (1024.0 * 1024.0), ok);

	return ok;
}

/*
================
NZPData_FindBasedir

Devuelve la ruta de basedir a usar, o NULL si no hay datos disponibles.
================
*/
const char *NZPData_FindBasedir(void)
{
	static char basedir[MAX_OSPATH];
	const char *external;
	char marker[MAX_OSPATH];
	int fd;

	/* ---- 1. Almacenamiento externo ya preparado por el usuario ---- */
	external = SDL_AndroidGetExternalStoragePath();
	if (external && NZPData_LooksUsable(external)) {
		NZPData_Log("basedir = almacenamiento externo: %s", external);
		return external;
	}

	/* ---- 2. Extraccion de los assets a almacenamiento interno ---- */
	{
		const char *internal = SDL_AndroidGetInternalStoragePath();
		JNIEnv *env;
		jobject activity;
		jclass activity_class;
		jmethodID get_assets;
		jobject asset_manager_obj;
		AAssetManager *manager = NULL;
		char extract_root[MAX_OSPATH];
		char data_root[MAX_OSPATH];

		if (!internal)
			return NULL;

		snprintf(marker, sizeof(marker), "%s/%s/%s", internal, NZP_EXTRACT_DIR,
			NZP_MARKER);
		snprintf(basedir, sizeof(basedir), "%s/%s", internal, NZP_EXTRACT_DIR);

		if (NZPData_FileExists(marker)) {
			NZPData_Log("basedir = interno ya extraido: %s", basedir);
			return basedir;
		}

		env = (JNIEnv *)SDL_AndroidGetJNIEnv();
		activity = (jobject)SDL_AndroidGetActivity();
		if (!env || !activity)
			return NULL;

		activity_class = (*env)->GetObjectClass(env, activity);
		get_assets = (*env)->GetMethodID(env, activity_class, "getAssets",
			"()Landroid/content/res/AssetManager;");
		if (get_assets) {
			asset_manager_obj = (*env)->CallObjectMethod(env, activity, get_assets);
			if (asset_manager_obj)
				manager = AAssetManager_fromJava(env, asset_manager_obj);
		}

		if (!manager) {
			NZPData_Log("no pude obtener el AssetManager");
			return NULL;
		}

		snprintf(extract_root, sizeof(extract_root), "base/%s", NZP_ASSET_ROOT);
		snprintf(data_root, sizeof(data_root), "%s/%s", basedir, NZP_ASSET_ROOT);
		NZPData_Log("extrayendo assets/%s -> %s", extract_root, data_root);

		if (!NZPData_ExtractTree(manager, extract_root, data_root))
			return NULL;

		fd = open(marker, O_WRONLY | O_CREAT | O_TRUNC, 0666);
		if (fd >= 0)
			close(fd);

		return basedir;
	}
}
