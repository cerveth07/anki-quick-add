# Third-party notices

Anki Quick Add itself is licensed under the MIT License. The Windows package also contains third-party components under their own licenses. The complete license texts are committed in the repository under `licenses/` and are copied into every Windows package.

## PySide6 and Qt

The Windows package is built with **PySide6 6.9.2** and includes Qt shared libraries distributed with PySide6.

For this community/open-source distribution, these components are used under the **GNU Lesser General Public License version 3 (LGPL-3.0)** where applicable. The package includes both the LGPL-3.0 text and the GPL-3.0 text referenced by it.

Source code and upstream project information:

- Qt for Python / PySide: https://code.qt.io/cgit/pyside/pyside-setup.git/
- Qt source archives: https://download.qt.io/archive/qt/6.9/6.9.2/
- Qt licensing information: https://www.qt.io/licensing/open-source-lgpl-obligations

### Replacing the Qt/PySide6 libraries

The Windows package uses PyInstaller's **onedir** layout. Qt/PySide6 DLLs remain separate files under:

```text
AnkiQuickAdd/_internal/PySide6/
```

Users may replace compatible Qt/PySide6 shared libraries with modified, interface-compatible builds. This project does not impose a restriction on reverse engineering for the purpose of debugging modifications to LGPL-covered libraries.

The source and build script for Anki Quick Add are provided in the repository. A Windows package can be rebuilt with:

```powershell
python -m pip install -r requirements-build.txt
pwsh -NoProfile -File tools/build_windows.ps1
```

## Python

The Windows package includes a **Python 3.12** runtime. Python is distributed under the Python Software Foundation License and related notices contained in `licenses/PYTHON-3.12.txt`.

Upstream source: https://github.com/python/cpython/tree/3.12

## OpenSSL

The packaged Python runtime includes **OpenSSL 3.x** runtime libraries used by Python's TLS/SSL support. OpenSSL 3.x is distributed under the Apache License 2.0.

The license text is included as `licenses/OPENSSL-3.txt`.

Upstream source: https://github.com/openssl/openssl

## PyInstaller

Windows packages are produced with **PyInstaller 6.16.0**. The generated executable contains the PyInstaller bootloader. PyInstaller's license includes an exception permitting redistribution of bundled applications under the application's own license, subject to the terms in its license.

The complete text is included as `licenses/PYINSTALLER-6.16.0.txt`.

Upstream source: https://github.com/pyinstaller/pyinstaller

## Fonts

The Windows package includes the following fonts:

- **Source Han Sans SC 2.005R** — SIL Open Font License 1.1.
- **Noto Sans JP (Noto CJK Sans2.004)** — SIL Open Font License 1.1.
- **Inter 4.1** — SIL Open Font License 1.1.

Their license texts are included as:

```text
licenses/OFL-SourceHanSans.txt
licenses/OFL-Noto.txt
licenses/OFL-Inter.txt
```

This notice is informational and does not replace or modify the terms of any third-party license.
