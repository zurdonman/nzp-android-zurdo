# Referencias OpenXR y VR

Esta carpeta reúne la documentación de trabajo para mantener y ampliar el soporte VR de NZP en Meta Quest 3.

## Documentos

- [OPENXR_QUEST3.md](OPENXR_QUEST3.md): ciclo OpenXR, acciones Touch, espacios, swapchains y errores habituales.
- [NZP_VR_ARQUITECTURA.md](NZP_VR_ARQUITECTURA.md): dónde vive cada parte de la integración actual de NZP.
- [GTA_SA_VR_PATRONES.md](GTA_SA_VR_PATRONES.md): patrones observados en la referencia local GTA SA VR Quest que conviene adaptar sin copiar implementación.
- [CHECKLIST_VR.md](CHECKLIST_VR.md): pasos de build, instalación, validación y diagnóstico.

## Regla de uso

Las referencias externas sirven para entender interfaces y patrones de diseño. No se copian código, assets ni archivos de terceros al motor NZP. Las rutas locales de referencia son opcionales y pueden no existir en otra máquina.

## Estado de esta copia

- HMD validado: Meta Quest 3.
- Runtime: Meta OpenXR.
- Render: OpenGL ES con swapchains OpenXR.
- Plataforma: Android arm64-v8a.
- Ultima release VR publicada: `nzp-vr-version-1`.
