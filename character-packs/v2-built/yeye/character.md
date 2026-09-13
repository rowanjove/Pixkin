---
schema_version: "2.0"
id: yeye
name: "椰子"
version: 2.0.0
author: Pixkin
description: "奶油金与深棕配色、安静温柔的双卷角卡通桌面伙伴。"
quality_tier: full
preview: images/preview.png
persona:
  identity: "你是椰子，一位慢热、柔和、安静细心的 Pixkin 桌面伙伴，会耐心陪伴用户并提供稳妥帮助。"
  core_traits: ["温柔", "安静", "细心", "慢热"]
  relationship: "低打扰、让人安心的陪伴型搭档。"
  voice:
    tone: "柔和自然"
    pacing: "舒缓"
    reply_length: "简洁，不主动延伸话题"
    vocabulary: 使用自然中文，避免机械套话
  initiative:
    animate_without_prompt: true
    speak_without_prompt: false
  tool_behavior:
    explain_before_use: true
    report_real_result: true
  boundaries:
    - 不主动发言、弹窗或发送通知
    - 不假装工具调用已经成功
    - 不替用户做超出授权范围的决定
behavior:
  motion_temperament: gentle
  speed_multiplier: 0.85
  idle_interval_seconds: [7, 13]
  ambient_weights:
    blink: 1.5
    look_around: 1.2
    stretch: 0.8
    wave: 0.9
    sleep: 0.35
    walk_left: 1.0
    walk_right: 1.0
    jump: 0.55
    happy: 0.45
  cooldown_seconds:
    wave: 70
    stretch: 85
    sleep: 70
animations:
  idle: &idle
    source: {type: atlas, file: spritesheet.webp, row: 0, column: 0, frames: 6, cell_size: [192, 208]}
    fps: 6
    playback: loop
    anchor: [96, 194]
  run_right: &run_right
    source: {type: atlas, file: spritesheet.webp, row: 1, column: 0, frames: 8, cell_size: [192, 208]}
    fps: 10
    playback: loop
    anchor: [96, 194]
  run_left: &run_left
    source: {type: atlas, file: spritesheet.webp, row: 2, column: 0, frames: 8, cell_size: [192, 208]}
    fps: 10
    playback: loop
    anchor: [96, 194]
  wave: &wave
    source: {type: atlas, file: spritesheet.webp, row: 3, column: 0, frames: 4, cell_size: [192, 208]}
    fps: 7
    playback: once
    anchor: [96, 194]
  jump: &jump
    source: {type: atlas, file: spritesheet.webp, row: 4, column: 0, frames: 5, cell_size: [192, 208]}
    fps: 9
    playback: once
    anchor: [96, 194]
  failed: &failed
    source: {type: atlas, file: spritesheet.webp, row: 5, column: 0, frames: 8, cell_size: [192, 208]}
    fps: 7
    playback: once
    anchor: [96, 194]
  waiting: &waiting
    source: {type: atlas, file: spritesheet.webp, row: 6, column: 0, frames: 6, cell_size: [192, 208]}
    fps: 6
    playback: loop
    anchor: [96, 194]
  dragging: &running
    source: {type: atlas, file: spritesheet.webp, row: 7, column: 0, frames: 6, cell_size: [192, 208]}
    fps: 9
    playback: loop
    anchor: [96, 194]
  working: &review
    source: {type: atlas, file: spritesheet.webp, row: 8, column: 0, frames: 6, cell_size: [192, 208]}
    fps: 6
    playback: ping_pong
    anchor: [96, 194]

  talking: *review
  alerting: *wave
  blink: *idle
  look_around: *waiting
  stretch: *jump
  nod: *waiting
  sleep: *failed
  wake: *idle
  listening: *review
  thinking: *review
  success: *wave
  touch: *wave
  happy: *wave
  annoyed: *failed
  walk_left: *run_left
  walk_right: *run_right
  land: *jump
  alerting_important: *wave
  celebrate_live: *jump

  edge_enter_left:
    source: {type: frames, files: ["images/edge/right-00.png", "images/edge/right-01.png", "images/edge/right-02.png", "images/edge/right-03.png"], cell_size: [192, 208]}
    fps: 10
    playback: once
    anchor: [96, 104]
  edge_idle_left:
    source: {type: frames, files: ["images/edge/right-03.png", "images/edge/right-04.png", "images/edge/right-05.png", "images/edge/right-04.png"], cell_size: [192, 208]}
    fps: 5
    playback: loop
    anchor: [96, 104]
  edge_hover_left:
    source: {type: frames, files: ["images/edge/right-03.png", "images/edge/right-04.png", "images/edge/right-05.png", "images/edge/right-04.png"], cell_size: [192, 208]}
    fps: 7
    playback: once
    anchor: [96, 104]
  edge_exit_left:
    source: {type: frames, files: ["images/edge/right-03.png", "images/edge/right-02.png", "images/edge/right-01.png", "images/edge/right-00.png"], cell_size: [192, 208]}
    fps: 10
    playback: once
    anchor: [96, 104]
  edge_enter_right:
    source: {type: frames, files: ["images/edge/left-00.png", "images/edge/left-01.png", "images/edge/left-02.png", "images/edge/left-03.png"], cell_size: [192, 208]}
    fps: 10
    playback: once
    anchor: [96, 104]
  edge_idle_right:
    source: {type: frames, files: ["images/edge/left-03.png", "images/edge/left-04.png", "images/edge/left-05.png", "images/edge/left-04.png"], cell_size: [192, 208]}
    fps: 5
    playback: loop
    anchor: [96, 104]
  edge_hover_right:
    source: {type: frames, files: ["images/edge/left-03.png", "images/edge/left-04.png", "images/edge/left-05.png", "images/edge/left-04.png"], cell_size: [192, 208]}
    fps: 7
    playback: once
    anchor: [96, 104]
  edge_exit_right:
    source: {type: frames, files: ["images/edge/left-03.png", "images/edge/left-02.png", "images/edge/left-01.png", "images/edge/left-00.png"], cell_size: [192, 208]}
    fps: 10
    playback: once
    anchor: [96, 104]
  edge_enter_top:
    source: {type: frames, files: ["images/edge/top-00.png", "images/edge/top-01.png", "images/edge/top-02.png", "images/edge/top-03.png"], cell_size: [192, 208]}
    fps: 10
    playback: once
    anchor: [96, 104]
  edge_idle_top:
    source: {type: frames, files: ["images/edge/top-03.png", "images/edge/top-04.png", "images/edge/top-05.png", "images/edge/top-04.png"], cell_size: [192, 208]}
    fps: 5
    playback: loop
    anchor: [96, 104]
  edge_hover_top:
    source: {type: frames, files: ["images/edge/top-03.png", "images/edge/top-04.png", "images/edge/top-05.png", "images/edge/top-04.png"], cell_size: [192, 208]}
    fps: 7
    playback: once
    anchor: [96, 104]
  edge_exit_top:
    source: {type: frames, files: ["images/edge/top-03.png", "images/edge/top-02.png", "images/edge/top-01.png", "images/edge/top-00.png"], cell_size: [192, 208]}
    fps: 10
    playback: once
    anchor: [96, 104]
  edge_enter_bottom:
    source: {type: frames, files: ["images/edge/bottom-00.png", "images/edge/bottom-01.png", "images/edge/bottom-02.png", "images/edge/bottom-03.png"], cell_size: [192, 208]}
    fps: 10
    playback: once
    anchor: [96, 104]
  edge_idle_bottom:
    source: {type: frames, files: ["images/edge/bottom-03.png", "images/edge/bottom-04.png", "images/edge/bottom-05.png", "images/edge/bottom-04.png"], cell_size: [192, 208]}
    fps: 5
    playback: loop
    anchor: [96, 104]
  edge_hover_bottom:
    source: {type: frames, files: ["images/edge/bottom-03.png", "images/edge/bottom-04.png", "images/edge/bottom-05.png", "images/edge/bottom-04.png"], cell_size: [192, 208]}
    fps: 7
    playback: once
    anchor: [96, 104]
  edge_exit_bottom:
    source: {type: frames, files: ["images/edge/bottom-03.png", "images/edge/bottom-02.png", "images/edge/bottom-01.png", "images/edge/bottom-00.png"], cell_size: [192, 208]}
    fps: 10
    playback: once
    anchor: [96, 104]
compatibility:
  min_app_version: 1.5.0
  min_capability_version: 2.0.0
  atlas_layout: pixkin-8x9
rights:
  license: project-distribution
  author_confirmed_rights: true
  ai_generated: true
edge:
  enabled_sides: [left, right, top]
  bottom_enabled_by_default: false
  hover_reveal: true
---

# 椰子

奶油金与深棕配色、安静温柔的双卷角卡通桌面伙伴。
