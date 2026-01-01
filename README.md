# 🎨 AstrBot Gitee AI Image Plugin （有免费额度）

本插件为 AstrBot 接入 Gitee AI 的图像生成能力。相比原版插件，本插件增加了**智能穿搭优化**、**本地缓存管理**、**并发控制**以及**LLM 逻辑增强**功能，支持多key轮询。

## ✨ 功能特性

- **💬 自然语言绘图**: 支持在对话中直接要求 Bot 画图（例如："帮我画一只猫"）。
- **👗 智能穿搭联动的 (Unique)**: 如果安装了 `life_scheduler` 插件，生图时会自动读取今日穿搭。
- **🧠 逻辑优化**: LLM文本模型调用，自动判断半身/全身照是否需要描写鞋袜，避免穿搭逻辑错误。
- **⚡ 高并发支持**: 支持配置多 API Key 轮询，支持设置最大并发数。
- **🧹 自动缓存管理**: 定时清理过期图片，防止磁盘占满。
- **📐 多比例支持**: 支持 16:9, 4:3, 9:16 等多种常见比例。

## 配置

在 AstrBot 管理面板中配置，或手动编辑配置：

| 配置项 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `base_url` | string | `https://ai.gitee.com/v1` | API 地址 |
| `api_key` | string/list | `[]` | 你的 API Key，支持用逗号分隔多个 Key 以实现轮询 |
| `model` | string | `z-image-turbo` | 绘图模型名称 |
| `size` | string | `1024x1024` | 默认分辨率 |
| `num_inference_steps` | int | 9 | 推理步数，越高质量越好但越慢 |
| `max_concurrent` | int | 3 | 同时生成的最大数量 |
| `cache_cleanup_enabled` | bool | `true` | 是否开启自动清理缓存 |
| `cache_max_count` | int | 200 | 缓存保留的最大图片数量 |


## Gitee AI API Key获取方法：
1.访问https://ai.gitee.com/serverless-api?model=z-image-turbo

2.<img width="2241" height="1280" alt="PixPin_2025-12-05_16-56-27" src="https://github.com/user-attachments/assets/77f9a713-e7ac-4b02-8603-4afc25991841" />

3.免费额度<img width="240" height="63" alt="PixPin_2025-12-05_16-56-49" src="https://github.com/user-attachments/assets/6efde7c4-24c6-456a-8108-e78d7613f4fb" />

4.可以涩涩，警惕违规被举报

5.好用可以给个🌟

##图像尺寸只支持以下，如果不在其中会报错
    "1:1 (256×256)": (256, 256),
    "1:1 (512×512)": (512, 512),
    "1:1 (1024×1024)": (1024, 1024),
    "1:1 (2048×2048)": (2048, 2048),
    "4:3 (1152×896)": (1152, 896),
    "4:3 (2048×1536)": (2048, 1536),
    "3:4 (768×1024)": (768, 1024),
    "3:4 (1536×2048)": (1536, 2048),
    "3:2 (2048×1360)": (2048, 1360),
    "2:3 (1360×2048)": (1360, 2048),
    "16:9 (1024×576)": (1024, 576),
    "16:9 (2048×1152)": (2048, 1152),
    "9:16 (576×1024)": (576, 1024),
    "9:16 (1152×2048)": (1152, 2048),

## 📖 使用方法

### 方式一：指令调用
```bash
/aiimg <提示词> [比例]
```
**示例：**
- `/aiimg 一个可爱的二次元女孩` (默认 1:1)
- `/aiimg 赛博朋克城市夜景 16:9`
- `/aiimg 手机壁纸风景 9:16`

**支持的比例：** `1:1`, `4:3`, `3:4`, `16:9`, `9:16`, `3:2`, `2:3`

### 方式二：自然语言 (LLM)
直接在对话中发送需求：
- "画一张你在海边散步的照片"
- "生成一个你穿着机甲的战士"

> 如果你配置了人设，Bot 会自动尝试维持人设一致性。

### 方式三：缓存管理指令
- `/aiimg_stats`: 查看当前缓存占用、并发状态。
- `/aiimg_clean`: 一键清空所有图片缓存。

## 注意事项

- 请确保您的 Gitee AI 账号有足够的额度。

- 生成的图片会临时保存在 `data/plugins/astrbot_plugin_gitee_aiimg/images` 目录下。


### 出图展示区

<img width="1152" height="2048" alt="29889b7b184984fac81c33574233a3a9_720" src="https://github.com/user-attachments/assets/c2390320-6d55-4db4-b3ad-0dde7b447c87" />

<img width="1152" height="2048" alt="60393b1ea20d432822c21a61ba48d946" src="https://github.com/user-attachments/assets/3d8195e5-5d89-4a12-806e-8a81e348a96c" />

<img width="1152" height="2048" alt="3e5ee8d438fa797730127e57b9720454_720" src="https://github.com/user-attachments/assets/c270ae7f-25f6-4d96-bbed-0299c9e61877" />








