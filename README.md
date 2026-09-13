# Pixkin

> **一只有自己小脾气的 AI 桌面萌宠伙伴。**

[简体中文](README.md) | [English](README.en.md) · [下载最新版 Release](https://github.com/rowanjove/Pixkin/releases) · [角色包规范](CHARACTER_PACKAGE_SPEC.md) · [报告问题](https://github.com/rowanjove/Pixkin/issues)

Pixkin 是一款专为 Windows 打造的灵动卡通桌面伙伴。它拥有鲜活的肢体语言与性格反馈：随机组合可爱小动作、响应鼠标点击拖拽、自动吸附在屏幕边缘探头探脑。常驻系统托盘，随时伴你工作与摸鱼。内置大模型对话能力、本地实用工具调用，并能贴心提醒 B 站与抖音主播开播！

发行版内置专属 Python 运行时，**开箱即用，无需配置环境**。

![Pixkin 对话气泡](docs/screenshots/chat-window.png)

<details>
<summary><b>查看设置中心与伙伴工坊截图</b></summary>

| 设置中心 | 伙伴工坊 (一图孵化角色) |
| :---: | :---: |
| ![设置中心](docs/screenshots/settings-center.png) | ![伙伴工坊](docs/screenshots/pet-lab.png) |

</details>

---

## 核心玩法与特性

### 🐾 1. 灵动活泼的桌面互动
* **趣味交互**：支持点击互动、轻快拖拽、四向贴边停靠与探头张望；
* **动作细腻**：呼吸、眨眼、伸懒腰、睡觉、挥手打招呼，单图立绘亦有灵动微动效；
* **三大内置伙伴**：开箱自带萌宠 **山山**、**凛凛**、**Pip**，支持托盘随时无缝切换。

### 💬 2. 大模型随心畅聊
* **通用接口兼容**：支持各类 OpenAI-compatible 接口与流式打字机回复；
* **实用本地工具**：支持安全计算器、当前时间、系统与磁盘状态、网页安全访问等小工具；
* **记忆胶囊**：会话独立隔离，可按角色、按日期回顾或导出聊天记录。

### 🎨 3. 伙伴工坊：一张图孵化专属桌宠
* **随心定制**：只需添加一张角色参考图，设定名字与性格，即可自动生成整套精灵图集与动作配置文件；
* **安全预览**：生成前提供预算确认与关键姿态一致性校验，支持逐动作 GIF 预览后一键安装。

### 📺 4. 实时直播开播提醒
* 支持同时关注多个 **Bilibili** 与 **抖音** 直播间；
* 智能状态轮询，开播瞬间在桌面右上角弹出贴心提醒气泡，不错过每一场精彩直播。

---

## 快速上手

1. 从 [Releases 页面](https://github.com/rowanjove/Pixkin/releases) 下载推荐的 `Pixkin-x.x.x-win64.zip`。
2. 完整解压文件夹，双击运行 `Pixkin.exe`。
3. 点击桌宠打开对话气泡，右键点击萌宠或双击右下角系统托盘图标，即可打开设置中心与伙伴工坊。

---

## 隐私与数据安全

* **数据全本地化**：角色包、对话数据库及配置保存在本机 `%LOCALAPPDATA%\Pixkin`，不会擅自上传；
* **凭据安全**：API Key 优先受保护保存在 Windows Credential Manager，安全隔离；
* **零后台越权**：所有工具调用与网页打开均需你的前台确认，安全透明。

---

## 本地开发

开发建议使用 Python 3.11：

```powershell
# 安装依赖
py -3.11 -m pip install -r requirements.txt

# 运行桌宠
py -3.11 main.py
```

更多二次开发说明、角色包制作与插件扩展，请参阅：
* [角色包完整规范 (CHARACTER_PACKAGE_SPEC.md)](./CHARACTER_PACKAGE_SPEC.md)
* [架构与模块说明 (docs/M8_ARCHITECTURE.md)](./docs/M8_ARCHITECTURE.md)

---

## 开源许可

本项目采用开源协议发布，第三方角色包请遵守各作者的版权授权。
