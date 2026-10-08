/*
 * sys_android_log.h -- registro del motor a un fichero legible en Descargas.
 *
 * En Android el texto del motor va a parar a stdout/stderr, que sys_sdl.c
 * redirige a logcat. Eso esta bien para depurar con el SDK puesto, pero el
 * usuario no puede leer logcat desde el movil, asi que este modulo duplica
 * TODO ese texto a un .txt que se pueda abrir (o compartir) sin ordenador:
 *
 *     Descargas/NZP_Logs/nzp-log-AAAAMMDD-HHMMSS.txt
 *
 * La escritura se hace por JNI contra org.libsdl.app.NZPLog (MediaStore en
 * Android 10+, sin permisos; ver ese fichero para los detalles).
 *
 * Puntos de enganche (todos en sys_sdl.c):
 *   - main()                 -> NZPLog_Init()  justo despues de montar los pipes
 *   - NZP_LogcatThread()     -> NZPLog_Write() por cada linea que pasa por el pipe
 *   - Sys_SystemError()      -> NZPLog_Error() vuelca el error final y cierra
 */
#ifndef __SYS_ANDROID_LOG_H__
#define __SYS_ANDROID_LOG_H__

/* Abre el fichero de log y escribe la cabecera. Seguro llamarla dos veces. */
void NZPLog_Init(void);

/* Anyade texto al log (len < 0 = usar strlen). Vacia a disco en cada llamada:
 * si el proceso aborta, lo que quede en el buffer se perderia. */
void NZPLog_Write(const char *text, int len);

/* Anyade texto con formato y vacia a disco. */
void NZPLog_Printf(const char *fmt, ...);

/* Vuelca a disco lo que quede en el buffer. */
void NZPLog_Flush(void);

/* Marca el final del log con el error fatal y cierra el fichero. */
void NZPLog_Error(const char *text);

/* Cierra el fichero sin escribir nada mas. */
void NZPLog_Close(void);

/* Ruta legible del fichero ("" si no se pudo abrir). No copiar. */
const char *NZPLog_Path(void);

#endif /* __SYS_ANDROID_LOG_H__ */
