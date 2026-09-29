/*
================================================================================
gl_gles.h -- OpenGL 1.x -> OpenGL ES 1.1 compatibility shim (Android port)
================================================================================

The Android NDK only ships OpenGL ES. GLES 1.1 has NO immediate mode
(glBegin/glEnd/glVertex*), NO double-precision entry points and NO GL_QUADS /
GL_POLYGON enums, but the GLQUAKE renderer (the same sources used by the
sdl / psp2 / ctr backends) relies on them. The engine needs exactly 18 entry
points that GLES does not provide:

    immediate mode : glBegin glEnd glVertex2f glVertex3f glVertex3fv
                     glTexCoord2f glTexCoord2fv glColor3f glColor4fv
    double / misc  : glClearDepth glDepthRange glFrustum glOrtho glFogi
                     glPolygonMode glDrawBuffer glReadBuffer
    GLU            : gluPerspective

...plus the GL_QUADS and GL_POLYGON enums.

This header must be included *after* <GLES/gl.h> and it:

  1. #defines the trivially-renamable calls onto their float GLES twins
     (glOrtho -> glOrthof, ...), or onto no-ops when GLES simply has no
     equivalent (glPolygonMode, glDrawBuffer, glReadBuffer, glFogi).

  2. Defines GL_QUADS / GL_POLYGON / GL_QUAD_STRIP as private pseudo-enums in
     the 0x0007.. range, which GLES leaves unused. They are only ever passed to
     glBegin(), never to a real GLES function, so their values are free; the
     batcher in gl_gles.c maps them onto real GLES primitive modes:
         GL_QUADS   -> GL_TRIANGLE_FAN   (every engine site emits exactly 4
                                          vertices; a 4-vertex fan is the same
                                          quad with the same winding)
         GL_POLYGON -> GL_TRIANGLE_FAN   (engine polygons are convex water/sky
                                          polygons, the exact contract of a fan)

  3. Declares the immediate-mode *batcher* implemented in gl_gles.c. It buffers
     glVertex* / glTexCoord* / glColor* / glNormal* calls and flushes them
     through the vertex-array path (glVertexPointer + glDrawArrays), which is
     how GLES is meant to be driven -- the same approach vitaGL uses for the
     PS Vita backend.
================================================================================
*/

#ifndef __GL_GLES_SHIM_H__
#define __GL_GLES_SHIM_H__

#include <GLES/gl.h>
#include <GLES/glext.h>

/* ------------------------------------------------------------------------- */
/* 0a. Tipos que GLES no define                                              */
/* ------------------------------------------------------------------------- */

/* GLES 1.1 no tiene punto flotante de doble precision: su cabecera no declara
 * GLdouble ni GLclampd. El renderer heredado de Quake los usa (glFrustum,
 * MYgluPerspective, glClearDepth, glDepthRange). Se definen como float; todas
 * las llamadas afectadas pasan antes por un #define que convierte a 'f'. */
#ifndef GLdouble
typedef float GLdouble;
#endif
#ifndef GLclampd
typedef float GLclampd;
#endif

/* ------------------------------------------------------------------------- */
/* 0b. Modos de primitiva que GLES no define                                 */
/* ------------------------------------------------------------------------- */

/* Private pseudo-enums. GLES uses 0x0000..0x0006 for its primitive modes, so
 * everything from 0x0007 up is free. Only glBegin() ever sees these values. */
#ifndef GL_QUADS
#define GL_QUADS      0x0007
#endif
#ifndef GL_QUAD_STRIP
#define GL_QUAD_STRIP 0x0008
#endif
#ifndef GL_POLYGON
#define GL_POLYGON    0x0009
#endif

/* ------------------------------------------------------------------------- */
/* 1. Trivial renames onto existing GLES entry points                         */
/* ------------------------------------------------------------------------- */

/* Double precision -> float precision: GLES only has the 'f' variants. */
#define glClearDepth(d)          glClearDepthf((GLfloat)(d))
#define glDepthRange(n, f)       glDepthRangef((GLfloat)(n), (GLfloat)(f))
#define glFrustum(l, r, b, t, n, f) \
	glFrustumf((GLfloat)(l), (GLfloat)(r), (GLfloat)(b), (GLfloat)(t), \
	           (GLfloat)(n), (GLfloat)(f))
#define glOrtho(l, r, b, t, n, f) \
	glOrthof((GLfloat)(l), (GLfloat)(r), (GLfloat)(b), (GLfloat)(t), \
	         (GLfloat)(n), (GLfloat)(f))

/* Fog: GLES exposes the fixed-point glFogx() for the integer glFogi() cases. */
#define glFogi(pname, param)     glFogx((GLenum)(pname), (GLfixed)(param))

/* The fixed pipeline in GLES has no polygon mode; it is always FILL. */
#define glPolygonMode(face, mode)   ((void)0)

/* GLES has no selectable draw/read buffer (only the default framebuffer). */
#define glDrawBuffer(mode)          ((void)0)
#define glReadBuffer(mode)          ((void)0)

/* GLU: the engine already ships an exact equivalent built on glFrustum
 * (MYgluPerspective in gl_rmain.c), so we reuse it instead of bundling GLU. */
void MYgluPerspective(GLdouble fovy, GLdouble aspect, GLdouble zNear, GLdouble zFar);
#define gluPerspective(fovy, aspect, znear, zfar) \
	MYgluPerspective((fovy), (aspect), (znear), (zfar))

/* ------------------------------------------------------------------------- */
/* 2. Immediate mode emulation (implemented in gl_gles.c)                     */
/* ------------------------------------------------------------------------- */

void glBegin(GLenum mode);
void glEnd(void);

void glVertex2f(GLfloat x, GLfloat y);
void glVertex3f(GLfloat x, GLfloat y, GLfloat z);
void glVertex3fv(const GLfloat *v);
void glTexCoord2f(GLfloat s, GLfloat t);
void glTexCoord2fv(const GLfloat *v);
void glColor3f(GLfloat r, GLfloat g, GLfloat b);
void glColor4fv(const GLfloat *v);
void glNormal3f_shim(GLfloat nx, GLfloat ny, GLfloat nz);

/* GLES *does* have glColor4f, but its state must also reach the batcher, so it
 * is redirected to the wrapper in gl_gles.c. */
void nz_glColor4f(GLfloat r, GLfloat g, GLfloat b, GLfloat a);
#define glColor4f(r, g, b, a)   nz_glColor4f((r), (g), (b), (a))

/* glNormal3f also exists in GLES, but routing it through the batcher keeps all
 * client-state handling in one place. gl_gles.c #undef's this. */
#define glNormal3f(nx, ny, nz)  glNormal3f_shim((nx), (ny), (nz))

#endif /* __GL_GLES_SHIM_H__ */
