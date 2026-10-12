# WordSearch

Windows 游戏鼠标词典，适合《极乐迪斯科》。支持快捷键取词、ECDICT 离线英汉词义、AI 语境翻译与追问，可自选 API 地址、Key 和模型。

## 使用

从 [Releases](https://github.com/Qi-huanye/WordSearch/releases) 下载 `WordSearch-1.6.0-win64.exe`，双击即可运行，无需解压、安装 Python 或保留依赖文件夹。程序常驻托盘；首次启动会自动释放内置依赖，请稍等。游戏使用窗口或无边框模式，鼠标指向单词后按 `Ctrl+D` 查一次；移动鼠标不会触发查词。配置 API 后自动翻译所在段落，框选多段会保留分段。

`Ctrl+D` 取词 · `Ctrl+Shift+D` 框选 · `Ctrl+Alt+D` 固定对白区域 · `Esc` 收起。单击托盘图标打开生词本，右键词卡可修正原文。

游戏取词使用无光标截图，保持鼠标原位。首次语境翻译与中文标黄合并请求；点击词卡外空白处收起。

## 开发

Windows x64、Python 3.13，需安装 Windows 英语 OCR 组件。

```powershell
pip install -r requirements.txt
python tools/prepare_dictionary.py
python main.py
```

[完整使用说明](docs/usage.md) · [第三方许可](THIRD_PARTY_NOTICES.md)。ECDICT 数据来自 [skywind3000/ECDICT](https://github.com/skywind3000/ECDICT)，遵循 MIT 许可证。
