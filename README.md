# Anki Quick Add

当前版本：**v1.3.1** · [更新日志](CHANGELOG.md)

一款面向外语学习的轻量级 Anki 制卡工具。配合 ChatGPT 等大语言模型，将查词、解释和制卡连接起来，减少手动整理卡片的步骤。

支持 **剪贴板自动识别、悬浮快速制卡、重复检查、图片制卡和自定义 AI 提示词**。

## 主要功能

- **智能识别**：复制符合格式的 JSON，自动填入卡片，支持修改和查重。
- **悬浮制卡**：可拖动的悬浮球，支持连续复制、待处理队列、手动确认或自动添加。
- **图片制卡**：支持粘贴或选择本地图片，添加到 Anki 卡片背面。
- **自定义提示词**：内置纯文本和智能配图两套日语制卡提示词，支持独立编辑和保存。
- **自动连接**：通过 AnkiConnect 连接 Anki，支持牌组选择与断线重连。

![悬浮制卡界面](docs/floating-preview.png)

## 下载与安装

**运行环境：Windows、Anki、AnkiConnect。**

1. 安装 [Anki](https://apps.ankiweb.net/)。
2. 在 Anki 的「工具 → 插件 → 获取插件」中输入 `2055492159`，安装 AnkiConnect 并重启 Anki。
3. 前往 [Releases](../../releases) 下载 `AnkiQuickAdd-Windows.zip`。
4. 完整解压，运行 `AnkiQuickAdd/AnkiQuickAdd.exe`（请保留同目录下的 `_internal` 文件夹）。

Windows 发布包已包含运行环境，无需额外安装 Python。

## 如何使用

1. 打开 Anki 和 Anki Quick Add。
2. 复制内置制卡提示词，在 ChatGPT 等 LLM 中查询词汇。
3. 复制生成的 JSON，程序会自动识别并显示卡片。
4. 检查内容、选择牌组，点击「加入 Anki」。

需要连续制卡时，可开启「悬浮制卡」，选择手动确认或自动添加。

需要配图时，可在主窗口粘贴图片或选择本地文件。悬浮模式目前仅支持文字卡片。

## 卡片格式

支持包含 `front` 和 `back` 两个字段的 JSON：

```json
{"front":"単語","back":"假名：たんご\n释义：单词；词语"}
```

图片通过主窗口单独添加，不需要写入 JSON。

## 开发与文档

源码运行需要 Python 3.12：

```powershell
python -m pip install -r requirements.txt
python app.py
```

- [详细使用说明](docs/USAGE.md)
- [更新日志](CHANGELOG.md)
- [第三方许可证](THIRD_PARTY_NOTICES.md)

## 许可证

本项目基于 [MIT License](LICENSE) 开源，是独立第三方工具，与 Anki 或 Ankitects 无官方关联。
