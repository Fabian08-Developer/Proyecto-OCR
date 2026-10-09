# Sistema de Verificación OCR de Documentos de Identidad

Compara cédulas de ciudadanía colombianas escaneadas (en un solo PDF) contra
un reporte de inscripción en Excel, usando OCR local (RapidOCR) — sin enviar
las imágenes a ningún servicio externo.

## Arquitectura

```
ocr-system/
├── backend/
│   ├── app/
│   │   ├── main.py              # API FastAPI (endpoints)
│   │   ├── ocr_engine.py        # PDF -> imágenes -> RapidOCR
│   │   ├── field_parser.py      # regex/heurísticas -> campos estructurados
│   │   ├── document_grouper.py  # agrupa páginas en "documentos" completos
│   │   ├── excel_reader.py      # lee el reporte de inscripción (.xls/.xlsx)
│   │   ├── matcher.py           # compara OCR vs Excel
│   │   └── schemas.py
│   └── requirements.txt
├── frontend/                    # HTML/CSS/JS simple, sin build (servido por FastAPI)
├── run.sh / run.bat
└── README.md
```

### Flujo de datos

1. El usuario sube el **PDF** (todas las cédulas escaneadas) y el **Excel**
   (reporte de inscripción) desde el navegador.
2. `POST /api/comparar` recibe ambos archivos:
   - Convierte cada página del PDF en imagen (PyMuPDF, sin depender de Poppler).
   - Corre RapidOCR sobre cada imagen.
   - `field_parser.py` intenta reconocer 3 layouts distintos de cédula:
     - Formato clásico (amarillo) — frente y reverso.
     - Formato nuevo (con chip / código QR / mariposas).
     - Bloque MRZ del reverso del formato nuevo (lectura muy confiable
       porque sigue un patrón fijo tipo ICAO TD1).
   - `document_grouper.py` une las páginas que pertenecen a la misma
     persona (por ejemplo frente en la página 3, reverso en la página 4)
     en un único registro.
   - `excel_reader.py` localiza automáticamente la tabla dentro del Excel
     (busca la fila con encabezados "Identificación" / "Nombre", sin
     asumir una posición fija).
   - `matcher.py` cruza ambos conjuntos por número de documento y valida
     el nombre con similitud de texto (tolerante a orden distinto de
     nombres/apellidos y a pequeños errores de OCR).
3. El frontend pinta un resumen y una tabla con el resultado de cada
   participante: **Coincide**, **Nombre no coincide**, **Datos
   incompletos** o **No encontrado en PDF** — más una pestaña de
   documentos del PDF que sobraron (no estaban en el Excel).

## Cómo correrlo

### Requisitos
- Python 3.10+
- (Linux) puede que necesites `libgl1` para OpenCV: `sudo apt install libgl1`

### Pasos

```bash
# Linux / Mac
./run.sh

# Windows
run.bat
```

El script crea un entorno virtual, instala dependencias (la primera vez
tarda unos minutos porque descarga los modelos de RapidOCR) y levanta el
servidor en `http://localhost:8000`. Abre esa URL en el navegador: el
propio backend sirve el frontend.

La primera ejecución de OCR también descargará los modelos ONNX de
RapidOCR (unos ~15 MB) y los dejará cacheados localmente.

### Acelerar el OCR con la tarjeta gráfica (Windows, opcional)

El reconocimiento de texto puede correr en la GPU del equipo mediante
DirectML (sirve con tarjetas NVIDIA, AMD o Intel, sin instalar CUDA).

1. Ejecuta `run.bat` al menos una vez (crea el entorno virtual).
2. Ejecuta `activar_gpu.bat`: instala `onnxruntime-directml`.
3. Ejecuta `probar_gpu.bat`: mide CPU contra GPU en este equipo, verifica
   que el texto reconocido sea igual y dice qué configuración conviene.
4. Vuelve a iniciar `run.bat`. `GET /api/health` muestra en
   `ocr_dispositivo` si el OCR quedó en `cpu` o en `gpu`.

Si la GPU falla al cargar o durante el OCR, el sistema vuelve solo a CPU.
Para quitar la GPU: `activar_gpu.bat --quitar`.

Ajustes (variables de entorno, se pueden poner en `run.bat`):

| Variable          | Valores                            | Para qué                                   |
|-------------------|------------------------------------|---------------------------------------------|
| `OCR_DISPOSITIVO` | `auto` (defecto), `gpu`, `cpu`     | Forzar CPU sin desinstalar nada             |
| `OCR_GPU_MODULOS` | `det,cls,rec` (defecto)            | Qué modelos van a la GPU                    |
| `OCR_GPU_ID`      | `0`, `1`, ...                      | Elegir tarjeta (por defecto la más potente) |

## Endpoints de la API

| Método | Ruta                | Descripción                                          |
|--------|----------------------|-------------------------------------------------------|
| POST   | `/api/extract-pdf`  | Solo OCR + parsing del PDF (útil para depurar)        |
| POST   | `/api/parse-excel`  | Solo lectura del Excel (útil para depurar)            |
| POST   | `/api/comparar`     | Flujo completo: OCR + Excel + comparación             |
| GET    | `/api/health`       | Chequeo de salud                                       |

Puedes probar `/api/extract-pdf` y `/api/parse-excel` por separado (con
Swagger en `http://localhost:8000/docs`) para revisar qué está
reconociendo el OCR antes de correr la comparación completa.

## Limitaciones conocidas / próximos ajustes

- **Los regex de `field_parser.py` están calibrados con los formatos de
  cédula que revisamos**, pero el OCR nunca es 100% determinístico. Si
  ves campos vacíos o mal separados en varios documentos, lo más
  eficiente es:
  1. Probar `/api/extract-pdf` solo, mirar el JSON de salida.
  2. Revisar qué texto crudo devolvió RapidOCR para esa página
     (se puede exponer fácilmente agregando `raw_text` a la respuesta).
  3. Ajustar la palabra clave o el rango de búsqueda en
     `field_parser.py` (todas las funciones `parse_*` son independientes
     y fáciles de tocar sin afectar el resto).
- El agrupamiento de páginas (`document_grouper.py`) asume que, dentro
  de una misma cédula, la página con el **número de documento** viene
  antes que las páginas de reverso sin número. Si el PDF llega
  desordenado, el agrupamiento fallará; se puede mitigar detectando
  también el nombre y agrupando por similitud si hace falta.
- El umbral de similitud de nombres (`NAME_MATCH_THRESHOLD = 0.72` en
  `matcher.py`) es ajustable. Bájalo si el Excel trae nombres muy
  abreviados; súbelo si quieres ser más estricto.
- Actualmente el procesamiento es **síncrono** (el usuario espera a que
  termine el OCR). Con PDFs grandes (+50 páginas) conviene pasar esto a
  un job en background con progreso (Celery/RQ o `BackgroundTasks` de
  FastAPI + polling) — dejo la arquitectura lista para ese cambio porque
  la lógica ya está separada del endpoint.
- Antes de subir a un VPS: correr detrás de Nginx + gunicorn/uvicorn con
  varios workers, y limitar tamaño de archivo también a nivel de Nginx
  (`client_max_body_size`).
