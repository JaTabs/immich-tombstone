# immich-tombstone

Evita que [Immich](https://immich.app) re-suba las fotos que borraste definitivamente.

> ⚠️ **Lee los [avisos](#%EF%B8%8F-avisos--descargo-de-responsabilidad) antes de usar esto.** Esta herramienta destruye contenido de ficheros a propósito. El código está generado por IA. Úsalo bajo tu propia responsabilidad.

## El problema

Cuando borras una foto definitivamente en Immich (vaciar papelera, o pasados los 30 días), el servidor elimina **todo registro** del asset — incluido su checksum SHA1. En el siguiente ciclo de backup, la app móvil compara la librería del teléfono con el servidor, no encuentra la foto y **la vuelve a subir**. La borras otra vez, vuelve otra vez. Para siempre, mientras la foto exista en tu teléfono.

Es una petición abierta desde 2023 ([discussion #4282](https://github.com/immich-app/immich/discussions/4282)). No hay solución nativa. En Android existe la opción experimental *Sync Remote Deletions* (que borra la foto del teléfono); en iOS no hay nada.

## Cómo funciona

Un script de Python sin dependencias corre desde cron y mantiene una **blocklist de checksums**:

1. **Captura** — registra el checksum SHA1 de todo asset en la papelera.
2. **Control** — cuando aparece un asset activo con checksum bloqueado (= la app lo re-subió), lo convierte en **lápida**: lo **archiva** (fuera del timeline, pero el checksum sigue en la BD → la app recibe `duplicate` y **deja de re-subirlo para siempre**) y **trunca a 0 bytes** el fichero original y el vídeo transcodificado, conservando las miniaturas (la lápida se sigue viendo en Archivo a calidad preview ocupando ~nada).
3. **Recuperación** — ¿quieres una foto de vuelta? Des-archívala en Immich. El script desbloquea el checksum y borra el cascarón vacío; el móvil re-sube la versión completa en el siguiente backup.
4. Restaurar desde la papelera (antes de purgar) también desbloquea.

Funciona porque Immich calcula el checksum **una sola vez, al subir**, y nunca re-verifica el contenido del fichero.

### Modos

| modo | corta el bucle | libera disco | necesita acceso al filesystem |
|---|---|---|---|
| `tombstone` (defecto) | ✅ | ✅ (quedan miniaturas, ~150 KB/foto) | ✅ corre en el host que guarda los media |
| `archive` | ✅ | ❌ (ficheros enteros, ocultos en Archivo) | ❌ solo API |
| `delete` | ❌ (bucle infinito) | ✅ | ❌ solo API |

## Requisitos e instalación

Immich **v3** (probado en v3.0.1), `python3` y cron. Para modo `tombstone`, ejecútalo en la máquina con acceso directo al `UPLOAD_LOCATION` de Immich. Necesitas una **API key** de tu usuario (Ajustes de cuenta → API Keys).

```bash
git clone https://github.com/JaTabs/immich-tombstone.git
cd immich-tombstone
sudo ./install.sh
```

El instalador pregunta todo, valida el acceso a la API y crea el cron (cada 15 min por defecto). Instalación desatendida con variables `IT_*` — ver cabecera de `install.sh`. Varios usuarios de Immich: una config por usuario, pasando su ruta como primer argumento.

## Limitaciones

- **Ventana ciega**: si borras *y* purgas en menos de un intervalo de cron (15 min), el checksum no se captura y esa foto seguirá re-subiéndose. Vacía la papelera al menos 15 min después de borrar.
- Las re-subidas son visibles en el timeline hasta un intervalo de cron antes de convertirse en lápida (el enfoque es reactivo).
- Los **vídeos** con lápida no se reproducen (solo queda la miniatura). Las fotos se ven a calidad preview.
- Asume el layout estándar de almacenamiento de Immich. Una versión futura de Immich puede requerir ajustes.

## ⚠️ Avisos / descargo de responsabilidad

- **Esta herramienta destruye de forma permanente e irreversible el contenido de los ficheros que convierte en lápida.** Si identificara mal un asset (bug, cambio de API, carrera), ese contenido se **pierde** salvo que siga en un teléfono o backup.
- **El autor no se hace responsable de ninguna pérdida de datos.** Sin garantía de ningún tipo. Úsalo con cautela, bajo tu propia responsabilidad y solo si entiendes lo que hace. Pruébalo primero con una foto desechable.
- **Este código está generado por una IA** (Claude) bajo dirección humana y probado contra una única instancia real (Immich v3.0.1). Revísalo antes de ejecutarlo — son ~250 líneas de Python sencillo.
- Ten **backups** de tu librería y base de datos de Immich.
- Sin afiliación con el proyecto Immich.

## Licencia

[MIT](LICENSE)
