# Gate gratuito de merges de DT-144

Estado: **servicio operativo; adopción de los dos repositorios en curso**
Ámbito: DT-144, Fase -1
Coste: **0 €**

## Decisión mínima

Forgejo en el NAS es la autoridad de merge de ReactPortfolioServer y
The_Swiss_Job. El primero debe permanecer privado y GitHub Free no permite
protegerlo. El segundo ya tiene protección GitHub, pero la única cuenta GitHub
no puede aprobar su propia PR; Forgejo aporta la identidad técnica separada sin
pagar ni crear otra cuenta externa. GitHub conserva gratuitamente el CI y, en
The_Swiss_Job, sus nueve checks y el bloqueo de pushes de terceros; la aprobación
humana no se duplica allí porque volvería imposible sincronizar el merge ya
aprobado en Forgejo.

GitHub Actions sigue siendo el único motor de CI de ambos repositorios; no se
instala Forgejo Actions ni un segundo runner.

Forgejo escucha exclusivamente en el loopback del NAS. QNAP resetea los canales
TCP de port-forward aunque la API local esté sana, por lo que no se anuncia una
URL de navegador. Los scripts llaman por defecto a ssh nas y ejecutan curl
contra http://127.0.0.1:3001 dentro del NAS. No se publica el puerto en la LAN
ni en Internet.

## Topología y secretos

- Manifiesto: deploy/forgejo/compose.yml.
- Persistencia: /share/Public/swissjob/dt144-forgejo/data.
- Base: SQLite; el volumen tiene dos usuarios y no justifica otro
  servidor.
- Imagen: Forgejo 16.0.5 fijada por digest.
- Red: bridge nativa de Docker; QNAP no debe crear otro vSwitch.
- Límite: 0,25 CPU y 256 MiB.
- Deshabilitados: registro, SSH, webhooks, hooks Git, OpenID y Actions.
- Ninguna contraseña ni token se versiona. Los tokens se pasan por
  GITHUB_TOKEN y FORGEJO_TOKEN.
- FORGEJO_SSH_HOST permite sustituir el alias nas; dejarlo vacío sólo es válido
  al ejecutar el script dentro del propio NAS o con un túnel comprobado.

## Roles que no se pueden mezclar

1. VRicardoP: propietario humano, único aprobador y único usuario autorizado a
   fusionar.
2. Autor técnico no administrador: crea ramas y PR, pero no puede aprobar ni
   fusionar.
3. Token verificador controlado por el propietario: publica únicamente
   dt144/github-backend-ci o dt144/github-swissjob-ci.
4. Token de despliegue de solo lectura: permite comprobar rama, protección y
   estado; no permite escribir.

El propietario debe cambiar la contraseña inicial antes de declarar operativa
la separación. El autor técnico nunca conserva su contraseña ni su token.

## Protección exacta de main

Debe leerse de vuelta por API y cumplir simultáneamente:

- push directo deshabilitado, también para administradores;
- merge y aprobación limitados a VRicardoP;
- una aprobación como mínimo;
- aprobación obsoleta descartada tras cada commit;
- solicitud CODEOWNER, rechazo y rama desactualizada bloqueantes;
- check requerido del repositorio: dt144/github-backend-ci o
  dt144/github-swissjob-ci;
- force-push y borrado no disponibles.

.forgejo/CODEOWNERS solicita a VRicardoP para cualquier cambio.

## Flujo de cambio

1. El autor parte del main protegido y empuja la misma rama a GitHub y Forgejo.
2. El push ejecuta .github/workflows/ci.yml en GitHub.
3. El propietario verifica y publica el resultado exacto desde el repositorio:

       python3 scripts/forgejo_gate.py publish-ci <SHA_COMPLETO>

4. Forgejo sólo permite fusionar después del estado verde y la aprobación
   humana actual.
5. El main fusionado se sincroniza con el mismo SHA a GitHub para conservar el
   remoto y su CI. En The_Swiss_Job el propietario puede efectuar sólo esta
   sincronización exacta; los nueve checks siguen obligatorios, pero GitHub no
   exige una segunda aprobación. Un SHA que no sea el main Forgejo no puede
   desplegarse.
6. El scripts/deploy_nas.sh de cada repositorio vuelve a consultar Forgejo antes
   de construir y sólo acepta que HEAD sea exactamente el main protegido con el
   estado verde.

El antiguo scripts/deploy_to_nas.sh, que cargaba una etiqueta mutable latest y
dejaba la recreación fuera del control, se elimina.

## Recuperación

- Una caída de Forgejo bloquea nuevos merges y despliegues; no afecta al
  runtime de ReactPortfolio, SwissJob ni Core.
- El estado completo está en el volumen data. El backup diario ejecuta el dump
  nativo de Forgejo, valida el tar y lo registra con la misma retención de siete
  días que los dumps de PostgreSQL.
- Restaurar exige la misma imagen por digest, el volumen y una lectura posterior
  de rama, protección y estado.
- Nunca se sortea una caída desplegando desde GitHub: se restaura el gate o se
  mantiene producción sin cambios.

## Evidencia de cierre

La Fase -1 no se declara superada hasta documentar:

1. contenedor sano, límites y exposición loopback leídos del NAS;
2. repositorio privado y dos identidades separadas;
3. cambio de contraseña del propietario confirmado sin revelar el valor;
4. protección exacta leída de vuelta;
5. prueba negativa de push, autoaprobación y merge sin CI;
6. prueba positiva sólo tras CI exacto y aprobación fresca;
7. guarda de despliegue rechazando un SHA distinto;
8. backup y restauración mínima verificados.

## Implantación medida el 2026-10-14

- Forgejo 16.0.5 responde pass en base y caché; usa 131,3 MiB tras el arranque,
  sin OOM, con límite de 256 MiB y 0,25 CPU.
- El único puerto publicado es 127.0.0.1:3001. QNAP no pudo crear otra red por
  un fallo dnsmasq; se reutilizó la bridge nativa, sin conectar Forgejo a las
  redes de bases o aplicaciones.
- Los directorios persistentes pertenecen a 1000:100 y tienen modo 700.
- Los main iniciales son ReactPortfolioServer 5bf457a y The_Swiss_Job e59283b.
- Ambas políticas se leyeron de vuelta con todos los campos de la sección
  anterior. La cuenta administrativa de bootstrap y sus tokens se eliminaron.
- Un dump real de 8.317.326 bytes se restauró sin red: SQLite reconstruyó dos
  usuarios, dos repositorios y dos protecciones; ambos repositorios pasaron
  git fsck y conservaron sus SHA exactos. El dump y el entorno temporal fueron
  eliminados.
- El backup diario corregido terminó con BACKUP_OK a las
  2026-10-14T07:58:15Z. Registró como verificado el dump Forgejo de 8.347.024
  bytes, verificó las tres bases PostgreSQL, aplicó retención y liberó el lock.
- Las PR de adopción son la 1 en ambos repositorios. ReactPortfolio apunta a
  72478d8 y The_Swiss_Job incorporó la implementación ef54418. En las dos, el
  autor recibió 422 al intentar autoaprobar y 405 al intentar fusionar.
