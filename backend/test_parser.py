"""Test comprehensivo del sistema OCR mejorado."""
import sys
sys.path.insert(0, '.')

from app.field_parser import parse_page

print('=== IMPORTACION OK ===')

tests = [
    ('Pagina 1 - PARRA HERNANDEZ', """REPUBLICA DE COLOMBIA
IDENTIFICACION PERSONAL
CEDULA DECIUDADANIA
NUMERO
1.117.489.876
PARRA HERNANDEZ
APELLIDOS
DIEGO ARMANDO
NOMBRES
FECHADENACIMIENTO
26-JUN-1986
1.71
B+
M
ESTATURA
G.S.RH
SEXO""",
     {'numero_documento': '1117489876', 'apellidos': 'PARRA HERNANDEZ', 'nombres': 'DIEGO ARMANDO', 'rh': 'B+', 'sexo': 'M'}),

    ('Pagina 3 - OYOLA (labels con errores)', """REPUBLICA DE COLOMBIA
IDENTIFICACIONPERSONAL
CEDULADECIUDADANIA
17711201
NUMERO
OYOLA SAB!
APELLUDOS
LEONEL
NOMGRES
11-MAR-1983
FECHADENACIMIENTO
1.66
B+
M
ESTATURA
G.S.RH
SEXO""",
     {'numero_documento': '17711201', 'apellidos': 'OYOLA', 'nombres': 'LEONEL', 'rh': 'B+', 'sexo': 'M'}),

    ('Pagina 6 - POSADA ALFONSO (ruido RETUELCAD)', """REPUBLICA DE COLOMBIA
IDENTIFICACIONPERSONAL
CEDULADECIUDADANIA
NUMERO
96.333.300
POSADA ALFONSO
APELLIDOS
RETUELCAD
RODRIGO
COLOMBIA
NOMORES
04-ABR-1982
FECHA DENACIMIENTO
1.68
B+
M
ESTATURA
G.S.RH
SEXO""",
     {'numero_documento': '96333300', 'apellidos': 'POSADA ALFONSO', 'nombres': 'RODRIGO', 'rh': 'B+', 'sexo': 'M'}),

    ('Pagina 9 - ANDRADE CADENA (digital)', """CEDULA DE
REPUBLICA DE COLOMBIA
CIUDADANIA
Apellidos
NUIP1.127.070.363
ANDRADE CADENA
Nombres
JHON JAMEZ
Nacionalidad
Estatura
Sexo
COL
1.66
M
Fecha de nacimiento
GS.
07JUN 1986
O-""",
     {'numero_documento': '1127070363', 'apellidos': 'ANDRADE CADENA', 'nombres': 'JHON JAMEZ', 'sexo': 'M'}),

    ('Pagina 31 - MURCIA ARTUNDUAGA (nombres concatenados)', """REPUBLICA DE COLOMBIA
IDENTIFICACION PERSONAL
CEDULA DE CIUDADANIA
003806124
NUMERO
MURCIAARTUNDUAGA
APELLIDOS
VANESSAALEXANDRA
NOMBRES
02-NOV-2001
FECHA DENACIMIENTO""",
     {'numero_documento': '3806124', 'apellidos': 'MURCIA ARTUNDUAGA', 'nombres': 'VANESSA ALEXANDRA'}),
]

all_ok = True
for name, text, expected in tests:
    result = parse_page(text)
    print(f'\n{name}:')
    for field, exp_val in expected.items():
        actual = getattr(result, field)
        ok = 'OK' if actual == exp_val else 'FALLO'
        if ok == 'FALLO':
            all_ok = False
        print(f'  {field}: {repr(actual)} (esperado: {repr(exp_val)}) [{ok}]')

if all_ok:
    print('\n===== RESULTADO: TODOS OK =====')
else:
    print('\n===== RESULTADO: HAY FALLOS =====')
