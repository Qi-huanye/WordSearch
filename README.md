# WordSearch

Windows 游戏鼠标词典，适合《极乐迪斯科》。支持悬停取词、ECDICT 离线英汉词义、AI 语境翻译与追问，可自选 API 地址、Key 和模型。

## 使用

从 [Releases](https://github.com/Qi-huanye/WordSearch/releases) 下载便携包，完整解压后运行 `WordSearch.exe`。游戏使用窗口或无边框模式，鼠标停在单词上即可查词；配置 API 后自动翻译对白。

`Ctrl+D` 取词 · `Ctrl+Shift+D` 框选 · `Ctrl+Alt+D` 固定对白区域 · `Esc` 收起。右键词卡打开设置或生词本。

## 开发

Windows x64、Python 3.13，需安装 Windows 英语 OCR 组件。

```powershell
pip install -r requirements.txt
python tools/prepare_dictionary.py
python main.py
```

[完整使用说明](docs/usage.md) · [第三方许可](THIRD_PARTY_NOTICES.md)。ECDICT 数据来自 [skywind3000/ECDICT](https://github.com/skywind3000/ECDICT)，遵循 MIT 许可证。
