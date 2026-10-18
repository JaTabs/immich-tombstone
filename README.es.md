# immich-tombstone

Conserva los checksums de fotos que borraste intencionadamente de [Immich](https://immich.app), para evitar que el siguiente backup del móvil las suba una y otra vez.

[English](README.md) · [Cambios y migración](CHANGELOG.md) · [Seguridad](SECURITY.md)

**Empieza por el modo `archive`.** Conserva los archivos completos. El modo `tombstone` vacía de forma deliberada e irreversible los originales y los transcodificados conocidos, conservando las previsualizaciones. Exige consentimiento explícito y backups independientes de la biblioteca y la base de datos. No hay garantía frente a pérdidas de datos. El proyecto está desarrollado con ayuda de IA y no está afiliado a Immich.

## Funcionamiento

1. Captura checksums de **tus propios assets internos** en papelera. Excluye partners y bibliotecas externas.
2. Cuando un archivo purgado vuelve a subirse, archiva el nuevo asset. Su registro conserva el checksum y el preflight de Immich responde que es un duplicado.
3. Solo en modo `tombstone`, comprueba el SHA1 del original y vuelve a verificar propietario, ruta, estado y visibilidad antes de truncar el original y los transcodificados de nombre exacto. Conserva las miniaturas.
4. Desarchivar solicita deshacer el bloqueo. En modo `archive` el original sigue completo. En modo `tombstone` puede eliminarse el cascarón vacío para permitir la re-subida; el estado sigue la eliminación asíncrona hasta confirmar la recuperación con el preflight. Las Live Photos y los assets editados vacíos requieren recuperación manual para no poner en riesgo sus archivos vinculados.

Es una solución reactiva al problema explicado en [Immich #4282](https://github.com/immich-app/immich/discussions/4282), no un plugin que intercepte subidas ni una garantía de compatibilidad con futuras versiones.

| Modo | Conserva el checksum | Originales | Acceso a archivos |
|---|---|---|---|
| `archive`, por defecto | Mientras exista el registro archivado | Completos | No hace falta |
| `tombstone`, con consentimiento | Mientras exista el registro archivado | Truncados; conserva previews | Necesario |

Se ha retirado `delete`: borrar otra vez el checksum provoca el mismo bucle de re-subidas.

## Requisitos

- Linux, Python 3.10 o posterior y cron para programarlo. No necesita paquetes pip ni acceso al socket Docker.
- Immich v3. Se han ejercitado comprobaciones de lectura de API contra v3.0.1 y v3.3.3; otras versiones y layouts necesitan validación. El instalador rechaza otras versiones mayores.
- Una API key dedicada del **usuario cuyos assets se gestionan**, no de un partner. Necesita lectura del usuario actual, búsqueda de metadata y lectura/actualización de assets. `tombstone` también necesita borrado de assets y preflight de subida. Una clave con todos los permisos de ese usuario simplifica la configuración, pero concede más de lo necesario; usa permisos granulares cuando estén disponibles. No guardes la clave en Git, argumentos de comandos ni informes públicos.
- Una URL base accesible, como `http://127.0.0.1:2283` o tu dominio HTTPS. Los proxies que redirigen al login no sirven: se rechazan las redirecciones para proteger la API key. No se desactiva la verificación TLS.
- Para `tombstone`, acceso directo y de escritura al `UPLOAD_LOCATION` real, con el layout `library/`, `upload/` y `encoded-video/`. No se siguen symlinks ni se truncan hardlinks. La cuenta que lo ejecute necesita permisos sobre los medios; no hace falta root por otros motivos.

## Instalación

```bash
git clone https://github.com/JaTabs/immich-tombstone.git
cd immich-tombstone
sudo ./install.sh
```

El instalador pregunta URL y API key sin mostrar esta última, descubre y fija el propietario, valida la API y una **simulación**, e instala el cron **cada minuto**. Directorio predeterminado: `/opt/immich-tombstone`. Modo predeterminado: `archive`.

Para instalar sin root, usando solo la API:

```bash
IT_DIR="$HOME/.local/share/immich-tombstone" ./install.sh
```

`install.sh` conserva el punto de entrada anterior; la lógica está en `install.py`, que usa solo la biblioteca estándar. No lo instales mediante una tubería de descarga. Lee el código antes de ejecutarlo.

### Instalación desatendida

Proporciona la clave mediante un entorno protegido o un gestor de secretos, no como texto literal que quede en el historial. No actives el trazado de shell durante la instalación.

| Variable | Valor predeterminado o uso |
|---|---|
| `IT_DIR` | `/opt/immich-tombstone` |
| `IT_API_URL` | Obligatoria al instalar de cero; URL base sin `/api` |
| `IT_API_KEY` | Obligatoria al instalar de cero; no se pasa como argumento ni se imprime |
| `IT_MODE` | `archive` o `tombstone`; predeterminado `archive` |
| `IT_ALLOW_TRUNCATE` | `1` autoriza explícitamente el truncado en una instalación desatendida |
| `IT_MEDIA_ROOT` | Ruta de `UPLOAD_LOCATION` en este host; necesaria en `tombstone` |
| `IT_MEDIA_PREFIX` | Prefijo dentro del contenedor/API; predeterminado `/data` |
| `IT_NTFY_URL` | URL opcional del topic de notificaciones; vacía para desactivar |
| `IT_CRON_MINUTES` | `1`; acepta 1–59 y usa el campo de minutos `*/N` de cron |
| `IT_NO_CRON` | `1` conserva el crontab sin modificarlo |

Una configuración existente se reutiliza salvo que sobrescribas valores. **Pausa los jobs y espera a que terminen las ejecuciones antes de actualizar**: el instalador rechaza un cron activo de la misma instalación. Respalda código, configuración y estado en `backups/`, con permisos privados, antes de reemplazar archivos. Si falla la validación no activa un cron nuevo; revisa ese respaldo antes de una reversión manual.

### Configuración manual y simulación

Copia [`config.example.json`](config.example.json) a una carpeta privada, edítalo y fija los permisos:

```bash
chmod 700 /ruta/a/instalacion-privada
chmod 600 /ruta/a/instalacion-privada/config.json
python3 -I immich_tombstone.py /ruta/a/instalacion-privada/config.json --dry-run
```

Sustituye las rutas de ejemplo por las tuyas. El config y su carpeta deben pertenecer a la cuenta que ejecuta el programa; otras personas no deben poder escribir en la carpeta. `--dry-run` consulta la API y comprueba archivos, pero no cambia assets, estado, logs ni medios, y tampoco envía notificaciones. Puede crear un archivo privado de bloqueo.

`owner_id` puede estar vacío al crear una configuración manual: se descubre desde la clave y queda fijado en el primer snapshot correcto. Los cambios de cuenta posteriores se rechazan. El instalador también guarda el ID descubierto en el config privado.

`media_prefix: "/data"` transforma `/data/library/...` de la API en `media_root/library/...` en este host. Se permite un prefijo distinto con comprobación del límite de directorio; no se admiten rutas arbitrarias de bibliotecas externas.

Elige el modo antes de la primera ejecución real. Para `tombstone`, tras revisar una simulación y probar con una foto **desechable**, configura `"mode": "tombstone"` y `"allow_truncate": true`. Si ya hay estado creado en modo `archive`, no cambies ese campo sin más: pausa la instalación anterior, consérvala y utiliza otra carpeta para el modo nuevo. Esa instalación empezará capturando la papelera actual; no convertirá retrospectivamente todos los archivos archivados en lápidas.

**Varios usuarios:** una carpeta privada, config, estado y cron independientes para cada usuario. Dos configs en la misma carpeta no están aislados. No reutilices el estado de otra cuenta ni cambies silenciosamente su modo.

## Uso y recuperación

- Borra a papelera como siempre. Antes de purgar espera una captura correcta, indicada por `OK` en `tombstone.log`. Nombres y checksums se guardan en el estado privado, no se incluyen en notificaciones.
- `flock` evita ejecuciones simultáneas: si una tarda más de un minuto, la siguiente sale sin crear otro escritor.
- La búsqueda reciente usa un solape de cinco minutos. La reconciliación completa es diaria e incluye lápidas antiguas y transcodificados tardíos. Se conserva el barrido de papelera para no depender de timestamps de borrado sin validar.
- `state.json` es un snapshot atómico ligado al propietario y al modo. Las intenciones se guardan antes de mutar la API. HTTP 400 puede significar ausencia **o falta de acceso**: no demuestra un borrado.
- Desarchivar solicita recuperar el original. Si el móvil recibió un duplicado mientras Immich seguía purgando el cascarón, vuelve a iniciar su backup después de completarse la operación pendiente. El script no puede deshacer una respuesta que el móvil ya recibió.
- Los errores quedan en `tombstone.log`; el cron también recoge errores de arranque en el privado `cron-errors.log`. ntfy es opcional y recibe cantidades, no nombres ni checksums. Configura la retención de logs que necesites.

Para instalaciones existentes, sigue la [migración](CHANGELOG.md#upgrading-from-the-original-version). Los registros TSV sin procedencia demostrable quedan en cuarentena y no pueden truncar archivos. Una nueva captura verificada en papelera puede restablecer su bloqueo.

## Límites y seguridad

- Borrar y purgar entre capturas sigue siendo una ventana ciega. Un minuto la reduce; una API caída o una ejecución larga puede alargarla.
- No intercepta subidas. Una re-subida puede aparecer hasta la próxima ejecución correcta.
- Los vídeos truncados no se reproducen y las miniaturas no sustituyen el original. No regeneres todas las miniaturas a partir de originales vacíos.
- API y filesystem no comparten una transacción. Las revalidaciones, SHA1, comprobaciones de enlaces y resolución por descriptores reducen el riesgo; no eliminan las carreras con Immich o con modificaciones concurrentes de una persona administradora.
- Una caída o un error de acceso ambiguo puede exigir intervención manual. Conserva backups del estado y revisa la operación pendiente; no borres el estado para ocultar un error.
- Guarda copias independientes de originales y BD. Esta herramienta no es un backup, no reconstruye originales truncados y no ofrece garantía ni responsabilidad del autor frente a pérdidas de datos.

## Pruebas

```bash
python3 -I -W error::ResourceWarning -m unittest discover -s . -p 'test_*.py'
sh -n install.sh
```

Usan datos sintéticos, archivos temporales, una API local ficticia y un crontab aislado. No se conectan a tu Immich ni requieren credenciales reales. CI las ejecuta con varias versiones de Python. Que pasen no demuestra compatibilidad con cualquier versión futura de Immich.

## Licencia

[MIT](LICENSE).
