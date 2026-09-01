# Pixkin — Windows AI 桌面伙伴

[简体中文](README.md) | [English](README.en.md)

Pixkin 是面向 Windows 10/11 x64 的桌面伙伴，支持角色动画、模型对话、角色包导入、图像生成工坊和 B 站／抖音开播提醒。伙伴可以响应点击与拖动、贴边停靠，也可常驻托盘。

[下载 v1.3.0](https://github.com/rowanjove/pixkin/releases/tag/v1.3.0) · [报告问题](https://github.com/rowanjove/pixkin/issues) · [角色包规范](CHARACTER_PACKAGE_SPEC.md)

发行版内置运行环境，不需要单独安装 Python。聊天、语音转写和角色生成可以调用外部模型服务，需要自己的接口配置，可能产生费用；它不是完全离线的 AI 应用。

## 安装与开始使用

从 Release 选择：

- `Pixkin-1.3.0-win64.zip`：推荐的文件夹版，完整解压后运行 `Pixkin.exe`。
- `Pixkin-Portable-1.3.0.exe`：单文件版，启动时需展开运行环境，可能更慢。
- `Pixkin-Setup-1.3.0.exe`：安装包。

首次启动默认使用山山，可在设置中切换凛凛、Pip，或导入角色 ZIP。点击伙伴打开对话气泡，从托盘进入设置与伙伴工坊。

当前二进制未进行商业代码签名，可能出现 SmartScreen 或安全软件提示。仅从本仓库 Release 获取文件；使用随包 `SHA256SUMS.txt` 核对完整性。

## 对话、记忆与工具

- 支持兼容 Chat Completions 的接口、流式回答和 Tool Calls。
- 每个角色拥有独立会话；可查看、导出 Markdown／JSON 或删除指定历史。
- 历史保留可选不保存、7 天、30 天或永久。
- 长期记忆仅保存逐条确认的内容，按角色隔离，并可查看回答使用的记忆。
- 提供快速／均衡／深入预设，以及停止生成、重试、编辑重发和延迟／token／费用估算。
- 内置算式、时间、系统与磁盘信息、HTTP(S) 网页和白名单应用工具。
- 打开网页或应用需逐次确认；可在本次运行中记住完全相同工具与参数的决定。
- 工具审计可查看、导出或清除；L2 状态修改与 L3 高风险工具默认禁用。

费用估算和接口能力检查不等于服务商账单或能力保证。首次发送前应检查目标地址与发送范围。

## 用参考图生成角色

在伙伴工坊中添加 1–4 张参考图，填写名字与性格，再选择生成范围。生成结果限于虚构卡通数字伙伴。

| 模式 | 计划图像调用 | 角色状态 |
| --- | --- | --- |
| 基础 | 5 次 | 1 张身份稿＋4 个核心状态 |
| 标准 | 20 次 | 1 张身份稿＋19 个标准状态 |
| 完整 | 45 次 | 1 张身份稿＋44 个状态 |

实际调用还可能包含重试与返工。开始前会确认计划和硬上限；默认额外预留 3 次返工，达到预算后停止，增加预算需用户操作。

身份稿、核心动作和最终安装都有审核环节。任务支持中断恢复、失败动作重试、候选版本切换、静态与运动 QA，以及安装前 GIF 预览。认证或请求错误会停止；符合条件的临时错误按受控退避重试，每次重试计入预算。

仅在用户确认角色左右完全对称时启用安全镜像，完整模式可减少 6 次生成；单侧配饰或文字不适用。源动作变化后对应镜像候选会失效。剩余时间基于实际任务耗时估算，等待人工审核的时间不在其中。

参考图会复制到本地任务目录。图像生成会把必要参考与描述发送到所配置接口，价格、速度和能力由服务商及模型决定。详细阶段见 [工坊状态机](docs/PET_GENERATION_STATE_MACHINE.md)。

## 角色包

角色 ZIP 需要 `character.md`（YAML Front Matter 元数据、人设和动作映射）及 PNG／WebP／JPG 资源。

v2 角色使用 `192 × 208` 画布，支持独立帧、动作条与图集。基础、标准、完整级分别检查所需状态，旧版 9 动作工坊任务可继续完成，但不会自动升级为标准级。首批官方角色使用 8×9 透明图集。

山山、凛凛、Pip 随包内置；椰子作为独立 ZIP 提供。内置角色不可删除，删除正在使用的自定义角色会切回山山。设置支持跟随 Windows、浅色和深色主题。

导入时检查路径穿越、符号链接、数量与解压体积。官方包通过内置 SHA-256 清单验证；第三方包展示作者、许可与指纹，但作者声明不自动获得信任。

[角色包格式](CHARACTER_PACKAGE_SPEC.md) · [v2 设计说明](PIXKIN_DESIGN_SPEC_V2.md)

## 语音、快捷键与直播提醒

语音默认关闭，仅按住说话时访问麦克风；录音在内存中，通过独立 HTTPS 接口转写。转写文本先进入输入框，用户确认后才发送给聊天模型。支持显示／隐藏、打开聊天、停止生成与麦克风静音快捷键。

可同时监听多个 B 站和抖音直播间，支持分组、跨午夜免打扰与可选重复提醒。通常在未开播变为开播时提醒，也可设置启动时提醒。平台凭据按房间隔离，外部适配器需显式启用。平台接口变化可能影响检测，不能保证每次开播都及时通知。

直播检测借鉴 [fideo-live-record](https://github.com/chenfan0/fideo-live-record) 的插件与 URL 路由思路，本项目不包含录流功能。

## 数据与隐私

- 角色、设置和日志位于 `%LOCALAPPDATA%/Pixkin`；历史位于 `chat-history.sqlite3`。
- 旧 `%LOCALAPPDATA%/DesktopPet` 数据在首次运行时迁移。
- API Key 持久化到 Windows Credential Manager，也可仅在当前运行的内存中保存。
- 历史默认存于本机；请求回答时会按所选上下文发送给配置的模型服务。
- 日志、工具审计和诊断会脱敏常见密钥格式；分享前仍应人工检查。
- 匿名问题 ZIP 使用文件允许列表，不包含参考图、生成图片、角色名字、人设或 API Key，并校验大小和 SHA-256；不要用整个任务目录替代匿名问题包。

## 源码运行与测试

需要 Python 3.11。图片、视频、图集和角色 ZIP 使用 Git LFS；克隆前安装 Git LFS，并确保资产已下载。

```powershell
git lfs install
git clone https://github.com/rowanjove/pixkin.git
cd pixkin
git lfs pull
py -3.11 -m pip install -r requirements.txt
py -3.11 main.py
```

完整开发／构建依赖与质量检查：

```powershell
py -3.11 -m pip install --require-hashes -r requirements-lock.txt
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_quality.ps1
```

质量检查包括版本元数据、编译、Ruff、Pyright、测试和分支覆盖率，覆盖率门槛为 70%。仅运行测试：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
py -3.11 -m pytest -q
```

## 构建 Windows 发行版

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_release.ps1
```

`release/` 包含程序、角色 ZIP、角色规范、SBOM、SHA-256 清单，以及正式发布所需更新元数据。版本以 `core/version.py` 为准，修改后运行：

```powershell
py -3.11 scripts/generate_version_info.py
```

修改顶层依赖后重新生成锁定文件：

```powershell
py -3.11 -m piptools compile --generate-hashes --strip-extras --output-file requirements-lock.txt requirements-build.txt
```

CI 的隔离启动、系统及缩放矩阵不能替代 Windows 10/11 真机显示器热插拔等人工验收。发布前完成 [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)。

## 进一步阅读与许可状态

[架构](docs/M8_ARCHITECTURE.md) · [安全边界](docs/M9_SECURITY.md) · [稳定性与恢复](docs/M10_STABILITY.md) · [安装、更新与回滚](docs/M11_INSTALL_UPDATE.md) · [产品能力](docs/M12_PRODUCT_CAPABILITIES.md)

界面与专题文档以中文为主。仓库当前未附独立 LICENSE 文件；本次文档不新增或推定代码及角色素材的再分发许可。第三方角色还应核对各自作者与许可。
