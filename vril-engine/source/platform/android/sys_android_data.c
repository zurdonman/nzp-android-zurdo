/*
================================================================================
sys_android_data.c -- resolucion del directorio de datos y usermaps en Android
================================================================================

Problema
--------
En Android el proceso no arranca con un directorio de trabajo util: "." es "/"
y ademas es de solo lectura. El motor busca los datos con

    nzp/progs.dat, nzp/config.cfg, nzp/maps/*.bsp ...

usando fopen()/stat() relativos a <basedir>, asi que hay que darle una ruta
absoluta real. Ademas "progs.dat" NO puede ir comprimido dentro del APK porque
se abre con file descriptor (Sys_FileOpenRead).

Estrategia (tres niveles)
-------------------------
1. ALMACENAMIENTO EXTERNO COMPLETO (preferido si ya existe un arbol completo)
       /sdcard/Android/data/<pkg>/files/nzp/progs.dat
   Si el usuario ha volcado ahi todos los datos, se usa ese directorio tal cual.

2. ASSETS DEL APK (primer arranque o actualizacion de APK)
       assets/base/nzp/**  ->  <interno>/nzpdata/nzp/**
   Se copia el arbol completo de assets a almacenamiento interno la primera vez,
   y si el APK se actualiza (cambia la huella FNV-1a de filelist.txt o falta
   algun fichero nuevo como maps/town.bsp), se extraen los ficheros nuevos
   conservando el config.cfg del jugador.

3. CARPETA DE USERMAPS EN RUNTIME (sin recompilar el APK)
   En cada arranque (y al abrir USER MAPS / comando `usermaps_rescan`), el motor
   crea y sincroniza automaticamente las carpetas de mapas de usuario hacia
   <basedir>/nzp/:
     - /sdcard/Android/data/com.nzpteam.nzportable/files/usermaps/  (recomendada:
       no requiere permisos especiales en Android 10..15 ni en Meta Quest 3)
     - /sdcard/NZP/usermaps/ y /sdcard/NZP/maps/
     - /sdcard/Download/NZP/usermaps/
   Formatos soportados dentro de `usermaps/`:
     - Archivos sueltos: .bsp, .way, .nsz, .txt, .mb2, .mbox, .hpt (+ .png/.tga/.jpg
       para miniatura de menu)
     - Carpetas desglosadas: maps/, gfx/, models/, sounds/, tracks/, textures/
     - Paquetes comprimidos: .pk3, .zip (Deflate/Stored via zlib) y .pak (Quake PACK)

Ver tambien: source/platform/android/sys_sdl.c (main)
             source/menu/menu_custommaps.c (Menu_CustomMaps_MapFinder)
================================================================================
*/

#include "../../nzportable_def.h"

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/types.h>

#ifdef __ANDROID__
#include <android/log.h>
#include <android/asset_manager.h>
#include <android/asset_manager_jni.h>
#include <jni.h>
#include <SDL.h>
#include <zlib.h>
#else
/* Declaraciones minimas de zlib para compilacion/test fuera del NDK */
typedef struct z_stream_s {
	const unsigned char *next_in;
	unsigned int avail_in;
	unsigned long total_in;
	unsigned char *next_out;
	unsigned int avail_out;
	unsigned long total_out;
	const char *msg;
	void *state;
	void *zalloc;
	void *zfree;
	void *opaque;
	int data_type;
	unsigned long adler;
	unsigned long reserved;
} z_stream;
#define Z_OK            0
#define Z_STREAM_END    1
#define Z_NO_FLUSH      0
#define MAX_WBITS       15
extern int inflateInit2_(z_stream *strm, int windowBits, const char *version, int stream_size);
extern int inflate(z_stream *strm, int flush);
extern int inflateEnd(z_stream *strm);
#define inflateInit2(strm, windowBits) inflateInit2_((strm), (windowBits), "1.2.13", (int)sizeof(z_stream))
#endif

#define NZP_DATA_TAG     "nzportable"
#define NZP_ASSET_ROOT   "nzp"           /* carpeta dentro de assets/base */
#define NZP_EXTRACT_DIR  "nzpdata"       /* carpeta de destino en almacen interno */
#define NZP_MARKER       ".complete"

static char s_resolved_basedir[MAX_OSPATH] = "";

static void NZPData_Log(const char *fmt, ...)
{
	va_list ap;
	va_start(ap, fmt);
#ifdef __ANDROID__
	__android_log_vprint(ANDROID_LOG_INFO, NZP_DATA_TAG, fmt, ap);
#else
	vfprintf(stdout, fmt, ap);
	fprintf(stdout, "\n");
#endif
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
	while (*start == '/')
		start++;
	if (start != path) {
		memmove(path, start, strlen(start) + 1);
	}
	for (cursor = path; *cursor; ++cursor) {
		while (cursor[0] == '/' && cursor[1] == '/')
			memmove(cursor, cursor + 1, strlen(cursor + 1) + 1);
	}
}

static int NZPData_MakeDirs(const char *file_path)
{
	char buffer[MAX_OSPATH * 2];
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

static int NZPData_DirExists(const char *path)
{
	struct stat st;
	return stat(path, &st) == 0 && S_ISDIR(st.st_mode);
}

/* Comprueba que un directorio de datos es utilizable de verdad. */
static int NZPData_LooksUsable(const char *basedir)
{
	char probe[MAX_OSPATH];
	snprintf(probe, sizeof(probe), "%s/" GAMENAME "/progs.dat", basedir);
	return NZPData_FileExists(probe);
}

static void NZPData_StrToLower(char *s)
{
	for (; *s; ++s)
		*s = (char)tolower((unsigned char)*s);
}

static uint64_t NZPData_HashFNV1a(const void *data, size_t len)
{
	const unsigned char *bytes = (const unsigned char *)data;
	uint64_t hash = 14695981039346656037ULL;
	size_t i;
	for (i = 0; i < len; ++i) {
		hash ^= (uint64_t)bytes[i];
		hash *= 1099511628211ULL;
	}
	return hash;
}

/*
================================================================================
MAPEADO INTELIGENTE DE USERMAPS (archivos sueltos, carpetas, .pk3, .zip, .pak)
================================================================================
*/

static int NZPData_IsIgnoredDocFile(const char *basename_lower)
{
	return (!strcmp(basename_lower, "readme.txt") ||
		!strcmp(basename_lower, "leeme.txt") ||
		!strcmp(basename_lower, "leeme_usermaps.txt") ||
		!strcmp(basename_lower, "license.txt") ||
		!strcmp(basename_lower, "credits.txt") ||
		!strcmp(basename_lower, "changelog.txt") ||
		!strcmp(basename_lower, "version.txt") ||
		!strcmp(basename_lower, "filelist.txt"));
}

/*
================
NZPData_MapUserEntryToGameRelPath

Convierte una ruta relativa procedente de `usermaps/` o del interior de un
archivo `.pk3`/`.zip`/`.pak` a su ruta relativa dentro de `<basedir>/nzp/`.
Devuelve 1 si el fichero debe instalarse, o 0 si debe ignorarse.
================
*/
int NZPData_MapUserEntryToGameRelPath(const char *entry_path, char *out_rel,
	size_t out_size)
{
	static const char *const kKnownDirs[] = {
		"maps/", "gfx/", "models/", "sounds/", "tracks/", "textures/", "progs/", "data/"
	};
	char clean[MAX_OSPATH * 2];
	const char *base;
	const char *ext;
	size_t len, i;

	if (!entry_path || !out_rel || out_size < 16)
		return 0;

	snprintf(clean, sizeof(clean), "%s", entry_path);
	NZPData_NormalizeAssetRelPath(clean);
	len = strlen(clean);
	if (len == 0 || clean[len - 1] == '/')
		return 0;

	/* Seguridad: rechazar traversal o metadatos de sistema/marcadores */
	if (strstr(clean, "..") != NULL ||
	    !strncasecmp(clean, "__MACOSX/", 9) ||
	    strstr(clean, "/.") != NULL ||
	    clean[0] == '.')
		return 0;

	base = strrchr(clean, '/');
	base = base ? (base + 1) : clean;
	if (base[0] == '\0' || base[0] == '.' ||
	    !strcasecmp(base, "Thumbs.db") ||
	    !strcasecmp(base, "desktop.ini") ||
	    !strcasecmp(base, "PUT_CUSTOM_SCREENS_HERE"))
		return 0;

	/* 1. Buscar si algun segmento coincide con un directorio estandar de NZ:P */
	for (i = 0; i < sizeof(kKnownDirs) / sizeof(kKnownDirs[0]); ++i) {
		const char *dir_tag = kKnownDirs[i];
		size_t tag_len = strlen(dir_tag);
		const char *p = clean;

		while (*p) {
			if (!strncasecmp(p, dir_tag, tag_len)) {
				const char *sub = p + tag_len;
				if (*sub == '\0')
					return 0;
				if (!strcasecmp(dir_tag, "maps/")) {
					/* Dentro de maps/, forzar el nombre del fichero a minusculas
					 * para que Menu_CustomMaps_MapFinder y COM_FOpenFile("maps/%s.txt")
					 * siempre coincidan en Android (filesystem case-sensitive). */
					const char *map_base = strrchr(sub, '/');
					char lower_map[MAX_QPATH];
					map_base = map_base ? (map_base + 1) : sub;
					snprintf(lower_map, sizeof(lower_map), "%s", map_base);
					NZPData_StrToLower(lower_map);
					if (NZPData_IsIgnoredDocFile(lower_map))
						return 0;
					snprintf(out_rel, out_size, "maps/%s", lower_map);
					return 1;
				}
				snprintf(out_rel, out_size, "%s%s", dir_tag, sub);
				return 1;
			}
			p = strchr(p, '/');
			if (!p)
				break;
			p++;
		}
	}

	/* 1b. Algunos mapas GoldSrc traen "sound/" en lugar de "sounds/" */
	{
		const char *p = clean;
		while (*p) {
			if (!strncasecmp(p, "sound/", 6) && p[6] != '\0') {
				snprintf(out_rel, out_size, "sounds/%s", p + 6);
				return 1;
			}
			p = strchr(p, '/');
			if (!p)
				break;
			p++;
		}
	}

	/* 2. Ficheros sueltos (en la raiz de usermaps/ o dentro de una carpeta con
	 *    el nombre del mapa sin subcarpeta maps/) */
	ext = strrchr(base, '.');
	if (!ext || ext[1] == '\0')
		return 0;
	ext++;

	if (!strcasecmp(ext, "bsp") ||
	    !strcasecmp(ext, "way") ||
	    !strcasecmp(ext, "nsz") ||
	    !strcasecmp(ext, "mb2") ||
	    !strcasecmp(ext, "mbox") ||
	    !strcasecmp(ext, "hpt")) {
		char lower_base[MAX_QPATH];
		snprintf(lower_base, sizeof(lower_base), "%s", base);
		NZPData_StrToLower(lower_base);
		snprintf(out_rel, out_size, "maps/%s", lower_base);
		return 1;
	}

	if (!strcasecmp(ext, "txt")) {
		char lower_base[MAX_QPATH];
		snprintf(lower_base, sizeof(lower_base), "%s", base);
		NZPData_StrToLower(lower_base);
		if (NZPData_IsIgnoredDocFile(lower_base))
			return 0;
		snprintf(out_rel, out_size, "maps/%s", lower_base);
		return 1;
	}

	if (!strcasecmp(ext, "png") ||
	    !strcasecmp(ext, "tga") ||
	    !strcasecmp(ext, "jpg")) {
		char lower_base[MAX_QPATH];
		snprintf(lower_base, sizeof(lower_base), "%s", base);
		NZPData_StrToLower(lower_base);
		snprintf(out_rel, out_size, "gfx/menu/custom/%s", lower_base);
		return 1;
	}

	return 0;
}

/*
================
NZPData_CopyDiskFileIfChanged

Copia `src_path` -> `dst_path` si `dst_path` no existe o tiene distinto tamano
o fecha de modificacion mas antigua. Devuelve 1 si copio el fichero.
================
*/
static int NZPData_CopyDiskFileIfChanged(const char *src_path, const char *dst_path)
{
	struct stat st_src, st_dst;
	FILE *in, *out;
	unsigned char buf[16384];
	size_t n;

	if (stat(src_path, &st_src) != 0 || !S_ISREG(st_src.st_mode))
		return 0;

	if (stat(dst_path, &st_dst) == 0 && S_ISREG(st_dst.st_mode)) {
		if (st_src.st_size == st_dst.st_size && st_src.st_mtime <= st_dst.st_mtime)
			return 0;
	}

	if (!NZPData_MakeDirs(dst_path))
		return 0;

	in = fopen(src_path, "rb");
	if (!in)
		return 0;

	out = fopen(dst_path, "wb");
	if (!out) {
		fclose(in);
		return 0;
	}

	while ((n = fread(buf, 1, sizeof(buf), in)) > 0) {
		if (fwrite(buf, 1, n, out) != n) {
			fclose(out);
			fclose(in);
			return 0;
		}
	}

	fclose(out);
	fclose(in);
	return 1;
}

/*
================================================================================
EXTRACTOR NATIVO DE PAQUETES .PK3 / .ZIP Y .PAK EN USERMAPS
================================================================================
*/

static uint16_t NZPData_ReadLE16(const unsigned char *p)
{
	return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}

static uint32_t NZPData_ReadLE32(const unsigned char *p)
{
	return (uint32_t)p[0] |
	       ((uint32_t)p[1] << 8) |
	       ((uint32_t)p[2] << 16) |
	       ((uint32_t)p[3] << 24);
}

static void NZPData_BuildStampPath(const char *game_root, const char *pkg_path,
	char *stamp_out, size_t stamp_size)
{
	const char *base = strrchr(pkg_path, '/');
	char safe_name[128];
	size_t i;

	base = base ? (base + 1) : pkg_path;
	snprintf(safe_name, sizeof(safe_name), "%s", base);
	for (i = 0; safe_name[i]; ++i) {
		unsigned char c = (unsigned char)safe_name[i];
		if (!isalnum(c) && c != '.' && c != '_' && c != '-')
			safe_name[i] = '_';
	}
	snprintf(stamp_out, stamp_size, "%s/.usermap_pkg_%s.stamp", game_root, safe_name);
}

static int NZPData_CheckPkgStamp(const char *stamp_path, const struct stat *st)
{
	char expected[128];
	char actual[128];
	FILE *f;

	snprintf(expected, sizeof(expected), "%lld:%lld",
		(long long)st->st_size, (long long)st->st_mtime);

	f = fopen(stamp_path, "rb");
	if (!f)
		return 0;
	if (!fgets(actual, sizeof(actual), f)) {
		fclose(f);
		return 0;
	}
	fclose(f);
	actual[strcspn(actual, "\r\n")] = '\0';
	return strcmp(expected, actual) == 0;
}

static void NZPData_WritePkgStamp(const char *stamp_path, const struct stat *st)
{
	FILE *f = fopen(stamp_path, "wb");
	if (!f)
		return;
	fprintf(f, "%lld:%lld\n", (long long)st->st_size, (long long)st->st_mtime);
	fclose(f);
}

/*
================
NZPData_ExtractZipOrPk3

Extrae un archivo `.zip` o `.pk3` recorriendo su Central Directory (compatible
con entradas Stored metodo 0 y Deflate metodo 8, incluso con data descriptors).
================
*/
static int NZPData_ExtractZipOrPk3(const char *zip_path, const char *game_root)
{
	struct stat st;
	char stamp_path[MAX_OSPATH * 2];
	FILE *fp;
	long file_size;
	long search_len;
	unsigned char *tail_buf;
	long eocd_pos = -1;
	long i;
	uint16_t cd_entries;
	uint32_t cd_offset;
	long cur_cd;
	int extracted = 0;

	if (stat(zip_path, &st) != 0 || !S_ISREG(st.st_mode) || st.st_size < 22)
		return 0;

	NZPData_BuildStampPath(game_root, zip_path, stamp_path, sizeof(stamp_path));
	if (NZPData_CheckPkgStamp(stamp_path, &st))
		return 0;

	fp = fopen(zip_path, "rb");
	if (!fp)
		return 0;

	file_size = (long)st.st_size;
	search_len = file_size < 65557L ? file_size : 65557L;
	tail_buf = (unsigned char *)malloc((size_t)search_len);
	if (!tail_buf) {
		fclose(fp);
		return 0;
	}

	if (fseek(fp, file_size - search_len, SEEK_SET) != 0 ||
	    fread(tail_buf, 1, (size_t)search_len, fp) != (size_t)search_len) {
		free(tail_buf);
		fclose(fp);
		return 0;
	}

	for (i = search_len - 22; i >= 0; --i) {
		if (tail_buf[i] == 0x50 && tail_buf[i + 1] == 0x4b &&
		    tail_buf[i + 2] == 0x05 && tail_buf[i + 3] == 0x06) {
			eocd_pos = i;
			break;
		}
	}

	if (eocd_pos < 0) {
		NZPData_Log("usermaps: %s no tiene cabecera EOCD ZIP valida", zip_path);
		free(tail_buf);
		fclose(fp);
		return 0;
	}

	cd_entries = NZPData_ReadLE16(tail_buf + eocd_pos + 10);
	cd_offset  = NZPData_ReadLE32(tail_buf + eocd_pos + 16);
	free(tail_buf);

	cur_cd = (long)cd_offset;
	for (i = 0; i < (long)cd_entries; ++i) {
		unsigned char cd_hdr[46];
		uint16_t method, name_len, extra_len, comment_len;
		uint32_t comp_size, local_ofs;
		char entry_name[MAX_OSPATH];
		char rel_path[MAX_OSPATH];
		char dest_path[MAX_OSPATH * 2];
		unsigned char loc_hdr[30];
		uint16_t loc_name_len, loc_extra_len;

		if (fseek(fp, cur_cd, SEEK_SET) != 0 ||
		    fread(cd_hdr, 1, sizeof(cd_hdr), fp) != sizeof(cd_hdr))
			break;

		if (cd_hdr[0] != 0x50 || cd_hdr[1] != 0x4b ||
		    cd_hdr[2] != 0x01 || cd_hdr[3] != 0x02)
			break;

		method      = NZPData_ReadLE16(cd_hdr + 10);
		comp_size   = NZPData_ReadLE32(cd_hdr + 20);
		name_len    = NZPData_ReadLE16(cd_hdr + 28);
		extra_len   = NZPData_ReadLE16(cd_hdr + 30);
		comment_len = NZPData_ReadLE16(cd_hdr + 32);
		local_ofs   = NZPData_ReadLE32(cd_hdr + 42);

		cur_cd += 46L + (long)name_len + (long)extra_len + (long)comment_len;

		if (name_len == 0 || name_len >= sizeof(entry_name))
			continue;

		if (fread(entry_name, 1, name_len, fp) != name_len)
			break;
		entry_name[name_len] = '\0';

		if (!NZPData_MapUserEntryToGameRelPath(entry_name, rel_path, sizeof(rel_path)))
			continue;

		if (fseek(fp, (long)local_ofs, SEEK_SET) != 0 ||
		    fread(loc_hdr, 1, sizeof(loc_hdr), fp) != sizeof(loc_hdr))
			continue;

		if (loc_hdr[0] != 0x50 || loc_hdr[1] != 0x4b ||
		    loc_hdr[2] != 0x03 || loc_hdr[3] != 0x04)
			continue;

		loc_name_len  = NZPData_ReadLE16(loc_hdr + 26);
		loc_extra_len = NZPData_ReadLE16(loc_hdr + 28);
		if (fseek(fp, (long)loc_name_len + (long)loc_extra_len, SEEK_CUR) != 0)
			continue;

		snprintf(dest_path, sizeof(dest_path), "%s/%s", game_root, rel_path);
		if (!NZPData_MakeDirs(dest_path))
			continue;

		if (method == 0) {
			/* Stored (sin compresion) */
			FILE *out = fopen(dest_path, "wb");
			uint32_t rem = comp_size;
			unsigned char inbuf[16384];
			int ok_entry = 1;

			if (!out)
				continue;
			while (rem > 0) {
				size_t chunk = rem < sizeof(inbuf) ? (size_t)rem : sizeof(inbuf);
				if (fread(inbuf, 1, chunk, fp) != chunk ||
				    fwrite(inbuf, 1, chunk, out) != chunk) {
					ok_entry = 0;
					break;
				}
				rem -= (uint32_t)chunk;
			}
			fclose(out);
			if (ok_entry)
				extracted++;
		} else if (method == 8) {
			/* Deflate (zlib raw -MAX_WBITS) */
			FILE *out = fopen(dest_path, "wb");
			z_stream strm;
			unsigned char inbuf[16384];
			unsigned char outbuf[16384];
			uint32_t rem = comp_size;
			int ok_entry = 1;

			if (!out)
				continue;

			memset(&strm, 0, sizeof(strm));
			if (inflateInit2(&strm, -MAX_WBITS) != Z_OK) {
				fclose(out);
				continue;
			}

			while (rem > 0) {
				size_t chunk = rem < sizeof(inbuf) ? (size_t)rem : sizeof(inbuf);
				int ret;
				if (fread(inbuf, 1, chunk, fp) != chunk) {
					ok_entry = 0;
					break;
				}
				rem -= (uint32_t)chunk;
				strm.next_in = inbuf;
				strm.avail_in = (unsigned int)chunk;

				do {
					size_t have;
					strm.next_out = outbuf;
					strm.avail_out = sizeof(outbuf);
					ret = inflate(&strm, Z_NO_FLUSH);
					if (ret != Z_OK && ret != Z_STREAM_END) {
						ok_entry = 0;
						break;
					}
					have = sizeof(outbuf) - strm.avail_out;
					if (have > 0 && fwrite(outbuf, 1, have, out) != have) {
						ok_entry = 0;
						break;
					}
				} while (strm.avail_out == 0);

				if (!ok_entry || ret == Z_STREAM_END)
					break;
			}

			inflateEnd(&strm);
			fclose(out);
			if (ok_entry)
				extracted++;
		} else {
			NZPData_Log("usermaps: metodo ZIP %u no soportado en %s (%s)",
				(unsigned int)method, zip_path, entry_name);
		}
	}

	fclose(fp);
	if (extracted > 0) {
		NZPData_WritePkgStamp(stamp_path, &st);
		NZPData_Log("usermaps: extraidos %d ficheros desde paquete %s",
			extracted, zip_path);
	}
	return extracted;
}

/*
================
NZPData_ExtractPakFile

Extrae un archivo `.pak` de Quake (cabecera "PACK") colocado en `usermaps/`.
================
*/
static int NZPData_ExtractPakFile(const char *pak_path, const char *game_root)
{
	struct stat st;
	char stamp_path[MAX_OSPATH * 2];
	FILE *fp;
	unsigned char hdr[12];
	uint32_t dirofs, dirlen, num_entries, i;
	int extracted = 0;

	if (stat(pak_path, &st) != 0 || !S_ISREG(st.st_mode) || st.st_size < 12)
		return 0;

	NZPData_BuildStampPath(game_root, pak_path, stamp_path, sizeof(stamp_path));
	if (NZPData_CheckPkgStamp(stamp_path, &st))
		return 0;

	fp = fopen(pak_path, "rb");
	if (!fp)
		return 0;

	if (fread(hdr, 1, 12, fp) != 12 || memcmp(hdr, "PACK", 4) != 0) {
		fclose(fp);
		return 0;
	}

	dirofs = NZPData_ReadLE32(hdr + 4);
	dirlen = NZPData_ReadLE32(hdr + 8);
	num_entries = dirlen / 64U;

	for (i = 0; i < num_entries; ++i) {
		unsigned char entry[64];
		char entry_name[57];
		char rel_path[MAX_OSPATH];
		char dest_path[MAX_OSPATH * 2];
		uint32_t fpos, flen, rem;
		FILE *out;
		unsigned char buf[16384];
		int ok_entry = 1;

		if (fseek(fp, (long)(dirofs + i * 64U), SEEK_SET) != 0 ||
		    fread(entry, 1, 64, fp) != 64)
			break;

		memcpy(entry_name, entry, 56);
		entry_name[56] = '\0';
		fpos = NZPData_ReadLE32(entry + 56);
		flen = NZPData_ReadLE32(entry + 60);

		if (!NZPData_MapUserEntryToGameRelPath(entry_name, rel_path, sizeof(rel_path)))
			continue;

		if (fseek(fp, (long)fpos, SEEK_SET) != 0)
			continue;

		snprintf(dest_path, sizeof(dest_path), "%s/%s", game_root, rel_path);
		if (!NZPData_MakeDirs(dest_path))
			continue;

		out = fopen(dest_path, "wb");
		if (!out)
			continue;

		rem = flen;
		while (rem > 0) {
			size_t chunk = rem < sizeof(buf) ? (size_t)rem : sizeof(buf);
			if (fread(buf, 1, chunk, fp) != chunk ||
			    fwrite(buf, 1, chunk, out) != chunk) {
				ok_entry = 0;
				break;
			}
			rem -= (uint32_t)chunk;
		}
		fclose(out);
		if (ok_entry)
			extracted++;
	}

	fclose(fp);
	if (extracted > 0) {
		NZPData_WritePkgStamp(stamp_path, &st);
		NZPData_Log("usermaps: extraidos %d ficheros desde PAK %s",
			extracted, pak_path);
	}
	return extracted;
}

/*
================
NZPData_SyncUserDirRecursive

Recorre recursivamente un directorio de usermaps copiando ficheros sueltos y
descomprimiendo paquetes `.pk3`, `.zip` y `.pak`.
================
*/
static int NZPData_SyncUserDirRecursive(const char *root_dir,
	const char *rel_prefix, const char *game_root, int depth)
{
	char current_dir[MAX_OSPATH * 2];
	DIR *dir;
	struct dirent *dp;
	int synced = 0;

	if (depth > 8)
		return 0;

	if (rel_prefix[0] != '\0')
		snprintf(current_dir, sizeof(current_dir), "%s/%s", root_dir, rel_prefix);
	else
		snprintf(current_dir, sizeof(current_dir), "%s", root_dir);

	dir = opendir(current_dir);
	if (!dir)
		return 0;

	while ((dp = readdir(dir)) != NULL) {
		char child_rel[MAX_OSPATH * 2];
		char full_path[MAX_OSPATH * 2];
		struct stat st;

		if (dp->d_name[0] == '\0' || dp->d_name[0] == '.')
			continue;

		if (rel_prefix[0] != '\0')
			snprintf(child_rel, sizeof(child_rel), "%s/%s", rel_prefix, dp->d_name);
		else
			snprintf(child_rel, sizeof(child_rel), "%s", dp->d_name);

		snprintf(full_path, sizeof(full_path), "%s/%s", root_dir, child_rel);
		if (stat(full_path, &st) != 0)
			continue;

		if (S_ISDIR(st.st_mode)) {
			synced += NZPData_SyncUserDirRecursive(root_dir, child_rel, game_root, depth + 1);
		} else if (S_ISREG(st.st_mode)) {
			const char *ext = strrchr(dp->d_name, '.');
			if (ext && (!strcasecmp(ext, ".pk3") || !strcasecmp(ext, ".zip"))) {
				synced += NZPData_ExtractZipOrPk3(full_path, game_root);
			} else if (ext && !strcasecmp(ext, ".pak")) {
				synced += NZPData_ExtractPakFile(full_path, game_root);
			} else {
				char mapped_rel[MAX_OSPATH];
				char dest_path[MAX_OSPATH * 2];
				if (NZPData_MapUserEntryToGameRelPath(child_rel, mapped_rel, sizeof(mapped_rel))) {
					snprintf(dest_path, sizeof(dest_path), "%s/%s", game_root, mapped_rel);
					if (NZPData_CopyDiskFileIfChanged(full_path, dest_path)) {
						NZPData_Log("usermaps: copiado %s -> %s", full_path, mapped_rel);
						synced++;
					}
				}
			}
		}
	}

	closedir(dir);
	return synced;
}

/*
================
NZPData_InitPrimaryUserMapsDir

Prepara la carpeta publica de la app en almacenamiento externo:
  /sdcard/Android/data/com.nzpteam.nzportable/files/usermaps/
que no necesita permisos especiales en ninguna version de Android ni en Quest 3.
================
*/
static void NZPData_InitPrimaryUserMapsDir(const char *external_files_dir)
{
	static const char *const kSubDirs[] = {
		"maps",
		"gfx/menu/custom",
		"gfx/lscreen",
		"gfx/env",
		"models",
		"sounds",
		"tracks"
	};
	char readme_path[MAX_OSPATH * 2];
	size_t i;

	if (!external_files_dir || external_files_dir[0] == '\0')
		return;

	for (i = 0; i < sizeof(kSubDirs) / sizeof(kSubDirs[0]); ++i) {
		char dummy_file[MAX_OSPATH * 2];
		snprintf(dummy_file, sizeof(dummy_file), "%s/usermaps/%s/.keep",
			external_files_dir, kSubDirs[i]);
		NZPData_MakeDirs(dummy_file);
	}

	snprintf(readme_path, sizeof(readme_path), "%s/usermaps/LEEME_USERMAPS.txt",
		external_files_dir);
	if (!NZPData_FileExists(readme_path)) {
		FILE *f = fopen(readme_path, "wb");
		if (f) {
			fprintf(f,
				"=== NZ:P Android (zurdo) - Carpeta de Usermaps ===\n"
				"\n"
				"Coloca aqui tus mapas custom para jugar sin recompilar el APK.\n"
				"Formatos admitidos directamente en esta carpeta (usermaps/):\n"
				"  1. Paquetes .pk3 o .zip (ej: town_update.pk3, busdepot.zip)\n"
				"  2. Archivos sueltos: <mapa>.bsp, <mapa>.way, <mapa>.nsz, <mapa>.txt\n"
				"     y opcionalmente <mapa>.png para su miniatura en el menu.\n"
				"  3. Carpetas desglosadas: maps/, gfx/, models/, sounds/, tracks/\n"
				"\n"
				"Ejemplo por ADB (PC -> movil o Meta Quest 3):\n"
				"  adb push mi_mapa.pk3 /sdcard/Android/data/com.nzpteam.nzportable/files/usermaps/\n"
				"\n"
				"Los mapas apareceran al principio de SOLO -> USER MAPS.\n");
			fclose(f);
		}
	}
}

/*
================
NZPData_SyncUserMaps

Sincroniza todas las carpetas de usermaps conocidas hacia `<basedir>/nzp`.
Si `basedir` es NULL, utiliza el ultimo `basedir` resuelto en el arranque.
Devuelve el numero de ficheros nuevos o actualizados.
================
*/
int NZPData_SyncUserMaps(const char *basedir)
{
	const char *target_base = (basedir && basedir[0] != '\0') ? basedir : s_resolved_basedir;
	char game_root[MAX_OSPATH * 2];
	const char *external = NULL;
	int total_synced = 0;
	size_t i;
	const char *shared_candidates[] = {
		"/sdcard/NZP/usermaps",
		"/sdcard/NZP/maps",
		"/storage/emulated/0/NZP/usermaps",
		"/storage/emulated/0/NZP/maps",
		"/sdcard/Download/NZP/usermaps",
		"/storage/emulated/0/Download/NZP/usermaps"
	};

	if (!target_base || target_base[0] == '\0')
		return 0;

	snprintf(game_root, sizeof(game_root), "%s/%s", target_base, NZP_ASSET_ROOT);

#ifdef __ANDROID__
	external = SDL_AndroidGetExternalStoragePath();
#else
	external = getenv("NZP_EXTERNAL_FILES_DIR");
#endif

	if (external && external[0] != '\0') {
		char ext_usermaps[MAX_OSPATH * 2];
		char ext_maps[MAX_OSPATH * 2];

		NZPData_InitPrimaryUserMapsDir(external);

		snprintf(ext_usermaps, sizeof(ext_usermaps), "%s/usermaps", external);
		if (NZPData_DirExists(ext_usermaps))
			total_synced += NZPData_SyncUserDirRecursive(ext_usermaps, "", game_root, 0);

		snprintf(ext_maps, sizeof(ext_maps), "%s/maps", external);
		if (NZPData_DirExists(ext_maps))
			total_synced += NZPData_SyncUserDirRecursive(ext_maps, "", game_root, 0);
	}

	for (i = 0; i < sizeof(shared_candidates) / sizeof(shared_candidates[0]); ++i) {
		if (NZPData_DirExists(shared_candidates[i]))
			total_synced += NZPData_SyncUserDirRecursive(shared_candidates[i], "", game_root, 0);
	}

	if (total_synced > 0)
		NZPData_Log("usermaps: %d ficheros sincronizados en %s", total_synced, game_root);

	return total_synced;
}

#ifdef __ANDROID__
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
NZPData_ReadFilelistSignature

Calcula la firma `<len>:<fnv1a>` de `<asset_root>/filelist.txt` para detectar
actualizaciones del APK (nuevos mapas o assets empaquetados).
================
*/
static int NZPData_ReadFilelistSignature(AAssetManager *manager,
	const char *asset_root, char *sig_out, size_t sig_size)
{
	char list_path[MAX_QPATH];
	AAsset *list_asset;
	size_t total;
	const void *buf;
	uint64_t hash;

	snprintf(list_path, sizeof(list_path), "%s/filelist.txt", asset_root);
	list_asset = AAssetManager_open(manager, list_path, AASSET_MODE_BUFFER);
	if (!list_asset)
		return 0;

	total = (size_t)AAsset_getLength(list_asset);
	buf = AAsset_getBuffer(list_asset);
	if (!buf || total == 0) {
		AAsset_close(list_asset);
		return 0;
	}

	hash = NZPData_HashFNV1a(buf, total);
	AAsset_close(list_asset);

	snprintf(sig_out, sig_size, "%zu:%016llx",
		total, (unsigned long long)hash);
	return 1;
}

static int NZPData_MarkerMatches(const char *marker_path, const char *expected_sig)
{
	char buf[128];
	FILE *f = fopen(marker_path, "rb");
	if (!f)
		return 0;
	if (!fgets(buf, sizeof(buf), f)) {
		fclose(f);
		return 0;
	}
	fclose(f);
	buf[strcspn(buf, "\r\n")] = '\0';
	return strcmp(buf, expected_sig) == 0;
}

static void NZPData_WriteMarker(const char *marker_path, const char *sig)
{
	FILE *f = fopen(marker_path, "wb");
	if (!f)
		return;
	fprintf(f, "%s\n", sig ? sig : "ok");
	fclose(f);
}

/*
================
NZPData_ExtractViaFilelist

Extrae el arbol usando el indice <asset_root>/filelist.txt, que genera
scripts/gen_asset_filelist.ps1 antes de compilar.
Si `preserve_user_cfg` es 1 y `config.cfg` ya existe en disco, no lo pisa.
================
*/
static int NZPData_ExtractViaFilelist(AAssetManager *manager,
	const char *asset_root, const char *dest_root, int preserve_user_cfg,
	int *files_out, size_t *bytes_out)
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

			if (preserve_user_cfg && !strcasecmp(rel_path, "config.cfg") &&
			    NZPData_FileExists(dest_path)) {
				files++;
			} else if (NZPData_CopyAsset(manager, asset_path, dest_path, &written)) {
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
y, si no existe, cae al recorrido con AAssetDir.
================
*/
static int NZPData_ExtractTree(AAssetManager *manager,
	const char *asset_root, const char *dest_root, int preserve_user_cfg)
{
	int files = 0;
	size_t bytes = 0;
	int ok;

	ok = NZPData_ExtractViaFilelist(manager, asset_root, dest_root,
		preserve_user_cfg, &files, &bytes);
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

static AAssetManager *NZPData_GetAssetManager(void)
{
	JNIEnv *env = (JNIEnv *)SDL_AndroidGetJNIEnv();
	jobject activity = (jobject)SDL_AndroidGetActivity();
	jclass activity_class;
	jmethodID get_assets;
	jobject asset_manager_obj;

	if (!env || !activity)
		return NULL;

	activity_class = (*env)->GetObjectClass(env, activity);
	get_assets = (*env)->GetMethodID(env, activity_class, "getAssets",
		"()Landroid/content/res/AssetManager;");
	if (!get_assets)
		return NULL;

	asset_manager_obj = (*env)->CallObjectMethod(env, activity, get_assets);
	if (!asset_manager_obj)
		return NULL;

	return AAssetManager_fromJava(env, asset_manager_obj);
}

/*
================
NZPData_FindBasedir

Devuelve la ruta de basedir a usar, o NULL si no hay datos disponibles.
Sincroniza ademas la carpeta de usermaps antes de devolver el directorio.
================
*/
const char *NZPData_FindBasedir(void)
{
	static char basedir[MAX_OSPATH];
	const char *external;
	char marker[MAX_OSPATH];

	/* ---- 1. Almacenamiento externo completo ya preparado por el usuario ---- */
	external = SDL_AndroidGetExternalStoragePath();
	if (external && NZPData_LooksUsable(external)) {
		snprintf(s_resolved_basedir, sizeof(s_resolved_basedir), "%s", external);
		NZPData_SyncUserMaps(external);
		NZPData_Log("basedir = almacenamiento externo: %s", external);
		return external;
	}

	/* ---- 2. Extraccion/actualizacion de assets a almacenamiento interno ---- */
	{
		const char *internal = SDL_AndroidGetInternalStoragePath();
		AAssetManager *manager;
		char extract_root[MAX_OSPATH];
		char data_root[MAX_OSPATH];
		char expected_sig[128] = "v1";
		int had_previous_install;

		if (!internal)
			return NULL;

		snprintf(marker, sizeof(marker), "%s/%s/%s", internal, NZP_EXTRACT_DIR,
			NZP_MARKER);
		snprintf(basedir, sizeof(basedir), "%s/%s", internal, NZP_EXTRACT_DIR);
		snprintf(extract_root, sizeof(extract_root), "base/%s", NZP_ASSET_ROOT);
		snprintf(data_root, sizeof(data_root), "%s/%s", basedir, NZP_ASSET_ROOT);

		manager = NZPData_GetAssetManager();
		if (manager) {
			NZPData_ReadFilelistSignature(manager, extract_root,
				expected_sig, sizeof(expected_sig));
		}

		had_previous_install = NZPData_FileExists(marker) && NZPData_LooksUsable(basedir);
		if (had_previous_install && NZPData_MarkerMatches(marker, expected_sig)) {
			NZPData_Log("basedir = interno ya extraido y al dia (%s): %s",
				expected_sig, basedir);
			snprintf(s_resolved_basedir, sizeof(s_resolved_basedir), "%s", basedir);
			NZPData_SyncUserMaps(basedir);
			return basedir;
		}

		if (!manager) {
			if (had_previous_install) {
				snprintf(s_resolved_basedir, sizeof(s_resolved_basedir), "%s", basedir);
				NZPData_SyncUserMaps(basedir);
				return basedir;
			}
			NZPData_Log("no pude obtener el AssetManager");
			return NULL;
		}

		NZPData_Log("extrayendo/actualizando assets/%s -> %s (sig=%s)",
			extract_root, data_root, expected_sig);

		if (!NZPData_ExtractTree(manager, extract_root, data_root, had_previous_install))
			return NULL;

		NZPData_WriteMarker(marker, expected_sig);
		snprintf(s_resolved_basedir, sizeof(s_resolved_basedir), "%s", basedir);

		/* ---- 3. Superponer usermaps del movil sin recompilar APK ---- */
		NZPData_SyncUserMaps(basedir);

		return basedir;
	}
}
#endif /* __ANDROID__ */
