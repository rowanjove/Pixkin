# M8 架构解耦实施说明

状态：`IMPLEMENTED`
目标版本：`1.5.0`

## 依赖规则

```text
ui / main
   ↓
services（业务流程与策略）
   ↓
providers / storage / generation（外部接口、持久化、工作流）
```

- UI 只负责呈现状态、收集输入和发送命令，不直接组织网络请求或文件事务。
- `QThread` 只负责线程生命周期、取消和 Qt 信号转发。
- Service 负责业务消息、重试/循环上限、状态转换和持久化边界。
- Provider 负责第三方协议和 SDK 细节，必须实现能力声明、健康检查、取消和关闭。
- 可测试的窗口定位、裁剪和状态判断优先下沉为不创建窗口的纯函数。
- 每次迁移保持旧配置、数据库、角色包和未完成孵化任务可读。

## 已完成阶段

### 对话链路

- `core/providers/chat/base.py` 定义 Provider、能力、健康状态和流式结果契约。
- `core/providers/chat/openai_compatible.py` 封装 OpenAI 兼容 SDK、流式文本和 Tool Call 拼装。
- `core/services/chat_service.py` 负责系统消息、上下文、工具调用循环和参数解析。
- `ChatSessionService` 负责当前角色/会话、上下文裁剪、历史写入和会话切换。
- `core/ai_engine.py` 的 `AiWorkerThread` 只保留线程、取消和信号转发职责。
- `DesktopPetApp` 不再直接裁剪聊天消息或操作聊天历史存储。
- 契约测试使用 Fake Provider 验证工具循环，不依赖网络或 Qt 窗口。

### 显示布局

- `core/services/display_layout.py` 统一保存位置恢复和聊天浮层定位规则。
- 负坐标副屏、无效位置回退和浮层边界使用无界面单元测试覆盖。
- CI 在 Windows Server 2022/2025 与 100%/200% 缩放组合运行 UI 兼容测试。
- GitHub 托管运行器不能替代 Windows 10/11 实机的显示器热插拔、任务栏位置和休眠唤醒验收。

### 伙伴工坊状态机

- `core/generation/state_machine.py` 定义 13 个阶段、合法跳转、阶段状态和产物契约。
- 每次状态变化写入连续历史，非法跳转、阶段/状态错配和被篡改的历史会被拒绝。
- 失败和取消根据中断前阶段及现有产物选择安全恢复入口。
- 保留任务清单 `schema_version: 1`，新增 `workflow_schema_version: 2`，
  使旧版本仍可读取新清单，新版本可迁移旧任务。
- 完整设计见 `docs/PET_GENERATION_STATE_MACHINE.md`。

### 角色包应用服务

- `core/services/character_service.py` 定义角色包的应用层生命周期入口。
- `CharacterInstallPlan` 是不可变预检结果，明确区分无冲突、已安装冲突和
  受保护的内置角色冲突。
- 预检记录 ZIP SHA-256；用户确认后、安装事务开始前会重新解析归档并核对
  摘要与角色 ID，避免确认对象与实际落盘对象不一致。
- 覆盖已有角色必须显式确认；第三方归档不能覆盖内置角色，只有应用随附的
  可信内置包同步路径可以显式放行。
- `CharacterPackageManager` 负责 ZIP 路径/大小/图片/schema 安全校验；
  staging、备份、失败回滚、重命名/删除事务和归档比对由内部独立事务组件负责。
- `DesktopPetApp`、`SettingsWindow`、`FirstRunWindow` 和 `PetLabWindow`
  共享或包装同一服务契约，不再直接调用导入、覆盖和删除事务。
- 删除当前自定义角色时，Service 返回明确的默认角色回退结果。
- 角色包 ZIP、安装目录和 `config.json` 字段均未改变，无需数据迁移。

### 伙伴工坊应用控制器

- `core/services/pet_lab_service.py` 负责审核决定、恢复路由、任务工具状态和
  候选版本生命周期，Qt 窗口只呈现对话框并执行返回命令。
- 身份稿、核心动作和自动 QA 的接受、返工、暂存决定会返回
  `resume`、`retry` 或 `defer` 显式命令，同时统一写入审核与阶段状态。
- 恢复任务时，Service 同时检查状态机阶段和必需产物是否真实存在；
  身份审核、核心审核、QA 审核、最终安装产物完整时进入对应界面，
  否则安全回到生成器恢复流程。
- 候选列表、重试状态和 QA 推荐返工顺序通过无界面数据契约提供给 UI。
- 候选切换统一复制当前精灵、选择清单版本、失效审核/QA/动画/包产物，
  并在允许水平镜像时重置由右向动作派生的左向任务。
- `ui/pet_lab_components.py` 提供身份/API 表单、候选版本选择、最终安装确认、
  动画循环预览和生成诊断等独立组件。
- `PetLabHatchInput` 是不可变、已去除首尾空白的表单数据契约；API Key 字段
  不参与 `repr`，窗口只负责凭据落盘事务并把契约传给 Worker。
- 孵化模式、镜像开关、计划调用次数、预算下限和提示文案由
  `PetLabHatchForm` 统一维护。
- `ReferenceImagePicker` 独立维护参考图顺序、去重、四张上限和缩略图，
  `PetLabWindow.reference_paths` 保留兼容读写入口。
- `PetLabTaskPanel` 负责未完成任务/动作工具选择、健康摘要及按钮启停；
  窗口只加载数据并执行 Service 返回的恢复命令。
- `PetLabDiagnosticsController` 负责诊断展示、剪贴板输出、匿名问题包导出与
  白名单检查；窗口保留原方法作为兼容代理。
- `PetLabReviewDialogController` 负责身份稿、动作一致性、QA 返工和历史候选
  对话；状态机决定与持久化仍由 `PetLabService` 承担。
- `PetLabInstallDialogController` 负责接触表优先预览、归档预览回退、动画入口
  和最终安装/覆盖确认；实际安装事务仍由 `CharacterService` 承担。
- 最终安装确认默认选择“否”，仍允许在确认过程中打开逐动作循环预览。
- 动画预览组件拥有并释放 `QMovie` 生命周期，关闭后主动清空文件名和画布，
  避免 Windows 阻止任务清理或再次生成。
- `PetLabWindow` 的可执行语句从 967 降至 462，已进入 500 行建议线。
- 孵化任务清单字段和工作流 schema 均未改变，旧任务无需再次迁移。

### 图像生成链路

- `core/providers/image/base.py` 定义图像 Provider、能力、健康状态和错误分类契约。
- `core/providers/image/openai_compatible.py` 封装 OpenAI 兼容图像编辑请求、
  模型检查、Base64 响应解码和 SDK 客户端关闭。
- `PetGenerationWorker` 只消费 Provider 契约，不再解析第三方响应或维护协议细节。
- Provider 可以在构造或恢复任务时注入；Fake Provider 测试无需 OpenAI SDK、
  网络或 Qt 窗口即可验证完整工作线程路径。
- 为兼容现有调用方，默认 Provider 仍延迟创建 OpenAI 兼容客户端，配置字段和
  孵化任务清单格式均未改变。
- `PetGenerationWorker` 的可执行语句从 618 降至 554。

### 实时平台链路

- `core/providers/live/base.py` 定义直播 Provider、能力、健康状态、房间输入和
  可信状态结果契约。
- `core/providers/live/platforms.py` 封装 B站与抖音 HTTP 请求、安全 URL 校验、
  响应解析和平台路由；`core/live_platforms.py` 仅保留旧导入兼容层。
- `core/services/live_service.py` 负责开播通知转换、最后可信状态、逐房间失败次数、
  带抖动的指数退避和周期汇总。
- 检测失败与可信下播被明确区分：失败会保留最后一次成功检测的直播状态、标题和主播，
  不会产生错误的下播状态；恢复成功后清除失败并回到正常间隔。
- `core/live_monitor.py` 只负责并发执行到期房间探测、响应停止事件和转发 Qt 信号。
- B站与抖音的脱敏响应样本保存在 `tests/fixtures/live/`，解析契约测试不访问网络。
- `ui/live_settings_components.py` 的 `LiveRoomEditor` 统一直播间表格、增删、
  空白过滤和字段规范化，并从 Provider 能力声明生成可选平台与能力提示。
- `SettingsWindow` 保留旧表格和方法兼容入口，但不再构建或遍历直播间表格。
- `ui/settings_profile_components.py` 的 `UserProfileEditor` 统一用户名称规范化、
  头像预览、选择与清除，并通过不可变 `UserProfileInput` 输出保存数据。
- `ui/settings_character_components.py` 的 `CharacterManagerPanel` 统一角色列表、
  详情、内置角色排序与保护、旧别名隐藏、预览和操作状态。
- `SettingsWindow` 保留旧控件名和方法兼容入口；头像验证与复制下沉到
  `UserProfileService`，可执行语句从 692 降至 440。
- 直播配置字段和信号参数均未改变，现有设置和主程序调用无需迁移。

### 消息渲染、导出与桌宠绘制

- `ui/chat_message_components.py` 统一不可变消息视图、头像、四类消息行、
  欢迎卡、日期标签和严格 HTML 转义的兼容转录；`ChatBubbleWindow`
  可执行语句降至 410。
- `ChatExportService` 统一 Markdown/JSON 格式化和同目录原子替换，
  `HistoryWindow` 只选择范围、目标和显示结果。
- `ui/pet_rendering.py` 的 `PetRenderer` 统一角色包帧缓存、方向贴边图、
  动画效果和内置角色绘制；`PetWindow` 只保留窗口事件、贴边、漫游和定位，
  可执行语句降至 412。

### 配置与聊天存储迁移

- `core/storage/migrations.py` 是配置 JSON 和聊天 SQLite 的统一版本/迁移入口。
- `config.json` 顶层使用整数 `schema_version`，当前为 1；字段缺失视为旧版 0。
- 配置 0→1 迁移保留未知和用户字段，完成旧 DeepSeek 模型名迁移，再由
  `ConfigManager` 递归补齐当前默认值并原子写回。
- 聊天数据库使用 SQLite 原生 `PRAGMA user_version`，当前为 1；
  0→1 迁移保留现有会话与消息并补齐表和索引。
- SQLite DDL、索引和 `user_version` 更新在同一个显式事务中执行，
  任一步骤失败都会回滚。
- 当前版本的配置与数据库重复加载不会重复迁移或重写。
- 高于当前版本的配置或数据库会抛出未来版本错误；启动器显示兼容提示，
  释放单实例锁并保持原文件不变。
- 打包冒烟已验证全新隔离目录会生成配置 schema 1 和聊天数据库 user_version 1。

## 完成审计

- Chat、Image、Live Provider 契约及 Fake/录制响应测试均不依赖真实网络。
- 配置、聊天 SQLite、角色包和旧工坊任务保持兼容；迁移重复执行与失败回滚有测试。
- UI 不直接导入 OpenAI SDK、HTTP 客户端或 SQLite，也不执行角色/资料文件事务。
- `PetLabWindow`、`SettingsWindow`、`ChatBubbleWindow`、`PetWindow` 和
  `CharacterPackageManager` 均不超过 500 条建议线。
- 最新质量门禁为 459 项测试与 46 个子测试、74.99% 总覆盖率；本轮修复后的
  PyInstaller 文件夹版与便携版构建并通过隔离用户目录冒烟，安装器尚未重建。

## 回滚边界

对话、显示布局和角色包服务阶段未修改角色包格式。状态机只向孵化任务增加
旧版可忽略的 `workflow_schema_version` 与 `state_history` 字段，并保留外层
schema 1。配置和聊天数据库现已显式升级到 schema 1；旧数据自动迁移，
未来版本拒绝覆盖。角色目录和孵化任务无需降级转换。
