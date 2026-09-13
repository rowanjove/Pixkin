# Pixkin v1.5.0 / Capability 2.0.0 落地与验收

状态：`RELEASE CANDIDATE`（质量门禁、三种 Windows 产物、隔离安装/启动/退出/卸载和发布哈希均已验证；安全修复与真实麦克风/云端语音、物理多显示器和外部 MCP 服务仍需发布前验收）
公开版本策略：版本号在 `v1.5.0` 封顶；`2.0.0` 是能力里程碑，不是公开版本号。

## 1. 需求边界

升级规划文档是产品提案，不是对源码的直接指令。本项目将其中的硬性验收转成以下工程契约：

| 领域 | 落地契约 | 当前实现 |
| --- | --- | --- |
| Runtime | Kernel、依赖容器、事件、动作统一入口；服务失败不能破坏已有数据 | `core/runtime/`，启动/停止可测试 |
| Provider | Chat、Image、TTS 统一注册；ASR/Vision/Embedding/Realtime/Trigger 留出同一 Registry 扩展点 | `core/providers/registry.py`、`catalog.py`、`model_profile.py` |
| Speech | VAD、ASR 边界、TTS 句子切分、状态机和 AI 打断可脱离 Qt 测试；传统按住说话继续兼容 | `core/speech/` +现有 Push-to-talk UI |
| Perception | 窗口、进程、空闲、剪贴板、按需截图；默认拒绝，原始内容不落盘，Prompt 有预算 | `ContextSensorService` + `PerceptionContextProjector` |
| Memory | SQLite + FTS5、五类记忆、版本/冲突、启用开关、JSON 幂等迁移 | `MemoryV2Service`，旧 `MemoryService` API 保留 |
| Character | 状态可恢复，Renderer 是平台无关契约，v1/v2 角色包兼容并可接受 v3 runtime sections | `CharacterStateService`、`core/renderers/`、角色包校验器 |
| Events/Plugins | 事件 envelope、权限动作、严格 manifest、进程/Job Object 故障隔离、safe mode、超时 | `core/runtime/`、`core/plugins/` |
| MCP | 初始化协商、能力发现、工具映射；stdio 与 Streamable HTTP；HTTPS/localhost、重定向/大小门禁（服务端 Origin 校验由对端负责） | `core/mcp/` |
| Tools | metadata、来源、权限、确认、超时、执行上下文和脱敏审计 | `ToolRegistry` + `ToolPermissionService` |
| Privacy | Window/System/Clipboard/Screen/Microphone 等资源统一四态；会话授权不得写入磁盘 | Privacy Center + `ContextPermissionService` |
| Governance | Apache-2.0、架构检查、质量门禁、版本资源同步 | `LICENSE`、`scripts/check_architecture.py`、`run_quality.ps1` |

不在本版本硬编码所有第三方模型厂商，也不把 Live2D、摄像头持续观察或全自治 Agent 伪装成已完成；它们必须通过上述 Provider/Plugin 合约接入。

## 2. 实施顺序

1. **冻结公开版本与迁移保护**：先备份旧配置、聊天库、角色和工坊任务，再创建 Memory/CharacterState 新文件；任何迁移失败都保留原文件并阻止继续启动。
2. **运行时骨架**：组合根构造服务、注册 Kernel；插件只发现不自动执行，Provider 只在调用边界创建实例。
3. **Provider 与模型画像**：UI 不再 `new` 具体 Provider；模型能力只通过 descriptor/profile 判断。
4. **感知与权限**：传感器读取前先 `decide`；剪贴板/截图只有显式授权才采样；Projection 在发送前按问题相关性和字符预算裁剪。
5. **记忆与角色状态**：旧 JSON 只读迁移到 SQLite；用户确认仍是写入门槛；角色状态按 character_id 分区并原子保存。
6. **语音**：保留 Push-to-talk 作为兼容入口，VAD、ASR、TTS 和打断通过 `core/speech` 合约演进；所有云端语音继续使用独立端点和独立密钥。
7. **扩展与工具**：插件进程采用 JSON-RPC 行协议；MCP 工具先注册 metadata，再进入同一个权限/超时/审计路径。
8. **交付**：运行架构、Ruff、Pyright、全量 pytest/coverage；再构建 onedir、portable、installer 并在隔离用户目录完成启动、退出、安装和卸载烟测；真实硬件与外部服务保留为发布前手工验收清单。

## 3. 验收场景映射

- **启动恢复**：Kernel 启动后角色、MemoryV2、CharacterState 和 PluginHost 均可查询；旧文件 hash 在测试前后不被破坏。
- **授权感知**：默认四类感知均为空；授权窗口/系统/剪贴板/截图后才产生对应字段；会话授权退出后消失。
- **错误上下文**：问题包含 traceback/代码语义时才投影剪贴板，且总字符数不超过 `ContextBudget`。
- **语音链路**：VAD 的 start/end 迟滞、ASR 注入、TTS 状态和 talking→listening 打断均有无硬件单元测试；UI Push-to-talk 仍须由用户松开后提交文本。
- **记忆冲突**：新记忆 supersede 旧记忆，旧版本保留但不再注入；禁用和删除不会物理擦除审计需要的版本记录。
- **插件故障**：插件初始化失败、返回超大消息或超时只终止该插件进程树；主 UI 和其他服务继续运行。进程隔离不是文件系统/网络沙箱，插件必须由用户明确受信。
- **MCP 安全**：initialize 先于 tools/list/tools/call；远端必须 HTTPS，localhost 才允许 HTTP；拒绝重定向、凭据 URL 和超大响应。
- **工具审计**：每次拒绝、成功、异常、超时都有脱敏参数、授权方式、耗时、session/character/plugin 上下文。

## 4. 版本与发布规则

- `core/version.py:VERSION`、Windows `version_info.txt`、PyInstaller 文件名和 README 统一 `1.5.0`。
- `CAPABILITY_MILESTONE = "2.0.0"` 只用于内部支持矩阵、文档和验收，不写入面向用户的安装包版本。
- `MINIMUM_UPDATE_VERSION` 保持 `1.3.0`，确保旧版用户可通过受签名更新链路升级。
- 发布状态已满足全量质量门禁、最终产物 hash 和隔离 Windows 烟测；真实硬件与外部服务场景不纳入本机自动化证据，须按第 5 节清单手工演练。

## 5. 深度审查清单

- 审查 `main.py` 是否绕过容器直接创建新的外部 Provider、线程或存储连接。
- 审查所有权限资源是否 default-deny，尤其是剪贴板、截图、麦克风和插件/MCP。
- 审查所有 JSON/SQLite/ZIP/HTTP 边界的大小、路径、重定向、超时、原子写和回滚。
- 审查线程退出顺序：先停止采样/网络/插件，再关闭 TTS/Memory，确保 Qt 不被后台回调重新访问。
- 审查日志、诊断包、工具审计和记忆 Prompt 不含 API Key、原始截图、剪贴板全文或本地绝对路径。
- 审查版本、文档状态与实际产物一致；任何“completed”都必须有对应命令输出或运行时证据。
