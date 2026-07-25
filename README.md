# Pixkin

**A tiny AI sidekick with a life of its own.**

Pixkin 是面向 Windows 10 / 11 的卡通桌面伙伴。它会随机组合动作、响应点击和拖动、
吸附屏幕边缘并探头；也能常驻托盘，进行大模型对话、调用经过白名单保护的本地工具，
以及同时监听 B 站和抖音直播状态。

发行版已经内置运行环境，用户不需要安装 Python。

## 用户使用

推荐下载兼容性优先的 `Pixkin-1.3.0-win64.zip`：

1. 解压完整的 `Pixkin` 文件夹。
2. 双击 `Pixkin.exe`。
3. 首次启动默认使用内置伙伴山山，也可在设置中切换到凛凛、Pip，或导入 ZIP 角色包。
4. 点击桌宠打开带头像的对话气泡；从托盘进入设置或伙伴工坊。

同时提供 `Pixkin-Portable-1.3.0.exe` 单文件版。单文件版每次启动都要先展开运行环境，
速度会稍慢，也更容易触发部分安全软件的未知程序提示；日常使用更推荐文件夹版。

## 伙伴工坊：用一张图孵化新伙伴

至少添加一张风格示意图，填写名字与性格，即可生成并安装新的桌宠角色包：

- 基础孵化：确认身份稿后生成 4 个核心动作，适合先验证风格。
- 完整孵化：确认身份稿后分别生成 9 种动作，减少不同动作间的外观漂移。
- 永久卡通约束：无论参考图是真人、动物或照片，最终都只生成虚构的卡通数字伙伴。
- 支持 1–4 张参考图；图片 API Key 优先保存在 Windows Credential Manager。

伙伴工坊新生成角色使用 v2 基础级格式和统一的 `192 × 208` 画布；每次孵化都会
保存阶段、审核决定、动作任务、尝试次数和产物记录。身份稿会先暂停等待确认，
参考图会复制进任务工作区；失败或退出后可从未完成任务继续，也可只重试失败动作；
四个核心动作会经过一致性审核，全部动作还会通过自动 QA。QA 会生成 JSON 报告和
动作接触表，发现空图、裁切、重复动作等阻断问题时必须先返工。每次生成和返工都会
保留原始响应与规范化精灵，可在审核界面或任务工具中切换历史候选；最终预览确认后
才会安装。

默认使用 OpenAI Image API 的 `gpt-image-2`，也可填写兼容的接口地址与模型名。

## 角色包

角色包是 ZIP，必须包含：

- `character.md`：YAML Front Matter 格式的角色信息、人设与动作映射。
- PNG / WebP / JPG 图片：支持每个动作多帧。

完整格式见 [CHARACTER_PACKAGE_SPEC.md](CHARACTER_PACKAGE_SPEC.md)；新版角色、动画、
贴边、人设和 QA 的设计基线见
[PIXKIN_DESIGN_SPEC_V2.md](PIXKIN_DESIGN_SPEC_V2.md)。

v1 兼容动作：

`idle`、`blink`、`stretch`、`wave`、`nod`、`sleep`、`dragging`、
`edge_docked`、`talking`、`alerting`。

即使动作只有单张图，Pixkin 也会叠加轻微呼吸、点头、挥手、说话和提醒动效。
v2 角色包支持独立帧、动作条和图集，并按基础级、标准级、完整级声明能力；
多帧 v2 动画不会再叠加旧版整体变形。
导入器会检查路径穿越、符号链接、文件数量和解压大小，避免恶意 ZIP 覆盖用户文件。
设置中心支持跟随 Windows、深色和浅色三种界面主题。
角色管理页支持导入、切换、重命名和删除；内置角色山山、凛凛与 Pip 不可删除，
删除当前自定义角色时会自动切回山山。椰子不再自动安装，只作为
`椰子.zip` 角色包供用户按需导入。

首批四个角色包均使用统一的 8×9、单格 `192×208` 透明图集和完整级动作映射。
山山、凛凛、Pip 随发行版内置；椰子采用相同质量标准，但仅以独立 ZIP 交付。

## AI 与工具

- 支持 OpenAI-compatible Chat Completions 与流式回复。
- 支持模型 Tool Calls。
- 内置查看时间、系统信息和打开白名单系统应用等安全工具。
- 角色包内的 `system_prompt` 可定义名字、语气和人格。

## 开播监听

- 支持同时添加多个 B 站与抖音直播间。
- 每轮并发查询，慢平台不会阻塞其他直播间。
- 仅在“未开播 → 开播”状态变化时弹出开播提醒气泡。
- 可选择程序启动时发现正在直播也提醒。

实现结构参考了
[chenfan0/fideo-live-record](https://github.com/chenfan0/fideo-live-record)
的“平台 crawler 插件 + URL 路由”思路；本项目独立实现状态检测，不包含录流代码。

## 隐私与兼容

- 角色包、设置、日志保存在 `%LOCALAPPDATA%\Pixkin`。
- 老版 `%LOCALAPPDATA%\DesktopPet` 数据会在首次运行时迁移。
- API Key 优先保存在 Windows Credential Manager。
- 新增会删除文件、发送消息或修改系统的工具时，应增加逐次确认与审计记录。
- 当前构建目标为 Windows 10 / 11 x64。

## 源码运行

建议使用 Python 3.11：

```powershell
py -3.11 -m pip install -r requirements.txt
py -3.11 main.py
```

## 测试

```powershell
$env:QT_QPA_PLATFORM='offscreen'
py -3.11 -m pytest -q
```

当前应用版本统一定义在 `core/version.py`。修改版本后运行：

```powershell
py -3.11 scripts\generate_version_info.py
```

CI 会检查 Windows 版本资源是否与应用版本一致，并在 Windows 环境完成测试和
PyInstaller 文件夹版构建冒烟。图片、视频、图集和角色 ZIP 使用 Git LFS 管理。

## 构建 Windows 发行版

```powershell
py -3.11 -m pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

构建结果：

```text
release/
├─ Pixkin-1.3.0-win64.zip
├─ Pixkin-Portable-1.3.0.exe
├─ 椰子.zip
├─ CHARACTER_PACKAGE_SPEC.md
└─ SHA256SUMS.txt
```

文件夹版使用 PyInstaller onedir，兼容性与启动速度更好；便携版使用 onefile。
当前二进制未进行商业代码签名，因此在部分电脑上可能出现 Windows SmartScreen 提示。
