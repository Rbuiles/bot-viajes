# Flight Alerts Colombia

Backend asíncrono en FastAPI que busca Súper Promociones de vuelos desde
Medellín (`MDE`) y Pereira (`PEI`) cada 5 minutos y envía una alerta por
WhatsApp cuando una tarifa está por debajo del umbral de su región.

## Características

- FastAPI + Uvicorn con ciclo de vida `lifespan`.
- Cliente HTTP asíncrono con `httpx`, timeouts y reintentos con backoff para
  errores transitorios de Travelpayouts (`429`, `5xx` y fallos de red).
- Búsqueda configurable de tarifas de solo ida e ida y vuelta mediante
  `ONLY_ROUND_TRIP`, sin filtrar fechas.
- APScheduler (`AsyncIOScheduler`) en segundo plano con una sola ejecución
  concurrente, `coalesce=True` y protección contra solapamientos.
- Filtrado estricto por origen, país permitido y umbral de precio.
- Mapa local de aeropuertos IATA (`airportsdata`) para resolver ciudad y país.
- SQLite asíncrono con SQLAlchemy y control de duplicados de 48 horas.
- Alertas por WhatsApp Cloud API (`v22.0`) con fecha de ida, fecha de regreso
  cuando aplica, tipo de viaje, texto formateado, vista previa del enlace y
  enlace de afiliado opcional a Aviasales (`marker`).
- Endpoint `/health` con estado del planificador, configuración de WhatsApp,
  modo de viaje y del último escaneo.
- Pruebas automatizadas con `pytest`, `ruff` y `mypy`.

## Requisitos de WhatsApp

El backend envía mensajes de texto libres con `preview_url=true` mediante el
endpoint:

```text
POST https://graph.facebook.com/v22.0/{WHATSAPP_PHONE_NUMBER_ID}/messages
```

Meta solo permite mensajes de texto libres dentro de la ventana de atención de
24 horas, es decir, después de que el destinatario haya escrito al número de
negocio. Para alertas proactivas fuera de esa ventana se debe crear y aprobar
una plantilla en Meta y adaptar el payload para usar `type=template`. La
implementación actual usa texto libre tal como fue solicitado.

El número `NOTIFICATION_PHONE` debe incluir el código de país en formato E.164,
por ejemplo `+573001234567`. El `WHATSAPP_PHONE_NUMBER_ID` es el identificador
del número en Meta, no el número telefónico.

## Orígenes y filtros

Los orígenes están fijos para cumplir la regla de negocio:

- `MDE`: Medellín, Colombia.
- `PEI`: Pereira, Colombia.

La consulta usa precios en USD, `direct=false` y orden por precio. Con
`ONLY_ROUND_TRIP=false` (valor por defecto) se consultan ida y vuelta
(`one_way=false`) y solo ida (`one_way=true`); con `true` solo se consulta ida y
vuelta. La API no ofrece un modo mixto, por lo que el modo abierto realiza dos
consultas por origen y página.

Con `ONLY_ROUND_TRIP=true` se descartan las ofertas sin fecha de regreso
(`return_at` o `return_date`). Con `false` se aceptan como vuelos de solo ida
antes de aplicar los filtros de precio y país.

### Países permitidos

| Región | Países |
| --- | --- |
| Sudamérica cercana | EC, PE, VE |
| Sudamérica lejana | AR, BO, BR, CL, PY, UY |
| Centroamérica, Caribe y México | CR, PA, GT, HN, SV, NI, DO, JM, CW, AW, MX |
| Europa | ES, FR, IT, DE, PT, NL, BE, CH, AT, GR, SE, NO, DK, FI, PL, CZ, HU, HR, RO, BG, GB, TR |
| Asia | TH, SG, PH, MY, ID, MV |

Todo país fuera de esas listas se descarta, incluida la lista explícita de
exclusión: AE, QA, SA, IL, JO, LB, OM, KW, BH, EG, KR, KP, US, CA, CN, RU, AU,
NZ e IN.

### Umbrales de Súper Promoción

El precio debe ser **menor o igual** al umbral. Todos los valores se pueden
sobrescribir desde `.env`.

| Región | Umbral por defecto | Variable |
| --- | ---: | --- |
| Sudamérica cercana (EC, PE, VE) | USD 200 | `THRESHOLD_SOUTH_AMERICA_NEAR_USD` |
| Sudamérica lejana (AR, BO, BR, CL, PY, UY) | USD 300 | `THRESHOLD_SOUTH_AMERICA_FAR_USD` |
| Centroamérica, Caribe y México | USD 220 | `THRESHOLD_CENTRAL_AMERICA_CARIBBEAN_USD` |
| Europa | USD 550 | `THRESHOLD_EUROPE_USD` |
| Asia | USD 950 | `THRESHOLD_ASIA_USD` |

## Arquitectura y estructura

La configuración y el ciclo de vida viven en `core`; la persistencia en
`database`; las integraciones y reglas de vuelos en `services`; la orquestación
periódica en `scheduler`; y la capa HTTP en `routers` y `schemas`.

```text
.
├── app/
│   ├── core/
│   │   ├── config.py
│   │   ├── lifespan.py
│   │   └── logging.py
│   ├── database/
│   │   └── db.py
│   ├── routers/
│   │   └── system.py
│   ├── schemas/
│   │   └── system.py
│   ├── scheduler/
│   │   ├── jobs.py
│   │   └── scheduler.py
│   ├── services/
│   │   ├── notifier.py
│   │   └── travelpayouts.py
│   └── main.py
├── data/
├── tests/
├── .env.example
├── .gitignore
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

`main.py` solo construye la aplicación, registra el `lifespan` y monta el router
del sistema.

## Instalación local

Requiere Python 3.11 o superior. El proyecto fue validado con Python 3.14.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Si el archivo `.env` no existe:

```bash
cp .env.example .env
```

Edita `.env` y completa, como mínimo:

1. `TRAVELPAYOUTS_TOKEN`: token de la API de Travelpayouts.
2. `WHATSAPP_PHONE_NUMBER_ID`: identificador del número en WhatsApp Cloud API.
3. `WHATSAPP_ACCESS_TOKEN`: token de acceso de Meta con permiso para enviar
   mensajes.
4. `NOTIFICATION_PHONE`: número destinatario en formato E.164.

`ONLY_ROUND_TRIP=false` acepta solo ida e ida y vuelta; usa `true` para
restringir las búsquedas a ida y vuelta.

`TRAVELPAYOUTS_MARKER` es opcional. Si se configura, los enlaces a Aviasales
incluyen ese marker; si está vacío, las alertas siguen funcionando sin enlace de
afiliado.

### Configuración en Meta

1. Crea una app en Meta for Developers y agrega el producto WhatsApp.
2. Obtén el `Phone Number ID` del número remitente.
3. Genera un token de acceso con permisos de mensajería. Para producción usa un
   token permanente de usuario del sistema; los tokens temporales caducan.
4. Agrega el número destinatario a la lista de números de prueba mientras la app
   esté en modo desarrollo.
5. Verifica que el destinatario haya aceptado recibir mensajes del negocio. Si
   no hay una conversación abierta en las últimas 24 horas, usa una plantilla
   aprobada.

## Ejecución

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Abre `http://127.0.0.1:8000/docs` para la documentación interactiva y
`http://127.0.0.1:8000/health` para el estado operativo.

El planificador arranca dentro del `lifespan`, ejecuta un primer escaneo de
inmediato y luego cada `CHECK_INTERVAL_MINUTES` minutos. No uses `--reload` ni
múltiples workers en producción: cada proceso tendría su propio planificador.
Para escalar horizontalmente, ejecuta el escaneo en un solo proceso o extrae el
planificador a un worker independiente.

## Ejecutar las pruebas y validaciones

```bash
pip install -r requirements-dev.txt
ruff check app tests
mypy app --strict --ignore-missing-imports
pytest -q
```

Las pruebas del notificador usan `httpx.MockTransport`; no envían mensajes
reales ni consumen la cuota de WhatsApp.

## Anti-duplicados y persistencia

Cada oferta genera un `deal_id` con uno de estos formatos:

```text
{origin}_{destination}_{departure_at}_{price}
{origin}_{destination}_{departure_at}_{price}_one_way
```

El sufijo `_one_way` distingue las ofertas de solo ida y evita que una tarifa
de ida y vuelta con la misma ruta, fecha y precio sea tratada como duplicada.

Antes de enviar una alerta se consulta SQLite. Si el mismo `deal_id` fue
notificado en las últimas `DEDUPLICATION_HOURS` horas (48 por defecto), se
omite. La alerta solo se registra después de que el envío por WhatsApp es
exitoso, de modo que un fallo de WhatsApp puede reintentarse en el siguiente
ciclo.

La base de datos se crea automáticamente en `data/flight_alerts.db` si se usa la
configuración por defecto. Los registros con más de
`NOTIFICATION_RETENTION_DAYS` días se purgan periódicamente.

## Variables de entorno

| Variable | Obligatoria | Valor por defecto |
| --- | --- | --- |
| `TRAVELPAYOUTS_TOKEN` | Sí, para consultar la API | vacío |
| `TRAVELPAYOUTS_MARKER` | No | vacío |
| `ONLY_ROUND_TRIP` | No | `false` |
| `WHATSAPP_PHONE_NUMBER_ID` | Sí, para enviar alertas | vacío |
| `WHATSAPP_ACCESS_TOKEN` | Sí, para enviar alertas | vacío |
| `NOTIFICATION_PHONE` | Sí, para enviar alertas | vacío |
| `CHECK_INTERVAL_MINUTES` | No | `5` |
| `DEDUPLICATION_HOURS` | No | `48` |
| `NOTIFICATION_RETENTION_DAYS` | No | `30` |
| `DATABASE_URL` | No | Ruta absoluta a `data/flight_alerts.db` dentro del proyecto |
| `TRAVELPAYOUTS_MARKET` | No | `us` |
| `TRAVELPAYOUTS_LIMIT` | No | `1000` |
| `TRAVELPAYOUTS_MAX_PAGES` | No | `1` |
| `TRAVELPAYOUTS_MAX_RETRIES` | No | `3` |
| `TRAVELPAYOUTS_TIMEOUT_SECONDS` | No | `20` |
| `WHATSAPP_TIMEOUT_SECONDS` | No | `20` |
| `LOG_LEVEL` | No | `INFO` |

Los umbrales de cada región también son variables de entorno; consulta la tabla
de umbrales y `.env.example`.

## Resiliencia

- Si Travelpayouts falla para un origen, el otro origen continúa.
- Los errores transitorios de Travelpayouts se reintentan con backoff y
  respetando `Retry-After` cuando está disponible.
- Un fallo total de la API no derriba FastAPI: se registra en logs y el
  planificador reintenta en el siguiente ciclo.
- Los errores HTTP o de red de WhatsApp se capturan y la oferta no se marca como
  notificada, por lo que puede reintentarse.
- El acceso a SQLite usa WAL, `busy_timeout` y transacciones con rollback.
- `/health` nunca expone el token de Travelpayouts, el token de WhatsApp ni el
  número destinatario. Solo indica si WhatsApp está configurado y el modo de
  viaje activo.

## Aviso

Travelpayouts devuelve tarifas en caché de búsquedas recientes. El precio y la
disponibilidad pueden cambiar antes de completar la compra. El mensaje de
WhatsApp incluye este aviso y, si está configurado, un enlace a Aviasales con el
marker de afiliado.
