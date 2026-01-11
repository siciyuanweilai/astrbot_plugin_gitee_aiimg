import asyncio
import aiohttp
from pathlib import Path
from openai import AsyncOpenAI
from astrbot.api import logger
from .image import ImageManager

EDIT_TASK_TYPES = ["id", "style", "subject", "background", "element"]

class ImageService:
    def __init__(self, config: dict, imgr: ImageManager):
        self.config = config
        self.imgr = imgr
        
        # 客户端管理
        self._clients: dict[str, AsyncOpenAI] = {}
        self.api_keys = self._parse_keys(config.get("api_key"))
        self._key_idx = 0
        
        # 图生图 Key
        self.edit_keys = self._parse_keys(config.get("edit_api_key")) or self.api_keys
        self._edit_key_idx = 0

    async def close(self):
        for c in self._clients.values():
            await c.close()
        self._clients.clear()

    @staticmethod
    def _parse_keys(keys) -> list[str]:
        if isinstance(keys, str): return [k.strip() for k in keys.split(",") if k.strip()]
        if isinstance(keys, list): return [str(k).strip() for k in keys if str(k).strip()]
        return []

    def _get_client(self, for_edit=False) -> tuple[AsyncOpenAI, str]:
        # 选择 Key
        if for_edit:
            keys = self.edit_keys
            idx = self._edit_key_idx
            self._edit_key_idx = (idx + 1) % len(keys)
        else:
            # 支持热更新
            if not self.api_keys:
                self.api_keys = self._parse_keys(self.config.get("api_key"))
            keys = self.api_keys
            if not keys: raise ValueError("未配置 API Key")
            idx = self._key_idx
            self._key_idx = (idx + 1) % len(keys)
        
        key = keys[idx]
        if key not in self._clients:
            base_url = self.config.get("base_url", "https://ai.gitee.com/v1")
            self._clients[key] = AsyncOpenAI(
                base_url=base_url,
                api_key=key,
                timeout=self.config.get("timeout", 60),
                max_retries=2
            )
        return self._clients[key], key

    # ========== 智能辅助 ==========

    async def smart_filter_outfit(self, outfit: str, user_prompt: str) -> str:
        """调用文本模型清洗穿搭"""
        try:
            client, _ = self._get_client()
            model = self.config.get("text_model", "deepseek-ai/DeepSeek-V3")
            
            system_prompt = (
                "你是一个 AI 绘画提示词专家。根据用户的【画面描述】，决定是否在【穿搭】中保留鞋子/靴子/袜子。"
                "1. 只有当描述包含“全身”、“Full body”、“从头到脚”时，保留鞋袜。"
                "2. 如果只是模糊的“站立”或未提及全身，删除鞋袜描述，防止构图崩坏。"
                "3. 仅输出修改后的穿搭字符串，不要包含解释。"
            )
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"穿搭: {outfit}\n画面: {user_prompt}"}
                ],
                temperature=0.1, max_tokens=200
            )
            result = resp.choices[0].message.content.strip()
            if len(result) > len(outfit) + 20: return outfit # 简单风控
            logger.debug(f"[SmartFilter] 原: {outfit} -> 新: {result}")
            return result
        except Exception as e:
            logger.warning(f"智能穿搭判断失败: {e}")
            return outfit

    # ========== 文生图 ==========

    async def generate(self, prompt: str, size: str) -> Path:
        client, _ = self._get_client()
        kwargs = {
            "prompt": prompt,
            "model": self.config.get("model", "z-image-turbo"),
            "size": size,
            "extra_body": {"num_inference_steps": self.config.get("num_inference_steps", 9)}
        }
        if self.config.get("negative_prompt"):
            kwargs["extra_body"]["negative_prompt"] = self.config.get("negative_prompt")
        
        try:
            resp = await client.images.generate(**kwargs)
            img = resp.data[0]
            if img.url: return await self.imgr.download_image(img.url)
            if img.b64_json: return await self.imgr.save_base64_image(img.b64_json)
            raise RuntimeError("无图片数据返回")
        except Exception as e:
            if "401" in str(e): raise RuntimeError("API Key 无效") from e
            if "429" in str(e): raise RuntimeError("请求过快") from e
            raise

    # ========== 图生图 ==========

    async def edit_image(self, prompt: str, images: list[bytes], types: list[str]) -> Path:
        # 1. 创建任务
        _, api_key = self._get_client(for_edit=True) # 仅为了轮询 Key
        base_url = self.config.get("edit_base_url") or self.config.get("base_url")
        
        data = aiohttp.FormData()
        data.add_field("prompt", prompt)
        data.add_field("model", "Qwen-Image-Edit-2511") # 固定模型
        data.add_field("num_inference_steps", "4")
        data.add_field("guidance_scale", "1.0")
        for t in types: data.add_field("task_types", t)
        
        for idx, img in enumerate(images):
            data.add_field("image", img, filename=f"img_{idx}.jpg", content_type="image/jpeg")

        headers = {"Authorization": f"Bearer {api_key}", "X-Failover-Enabled": "true"}
        
        async with self.imgr._session.post(f"{base_url}/async/images/edits", headers=headers, data=data) as resp:
            res = await resp.json()
            if resp.status != 200: raise RuntimeError(f"API Error: {res}")
            task_id = res.get("task_id")

        # 2. 轮询状态
        for _ in range(60): # 300s timeout
            await asyncio.sleep(5)
            async with self.imgr._session.get(f"{base_url}/task/{task_id}", headers=headers) as resp:
                res = await resp.json()
                status = res.get("status")
                if status == "success":
                    return await self.imgr.download_image(res["output"]["file_url"])
                if status in ["failed", "cancelled"]:
                    raise RuntimeError(f"Task {status}: {res.get('error')}")
        
        raise RuntimeError("图生图任务超时")
