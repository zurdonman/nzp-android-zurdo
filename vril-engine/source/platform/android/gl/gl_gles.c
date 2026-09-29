/*
================================================================================
gl_gles.c -- immediate mode emulation for OpenGL ES 1.1 (Android port)
================================================================================

See gl_gles.h for the rationale. This file implements the subset of the
OpenGL 1.x immediate mode API used by the GLQUAKE renderer, on top of the
GLES 1.1 vertex-array API.

What the engine emits between glBegin() and glEnd() is always one of:

    glTexCoord2f / glTexCoord2fv      (per vertex, before the vertex)
    glVertex2f / glVertex3f / glVertex3fv

Colour is set *before* glBegin() at every site in this engine, so a single
colour per batch is enough -- it is stored per vertex all the same, so that a
future call site that tints mid-primitive keeps working.

The batcher turns

    glBegin(GL_QUADS) + 4 vertices + glEnd()

into

    glEnableClientState(GL_VERTEX_ARRAY / _TEXTURE_COORD_ARRAY / _COLOR_ARRAY)
    glVertexPointer / glTexCoordPointer / glColorPointer   (interleaved array)
    glDrawArrays(GL_TRIANGLE_FAN, 0, 4)

Mode translation
----------------
    GL_QUADS   -> GL_TRIANGLE_FAN   (every engine site emits exactly 4
                                     vertices, and a 4-vertex fan is the same
                                     quad with the same winding)
    GL_POLYGON -> GL_TRIANGLE_FAN   (engine polygons are convex water/sky
                                     polygons, which is exactly the contract
                                     of GL_TRIANGLE_FAN)
    GL_TRIANGLES / GL_TRIANGLE_FAN / GL_TRIANGLE_STRIP pass through untouched.

Memory: one static buffer that grows on demand. It never touches the engine's
Hunk/zone allocator.
================================================================================
*/

#include <GLES/gl.h>
#include <stdlib.h>

/* This file must call the *real* GLES entry points, not its own shims. */
#include "gl_gles.h"
#undef glColor4f
#undef glNormal3f

#define GLES_BATCH_INITIAL_VERTS 2048

typedef struct {
	GLfloat xyz[3];
	GLfloat uv[2];
	GLfloat rgba[4];
} gles_vert_t;

static gles_vert_t *s_verts;
static int s_capacity;
static int s_count;
static GLenum s_mode;
static int s_in_begin;

static GLfloat s_pending_uv[2] = { 0.0f, 0.0f };
static GLfloat s_pending_color[4] = { 1.0f, 1.0f, 1.0f, 1.0f };

static int gles_batch_grow(int needed)
{
	int cap;
	gles_vert_t *grown;

	if (needed <= s_capacity)
		return 1;

	cap = s_capacity ? s_capacity : GLES_BATCH_INITIAL_VERTS;
	while (cap < needed)
		cap *= 2;

	grown = (gles_vert_t *)realloc(s_verts, (size_t)cap * sizeof(*grown));
	if (!grown)
		return 0;

	s_verts = grown;
	s_capacity = cap;
	return 1;
}

static void gles_batch_flush(void)
{
	GLenum mode;
	GLsizei stride;
	const GLubyte *base;

	if (s_count < 3) {
		s_count = 0;
		return;
	}

	mode = (s_mode == GL_POLYGON || s_mode == GL_QUADS) ? GL_TRIANGLE_FAN : s_mode;

	/* One interleaved array: every attribute uses the same stride. */
	stride = (GLsizei)sizeof(gles_vert_t);
	base = (const GLubyte *)s_verts;

	glEnableClientState(GL_VERTEX_ARRAY);
	glVertexPointer(3, GL_FLOAT, stride, base);

	glEnableClientState(GL_TEXTURE_COORD_ARRAY);
	glTexCoordPointer(2, GL_FLOAT, stride, base + sizeof(GLfloat) * 3);

	glEnableClientState(GL_COLOR_ARRAY);
	glColorPointer(4, GL_FLOAT, stride, base + sizeof(GLfloat) * 5);

	glDrawArrays(mode, 0, s_count);

	glDisableClientState(GL_COLOR_ARRAY);
	glDisableClientState(GL_TEXTURE_COORD_ARRAY);
	glDisableClientState(GL_VERTEX_ARRAY);

	s_count = 0;
}

void glBegin(GLenum mode)
{
	s_mode = mode;
	s_count = 0;
	s_in_begin = 1;
}

void glEnd(void)
{
	if (!s_in_begin)
		return;

	s_in_begin = 0;
	gles_batch_flush();
}

void glTexCoord2f(GLfloat s, GLfloat t)
{
	s_pending_uv[0] = s;
	s_pending_uv[1] = t;
}

void glTexCoord2fv(const GLfloat *v)
{
	s_pending_uv[0] = v[0];
	s_pending_uv[1] = v[1];
}

void glColor3f(GLfloat r, GLfloat g, GLfloat b)
{
	s_pending_color[0] = r;
	s_pending_color[1] = g;
	s_pending_color[2] = b;
	s_pending_color[3] = 1.0f;
	glColor4f(r, g, b, 1.0f);
}

void glColor4fv(const GLfloat *v)
{
	s_pending_color[0] = v[0];
	s_pending_color[1] = v[1];
	s_pending_color[2] = v[2];
	s_pending_color[3] = v[3];
	glColor4f(v[0], v[1], v[2], v[3]);
}

/* GLES *does* have glColor4f, but the engine's colour state must also reach
 * the batcher, so the shim header redirects glColor4f here. */
void nz_glColor4f(GLfloat r, GLfloat g, GLfloat b, GLfloat a)
{
	s_pending_color[0] = r;
	s_pending_color[1] = g;
	s_pending_color[2] = b;
	s_pending_color[3] = a;
	glColor4f(r, g, b, a);
}

void glNormal3f_shim(GLfloat nx, GLfloat ny, GLfloat nz)
{
	(void)nx;
	(void)ny;
	(void)nz;
	/* GLES has glNormal3f, but this engine never enables client-side
	 * lighting in the GLQUAKE path, so normals are dropped instead of
	 * forcing a fourth client-state array for no visual benefit. */
}

void glVertex2f(GLfloat x, GLfloat y)
{
	glVertex3f(x, y, 0.0f);
}

void glVertex3fv(const GLfloat *v)
{
	glVertex3f(v[0], v[1], v[2]);
}

void glVertex3f(GLfloat x, GLfloat y, GLfloat z)
{
	gles_vert_t *v;

	if (!s_in_begin)
		return;

	if (!gles_batch_grow(s_count + 1)) {
		/* Out of memory: flush what we already have instead of dropping it. */
		gles_batch_flush();
		if (!gles_batch_grow(s_count + 1))
			return;
	}

	v = &s_verts[s_count++];
	v->xyz[0] = x;
	v->xyz[1] = y;
	v->xyz[2] = z;
	v->uv[0] = s_pending_uv[0];
	v->uv[1] = s_pending_uv[1];
	v->rgba[0] = s_pending_color[0];
	v->rgba[1] = s_pending_color[1];
	v->rgba[2] = s_pending_color[2];
	v->rgba[3] = s_pending_color[3];
}
