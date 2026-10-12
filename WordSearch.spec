from PyInstaller.utils.hooks import collect_submodules, collect_dynamic_libs, copy_metadata

hidden = collect_submodules('winrt')
binaries = collect_dynamic_libs('winrt')
metadata = []
for dependency in ['PySide6-Essentials', 'shiboken6', 'Pillow', 'httpx', 'httpcore', 'pywin32',
                   'anyio', 'h11', 'idna', 'certifi', 'winrt-runtime',
                   'winrt-Windows.Media.Ocr', 'winrt-Windows.Graphics.Imaging',
                   'winrt-Windows.Globalization', 'winrt-Windows.Storage.Streams',
                   'winrt-Windows.Foundation', 'winrt-Windows.Foundation.Collections',
                   'windows-capture', 'numpy', 'opencv-python']:
    metadata += copy_metadata(dependency)
a = Analysis(['main.py'], pathex=[], binaries=binaries,
             datas=[('README.md', '.'), ('THIRD_PARTY_NOTICES.md', '.'), ('docs', 'docs'),
                    ('assets/dictionary', 'assets/dictionary')] + metadata,
             hiddenimports=hidden, hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtDesigner',
                       'PySide6.QtUiTools', 'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets',
                       'PySide6.QtSql', 'PySide6.QtTest', 'tkinter', 'unittest'], noarchive=False)
# Resolve Windows ICU and API-set contracts from the operating system, rather
# than similarly named, incompatible DLLs supplied by other tools on PATH.
a.binaries = [entry for entry in a.binaries
              if entry[0].replace('\\', '/').split('/')[-1].lower() not in ('icuuc.dll', 'icuin.dll', 'icudt.dll')
              and not entry[0].replace('\\', '/').split('/')[-1].lower().startswith(('api-ms-win-', 'ext-ms-win-'))]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='WordSearch', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          disable_windowed_traceback=False, icon='assets/WordSearch.ico')
