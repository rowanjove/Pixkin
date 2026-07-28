# Pixkin 角色包规范

Pixkin 同时支持：

- **v1 兼容包**：旧版文件列表格式，缺失动作回退到 `idle`，单帧资源可使用兼容动效。
- **v2 角色包**：结构化人设、质量等级、独立帧/动作条/图集统一解析和严格资源校验。

完整产品设计、动作表、贴边标准和 QA 门槛见
[PIXKIN_DESIGN_SPEC_V2.md](PIXKIN_DESIGN_SPEC_V2.md)。

## 通用 ZIP 结构

ZIP 根目录或唯一顶层文件夹中必须包含 `character.md`：

```text
my-character.zip
├─ character.md
├─ images/
│  ├─ preview.png
│  ├─ idle-01.png
│  ├─ talking-strip.png
│  └─ atlas-core.webp
└─ qa/
   └─ validation.json
```

## v1 兼容格式

没有 `schema_version` 的包按 v1 处理：

```markdown
---
id: my-character
name: 我的角色
version: 1.0.0
author: 作者名
description: 显示在设置页的简短介绍
preview: images/idle-01.png
system_prompt: >-
  你是一个温柔又机灵的桌面助手。
animations:
  idle:
    files: [images/idle-01.png, images/idle-02.png]
    fps: 8
    loop: true
  talking:
    files: [images/talking-01.png]
    fps: 8
    loop: true
  alerting: images/alerting.png
---

# 角色说明
```

v1 至少需要 `idle`。兼容状态：

`idle`、`blink`、`stretch`、`wave`、`nod`、`sleep`、`dragging`、
`edge_docked`、`talking`、`alerting`

## v2 顶层格式

```markdown
---
schema_version: "2.0"
id: my-character
name: 我的角色
version: 2.0.0
author: 作者名
description: 一句话角色介绍
quality_tier: basic
preview: images/preview.png
persona:
  identity: 你是我的角色，一位可靠的 Pixkin 桌面伙伴。
  core_traits: [温暖, 可靠]
  initiative:
    animate_without_prompt: true
    speak_without_prompt: false
behavior:
  motion_temperament: calm
  speed_multiplier: 0.9
  idle_interval_seconds: [9, 18]
  ambient_weights:
    blink: 5
    wave: 0.5
animations: {}
compatibility: {}
rights:
  license: personal-use
  author_confirmed_rights: true
  ai_generated: false
---
```

v2 顶层允许字段：

`schema_version`、`id`、`name`、`version`、`author`、`description`、
`quality_tier`、`preview`、`persona`、`behavior`、`animations`、
`compatibility`、`rights`、`edge`、`extensions`、`system_prompt`

其他顶层字段会被视为拼写或格式错误。自定义扩展必须放在 `extensions` 中。

## v2 动作来源

### 独立帧

```yaml
animations:
  idle:
    source:
      type: frames
      files:
        - images/idle-01.png
        - images/idle-02.png
        - images/idle-03.png
        - images/idle-04.png
      cell_size: [192, 208]
    fps: 8
    playback: loop
    anchor: [96, 194]
```

### 横向或纵向动作条

```yaml
animations:
  talking:
    source:
      type: strip
      file: images/talking-strip.png
      frames: 4
      direction: horizontal
      cell_size: [192, 208]
    fps: 8
    playback: loop
```

横向动作条尺寸必须严格等于：

`单格宽度 × 帧数` × `单格高度`

纵向动作条尺寸必须严格等于：

`单格宽度` × `单格高度 × 帧数`

### 图集

```yaml
animations:
  wave:
    source:
      type: atlas
      file: images/atlas-core.webp
      row: 2
      column: 0
      frames: 5
      cell_size: [192, 208]
    fps: 8
    playback: once
```

推荐图集页面为 8 列 × 9 行，单格 `192 × 208`，页面
`1536 × 1872`。角色可以声明多个图集页面。

`playback` 支持：

- `once`
- `loop`
- `ping_pong`

## v2 质量等级

### 基础级 `basic`

必须包含：

`idle`、`talking`、`dragging`、`alerting`

### 标准级 `standard`

必须包含基础级以及：

`blink`、`look_around`、`stretch`、`wave`、`nod`、`sleep`、`wake`、
`listening`、`thinking`、`working`、`waiting`、`success`、`failed`、
`touch`、`happy`

### 完整级 `full`

必须包含标准级以及：

`annoyed`、`walk_left`、`walk_right`、`run_left`、`run_right`、`jump`、
`land`、`alerting_important`、`celebrate_live`

并提供左、右、上、下四个方向的：

`edge_enter_*`、`edge_idle_*`、`edge_hover_*`、`edge_exit_*`

## 安全与资源限制

- `id`：2–48 位小写字母、数字、`_`、`-`。
- 支持 PNG、WebP、JPG/JPEG。
- v2 独立动画帧默认必须为 `192 × 208`。
- 动作条和图集声明区域不得超出图片边界。
- 单张图片不得超过 20 MP。
- 解压总大小不得超过 100 MB。
- 单文件不得超过 25 MB。
- 最多 500 个文件。
- 禁止绝对路径、路径穿越和符号链接。
- 角色默认可以主动做无声动作，但 `speak_without_prompt` 必须为 `false`。

