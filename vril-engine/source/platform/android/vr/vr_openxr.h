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
// VR_EndFrame hace xrEndFrame. Ambas son no-op si VR no esta activa.
qboolean VR_BeginFrame (void);
qboolean VR_EndFrame (void);

// Estado para el motor.
qboolean VR_IsAvailable (void);	// hay runtime + HMD
qboolean VR_IsActive (void);	// disponible Y vr_enabled 1

// Pose de la cabeza del ultimo frame (grados, convencion del motor).
void VR_GetHeadYawPitch (float *yaw, float *pitch);

#endif // NZP_VR_OPENXR

#endif // __VR_OPENXR_H__
