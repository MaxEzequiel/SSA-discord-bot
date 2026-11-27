# Comandos

Todos son comandos de barra (`/`) y quedan registrados en el servidor normal y en el de staff. Ya no funcionan con `!`.

Los permisos (staff, admin+, puntos) siempre se validan contra tu usuario del **Discord normal**, así que podés usar los comandos desde el servidor de staff sin problema.

## Copia de estructura y foros

- Ejecutar `python copiar_servidor.py` para iniciar el bot de sincronización.
- En el servidor destino, usar `/skideo_larper` con el permiso Administrar servidor.
- Primero copia roles, categorías y canales (texto/anuncios, voz, escenario y foro) por nombre: crea lo que falta y actualiza permisos y posiciones de lo que ya existe. Al terminar esa fase espera `COPY_PHASE_COOLDOWN_SECONDS` segundos (20 por defecto) y recién ahí copia las publicaciones de los foros, que tienen su propia pausa.
- El bot necesita estar en ambos servidores, tener Administrador en el emisor y Administrar roles/canales/hilos, acceso al historial y Adjuntar archivos en el destino; además el intent privilegiado Message Content debe estar habilitado en el portal de Discord. Si le falta un permiso o Discord rechaza un elemento, imprime el error y continúa con los demás.
- Antes de cada petición de escritura espera `COPY_STRUCTURE_DELAY_SECONDS` en roles y canales, y `COPY_DELAY_SECONDS` en foros (3 segundos cada uno por defecto). Si aparecen errores 429, subí esos valores.
- Al terminar, el comando muestra en el chat y en la consola la lista de todo lo que agregó como nuevo: roles, categorías, canales y temas de foro. Lo que ya existía solo se actualiza y no aparece en la lista.
- En los foros copia publicaciones activas y archivadas, sus respuestas, textos, embeds y archivos adjuntos. Las menciones a roles se reemplazan por las menciones a sus roles equivalentes en el destino. Cada imagen o video se sube en una petición separada como mensaje dentro del mismo tema; si Discord rechaza un archivo (por ejemplo, por tamaño), lo informa y continúa. Todos los mensajes aparecerán publicados por el bot, no por el autor original. Los emojis personalizados usados en etiquetas o como reacción predeterminada no se trasladan.
- Si el servidor destino no admite iconos o colores degradados de roles, los omite y avisa al finalizar; el resto de los roles se sincroniza normalmente.
- El rol configurado en `COPY_SKIP_ROLE_ID` se deja intacto; si existe su equivalente en el destino, se conserva su mapeo para los permisos de canales. Los demás roles que el bot no pueda modificar o reordenar se saltean y se informan al finalizar.
- Guarda la relación entre publicaciones y mensajes en `FORUM_COPY_STATE_DB` para actualizar las copias y no duplicarlas al ejecutar el comando de nuevo. Conservá ese archivo para mantener esa relación.
- No elimina canales, roles, publicaciones ni respuestas sobrantes del destino; tampoco copia miembros, reacciones ni roles administrados por integraciones.

## Paneles

- `/setupss` — admin, en el canal de guía. Publica el panel de SS.
- `/setupbypass` — admin, en el canal de bypass. Publica el panel de bypass.

##  Ticket ss

- **Reclamar / Cerrar ticket** — staff, desde el ticket.
- `/result` — staff. Abre el formulario del resultado; solo quien ejecutó el comando puede abrirlo desde los botones (también aplica a bypass). En tickets de SS las fotos son opcionales: si no hay, se registra sin pruebas y no se adjunta nada al resultado. En bypass las fotos siguen siendo obligatorias. La carga y publicación del resultado quedan registradas en el canal de logs.
- `/unclaim` — staff, quita el claim.
- `/remember` — staff, manda el recordatorio por MD con link al ticket.

## ticket Bypass

- `/bypassban usuario` — admin, banea de tickets de bypass.
- `/bypassunban usuario` — admin, quita el ban.
- `/bypasstop` — top 5 de bypassers. Si bypassed es "Sí" se suma 1 punto.
- El formulario de resultado bypass permite indicar si el usuario se publica como anónimo.

## Puntos (CO CEO / CEO / ADMIN)

- `/points tipo:bypass accion:add usuario:<usuario> cantidad:<puntos>`
- `/points tipo:bypass accion:remove usuario:<usuario> cantidad:<puntos>`
- `/points tipo:ss accion:add usuario:<usuario> cantidad:<puntos>`
- `/points tipo:ss accion:remove usuario:<usuario> cantidad:<puntos>`

El usuario se elige con el autocompletado del servidor. Cada uso deja un log naranja en el canal de logs.

## Cooldowns

- Cooldown por defecto: 7 días (`TEST_COOLDOWN`). Se aplica el rol con menor cooldown, en este orden: Booster 3 días (`BOOSTER_ROLE_ID` / `BOOSTER_COOLDOWN`), Staff del Mes 4 días (`STAFF_MONTH_ROLE_ID` / `STAFF_MONTH_COOLDOWN`, se busca en el servidor de staff) y VIP 5 días (`VIP_ROLE_ID` / `VIP_COOLDOWN`).
- `/cooldown [usuario]` — cualquier usuario puede ver su propio cooldown; con el parámetro opcional consulta a otro usuario (autocompletado).
- `/addcooldown usuario duracion` — admin, aplica un cooldown. La duración va en formato `7d`, `12h`, `30m` y reemplaza el cooldown que tuviera.
- `/removecooldown usuario` — admin, lo quita.

## Actividad

- `/actividad` — staff, reporte de puntos del staff.
- Los claimeos, unclaim, cargas de pruebas y resultados publicados se registran en el canal de logs.

## Moderación

- `/clear numero` — admin+ (validado contra el servidor normal), borra esa cantidad de mensajes del canal actual. Funciona en el Discord normal y en el de staff. Máximo 50.

## Strikes y warns del staff (admin+)

- `/warn usuario cantidad razon` — suma warns. Cada **5 warns** se convierte en **1 strike** y el sobrante queda guardado (ej: tenía 4 y le suman 3 → 1 strike y 2 warns).
- `/strike usuario razon` — suma 1 strike.
- `/strikeremove usuario cantidad` — resta strikes (nunca baja de 0).
- Al llegar a **3 strikes**: el bot lo expulsa (kick) del servidor de staff y le quita todos los roles de staff del servidor normal (STAFF, ADMIN, HEAD_ADMIN, CO_CEO, CEO).
- Cada strike se avisa en `STRIKE_CHANNEL_ID` con el formato `<@usuario> 1/3 — razón`.
- `/bypassactivity add usuario` — lo excluye de los strikes por inactividad (no se le da strike ni se avisa).
- `/bypassactivity remove usuario` — lo vuelve a incluir.
- `/bypassactivity list` — lista de staff exentos.

## Cierres automáticos

- **Domingo** a la hora de `ACTIVIDAD_HORA`: se manda el embed final de actividad semanal a `WEEKLY_ACTIVITY_CHANNEL_ID` y después se revisan los staff con el rol `STAFF` del servidor normal: si tienen menos de **5 puntos** en la semana se les suma 1 strike (los exentos de `/bypassactivity` no reciben strike ni aviso).
- **Último día del mes** a la hora de `ACTIVIDAD_HORA`: se manda el embed final de actividad mensual a `MONTHLY_ACTIVITY_CHANNEL_ID`.
