from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class OcrDocumentOut(BaseModel):
    numero_documento: Optional[str]
    apellidos: Optional[str]
    nombres: Optional[str]
    nombre_completo: Optional[str]
    fecha_nacimiento: Optional[str]
    lugar_nacimiento: Optional[str]
    sexo: Optional[str]
    estatura: Optional[str]
    rh: Optional[str]
    fecha_expedicion: Optional[str]
    lugar_expedicion: Optional[str]
    fecha_expiracion: Optional[str]
    nacionalidad: Optional[str]
    fuente: Optional[str]
    paginas_pdf: List[int]


class CompareResponse(BaseModel):
    resumen: Dict[str, int]
    resultados: List[Dict[str, Any]]
    sobrantes_en_pdf: List[Dict[str, Any]]
    documentos_sin_numero_detectado: List[Dict[str, Any]]


class ExtractResponse(BaseModel):
    total_paginas: int
    total_documentos: int
    documentos: List[Dict[str, Any]]
