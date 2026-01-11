# 🎨 AstrBot Gitee AI Image Plugin（有免费额度）

<div align="center">

**本插件为 AstrBot 接入 Gitee AI 的图像生成能力。支持文生图、图生图（智能改图）、多Key轮询、智能穿搭优化及人设保持。**

</div>

## ✨ 核心特性

- **💬 自然语言绘图**: 在对话中直接要求 Bot 画图，支持复杂的中文描述（例如："帮我画一张赛博朋克风格的猫"）。
- **🖌️ 智能图生图 (New)**: 支持对已有图片进行编辑，包括**换背景、换风格、主体替换、元素编辑**等（基于 Qwen-Image-Edit）。
- **👗 智能穿搭联动**: 若安装了 `life_scheduler` 插件，生图时会自动读取今日穿搭。
- **🧠 逻辑增强**: 内置 LLM 文本模型（默认 DeepSeek），智能判断画面构图（如半身/全身），自动清洗穿搭描述（如半身照自动去掉鞋袜描述），防止画面崩坏。
- **👤 人设一致性**: 支持配置 `persona_prefix`，在生图时自动注入角色外貌特征，保持画风和角色一致。
- **⚡ 高并发 & 多Key**: 支持配置多个 API Key 自动轮询，设有防抖动和最大并发限制。
- **🧹 自动缓存管理**: 定时清理过期图片，支持查看缓存统计，防止磁盘占满。
- **📐 全面比例支持**: 完美支持 16:9, 4:3, 9:16, 1:1 等多种主流分辨率。

---

## ⚙️ 配置说明

在 AstrBot 管理面板中配置，或手动编辑配置文件。

### 基础配置
| 配置项 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `api_key` | `[]` | **必填**。Gitee AI API Key。点击 `+` 可添加多个 Key，插件会自动轮询使用。 |
| `base_url` | `https://ai.gitee.com/v1` | API 地址，通常保持默认。 |
| `model` | `z-image-turbo` | 文生图模型名称。 |
| `text_model` | `deepseek-ai/DeepSeek-V3` | **辅助文本模型**。用于优化 Prompt 和判断穿搭逻辑。 |
| `size` | `1024x1024` | 默认生图分辨率。 |

### 进阶配置
| 配置项 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `edit_model` | `Qwen-Image-Edit-2511` | 图生图/改图使用的模型。 |
| `persona_prefix` | (空) | **人设前缀**。例如：`1girl, pink hair, blue eyes`。会自动加在所有生图请求最前面。 |
| `cache_cleanup_enabled` | `true` | 是否开启缓存自动清理。 |
| `cache_max_count` | `200` | 本地保留的最大图片数量。 |

---

## 📖 使用指南

### 1. 文生图 (Text-to-Image)

#### 方式 A：自然语言（推荐）
直接在聊天中发送需求，Bot 会自动识别：
> "画一张你在海边散步的照片"
> "生成一个穿着机甲的战士，要在雨夜"

*注：如果你配置了人设前缀，Bot 画“你”或“自拍”时会自动带上人设特征。*

#### 方式 B：指令调用
```bash
/aiimg <提示词> [比例]
```
**示例：**
- `/aiimg 二次元少女` (使用默认 1:1)
- `/aiimg 赛博朋克城市夜景 16:9`
- `/aiimg 手机壁纸风景 9:16`

---

### 2. 图生图 / 智能改图 (Image Editing) 🆕

该功能基于 `Qwen-Image-Edit` 模型，可以对图片进行智能修改。

#### 方式 A：指令调用
先发送图片（或引用一张图片），然后输入：
```bash
/aiedit <提示词> [任务类型]
```
**任务类型 (Task Types):**
如果不指定，默认为 `id`。
- `id`: **保持身份**（默认）。改变环境或动作，但保持人物长相特征不变。
- `style`: **风格迁移**。例如 "变成素描风格"、"变成油画风格"。
- `background`: **背景替换**。例如 "把背景换成沙滩"。
- `subject`: **主体替换**。例如 "把猫变成狗"。
- `element`: **元素编辑**。修改画面中的某个细节。

**示例：**
1. 发送一张照片。
2. 回复该照片：`/aiedit 变成梵高星空风格 style`
3. 回复该照片：`/aiedit 在雪山上滑雪 id` (保持人物长相，换成滑雪场景)

#### 方式 B：自然语言
> 引用图片并发送："把这张图的背景换成外太空"

---

### 3. 缓存管理
- `/aiimg_stats`: 查看当前缓存数量、占用空间及清理策略状态。
- `/aiimg_clean`: 一键清空所有图片缓存。

---

## 📐 支持的分辨率列表

插件会自动根据指令中的比例（如 `16:9`）匹配最接近的模型支持分辨率：

| 比例 | 分辨率 (像素) | 适用场景 |
| :--- | :--- | :--- |
| **1:1** | 1024x1024, 2048x2048 | 头像、标准图 |
| **16:9** | 2048x1152, 1024x576 | 电脑壁纸、横屏插画 |
| **9:16** | 1152x2048, 576x1024 | 手机壁纸、人物全身像 |
| **4:3** | 2048x1536, 1152x896 | 传统摄影比例 |
| **3:4** | 1536x2048, 768x1024 | 竖构图摄影 |
| **3:2** | 2048x1360 | 单反相机常用 |
| **2:3** | 1360x2048 | 海报 |

---

## Gitee AI API Key获取方法：
1.访问https://ai.gitee.com/serverless-api?model=z-image-turbo

2.<img width="2241" height="1280" alt="PixPin_2025-12-05_16-56-27" src="https://github.com/user-attachments/assets/77f9a713-e7ac-4b02-8603-4afc25991841" />

3.免费额度<img width="240" height="63" alt="PixPin_2025-12-05_16-56-49" src="https://github.com/user-attachments/assets/6efde7c4-24c6-456a-8108-e78d7613f4fb" />

4.可以涩涩，警惕违规被举报

---

## 📂 文件保存位置

生成的图片会临时保存在：
`data/plugins/astrbot_plugin_gitee_aiimg/images`

---

### 🎉 出图效果展示

<div align="center">
  <img src="https://github.com/user-attachments/assets/c2390320-6d55-4db4-b3ad-0dde7b447c87" width="30%" />
  <img src="https://github.com/user-attachments/assets/3d8195e5-5d89-4a12-806e-8a81e348a96c" width="30%" />
  <img src="https://github.com/user-attachments/assets/c270ae7f-25f6-4d96-bbed-0299c9e61877" width="30%" />
</div>