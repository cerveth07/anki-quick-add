# Runtime fonts

The Windows build fetches pinned open-source fonts before PyInstaller runs:

- Source Han Sans SC 2.005R static OTFs: `SourceHanSansSC-Regular.otf` and `SourceHanSansSC-Medium.otf`, used for Chinese UI text.
- Noto Sans JP variable TTF: Noto Sans CJK `Sans2.004`, used for Japanese text.
- Inter variable font: official Inter 4.1 release archive, used for Latin text and numbers.

`tools/build_windows.ps1` downloads the pinned font binaries into this directory
and PyInstaller bundles them with the Windows package. The package therefore does
not depend on the target machine having these fonts installed.

Direct source runs without downloaded font files use the fallback chains defined
in `ui/theme.py`.

Font license texts are committed separately under `licenses/` and copied into
every published Windows package.
