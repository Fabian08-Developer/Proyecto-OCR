"""
Parser de campos para cédulas de ciudadanía colombianas.

Soporta:
  1. Formato "amarillo" clásico con hologramas.
  2. Formato digital nuevo (policarbonato con chip, QR y zona MRZ).
  3. Tolerancia a ruido de OCR, múltiples formatos de fecha (texto y numéricos),
     y filtros de palabras reservadas (stop words) para evitar falsos positivos
     en nombres y apellidos.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, asdict
from typing import Optional, List


# --------------------------------------------------------------------------- #
# Utilidades de normalización y limpieza
# --------------------------------------------------------------------------- #

def strip_accents(text: str) -> str:
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_for_match(text: str) -> str:
    """Minúsculas, sin tildes, caracteres no alfanuméricos eliminados y espacios colapsados."""
    text = strip_accents(text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# Palabras reservadas / encabezados oficiales que no pueden ser nombres ni apellidos
STOP_WORDS_NAME = {
    "republica", "colombia", "identificacion", "personal", "cedula", "ciudadania",
    "registraduria", "nacional", "estado", "civil", "firma", "indice", "derecho",
    "tarjeta", "extranjeria", "titular", "huella", "registrador", "delegado",
    "numero", "nombres", "apellidos", "nacimiento", "expedicion", "expiracion",
    "estatura", "sexo", "lugar", "fecha", "nuip", "gs", "rh", "vigencia",
    "duplicado", "colombiana", "nacionalidad", "col", "documento", "departamento",
    "municipio", "ciudad", "bogota", "medellin", "cali", "barranquilla", "cartagena",
    "bucaramanga", "pereira", "manizales", "armenia", "ibague", "cucuta", "pasto",
    "villavicencio", "monteria", "valledupar", "sincelejo",
    "quibdo", "riohacha", "florencia", "mocoa", "leticia", "inirida", "mitu",
    "puerto", "asis", "yopal", "arauca", "distrito", "especial",
    # Variantes concatenadas que el OCR produce cuando pega varias palabras
    "lugardenacimiento", "fechadenacimiento", "indicederecho", "registradornacional",
    "ceduladeciudadania", "identificacionpersonal", "republicadecolombia",
    "fechaylugardeexpedicion", "fechaexpedicion", "fechaexpiracion", "fechanacimiento",
    "lugardeexpedicion", "lugarexpedicion", "indicederecho", "huelladerecha",
    "tarjetadeidentidad", "tarjetageieentead", "tarjetaidentidad", "tarjetade",
    "primeravezcc", "primeravez", "contrasena", "comprobante", "tramite",
    # Variantes OCR de labels que no deben confundirse con nombres
    "nomrres", "nomdres", "nombeas", "nomgres", "nomores",
    "apelludos", "apellrdos", "apllidos", "apellidosnous", "arellidosinous", "arclldosn",
    "ndneaes", "nclicnes", "nlaierfs", "nlalderfs", "clicnes",
    # Variantes OCR donde se pierde la 'A' inicial de APELLIDOS
    "pelldos", "pellidos", "pelidos", "pellldos", "pelldo",
    "pellods", "pelldo", "pellds", "pells", "pelid",
    "apeldos", "apelldo", "apelldas", "apells",
    # Tokens de ruido que el OCR produce leyendo partes de frases de la cedula
    "iunro", "iuuro", "iunoro", "lunro", "lunoro",
    "mnro", "mnros", "muros", "mros", "umo", "numo", "numer0", "numr0", "nro",
    # Variantes de NOMBRES sin las primeras letras
    "ombres", "ombr", "mbre", "mbres",
    # Ciudades/departamentos adicionales
    "quindio", "caqueta", "huila", "tolima", "nari", "cauca", "boyaca",
    "cundinamarca", "antioquia", "santander", "atlantico", "bolivar",
    "cordoba", "magdalena", "sucre", "guajira", "choco",
    "vaupes", "guainia", "amazonas", "vichada", "guaviare", "putumayo",
    "casanare", "meta", "caguan", "sanvicente", "sanvicentedelcaguan",
    "eldoncello", "elpaujil", "cartagenadechaira",
    # Variantes OCR de "Nacionalidad"
    "nacionaidad", "naconalidad", "nacionanlidad", "nacion", "nacionalidade",
    # Abreviaciones comunes que no son nombres
    "gsr", "gsrh", "nuip", "col", "mf", "col1", "rma", "estatuaa", "estatura",
    # Variantes concatenadas de "REPUBLICA DE COLOMBIA"
    "republicadecolombia", "republicadecolombla", "republicadecolomla", "republicadecolumbia",
    # Encabezados de cedula digital y clasica con errores OCR
    "cedulade", "ciudadania", "ceduladeciadudania", "ceduladeciudadamia", "ceduladeci", "udadamia",
    "ceduda", "ceduia", "cedla", "cedua", "cdeula", "ceula", "ceduida", "cedudadeciudadania",
    "cudadania", "ciadudania", "udanania", "udananias", "oulade", "oula", "ouladecolombia",
    # Otras frases de encabezado OCR
    "identificacionpersonal",
    # Texto de firma/registro
    "registradornacional", "registradorhnacional", "registradohrnacional", "registradcrnagonal",
    "registradcrnaconal", "pegstaadonncional",
    "firm", "firme", "firma", "firmar", "firmado", "ndicecerechog", "indicecerecho", "indicerecho",
    "apelliocs", "apeliocs", "korahes", "korahe",
    "de", "del", "la", "las", "los",
}


# --------------------------------------------------------------------------- #
# Limpieza de nombres
# --------------------------------------------------------------------------- #

def is_likely_noise(text: str) -> bool:
    """
    Detecta si un texto parece ser ruido OCR (firma, sello, marca de agua).
    """
    if not text:
        return False
    text_strip = text.strip()

    # Secuencias tipicas de sellos/marcas de agua/camaras
    noise_patterns = [
        r"[Cc]am[Ss]canner",
        r"[Ss]canned\s?with",
        r"[Pp]owered\s?by",
        r"^CS$",
        r"^BL$",
        r"^RM$",
        r"^MW$",
        r"^PCRO+$",
    ]
    for pat in noise_patterns:
        if re.search(pat, text_strip):
            return True

    # Revisar cada palabra individualmente
    words = text_strip.split()
    for word in words:
        word_alpha = re.sub(r"[^A-Za-z]", "", word).upper()
        if not word_alpha or len(word_alpha) < 2:
            continue

        # Secuencias de 5+ consonantes puras seguidas (imposible en espanol, sin Y ni H)
        if re.search(r"[BCDFGJKLMNPQRSTVWXZ]{5,}", word_alpha):
            return True

        # Palabras que empiezan con 2+ consonantes inusuales en espanol
        valid_starts = {
            "BL", "BR", "CL", "CR", "DR", "FL", "FR", "GL", "GR",
            "PL", "PR", "TR", "CH", "LL", "RR", "JH", "SH", "ST",
            "SP", "SC", "SM", "SN", "SW", "TH", "VL", "KR", "KL",
            "TL", "WR", "WH", "PH", "RH", "QU", "GU", "PS", "GN", "MN"
        }
        if len(word_alpha) >= 4:
            first2 = word_alpha[:2]
            # Si los 2 primeros son consonantes puras y no son un inicio valido
            if (first2[0] in "BCDFGJKLMNPQRSTVWXZ" and
                    first2[1] in "BCDFGJKLMNPQRSTVWXZ" and
                    first2 not in valid_starts):
                return True

        # Palabras con combinaciones de ruido conocidas (se excluye DW para permitir EDWAR, EDWIN, EDWARD)
        if re.search(r"(WW|QQ|PW|WP|MOP$|DMP$|WMO|OIM)", word_alpha):
            return True

        # Proporcion de vocales muy baja (incluyendo Y)
        if len(word_alpha) >= 6:
            vowels = sum(1 for c in word_alpha if c in "AEIOUY")
            if vowels / len(word_alpha) < 0.15:
                return True

    return False


COLOMBIAN_NAMES_LEXICON = {
    # Nombres de pila comunes en Colombia
    'JOSE', 'JUAN', 'MARIA', 'LUIS', 'CARLOS', 'JHON', 'BRAYAN', 'VICTOR', 'EDWAR', 'EDWARD',
    'YENIFER', 'YENNIFER', 'WILKIN', 'MIGUEL', 'DONALDO', 'CESAR', 'DANIEL', 'ANDRES', 'ESTIBEN',
    'ESTEBAN', 'STIVEN', 'JHULIAN', 'DANILO', 'WALTER', 'JAVIER', 'SANTIAGO', 'ANGEL', 'SERGIO',
    'MANUEL', 'YEISON', 'YODMAN', 'ELIAN', 'SEBASTIAN', 'KEINER', 'YHORLAN', 'ERLENDY', 'PEDRO',
    'ALEXANDRA', 'CRISTHIAM', 'MAURICIO', 'LUIZ', 'YOHAN', 'HERNAN', 'FABIAN', 'EDINSON', 'JORGE',
    'DAVID', 'JESUS', 'DIEGO', 'ALVARO', 'OSCAR', 'FERNANDO', 'RICARDO', 'GUSTAVO', 'ALEXANDER',
    'LEONARDO', 'GABRIEL', 'RODRIGO', 'FRANCISCO', 'ALEJANDRO', 'CAMILO', 'FELIPE', 'SEBASTIAN',
    'ALEX', 'FREDY', 'FREDDY', 'EDGAR', 'EDWIN', 'HECTOR', 'WILSON', 'JAVIER', 'JAIME', 'JULIO',
    'ORLANDO', 'HUGO', 'ROBERTO', 'RAFAEL', 'ENRIQUE', 'ALFONSO', 'ARMANDO', 'BERNARDO',
    'GERMAN', 'GONZALO', 'GUILLERMO', 'IVAN', 'NELSON', 'OMAR', 'RAUL', 'RUBEN',
    # Apellidos comunes en Colombia
    'MONTOYA', 'MANCERA', 'CHARO', 'MOSQUERA', 'ESCALANTE', 'LOPEZ', 'GUTIERREZ', 'POLOCHE',
    'GONZALEZ', 'AYALA', 'CASTRO', 'QUIROZ', 'ARTUNDUAGA', 'TORRES', 'MUNOZ', 'MUÑOZ', 'AMARIS',
    'MENDOZA', 'RAMIREZ', 'JIMENEZ', 'DEVIA', 'ALVAREZ', 'COLLAZOS', 'GUEVARA', 'MORALES',
    'PINEDA', 'CLAROS', 'HOYOS', 'ANDRADE', 'TOVAR', 'CASTILLO', 'SANCHEZ', 'CONDE', 'ZULUAICA',
    'ESCOBAR', 'MURIEL', 'CALDERON', 'TORO', 'RODRIGUEZ', 'MARTINEZ', 'GOMEZ', 'PEREZ', 'DIAZ',
    'HERNANDEZ', 'MORENO', 'VARGAS', 'SUAREZ', 'ROMERO', 'ROJAS', 'SILVA', 'RUIZ', 'ARIAS',
    'ORTEGA', 'DELGADO', 'CARDONA', 'MEDINA', 'CORREA', 'VALENCIA', 'VEGA', 'GUZMAN', 'CORTES',
    'CARVAJAL', 'PENAGOS', 'GIRALDO', 'VACHA', 'GALINDO', 'ARIZA', 'PINZON', 'MURCIA', 'OSPINA',
    'SALAZAR', 'CARDENAS', 'RESTREPO', 'JARAMILLO', 'ZAPATA', 'LONDOÑO', 'LONDONO', 'BOTERO',
    'HENAO', 'QUINTERO', 'BEDOYA', 'CASTAÑO', 'CASTANO', 'VILLEGAS', 'DUQUE', 'ECHEVARRIA',
    'CHAVARRIA', 'MONROY', 'BUITRAGO', 'CACERES', 'TRIANA', 'FORERO', 'RINCON', 'GAVIRIA',
    'PARRA', 'BAUTISTA', 'CAMACHO', 'SERNA', 'PEÑA', 'PENA', 'BARRIOS', 'BARRETO', 'SALGADO',
    'CABRERA', 'MORA', 'PACHECO', 'NAVARRO', 'BELTRAN', 'VILLANUEVA', 'TRUJILLO', 'BERNAL',
    'ACOSTA', 'RIVERA', 'MOLINA', 'AGUIRRE', 'CABALLERO', 'HERRERA', 'LOZANO', 'PADILLA',
    'PALACIOS', 'PAREDES', 'PLAZAS', 'PRADA', 'REYES', 'SALCEDO', 'SANDOVAL',
    'SARMIENTO', 'SOTO', 'TELLEZ', 'URBINA', 'URIBE', 'VALLEJO', 'VELASQUEZ', 'ZAMBRANO',
}

_SORTED_NAMES_LEXICON = sorted(COLOMBIAN_NAMES_LEXICON, key=len, reverse=True)


def split_concatenated_name(name: str) -> str:
    """
    Separa nombres o apellidos que quedaron concatenados sin espacios por el OCR.
    Usa un lexicón curado de nombres y apellidos colombianos para evitar cortes erróneos.
    Ej: MONTOYAMANCERA -> MONTOYA MANCERA
    Ej: CHAROMOSQUERA -> CHARO MOSQUERA
    Ej: ESCALANTELOPEZ -> ESCALANTE LOPEZ
    Ej: MAURICIO -> MAURICIO (NO se corta)
    """
    if not name or ' ' in name or len(name) <= 6:
        return name

    name_upper = name.upper()

    # Si es exactamente una palabra conocida del lexicón, no dividir
    if name_upper in COLOMBIAN_NAMES_LEXICON:
        return name_upper

    # Manejar caso particular "DELAHOZ" -> "DE LA HOZ"
    if name_upper == "DELAHOZ":
        return "DE LA HOZ"
    if name_upper.endswith("DELAHOZ") and len(name_upper) > 7:
        prefix = name_upper[:-7]
        return f"{split_concatenated_name(prefix)} DE LA HOZ"

    # Buscar coincidencia por prefijo en el lexicón
    for w in _SORTED_NAMES_LEXICON:
        if name_upper.startswith(w) and len(name_upper) >= len(w) + 3:
            rest = name_upper[len(w):]
            rest_split = split_concatenated_name(rest)
            return f"{w} {rest_split}"

    # Buscar coincidencia por sufijo en el lexicón
    for w in _SORTED_NAMES_LEXICON:
        if name_upper.endswith(w) and len(name_upper) >= len(w) + 3:
            first = name_upper[:-len(w)]
            first_split = split_concatenated_name(first)
            return f"{first_split} {w}"

    return name


def is_header_or_stopword(text: str) -> bool:
    """Verifica si un texto o palabra corresponde a un encabezado oficial o stop word."""
    norm = normalize_for_match(text)
    if not norm:
        return True
    if norm in STOP_WORDS_NAME:
        return True
    header_roots = (
        "republica", "colombia", "colombla", "colomla", "columbia",
        "identificac", "identif", "hdentif", "identid", "personal",
        "cedul", "ciudadan", "ciudadam",
        "tarjet", "tarje", "contrasen", "comprobant", "primeravez",
        "registrad", "nacional", "expedic", "nacimient", "expirac",
        "estatur", "huella", "indicederech", "registrador", "delegado",
        "duplicado", "vigencia", "nacionaid", "naconalid", "scannedwith",
        "camscanner", "poweredby"
    )
    for root in header_roots:
        if root in norm:
            return True
    return False


def is_blacklisted_line(line: str) -> bool:
    """Verifica si la línea contiene palabras reservadas o encabezados que no pueden ser nombres."""
    if is_header_or_stopword(line):
        return True
    norm = normalize_for_match(line)
    words = norm.split()
    if not words:
        return True
    stop_count = sum(1 for w in words if is_header_or_stopword(w))
    if stop_count / len(words) >= 0.4:
        return True
    return False


def _line_looks_like_label(line: str, variants: List[str]) -> bool:
    """Devuelve True si la línea es (o contiene) una etiqueta de campo según variantes conocidas."""
    norm = normalize_for_match(line)
    return any(v in norm for v in variants)


def clean_name_line(line: str) -> Optional[str]:
    """
    Limpia y valida una línea candidata a ser nombre o apellido.
    Elimina ruido OCR, números, caracteres especiales y filtra stop words.
    """
    if not line:
        return None

    # Filtrar líneas que son claramente ruido OCR o encabezados
    if is_likely_noise(line) or is_header_or_stopword(line):
        return None

    # Si la línea normalizada es exactamente una variante de etiqueta (ej: "APELLDOS", "NOMBRES"), descartar
    norm_line = normalize_for_match(line)
    if any(norm_line == v or norm_line.replace(" ", "") == v.replace(" ", "") for v in (APELLIDOS_VARIANTS + NOMBRES_VARIANTS)):
        return None

    # Quitar posibles prefijos de etiqueta pegados al valor
    cleaned = re.sub(
        r"(?i)^(apellidos?|nombres?|apellido|nombre)[:\s\-]+", "", line
    ).strip()

    # Aplicar correcciones OCR
    cleaned = fix_ocr_name(cleaned)

    # Eliminar números y caracteres no alfabéticos
    cleaned = re.sub(r"[^A-ZÁÉÍÓÚÑa-záéíóúñ\s]", " ", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)

    if len(cleaned) < 2:
        return None

    # Intentar separar palabras que quedaron concatenadas por el OCR
    words_raw = cleaned.split()
    words_expanded = []
    for w in words_raw:
        if len(w) > 7 and w.isupper() and not is_header_or_stopword(w):
            words_expanded.append(split_concatenated_name(w))
        else:
            words_expanded.append(w)
    cleaned = " ".join(words_expanded)

    # Quitar palabras sueltas que sean stop words o muy cortas (1 carácter)
    words = [
        w.strip(".-,;:_!¡?¿/") for w in cleaned.split()
        if len(w) > 1 and not is_header_or_stopword(w)
    ]
    if not words:
        return None

    # Si todas las palabras son preposiciones comunes ("de", "del", "la", etc.), descartar
    pure_preps = {"DE", "DEL", "LA", "LAS", "LOS", "SAN", "SANTA", "COL"}
    if all(w.upper() in pure_preps for w in words):
        return None

    # Requiere al menos 2 caracteres por palabra y al menos 1 palabra
    result = " ".join(words).upper()

    # Verificar que luzca como un nombre (mayoría de letras)
    alpha_ratio = sum(c.isalpha() for c in result) / max(1, len(result.replace(" ", "")))
    if alpha_ratio < 0.85:
        return None

    # Rechazar tokens individuales que parezcan ruido OCR puro (5+ consonantes puras consecutivas)
    final_words = result.split()
    clean_words = []
    for w in final_words:
        consonant_runs = re.findall(r"[BCDFGJKLMNPQRSTVWXZ]{5,}", w)
        if consonant_runs and len(w) > 5:
            continue
        clean_words.append(w)

    if not clean_words:
        return None

    result = " ".join(clean_words)
    if len(result) < 2:
        return None

    return result

# Variaciones de etiquetas que el OCR puede producir para "APELLIDOS" y "NOMBRES"
APELLIDOS_VARIANTS = [
    "apellidos", "apellido", "apell1dos", "apeli", "ape11idos", "apelidos",
    "ape11ido", "apell", "apellldos", "apeilidos", "apeliidos",
    # Variantes adicionales detectadas en PDF real
    "apelludos", "apell|dos", "apelldos", "ap3llidos", "ape1lidos",
    "apellrdos", "apel1idos", "apellldos", "ap\u20acllidos", "apelilidos",
    "apellidos", "apeliidos", "apell!dos", "apelliocs", "apeliocs", "apellocs", "apelocs",
    # Variantes donde se pierde la 'A' inicial (el OCR ve PELLIDOS, PELLDOS, etc.)
    "pelldos", "pellidos", "pelidos", "pellldos", "pelldo", "pellods", "pellds", "apeldos",
    # Variantes extremas de OCR de la etiqueta completa (TI / Contraseñas)
    "apelltdos", "apellidqs", "apellidds", "apell1d0s", "apell|d0s",
    "apllidos", "apellidosnous", "arellidosinous", "arclldosn", "arclldos", "arcados",
    "spellidos", "spelldos", "arclldo", "apcllidosinous",
]
NOMBRES_VARIANTS = [
    "nombres", "nombre", "n0mbres", "nornbres", "nombr3s", "nornbre",
    "nom8res", "nomb", "nonbres", "nomhres", "n0mbre",
    # Variantes adicionales detectadas en PDF real
    "nomgres", "nomores", "nomdres", "nom8r3s", "n0mores",
    "nomhre", "nornbr3s", "nomres", "n0mbr3s", "nombeas",
    "nonbre", "nomhres", "korahes", "korahe", "koranes", "korane",
    "ndneaes", "nclicnes", "nlaierfs", "nlalderfs", "clicnes",
]


MESES = {
    "ene": "01", "enero": "01", "jan": "01", "january": "01",
    "feb": "02", "febrero": "02", "february": "02",
    "mar": "03", "marzo": "03", "march": "03",
    "abr": "04", "abril": "04", "apr": "04", "april": "04",
    "may": "05", "mayo": "05",
    "jun": "06", "junio": "06", "june": "06",
    "jul": "07", "julio": "07", "july": "07",
    "ago": "08", "agosto": "08", "aug": "08", "august": "08",
    "sep": "09", "septiembre": "09", "set": "09", "setiembre": "09", "sept": "09", "september": "09",
    "oct": "10", "octubre": "10", "october": "10",
    "nov": "11", "noviembre": "11", "november": "11",
    "dic": "12", "diciembre": "12", "dec": "12", "december": "12",
}

# Fechas con meses textuales (ej: 26-JUN-1986, 07 JUN 1986, 26 DE JUNIO DE 1986)
DATE_TEXT_RE = re.compile(
    r"(\b\d{1,2})\s*(?:DE|\-|\/|\.|\s)?\s*([A-Za-zÁÉÍÓÚáéíóú]{3,10})\s*(?:DE|\-|\/|\.|\s)?\s*(\d{2,4})\b",
    re.IGNORECASE
)

# Fechas numéricas (ej: 26/06/1986, 26-06-1986, 26.06.1986, 1986-06-26)
DATE_NUM_RE = re.compile(
    r"\b(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{2,4})\b"
)
DATE_ISO_RE = re.compile(
    r"\b(\d{4})[\/\-\.](\d{1,2})[\/\-\.](\d{1,2})\b"
)


def normalize_date(raw: str) -> Optional[str]:
    """
    Convierte cualquier formato de fecha reconocido (texto o numérico) a 'YYYY-MM-DD' (ISO).
    Soporta: '26-JUN-1986', '07 JUN 1986', '26/06/1986', '26-06-1986', '26 DE JUNIO DE 1986', etc.
    """
    if not raw:
        return None

    cleaned = raw.replace("\n", " ").strip()

    # 1. Intentar fecha con mes en texto
    m_text = DATE_TEXT_RE.search(cleaned)
    if m_text:
        day_s, mon_s, year_s = m_text.groups()
        mon_key = strip_accents(mon_s.strip().lower())
        # Buscar en diccionario de meses por prefijo o match exacto
        month = MESES.get(mon_key) or MESES.get(mon_key[:3])
        if month:
            day = int(day_s)
            year = int(year_s)
            if year < 100:
                year = 1900 + year if year > 30 else 2000 + year
            if 1 <= day <= 31 and 1900 <= year <= 2100:
                return f"{year:04d}-{month}-{day:02d}"

    # 2. Intentar formato ISO: AAAA-MM-DD
    m_iso = DATE_ISO_RE.search(cleaned)
    if m_iso:
        year, month, day = map(int, m_iso.groups())
        if 1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"

    # 3. Intentar formato numérico estándar: DD/MM/AAAA
    m_num = DATE_NUM_RE.search(cleaned)
    if m_num:
        part1, part2, year = map(int, m_num.groups())
        if year < 100:
            year = 1900 + year if year > 30 else 2000 + year
        if 1900 <= year <= 2100:
            # Asumir DD/MM/AAAA
            if 1 <= part1 <= 31 and 1 <= part2 <= 12:
                return f"{year:04d}-{part2:02d}-{part1:02d}"
            # Ocasional MM/DD/AAAA
            if 1 <= part1 <= 12 and 1 <= part2 <= 31:
                return f"{year:04d}-{part1:02d}-{part2:02d}"

    return None


DOC_NUMBER_RE = re.compile(r"\b(\d{1,3}(?:[.:]\d{3}){1,4}|\d{6,12})\b")


def clean_document_number(raw: str) -> Optional[str]:
    """
    Limpia y valida un número de cédula/TI o contraseña (6 a 12 dígitos).
    Maneja variaciones OCR con dos puntos o comas (ej: 1:117.513.499),
    y remueve prefijos pegados de etiquetas oficiales (NUMER0, UMO, NUIP, etc.).
    """
    if not raw:
        return None

    # Quitar prefijos de etiquetas conocidos que puedan estar pegados al número
    cleaned = re.sub(
        r"(?i)^(?:numero|número|numer0|numr0|numo|num|nuip|umo|nro|no\.?|tarjeta|cedula|primeravezcc|cc|ti)[:\s\-\.]*",
        "",
        raw.strip()
    )

    # Normalizar dos puntos o comas entre dígitos a puntos (ej: 1:117.513.499 -> 1.117.513.499)
    cleaned = re.sub(r"(\d)[:\,](\d)", r"\1.\2", cleaned)

    m = DOC_NUMBER_RE.search(cleaned)
    if m:
        digits = re.sub(r"[^\d]", "", m.group(1))
        if 6 <= len(digits) <= 12:
            return digits

    digits = re.sub(r"[^\d]", "", cleaned)
    if 6 <= len(digits) <= 12:
        return digits

    return None


# Correcciones de caracteres que el OCR confunde frecuentemente en nombres.
# NOTA: Solo se aplican substituciones SEGURAS (contexto inequívoco).
# Substituciones ambiguas como 0->O en medio de palabra o 1->Z se omiten
# porque son imposibles de distinguir sin diccionario.
OCR_CHAR_FIXES = [
    # | -> I: la barra vertical casi nunca aparece en nombres, siempre es I
    (r"\|", "I"),
    # 0 al INICIO de palabra seguido de 3+ letras -> O  (ej: 0SPINA -> OSPINA)
    (r"\b0(?=[A-ZÁÉÍÓÚÑa-záéíóúñ]{3})", "O"),
    # l minúscula entre DOS letras mayúsculas -> L  (ej: APELlDOS -> APELLDOS)
    (r"(?<=[A-ZÁÉÍÓÚÑ])l(?=[A-ZÁÉÍÓÚÑ])", "L"),
    # VV -> W
    (r"\bVV\b", "W"),
    # ! -> I (signo exclamación confundido con I)
    (r"!", "I"),
    # Quitar guiones medios aislados que no son separadores de nombre
    (r"\s-\s", " "),
    # 1 al inicio de palabra seguido de 3+ letras -> I (ej: 1BAÑEZ -> IBAÑEZ)
    (r"\b1(?=[A-ZÁÉÍÓÚÑa-záéíóúñ]{3})", "I"),
    # @ -> A en nombres
    (r"@", "A"),
    # $ -> S en nombres
    (r"\$", "S"),
    # 3 -> E al inicio o fin de palabra en nombre (ej: 3SPINOZA -> ESPINOZA)
    (r"\b3(?=[A-ZÁÉÍÓÚÑa-záéíóúñ]{3})", "E"),
    # Quitar caracteres extraños al inicio/fin de nombres
    (r"^[^A-ZÁÉÍÓÚÑa-záéíóúñ]+", ""),
]


def fix_ocr_name(text: str) -> str:
    """Aplica correcciones de caracteres OCR seguras en nombres propios."""
    for pattern, replacement in OCR_CHAR_FIXES:
        text = re.sub(pattern, replacement, text)
    return text

@dataclass
class ParsedFields:
    numero_documento: Optional[str] = None
    apellidos: Optional[str] = None
    nombres: Optional[str] = None
    fecha_nacimiento: Optional[str] = None       # ISO yyyy-mm-dd
    lugar_nacimiento: Optional[str] = None
    sexo: Optional[str] = None
    estatura: Optional[str] = None
    rh: Optional[str] = None
    fecha_expedicion: Optional[str] = None
    lugar_expedicion: Optional[str] = None
    fecha_expiracion: Optional[str] = None
    nacionalidad: Optional[str] = None
    fuente: Optional[str] = None  # "frente_clasico" | "reverso_clasico" | "frente_nuevo" | "mrz"

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def nombre_completo(self) -> Optional[str]:
        parts = [p for p in [self.nombres, self.apellidos] if p]
        return " ".join(parts).strip() if parts else None

    def merge(self, other: "ParsedFields") -> "ParsedFields":
        """Combina dos ParsedFields sin sobreescribir valores existentes.
        
        Para numero_documento: usa el numero mas largo (mas digitos = mas confiable).
        Para nombres/apellidos: usa el primer valor encontrado.
        """
        merged = ParsedFields(**self.to_dict())
        for key, value in other.to_dict().items():
            if key == "fuente":
                continue
            if not value:
                continue
            current = getattr(merged, key)
            if not current:
                # Sin valor actual -> usar el nuevo
                setattr(merged, key, value)
            elif key == "numero_documento":
                # Para numero de documento: preferir el mas largo (mas digitos)
                import re as _re
                digits_current = _re.sub(r"[^\d]", "", str(current))
                digits_new = _re.sub(r"[^\d]", "", str(value))
                if len(digits_new) > len(digits_current):
                    setattr(merged, key, value)
            # Para otros campos: mantener el primero (ya establecido)
        fuentes = {f for f in [self.fuente, other.fuente] if f}
        merged.fuente = "+".join(sorted(fuentes)) if fuentes else None
        return merged


# --------------------------------------------------------------------------- #
# Heurísticas de búsqueda de campos
# --------------------------------------------------------------------------- #

def _find_label_indices(lines: List[str], *keywords: str) -> List[int]:
    matches = []
    for i, line in enumerate(lines):
        norm = normalize_for_match(line)
        for kw in keywords:
            if kw in norm.split():
                matches.append(i)
                break
    return matches


def _find_first_label_index(lines: List[str], *keywords: str) -> Optional[int]:
    matches = _find_label_indices(lines, *keywords)
    return matches[0] if matches else None


def _get_candidate_name_near(
    lines: List[str], idx: int, offsets: List[int], exclude: Optional[str] = None
) -> Optional[str]:
    """
    Busca en una lista ordenada de offsets el primer valor que sea un nombre válido.
    Además intenta concatenar líneas contiguas si el nombre está partido en dos líneas.
    Ignora valores que coincidan con `exclude` (ej: no tomar apellidos como nombres).
    """
    for offset in offsets:
        j = idx + offset
        if 0 <= j < len(lines):
            cand = clean_name_line(lines[j])
            if cand:
                # Si coincide con el valor excluido, ignorar
                if exclude and (cand == exclude or cand in exclude or exclude in cand):
                    continue
                combined = cand
                combined_words = combined.split()
                if len(combined_words) <= 1 or combined_words[-1] in ("DE", "DEL", "LA", "LAS", "LOS", "SAN", "SANTA"):
                    for look_ahead in [1]:
                        k = j + look_ahead
                        if k >= len(lines):
                            break
                        # No combinar si la siguiente línea es una etiqueta
                        if _line_looks_like_label(lines[k], APELLIDOS_VARIANTS + NOMBRES_VARIANTS):
                            break
                        next_cand = clean_name_line(lines[k])
                        if next_cand and (not exclude or (next_cand != exclude and next_cand not in exclude)):
                            next_words = next_cand.split()
                            if len(next_words) <= 2 and not any(
                                nw in combined_words for nw in next_words
                            ):
                                combined = f"{combined} {next_cand}"
                        break
                return combined
    return None


def _find_label_fuzzy(lines: List[str], variants: List[str]) -> Optional[int]:
    """
    Búsqueda tolerante de etiquetas: acepta variaciones de OCR y coincidencias parciales.
    Devuelve el índice de la primera línea que coincide.
    """
    for i, line in enumerate(lines):
        norm = normalize_for_match(line)
        for variant in variants:
            if variant in norm:
                return i
        # Tolerancia adicional: línea que contiene solo la etiqueta con pequeño error
        # (distancia de edición = 1 carácter cambiado en la etiqueta principal)
        # IMPORTANTE: la linea debe tener longitud comparable a la variante
        # para evitar falsos positivos (ej: "O+" no debe matchear "apellidos")
        for variant in variants[:3]:  # Solo las variantes más comunes
            min_len = max(4, len(variant) - 3)  # minimo 60% del largo de la variante
            if min_len <= len(norm) <= len(variant) + 4 and sum(
                a != b for a, b in zip(norm[:len(variant)], variant)
            ) <= 1:
                return i
    return None


# --------------------------------------------------------------------------- #
# Parseo: Frente Formato Clásico (Amarillo)
# --------------------------------------------------------------------------- #

def parse_classic_front(lines: List[str]) -> ParsedFields:
    pf = ParsedFields(fuente="frente_clasico")

    # 1. Cédula / Número / TI / Contraseña
    idx_num = _find_first_label_index(
        lines,
        "numero", "número", "numer0", "numr0", "num", "nuip",
        "tarjeta", "identidad", "contrasena", "primeravezcc",
        "primeravez", "comprobante"
    )
    if idx_num is not None:
        doc_num = clean_document_number(lines[idx_num])
        if doc_num:
            pf.numero_documento = doc_num
        else:
            # Buscar en las 3 líneas siguientes
            for j in range(idx_num + 1, min(idx_num + 4, len(lines))):
                doc_num = clean_document_number(lines[j])
                if doc_num:
                    pf.numero_documento = doc_num
                    break

    # Si no se encontró por etiqueta, buscar en las primeras 6 líneas
    if not pf.numero_documento:
        for line in lines[:6]:
            doc_num = clean_document_number(line)
            if doc_num:
                pf.numero_documento = doc_num
                break

    # Búsqueda general en cualquier línea
    if not pf.numero_documento:
        for line in lines:
            doc_num = clean_document_number(line)
            if doc_num:
                pf.numero_documento = doc_num
                break

    # 2. Apellidos — búsqueda con variantes OCR tolerantes
    idx_apell = _find_label_fuzzy(lines, APELLIDOS_VARIANTS)
    if idx_apell is not None:
        # Intentar extraer desde la misma línea solo si contiene más que la etiqueta
        same_line = lines[idx_apell]
        for v in APELLIDOS_VARIANTS:
            same_line = re.sub(rf"(?i)\b{re.escape(v)}\b", "", same_line).strip()
        same_line_val = clean_name_line(same_line) if same_line else None
        if same_line_val and len(same_line_val.split()) >= 1:
            pf.apellidos = same_line_val
        else:
            # Buscar hasta 4 líneas alrededor (cédulas físicas los tienen arriba o abajo)
            pf.apellidos = _get_candidate_name_near(lines, idx_apell, [-1, 1, -2, 2, -3, 3])

    # 3. Nombres — búsqueda con variantes OCR tolerantes
    idx_nomb = _find_label_fuzzy(lines, NOMBRES_VARIANTS)
    if idx_nomb is not None:
        same_line = lines[idx_nomb]
        for v in NOMBRES_VARIANTS:
            same_line = re.sub(rf"(?i)\b{re.escape(v)}\b", "", same_line).strip()
        same_line_val = clean_name_line(same_line) if same_line else None
        if same_line_val and len(same_line_val.split()) >= 1 and same_line_val != pf.apellidos:
            pf.nombres = same_line_val
        else:
            pf.nombres = _get_candidate_name_near(lines, idx_nomb, [-1, 1, -2, 2, -3, 3], exclude=pf.apellidos)

    # 4. Fallback: si no se encontraron por etiqueta, buscar bloques de texto
    #    en mayúsculas que no sean stop words (posición típica en cédula clásica)
    if not pf.apellidos or not pf.nombres:
        name_candidates = []
        for line in lines:
            cand = clean_name_line(line)
            if cand and len(cand.split()) >= 1 and cand not in name_candidates:
                # Descartar si contiene dígitos o es muy corta
                if len(cand) >= 3 and not any(c.isdigit() for c in cand):
                    name_candidates.append(cand)
        # Asignar los primeros candidatos que no sean ya el número de cédula
        usable = [
            c for c in name_candidates
            if not clean_document_number(c)  # no es un número
        ]
        if not pf.apellidos and len(usable) >= 1:
            pf.apellidos = usable[0]
        if not pf.nombres and len(usable) >= 2:
            pf.nombres = usable[1]

    # Evitar colisión: nombres y apellidos no pueden ser iguales
    if pf.apellidos and pf.nombres and pf.apellidos == pf.nombres:
        pf.nombres = None

    # 5. Fecha de nacimiento en frente / contraseña / TI
    idx_fnac = _find_first_label_index(lines, "nacimiento", "nacveheo", "nacmento", "nacmiento")
    if idx_fnac is not None:
        window = " ".join(lines[idx_fnac: min(len(lines), idx_fnac + 3)])
        pf.fecha_nacimiento = normalize_date(window)
    if not pf.fecha_nacimiento:
        for line in lines:
            d = normalize_date(line)
            if d:
                yr = int(d.split("-")[0])
                if 1930 <= yr <= 2018:
                    pf.fecha_nacimiento = d
                    break

    return pf


# --------------------------------------------------------------------------- #
# Parseo: Reverso Formato Clásico (Amarillo)
# --------------------------------------------------------------------------- #

def parse_classic_back(lines: List[str]) -> ParsedFields:
    pf = ParsedFields(fuente="reverso_clasico")
    full_text = "\n".join(lines)

    # 1. Fecha de Nacimiento
    idx_fnac = _find_first_label_index(lines, "nacimiento")
    if idx_fnac is not None:
        # Probar misma línea y ventana alrededor
        window = " ".join(lines[max(0, idx_fnac - 1): min(len(lines), idx_fnac + 3)])
        pf.fecha_nacimiento = normalize_date(window)

    if not pf.fecha_nacimiento:
        # Búsqueda solo si la línea no contiene "expedicion" o "expiracion"
        for line in lines:
            norm_l = normalize_for_match(line)
            if "expedic" in norm_l or "expirac" in norm_l or "vencim" in norm_l:
                continue
            d = normalize_date(line)
            if d:
                yr = int(d.split("-")[0])
                if 1930 <= yr <= 2018:
                    pf.fecha_nacimiento = d
                    break

    # 2. Lugar de Nacimiento
    idx_lnac = _find_first_label_index(lines, "lugar")
    if idx_lnac is not None:
        for offset in (-1, -2, 1, 2):
            j = idx_lnac + offset
            if 0 <= j < len(lines):
                cand = clean_name_line(lines[j].replace("(", "").replace(")", ""))
                if cand and len(cand) >= 3:
                    pf.lugar_nacimiento = lines[j].strip()
                    break

    # 3. Datos combinados: Estatura, RH, Sexo
    # Excluir línea de código de barras
    non_barcode_lines = [l for l in lines if not re.match(r"^[A-Z]-\d{6,}-\d+-[MF]-\d+-\d{8}", l.strip())]
    non_barcode_text = " \n ".join(non_barcode_lines)

    # Coincidencia combinada típica: "1.72 O+ M" o "1.72  A+  F"
    m_combo = re.search(r"(\d\.\d{2})\s+([ABO]{1,2}\s?[+\-])\s+([MF])\b", non_barcode_text)
    if m_combo:
        pf.estatura = m_combo.group(1)
        pf.rh = m_combo.group(2).replace(" ", "")
        pf.sexo = m_combo.group(3).upper()
    else:
        # Intento 2: RH y estatura en la misma línea, sexo en línea aparte o al final
        m_combo2 = re.search(
            r"([12]\.\d{2}).*?([ABO]{1,2}\s?[+\-]).*?\b([MF])\b",
            non_barcode_text, re.DOTALL
        )
        if m_combo2:
            pf.estatura = m_combo2.group(1)
            pf.rh = m_combo2.group(2).replace(" ", "")
            pf.sexo = m_combo2.group(3).upper()
        else:
            m_rh = re.search(r"\b([ABO]{1,2}\s?[+\-])\b", non_barcode_text)
            if m_rh:
                pf.rh = m_rh.group(1).replace(" ", "")

            m_estatura = re.search(r"\b([12]\.\d{2})\b", non_barcode_text)
            if m_estatura:
                pf.estatura = m_estatura.group(1)

            # Buscar sexo: M o F solos en una línea, o después de SEXO
            m_sexo = re.search(r"^\s*([MF])\s*$", non_barcode_text, re.MULTILINE | re.IGNORECASE)
            if m_sexo:
                pf.sexo = m_sexo.group(1).upper()
            else:
                m_sexo2 = re.search(r"\b([MF])\b\s*(?:SEXO|\n|$)", non_barcode_text, re.IGNORECASE)
                if m_sexo2:
                    pf.sexo = m_sexo2.group(1).upper()
                else:
                    m_sexo_kw = re.search(r"SEXO[:\s\-]*([MF])\b", non_barcode_text, re.IGNORECASE)
                    if m_sexo_kw:
                        pf.sexo = m_sexo_kw.group(1).upper()
                    else:
                        # Última opción: buscar G.S.RH línea y la línea que le sigue
                        m_gsrh = re.search(r"G\.S\.RH.*?\n.*?([MF])\b", non_barcode_text, re.IGNORECASE)
                        if m_gsrh:
                            pf.sexo = m_gsrh.group(1).upper()

    # 4. Fecha y lugar de expedición
    idx_exp = _find_first_label_index(lines, "expedicion", "expedición")
    if idx_exp is not None:
        window = " ".join(lines[max(0, idx_exp - 2): min(len(lines), idx_exp + 2)])
        d_exp = normalize_date(window)
        if d_exp and d_exp != pf.fecha_nacimiento:
            pf.fecha_expedicion = d_exp

    # 5. Código de barras posterior: A-4400100-00384749-M-1117489876-20120628
    m_barcode = re.search(r"[A-Z]-\d+-\d+-[MF]-(\d{6,12})-(\d{8})", full_text)
    if m_barcode:
        pf.numero_documento = pf.numero_documento or m_barcode.group(1)
        exp = m_barcode.group(2)
        if not pf.fecha_expedicion:
            pf.fecha_expedicion = f"{exp[0:4]}-{exp[4:6]}-{exp[6:8]}"

    return pf


# --------------------------------------------------------------------------- #
# Parseo: Frente Formato Nuevo (Digital / Policarbonato)
# --------------------------------------------------------------------------- #

def parse_new_format_front(lines: List[str]) -> ParsedFields:
    pf = ParsedFields(fuente="frente_nuevo")

    # 1. NUIP / Cedula - la linea puede ser "NUIP1.127.070.363" (pegado) o separado
    idx_nuip = None
    for i, line in enumerate(lines):
        norm = normalize_for_match(line)
        if "nuip" in norm:
            idx_nuip = i
            break

    if idx_nuip is not None:
        # Extraer el numero de la misma linea (puede venir pegado al "NUIP")
        # Quitar "NUIP" y quedarse con el numero
        # NUIP puede estar pegado al numero: NUIP1.003.806.124
        # Quitar solo la palabra NUIP dejando el numero completo
        # NUIP puede estar pegado al numero: "NUIP1.003.806.124"
        # Solo quitar "NUIP" y preservar el numero completo que viene despues
        raw_line = lines[idx_nuip]
        # Extraer directamente el numero del documento de la linea completa
        # Buscar patron de numero de cedula (con o sin puntos)
        nums_in_line = re.findall(r"[\d.]+", raw_line)
        # Tomar el numero mas largo (ignorar numeros de 1-2 digitos)
        candidate_nums = [n for n in nums_in_line if len(re.sub(r"[^\d]", "", n)) >= 6]
        line_clean = candidate_nums[0] if candidate_nums else ""
        pf.numero_documento = clean_document_number(line_clean)
        if not pf.numero_documento:
            # Buscar en las lineas siguientes
            for j in range(idx_nuip + 1, min(idx_nuip + 3, len(lines))):
                doc_num = clean_document_number(lines[j])
                if doc_num:
                    pf.numero_documento = doc_num
                    break

    # 2. Apellidos - busqueda tolerante (en cedula digital el label va ANTES del valor)
    idx_apell = _find_label_fuzzy(lines, APELLIDOS_VARIANTS)
    if idx_apell is not None:
        # En cedula digital: label encima, valor abajo
        pf.apellidos = _get_candidate_name_near(lines, idx_apell, [1, 2, -1, 3, -2])

    # 3. Nombres - busqueda tolerante
    idx_nomb = _find_label_fuzzy(lines, NOMBRES_VARIANTS)
    if idx_nomb is not None:
        # En cedula digital: label encima, valor abajo
        cand = _get_candidate_name_near(lines, idx_nomb, [1, 2, -1, 3, -2], exclude=pf.apellidos)
        if cand:
            pf.nombres = cand
        # Si no se encontraron apellidos por etiqueta, intentar usar la linea ANTES del label Nombres
        if not pf.apellidos and idx_nomb is not None:
            # La linea inmediatamente anterior al label "Nombres" suele ser los apellidos
            # en cedulas donde el OCR no capturo el label "Apellidos"
            for offset in [-1, -2, -3]:
                j = idx_nomb + offset
                if 0 <= j < len(lines):
                    cand_ap = clean_name_line(lines[j])
                    if cand_ap and cand_ap != pf.nombres:
                        pf.apellidos = cand_ap
                        break

    # 4. Nacionalidad
    idx_nac = _find_first_label_index(lines, "nacionalidad")
    if idx_nac is not None:
        pf.nacionalidad = "COL"

    # 5. Fecha de nacimiento
    idx_fnac = _find_first_label_index(lines, "nacimiento")
    if idx_fnac is not None:
        window = " ".join(lines[idx_fnac: min(len(lines), idx_fnac + 3)])
        pf.fecha_nacimiento = normalize_date(window)

    # 6. Lugar de nacimiento
    idx_lnac = _find_first_label_index(lines, "lugar")
    if idx_lnac is not None:
        cand = _get_candidate_name_near(lines, idx_lnac, [1, 2])
        if cand:
            pf.lugar_nacimiento = cand

    # 7. Fecha de expedicion y expiracion
    idx_exp = _find_first_label_index(lines, "expedicion", "expedicion")
    if idx_exp is not None:
        window = " ".join(lines[idx_exp: min(len(lines), idx_exp + 3)])
        pf.fecha_expedicion = normalize_date(window)

    idx_expi = _find_first_label_index(lines, "expiracion", "expiracion", "vencimiento")
    if idx_expi is not None:
        window = " ".join(lines[idx_expi: min(len(lines), idx_expi + 3)])
        pf.fecha_expiracion = normalize_date(window)

    # 8. Estatura, RH y Sexo
    full_text = "\n".join(lines)
    for line in lines:
        m_est = re.search(r"\b([12]\.\d{2})\b", line)
        if m_est and not pf.estatura:
            pf.estatura = m_est.group(1)

        m_sex = re.search(r"\bSexo[:\s\-]*([MF])\b", line, re.IGNORECASE)
        if m_sex and not pf.sexo:
            pf.sexo = m_sex.group(1).upper()

    # RH tambien puede aparecer en la cedula digital
    m_rh = re.search(r"\b([ABO]{1,2}\s?[+\-])\b", full_text)
    if m_rh and not pf.rh:
        pf.rh = m_rh.group(1).replace(" ", "")

    return pf



# --------------------------------------------------------------------------- #
# Parseo: MRZ (Machine Readable Zone - Reverso Nuevo)
# --------------------------------------------------------------------------- #

MRZ_LINE2_RE = re.compile(
    r"(\d{6})\d([MF<])(\d{6})\d([A-Z0-9]{3})(\d{6,12})"
)


def parse_mrz(lines: List[str]) -> Optional[ParsedFields]:
    """Parsea el bloque MRZ ICAO TD1 del reverso de cédulas digitales."""
    # Buscar en lineas individuales primero (mas preciso)
    joined = "".join(l.strip().upper().replace(" ", "") for l in lines)
    # Normalizar 0 -> O en contexto de paises (C0L -> COL)
    joined_norm = re.sub(r"C0L", "COL", joined)

    m2 = MRZ_LINE2_RE.search(joined_norm)
    if not m2:
        m2 = MRZ_LINE2_RE.search(joined)
    if not m2:
        return None

    birth, sex, expiry, nat, docnum = m2.groups()
    pf = ParsedFields(fuente="mrz")
    pf.numero_documento = docnum  # Preservar numero exacto del MRZ
    pf.sexo = None if sex == "<" else sex
    pf.nacionalidad = nat

    # Fecha de nacimiento (AAMMDD)
    try:
        yy, mm, dd = int(birth[0:2]), int(birth[2:4]), int(birth[4:6])
        year_full = 1900 + yy if yy > 30 else 2000 + yy
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            pf.fecha_nacimiento = f"{year_full:04d}-{mm:02d}-{dd:02d}"
    except Exception:
        pass

    # Fecha de expiración (AAMMDD)
    try:
        yy, mm, dd = int(expiry[0:2]), int(expiry[2:4]), int(expiry[4:6])
        year_full = 2000 + yy
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            pf.fecha_expiracion = f"{year_full:04d}-{mm:02d}-{dd:02d}"
    except Exception:
        pass

    # Nombres y Apellidos en MRZ (Linea 3: APELLIDOS<<NOMBRES)
    # IMPORTANTE: Buscar en lineas INDIVIDUALES para no mezclar con texto de CamScanner
    # Una linea de nombres MRZ tiene formato: APELLIDO<APELLIDO<<NOMBRE<NOMBRE<
    mrz_name_line = None
    for line in lines:
        line_up = line.strip().upper()
        # Reemplazar K por < para detectar lineas MRZ donde OCR confundio < con K
        line_norm = line_up.replace("K", "<")
        # Una linea MRZ de nombres debe tener << y ser mayormente letras y <
        if "<<" in line_norm:
            alpha_and_fill = sum(1 for c in line_norm if c.isalpha() or c == "<")
            if alpha_and_fill / max(1, len(line_norm)) > 0.85:
                mrz_name_line = line_norm
                break

    if mrz_name_line:
        parts = mrz_name_line.split("<<")
        if len(parts) >= 1:
            ape_raw = parts[0].replace("<", " ").strip()
            # Filtrar: solo palabras alfabeticas de longitud razonable
            ape_words = [w for w in ape_raw.split() if 2 <= len(w) <= 15 and w.isalpha()]
            if ape_words:
                pf.apellidos = " ".join(ape_words)
        if len(parts) >= 2:
            nom_raw = parts[1].replace("<", " ").strip()
            nom_words = [w for w in nom_raw.split() if 2 <= len(w) <= 12 and w.isalpha()]
            nom_clean = " ".join(nom_words[:3])  # max 3 palabras de nombre
            if len(nom_clean) >= 3:
                pf.nombres = nom_clean

    return pf


# --------------------------------------------------------------------------- #
# Punto de entrada principal
# --------------------------------------------------------------------------- #


def _parse_mrz_line(mrz_text: str) -> dict:
    """
    Extrae apellidos y nombres de una linea MRZ de cedula colombiana.
    Formato: APELLIDO<APELLIDO<<NOMBRE<NOMBRE<
    Ejemplo: ALDANA<BOHORQUEZ<<LIDA<YASMIN<
    """
    result = {"apellidos": None, "nombres": None}
    # Buscar linea tipo TD1/TD3 con apellidos y nombres separados por <<
    # La zona de nombres en MRZ colombiano usa < como separador
    m = re.search(
        r"([A-Z][A-Z<]{2,})<<([A-Z][A-Z<]+?)(?:<{2,}|$)",
        mrz_text.upper().replace("K", "")  # quitar K que a veces aparece extra
    )
    if not m:
        # Intentar con K como separador (variante de OCR)
        m = re.search(r"([A-Z][A-Z<K]{2,})<<([A-Z][A-Z<K]+?)(?:<<|K<|$)", mrz_text.upper())
    if m:
        ape_raw = m.group(1).replace("<", " ").strip()
        nom_raw = m.group(2).replace("<", " ").replace("K", "").strip()
        if ape_raw and len(ape_raw) >= 3:
            result["apellidos"] = ape_raw
        if nom_raw and len(nom_raw) >= 3:
            result["nombres"] = nom_raw
    return result


def parse_page(raw_text: str) -> ParsedFields:
    """Parsea el texto plano extraído de una página e intenta todos los formatos."""
    if not raw_text:
        return ParsedFields(fuente="sin_datos")

    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    norm_full = normalize_for_match(raw_text)

    results: List[ParsedFields] = []

    # 1. Cédula digital frente (prioritaria si tiene NUIP/nacionalidad)
    is_new = "nuip" in norm_full or ("nacionalidad" in norm_full and "col" in norm_full)
    if is_new:
        results.append(parse_new_format_front(lines))

    # 2. Cédula clásica frente / Tarjeta de Identidad / Contraseña / Documentos en trámite
    is_front = (
        "numero" in norm_full
        or "cedula" in norm_full
        or "ciudadania" in norm_full
        or "tarjeta" in norm_full
        or "identidad" in norm_full
        or "contrasena" in norm_full
        or "comprobante" in norm_full
        or "primeravezcc" in norm_full
        or "primeravez" in norm_full
        or "nuip" in norm_full
        or any(v in norm_full for v in ("apell", "nomb", "apel", "nom", "korahe"))
        or any(clean_document_number(l) is not None for l in lines[:6])
    )
    if is_front and not is_new:
        results.append(parse_classic_front(lines))

    # 3. Cédula clásica reverso
    if "nacimiento" in norm_full or "expedicion" in norm_full or re.search(r"[A-Z]-\d+-\d+-[MF]-\d+-\d{8}", raw_text):
        results.append(parse_classic_back(lines))

    # 4. Zona MRZ
    mrz = parse_mrz(lines)
    if mrz:
        results.append(mrz)

    # Intento de rescate si ningún parser fue activado
    if not results:
        front_res = parse_classic_front(lines)
        if any(v for k, v in front_res.to_dict().items() if k not in ("fuente",) and v):
            results.append(front_res)

    if not results:
        return ParsedFields(fuente="sin_datos")

    merged = results[0]
    for r in results[1:]:
        merged = merged.merge(r)

    return merged
