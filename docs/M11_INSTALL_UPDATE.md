# M11 安装、升级与自动更新

状态：`IMPLEMENTED`

验收日期：2026-09-12

## 1. 安装器

- 推荐交付为 Inno Setup 安装器 `Pixkin-Setup-<version>.exe`。
- 固定 AppId：`{A31F7F5B-CE81-49BA-93B9-5EFCF8695731}`。
- 默认安装到当前用户的 `%LOCALAPPDATA%\Programs\Pixkin`，不要求管理员权限。
- 桌面快捷方式和开机启动都是未默认勾选的可选任务。
- 覆盖升级复用原安装目录；检测到降级时默认拒绝。
- 卸载时明确询问保留或彻底删除 `%LOCALAPPDATA%\Pixkin` 用户数据。
- 无论应用曾否自行开启开机启动，卸载都会清理 Pixkin 的当前用户 Run 项。
- 文件夹 ZIP 和便携版 EXE 继续交付，并通过 `install-mode.json` 与安装版更新路径隔离。

## 2. 更新完整性

- 清单通过 HTTPS 获取，并使用离线 Ed25519 私钥签名。
- 应用只内置公钥，公钥文件 SHA-256 为
  `6c263dfda9637533e2bce8e76c5fbddc9b7e716464d7c9c35c5fa0ebf6a38d71`。
- 清单只接受版本、渠道、发布时间、最低兼容版本、发布说明和产物白名单字段。
- 安装器只接受无凭据 HTTPS URL、单一 `.exe` 文件名、正整数大小和 64 位
  SHA-256；重定向后的最终 URL 也必须是 HTTPS。
- 下载使用临时文件、大小上限、流式 SHA-256 和原子替换，任何校验失败都不会启动。
- 清单不能传入命令、脚本或安装参数；应用只使用内置的固定 Inno 参数。
- stable、beta 两个渠道独立；自动检查每天最多一次，可关闭。
- 下载和安装分开确认；安装前自动导出配置、聊天、角色、资料和伙伴工坊任务。

## 3. 回滚

- 每次成功安装会把来源安装器复制到本地回滚目录，并写入独立 SHA-256。
- 设置页只展示低于当前版本且本地哈希验证通过的最近安装器。
- 回滚需要用户确认，回滚前同样执行全量本地数据备份。
- 正常安装器拒绝降级；只有应用的受控回滚入口使用固定 `/ALLOWDOWNGRADE` 参数。
- Inno Setup 自身负责安装事务失败时的文件回滚；数据备份用于跨版本数据恢复。

## 4. 发布密钥

本机开发验收私钥位于工作区之外：

```text
C:\Users\26241\.pixkin-release\update-private-key.pem
```

该路径不会进入仓库、EXE、安装器或诊断包。发布前必须：

1. 把私钥离线备份到至少两个受控介质。
2. 将 PEM 的 Base64 作为 GitHub Actions Secret
   `PIXKIN_UPDATE_PRIVATE_KEY_B64`，不要把路径或内容写入仓库。
3. 设置实际远程仓库后，由 Release Workflow 使用 GitHub Release 的 HTTPS
   地址生成 `update-stable.json`。
4. 若私钥疑似泄露，必须发布包含新公钥的过渡版本；旧公钥应用不能无条件信任新钥匙。

源码与本地安装器构建不要求私钥；正式 Release Workflow 在缺少签名清单时会失败。

## 5. 验收证据

- 459 项测试与 46 个子测试通过，总覆盖率 75.03%。
- Ruff、Pyright（Python 3.11）通过；本轮最终源码上的文件夹版、便携版和 Inno Setup
  安装器已重建，并通过隔离启动/退出烟测。
- `pip-audit` 对 `requirements-lock.txt` 报告未发现已知漏洞，CycloneDX SBOM 已生成。
- 更新协议覆盖签名篡改、字段注入、渠道错配、降级、最低兼容版本、HTTP
  降级、大小、哈希、不完整下载和用户确认测试。
- 本机私钥签名安装器清单，再由仓库内置公钥复验的端到端链路通过；清单中的安装器
  SHA-256 与 `SHA256SUMS.txt` 一致。
- Inno Setup 6.7.3 编译通过；最终 `release/` 已包含应用包、安装器、角色包、SBOM、
  统一哈希和 `update-stable.json`。
- 仍需在 GitHub 上传后重新下载公开资产，并由真实 Windows 10/11 设备继续完成人工
  硬件、多显示器热插拔、SmartScreen 和外部服务验收。
