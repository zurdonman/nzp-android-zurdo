/*
 * sys_android_log.c -- volcado del registro del motor a Descargas/NZP_Logs.
 *
 * Motivo: en Android, cuando el motor aborta con Sys_Error solo aparece un
 * recuadro en pantalla y el texto se pierde en logcat. El usuario no puede
 * copiarlo ni leerlo sin ordenador, asi que aqui se duplica TODO el texto que
 * pasa por stdout/stderr a un .txt en la carpeta publica de Descargas.
 *
 * Contraparte Java: android-app/app/src/main/java/org/libsdl/app/NZPLog.java
 *
 * Todo el modulo es inofensivo: si algo falla (sin JNI, sin almacenamiento,
 * sin permisos) simplemente no se escribe nada y el juego sigue igual.
 */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#ifdef __ANDROID__
#include <android/log.h>
#include <jni.h>
#include <pthread.h>
#include <SDL.h>
#endif

#include "sys_android_log.h"

#ifdef __ANDROID__

#define NZPLOG_TAG   "nzportable-log"
#define NZPLOG_BUF   32768

static char          s_path[512] = "";
static char          s_buf[NZPLOG_BUF];
static int           s_buflen = 0;
static int           s_ready = 0;
static int           s_in_write = 0;      /* evita recursion si el log imprime */
static pthread_mutex_t s_mutex = PTHREAD_MUTEX_INITIALIZER;

static jclass        s_class = NULL;
static jmethodID     m_open  = NULL;
static jmethodID     m_write = NULL;
static jmethodID     m_flush = NULL;
static jmethodID     m_close = NULL;

/* -------------------------------------------------------------------------- */
/* Enlace con la clase Java (una sola vez).                                    */
/* -------------------------------------------------------------------------- */
static int NZPLog_BindJNI(void)
{
	JNIEnv *env;
	jclass  cls;

	if (s_class)
		return 1;

	env = (JNIEnv *)SDL_AndroidGetJNIEnv();
	if (!env)
		return 0;

	cls = (*env)->FindClass(env, "org/libsdl/app/NZPLog");
	if (!cls) {
		(*env)->ExceptionClear(env);
		__android_log_print(ANDROID_LOG_WARN, NZPLOG_TAG,
			"no se encontro org.libsdl.app.NZPLog");
		return 0;
	}

	s_class = (jclass)(*env)->NewGlobalRef(env, cls);
	(*env)->DeleteLocalRef(env, cls);
	if (!s_class)
		return 0;

	m_open  = (*env)->GetStaticMethodID(env, s_class, "open",
		"(Ljava/lang/String;)Ljava/lang/String;");
	m_write = (*env)->GetStaticMethodID(env, s_class, "write", "([B)V");
	m_flush = (*env)->GetStaticMethodID(env, s_class, "flush", "()V");
	m_close = (*env)->GetStaticMethodID(env, s_class, "close", "()V");

	if (!m_open || !m_write || !m_flush || !m_close) {
		(*env)->ExceptionClear(env);
		__android_log_print(ANDROID_LOG_WARN, NZPLOG_TAG,
			"NZPLog: firmas JNI no encontradas");
		s_class = NULL;
		return 0;
	}
	return 1;
}

/* -------------------------------------------------------------------------- */
/* API publica                                                                */
/* -------------------------------------------------------------------------- */
void NZPLog_Init(void)
{
	JNIEnv  *env;
	jstring  jname;
	jstring  jpath;
	const char *path;
	char     name[128];
	char     stamp[64];
	time_t   t;

	if (s_ready)
		return;

	if (!NZPLog_BindJNI())
		return;

	env = (JNIEnv *)SDL_AndroidGetJNIEnv();
	if (!env)
		return;

	time(&t);
	/* strftime local: suficiente para ordenar ficheros por fecha. */
	{
		struct tm *tmv = localtime(&t);
		if (tmv) {
			strftime(stamp, sizeof(stamp), "%Y-%m-%d %H:%M:%S", tmv);
			snprintf(name, sizeof(name), "nzp-log-%04d%02d%02d-%02d%02d%02d.txt",
				tmv->tm_year + 1900, tmv->tm_mon + 1, tmv->tm_mday,
				tmv->tm_hour, tmv->tm_min, tmv->tm_sec);
		} else {
			snprintf(name, sizeof(name), "nzp-log.txt");
			snprintf(stamp, sizeof(stamp), "(sin fecha)");
		}
	}

	jname = (*env)->NewStringUTF(env, name);
	if (!jname) {
		if ((*env)->ExceptionCheck(env))
			(*env)->ExceptionClear(env);
		return;
	}

	jpath = (jstring)(*env)->CallStaticObjectMethod(env, s_class, m_open, jname);
	(*env)->DeleteLocalRef(env, jname);
	if ((*env)->ExceptionCheck(env))
		(*env)->ExceptionClear(env);

	s_path[0] = '\0';
	if (jpath) {
		path = (*env)->GetStringUTFChars(env, jpath, NULL);
		if (path && path[0] != '\0')
			snprintf(s_path, sizeof(s_path), "%s", path);
		if (path)
			(*env)->ReleaseStringUTFChars(env, jpath, path);
		(*env)->DeleteLocalRef(env, jpath);
	}

	if (s_path[0] == '\0') {
		__android_log_print(ANDROID_LOG_WARN, NZPLOG_TAG,
			"NZPLog.open() no devolvio ruta: no se guardara el log");
		return;
	}

	s_ready = 1;

	__android_log_print(ANDROID_LOG_INFO, NZPLOG_TAG, "log en %s", s_path);

	NZPLog_Printf(
		"================================================\n"
		"  Nazi Zombies: Portable - registro de sesion\n"
		"  Inicio : %s\n"
		"  Fichero: %s\n"
		"================================================\n",
		stamp, s_path);
}

/* Vuelca el buffer a Java. El mutex debe estar YA tomado. */
static void NZPLog_FlushInternal(void)
{
	JNIEnv    *env;
	jbyteArray arr;

	if (!s_ready || s_buflen == 0 || s_in_write)
		return;
	s_in_write = 1;

	env = (JNIEnv *)SDL_AndroidGetJNIEnv();
	if (env && s_class) {
		arr = (*env)->NewByteArray(env, s_buflen);
		if (arr) {
			(*env)->SetByteArrayRegion(env, arr, 0, s_buflen, (const jbyte *)s_buf);
			(*env)->CallStaticVoidMethod(env, s_class, m_write, arr);
			(*env)->DeleteLocalRef(env, arr);
		}
		(*env)->CallStaticVoidMethod(env, s_class, m_flush);
		if ((*env)->ExceptionCheck(env))
			(*env)->ExceptionClear(env);
	}

	s_buflen = 0;
	s_in_write = 0;
}

void NZPLog_Flush(void)
{
	if (!s_ready)
		return;

	pthread_mutex_lock(&s_mutex);
	NZPLog_FlushInternal();
	pthread_mutex_unlock(&s_mutex);
}

void NZPLog_Write(const char *text, int len)
{
	if (!s_ready || !text)
		return;
	if (len < 0)
		len = (int)strlen(text);
	if (len <= 0)
		return;

	pthread_mutex_lock(&s_mutex);

	/* Si no cabe, volcar primero lo acumulado. */
	if (s_buflen + len > (int)sizeof(s_buf))
		NZPLog_FlushInternal();

	/* Trozo mas grande que el buffer: escribirlo directamente. */
	if (len > (int)sizeof(s_buf)) {
		JNIEnv    *env = (JNIEnv *)SDL_AndroidGetJNIEnv();
		jbyteArray arr;
		if (env && s_class) {
			arr = (*env)->NewByteArray(env, len);
			if (arr) {
				(*env)->SetByteArrayRegion(env, arr, 0, len, (const jbyte *)text);
				(*env)->CallStaticVoidMethod(env, s_class, m_write, arr);
				(*env)->DeleteLocalRef(env, arr);
			}
			(*env)->CallStaticVoidMethod(env, s_class, m_flush);
			if ((*env)->ExceptionCheck(env))
				(*env)->ExceptionClear(env);
		}
		pthread_mutex_unlock(&s_mutex);
		return;
	}

	memcpy(s_buf + s_buflen, text, (size_t)len);
	s_buflen += len;

	pthread_mutex_unlock(&s_mutex);

	/* Vaciado inmediato: el motor puede abortar en cualquier momento y lo que
	 * quede sin volcar (justo el final, que es lo importante) se perderia. */
	NZPLog_Flush();
}

void NZPLog_Printf(const char *fmt, ...)
{
	char  tmp[2048];
	va_list ap;

	if (!s_ready)
		return;

	va_start(ap, fmt);
	vsnprintf(tmp, sizeof(tmp), fmt, ap);
	va_end(ap);
	tmp[sizeof(tmp) - 1] = '\0';

	NZPLog_Write(tmp, -1);
}

void NZPLog_Error(const char *text)
{
	if (!s_ready)
		return;

	NZPLog_Write("\n\n================================================\n", -1);
	NZPLog_Write("  ERROR FATAL DEL MOTOR\n", -1);
	NZPLog_Write("================================================\n", -1);
	if (text)
		NZPLog_Write(text, -1);
	NZPLog_Write("\n================================================\n", -1);
	NZPLog_Write("  Fin del registro.\n", -1);

	NZPLog_Flush();
	NZPLog_Close();
}

void NZPLog_Close(void)
{
	JNIEnv *env;

	if (!s_ready)
		return;

	NZPLog_Flush();

	env = (JNIEnv *)SDL_AndroidGetJNIEnv();
	if (env && s_class) {
		(*env)->CallStaticVoidMethod(env, s_class, m_close);
		if ((*env)->ExceptionCheck(env))
			(*env)->ExceptionClear(env);
	}

	s_ready = 0;
	s_buflen = 0;
}

const char *NZPLog_Path(void)
{
	return s_path;
}

#else /* !__ANDROID__ */

/* Fuera de Android el modulo no hace nada (el texto ya va a la consola). */
void NZPLog_Init(void) {}
void NZPLog_Write(const char *text, int len) { (void)text; (void)len; }
void NZPLog_Printf(const char *fmt, ...) { (void)fmt; }
void NZPLog_Flush(void) {}
void NZPLog_Error(const char *text) { (void)text; }
void NZPLog_Close(void) {}
const char *NZPLog_Path(void) { return ""; }

#endif /* __ANDROID__ */
