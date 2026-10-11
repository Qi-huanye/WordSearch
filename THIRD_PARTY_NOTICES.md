# 第三方组件

本程序使用以下开源依赖。分发版本包含 PyInstaller 收集的运行库；组件的原始许可证和版权声明适用。

- Python：https://www.python.org/ — Python Software Foundation License。
- Qt / PySide6 Essentials：https://www.qt.io/qt-for-python — LGPLv3 / GPLv3；本工具动态链接 Qt，不修改 Qt 源码。对应源码与许可：https://code.qt.io/ 与 https://doc.qt.io/qtforpython-6/licenses.html。
- Pillow：https://python-pillow.org/ — HPND。
- HTTPX：https://www.python-httpx.org/ — BSD-3-Clause。
- PyWinRT：https://github.com/pywinrt/pywinrt — MIT。
- pywin32：https://github.com/mhammond/pywin32 — Python Software Foundation License。
- PyInstaller：https://pyinstaller.org/ — GPL with bootloader exception。

Windows OCR 和 DPAPI 由操作系统提供，不随应用重新分发。该工具不附带《极乐迪斯科》的游戏素材、剧情数据库或任何 AI 服务凭据。

## 本地词典

ECDICT：https://github.com/skywind3000/ECDICT — MIT。随程序提供英汉双解数据的 SQLite 索引、完整 `ECDICT-LICENSE.txt` 和含上游版本及校验值的 `source.json`，位于 `assets/dictionary`。
