# Empaquetado (PyInstaller)

Genera un ejecutable de escritorio independiente, para poder repartir la
app a alguien sin Python instalado.

## Requisitos

```bash
pip install -r requirements-dev.txt
```

## Build

```bash
pyinstaller adaptive-remesher.spec
```

El resultado queda en `dist/adaptive-remesher/` (carpeta con
`adaptive-remesher.exe` en Windows, o el binario equivalente en
macOS/Linux, junto a sus dependencias). Ese directorio completo es lo que
hay que distribuir; el `.exe`/binario suelto no arranca sin el resto de
archivos.

`adaptive-remesher.spec` ya incluye lo necesario: PySide6, PyVista/VTK y
PyMeshLab se detectan solos via los hooks que trae PyInstaller (no hace
falta declarar hidden-imports ni datas a mano). Si tras actualizar alguna
dependencia el build deja de arrancar, suele bastar con volver a generar
el spec desde cero:

```bash
pyinstaller --windowed --name adaptive-remesher app.py
```

y comparar el `adaptive-remesher.spec` resultante con el que hay en el
repo (revisar el diff antes de sustituirlo).

## Verificar el build

```bash
./dist/adaptive-remesher/adaptive-remesher.exe
```

Comprobado que arranca sin argumentos ni configuracion adicional en
Windows con Python 3.14; en macOS/Linux no se ha probado (PyInstaller es
multiplataforma, pero cada SO tiene sus propias peculiaridades de firma/
notarizacion o de librerias del sistema que aqui no se han verificado).

## Notas

- El build es especifico de la plataforma en la que se ejecuta
  `pyinstaller`: para distribuir en Windows, macOS y Linux hace falta
  generar el ejecutable por separado en cada uno (no hay cross-compilation).
- `build/` y `dist/` estan en `.gitignore`: son artefactos generados, no
  se versionan.
