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
// vr_openxr.h -- capa VR OpenXR (Android). Solo se compila con NZP_VR_OPENXR.
#ifndef __VR_OPENXR_H__
#define __VR_OPENXR_H__

#include "../../../nzportable_def.h"

#ifdef NZP_VR_OPENXR

// Inicializa loader OpenXR + instance + system. Seguro llamar siempre:
// si no hay runtime en el dispositivo, deja VR desactivada y devuelve false.
qboolean VR_Init (void);

// Libera todo (sesion, spaces, swapchains, instance).
void VR_Shutdown (void);

// Ciclo por frame. VR_BeginFrame hace xrWaitFrame/xrBeginSession/xrBeginFrame
// (crea la sesion la primera vez, con el contexto EGL ya actual) y
// VR_EndFrame hace xrEndFrame con la capa de proyeccion estereo. Ambas son
// no-op si VR no esta activa.
qboolean VR_BeginFrame (void);
qboolean VR_EndFrame (void);

// Diagnostico: escribe en vr_log.txt desde fuera del modulo VR (bucle principal).
void VR_DiagLog (const char *fmt, ...);

// Estado para el motor.
qboolean VR_IsAvailable (void);	// hay runtime + HMD
qboolean VR_IsActive (void);	// disponible Y vr_enabled 1

// true tras xrBeginSession exitoso: la ventana 2D ya no se puede presentar
// (el compositor VR tiene el display) y GL_EndRendering debe saltar el swap.
qboolean VR_IsSessionStarted (void);

// true mientras haya un frame VR en curso (entre VR_BeginFrame y VR_EndFrame):
// SCR_UpdateScreen debe dibujar en los ojos (VR_BeginEye/VR_EndEye).
qboolean VR_IsRendering (void);

// Paso 4: delta de rotacion de la cabeza desde el frame anterior (grados,
// convencion del motor: yaw horario, pitch positivo = abajo).
void VR_GetHeadDelta (float *dyaw, float *dpitch);

// Paso 5: stick izquierdo de los mandos (-1..1, y positivo = adelante).
void VR_GetMoveStick (float *x, float *y);

// Paso 5: stick derecho en juego (-1..1) para girar la vista.
void VR_GetLookStick (float *x, float *y);

// Cvars VR (definidos en vr_openxr.c).
extern cvar_t	vr_enabled;
extern cvar_t	vr_debug;
extern cvar_t	vr_turn_speed;

// Resolucion recomendada por ojo (para viewports y FBOs).
void VR_GetEyeSize (int *width, int *height);

// Angulos de fov del ojo actual (radianes positivos left/right/up/down) para
// la proyeccion asimetrica de cada ojo.
void VR_GetEyeFov (float *left, float *right, float *up, float *down);

// Render por ojo (paso 3): VR_BeginEye adquiere la imagen del swapchain del
// ojo, vincula su FBO y fija el viewport; VR_EndEye desvincula y libera.
// VR_BeginEye devuelve false si ese ojo no se puede dibujar.
qboolean VR_BeginEye (int eye);
void VR_EndEye (int eye);

#endif // NZP_VR_OPENXR

#endif // __VR_OPENXR_H__
