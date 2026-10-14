package org.libsdl.app;

import android.content.ContentValues;
import android.content.Context;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;

/**
 * NZPLog -- volcado del registro del motor a un fichero legible por el usuario.
 *
 * El motor escribe TODO su texto (Con_Printf, Sys_Printf, banners de arranque y
 * el error final de Sys_Error) en stdout/stderr; sys_sdl.c redirige esos pipes a
 * logcat y, desde aqui, tambien a un fichero de texto.
 *
 * Ruta de destino, por orden de preferencia:
 *
 *   1) Android 10 (API 29) y superior: MediaStore -> Download/NZP_Logs/.
 *      NO necesita ningun permiso y el fichero se ve en la app "Files"/Descargas.
 *   2) Android 9 e inferior: ruta publica directa
 *      /sdcard/Download/NZP_Logs/ (usa WRITE_EXTERNAL_STORAGE, maxSdk 29).
 *   3) Ultimo recurso: la carpeta externa de la app
 *      (getExternalFilesDir), que siempre se puede escribir.
 *
 * Nada de Android/data: en versiones recientes de Android el usuario no puede
 * leer esa carpeta, asi que solo se usa como ultimo recurso.
 *
 * Llamado por JNI desde vril-engine/source/platform/android/sys_android_log.c.
 */
public class NZPLog {

    private static final String SUBDIR = "NZP_Logs";

    private static OutputStream sStream = null;
    private static String sPath = "";

    /**
     * Abre (o reabre) el fichero de log.
     *
     * @param name nombre del fichero, p. ej. "nzp-log-20261008-142000.txt"
     * @return ruta legible por el usuario, o "" si no se pudo abrir
     */
    public static synchronized String open(String name) {
        close();
        sPath = "";

        Context ctx = SDL.getContext();
        if (ctx == null) {
            return "";
        }

        // ---- 1) Android 10+: MediaStore en Descargas (sin permisos) ---------
        if (Build.VERSION.SDK_INT >= 29) {
            try {
                ContentValues cv = new ContentValues();
                cv.put(MediaStore.Downloads.DISPLAY_NAME, name);
                cv.put(MediaStore.Downloads.MIME_TYPE, "text/plain");
                cv.put(MediaStore.Downloads.RELATIVE_PATH,
                        Environment.DIRECTORY_DOWNLOADS + "/" + SUBDIR);
                Uri uri = ctx.getContentResolver()
                        .insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv);
                if (uri != null) {
                    OutputStream os = ctx.getContentResolver().openOutputStream(uri);
                    if (os != null) {
                        sStream = os;
                        sPath = Environment.DIRECTORY_DOWNLOADS + "/" + SUBDIR + "/" + name;
                        return sPath;
                    }
                }
            } catch (Throwable ignored) {
                // Sin MediaStore o sin almacenamiento: se prueba la via clasica.
            }
        }

        // ---- 2) Android 9-: ruta publica de Descargas -----------------------
        try {
            File dir = new File(
                    Environment.getExternalStoragePublicDirectory(
                            Environment.DIRECTORY_DOWNLOADS), SUBDIR);
            dir.mkdirs();
            File f = new File(dir, name);
            FileOutputStream os = new FileOutputStream(f, false);
            sStream = os;
            sPath = f.getAbsolutePath();
            return sPath;
        } catch (Throwable ignored) {
            // Sin permiso de escritura: se usa la carpeta de la app.
        }

        // ---- 3) Ultimo recurso: carpeta externa de la app -------------------
        try {
            File dir = new File(ctx.getExternalFilesDir(null), SUBDIR);
            dir.mkdirs();
            File f = new File(dir, name);
            FileOutputStream os = new FileOutputStream(f, false);
            sStream = os;
            sPath = f.getAbsolutePath();
            return sPath;
        } catch (Throwable ignored) {
            return "";
        }
    }

    /** Escribe datos binarios tal cual (el motor ya manda UTF-8/ASCII). */
    public static synchronized void write(byte[] data) {
        if (sStream == null || data == null) {
            return;
        }
        try {
            sStream.write(data);
        } catch (Throwable ignored) {
            // Si el fichero se cierra por error, el log simplemente se pierde.
        }
    }

    /** Fuerza el volcado a disco. IMPORTANTE: si el proceso aborta, lo no
     *  volcado se pierde, asi que el motor llama a flush() a menudo. */
    public static synchronized void flush() {
        if (sStream == null) {
            return;
        }
        try {
            sStream.flush();
        } catch (Throwable ignored) {
        }
    }

    /** Cierra el fichero (llamar en Sys_Error y al salir). */
    public static synchronized void close() {
        if (sStream != null) {
            try {
                sStream.flush();
            } catch (Throwable ignored) {
            }
            try {
                sStream.close();
            } catch (Throwable ignored) {
            }
            sStream = null;
        }
    }

    /** Ruta del fichero abierto ("" si no hay ninguno). */
    public static synchronized String path() {
        return sPath;
    }
}
