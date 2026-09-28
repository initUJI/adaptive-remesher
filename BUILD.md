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
Windows con Python 3.14. En macOS/Linux no se ha probado *manualmente*,
pero `.github/workflows/release.yml` incluye ahora un paso de "prueba de
humo" automatico que arranca el ejecutable recien construido en las tres
plataformas (con `QT_QPA_PLATFORM=offscreen`) y falla el build si el
proceso termina solo antes de unos segundos -- el fallo tipico si falta
una dependencia nativa o un import se rompe al empaquetar. No sustituye a
probar la app de verdad (no interactua con la ventana, solo confirma que
arranca y no revienta al importar), pero detecta el problema mas comun de
un build roto en un SO donde no hay forma de probarlo a mano.

## Notas

- El build es especifico de la plataforma en la que se ejecuta
  `pyinstaller`: para distribuir en Windows, macOS y Linux hace falta
  generar el ejecutable por separado en cada uno (no hay cross-compilation).
- `build/` y `dist/` estan en `.gitignore`: son artefactos generados, no
  se versionan.

## Releases automaticas (multi-SO)

`.github/workflows/release.yml` construye el ejecutable en Windows, macOS
y Linux y los adjunta como assets a una GitHub Release, al empujar un tag
`vX.Y.Z`:

```bash
git tag v0.2.0
git push origin v0.2.0
```

El build de Windows se ha verificado manualmente (ver mas arriba); los de
macOS/Linux pasan por la prueba de humo automatica del workflow, pero no
se han probado interactivamente -- revisa los logs de esa Action tras el
primer tag si algo falla alli.
