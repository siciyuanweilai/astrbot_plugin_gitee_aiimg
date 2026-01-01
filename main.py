from astrbot.api.message_components import Plain, Image
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger
import os
import time
import datetime 
import base64
import asyncio
import aiohttp
import aiofiles  
from asyncio import Lock, Semaphore
from openai import AsyncOpenAI
from pathlib import Path
from typing import Optional, Tuple


# 常量定义
DEFAULT_BASE_URL = "https://ai.gitee.com/v1"
DEFAULT_MODEL = "z-image-turbo"
DEFAULT_SIZE = "1024x1024"
DEFAULT_INFERENCE_STEPS = 9
DEFAULT_NEGATIVE_PROMPT = (
    "low quality, bad anatomy, bad hands, text, error, missing fingers, "
    "extra digit, fewer digits, cropped, worst quality, normal quality, "
    "jpeg artifacts, signature, watermark, username, blurry"
)

# 用于逻辑判断的文本模型名称 (Gitee AI / SiliconFlow 默认支持)
TEXT_MODEL_NAME = "deepseek-ai/DeepSeek-V3" 


@register(
    "astrbot_plugin_gitee_aiimg", 
    "木有知 & 四次元未来", 
    "接入 Gitee AI 图像生成模型。支持 LLM 智能绘图、指令绘图、穿搭自动优化及多分辨率支持。", 
    "1.0.0"
)
class GiteeAIImage(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.base_url = config.get("base_url", DEFAULT_BASE_URL)
        
        # API Keys 配置
        self.api_keys = []
        api_keys = config.get("api_key", [])
        if isinstance(api_keys, str):
            if api_keys:
                self.api_keys = [k.strip() for k in api_keys.split(",") if k.strip()]
        elif isinstance(api_keys, list):
            self.api_keys = [str(k).strip() for k in api_keys if str(k).strip()]
        self.current_key_index = 0
        
        # 模型配置
        self.model = config.get("model", DEFAULT_MODEL)
        self.default_size = config.get("size", DEFAULT_SIZE)
        self.num_inference_steps = config.get("num_inference_steps", DEFAULT_INFERENCE_STEPS)
        self.negative_prompt = config.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT)
        
        # 性能配置
        self.generation_timeout = config.get("generation_timeout", 50)
        self.max_concurrent = config.get("max_concurrent", 3)
        
        # 缓存清理配置
        self.cache_cleanup_enabled = config.get("cache_cleanup_enabled", True)
        self.cache_max_age_hours = config.get("cache_max_age_hours", 24)
        self.cache_max_count = config.get("cache_max_count", 200)
        self.cache_protect_minutes = config.get("cache_protect_minutes", 5)
        self.cache_cleanup_interval = config.get("cache_cleanup_interval_minutes", 30) * 60
        
        # 支持的图片比例
        self.supported_ratios = {
            "1:1": ["256x256", "512x512", "1024x1024", "2048x2048"],
            "4:3": ["1152x896", "2048x1536"],
            "3:4": ["768x1024", "1536x2048"],
            "3:2": ["2048x1360"],
            "2:3": ["1360x2048"],
            "16:9": ["1024x576", "2048x1152"],
            "9:16": ["576x1024", "1152x2048"]
        }
        
        self.image_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
        
        # 并发控制
        self._state_lock = Lock()
        self._concurrent_limit = Semaphore(self.max_concurrent)
        
        # 状态管理
        self.processing_users = set()
        self.processed_message_ids = {}
        self.user_completion_times = {}
        
        # 定时任务
        self._cleanup_task: Optional[asyncio.Task] = None
        self._state_cleanup_task: Optional[asyncio.Task] = None

        # 人设增强配置
        self.persona_prefix = config.get("persona_prefix", "")
        self.auto_inject_persona = config.get("auto_inject_persona", False)

        # 资源复用
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._openai_clients: dict[str, AsyncOpenAI] = {}
        self._background_tasks: set[asyncio.Task] = set()

        self._start_cleanup_task()

    # 获取穿搭的方法
    async def _get_scheduler_outfit(self) -> str:
        """尝试从 life_scheduler 插件获取今日穿搭"""
        try:
            # 寻找 life_scheduler 插件实例
            scheduler_plugin = None
            for plugin in self.context.get_all_stars():
                # 根据插件注册名寻找
                if "life_scheduler" in getattr(plugin, "name", ""):
                    scheduler_plugin = getattr(plugin, "star_cls", None)
                    break
            
            if not scheduler_plugin:
                return ""

            # 获取今日日期字符串
            today_str = datetime.datetime.now().strftime("%Y-%m-%d")
            
            # 直接读取该插件的数据字典
            if hasattr(scheduler_plugin, "schedule_data"):
                data = scheduler_plugin.schedule_data.get(today_str, {})
                outfit = data.get("outfit", "")
                if outfit:
                    logger.debug(f"[GiteeAIImage] 已获取今日穿搭: {outfit[:15]}...")
                    return outfit
            return ""
        except Exception as e:
            logger.warning(f"[GiteeAIImage] 获取穿搭异常: {e}")
            return ""
    
    # 智能穿搭过滤方法
    async def _smart_filter_outfit(self, outfit: str, user_prompt: str) -> str:
        """使用 LLM 智能判断是否需要在穿搭中保留鞋子"""
        try:
            # 使用相同的 API Key 和 Base URL
            client = self._get_client()
            
            system_prompt = (
                "你是一个 Prompt 优化专家。"
                "任务：根据用户的【画面描述】，判断是否应该在【穿搭】中保留鞋子/靴子/袜子。"
                "规则："
                "1. 如果画面暗示【看不见脚】（如：自拍、半身像、坐姿特写、上半身、大头照、坐在桌后），请从穿搭中【删除】鞋袜描述。"
                "2. 如果画面暗示【能看见脚】（如：全身照、站立、行走、对镜自拍、展示穿搭），请【保留】鞋袜描述。"
                "3. 仅输出修改后的穿搭字符串，不要包含任何解释或标点之外的内容。"
            )
            
            user_msg = f"当前穿搭：{outfit}\n画面描述：{user_prompt}"

            # 调用 Chat 接口
            response = await client.chat.completions.create(
                model=TEXT_MODEL_NAME, # 使用硬编码的通用文本模型
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg}
                ],
                temperature=0.1, # 低温度以保证输出稳定
                max_tokens=200
            )
            
            result = response.choices[0].message.content.strip()
            # 简单清洗一下可能的废话
            if "穿搭" in result and len(result) > len(outfit) + 10:
                 # 如果LLM废话太多，回退
                 return outfit
                 
            logger.debug(f"[GiteeAIImage] LLM 智能优化:\n原: {outfit}\n场景: {user_prompt}\n新: {result}")
            return result

        except Exception as e:
            # 如果文本模型调用失败，静默失败并返回原穿搭
            logger.warning(f"[GiteeAIImage] 智能穿搭判断失败 (回退原样): {e}")
            return outfit

    def _start_cleanup_task(self):
        """启动后台清理任务"""
        if self.cache_cleanup_enabled and self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("[GiteeAIImage] 缓存清理任务已启动")
        
        if self._state_cleanup_task is None:
            self._state_cleanup_task = asyncio.create_task(self._state_cleanup_loop())

    async def _state_cleanup_loop(self):
        """状态清理循环（防止内存泄漏）"""
        while True:
            await asyncio.sleep(300)
            try:
                current_time = time.time()
                async with self._state_lock:
                    # 清理过期的消息 ID 记录
                    self.processed_message_ids = {
                        k: v for k, v in self.processed_message_ids.items()
                        if current_time - v <= 600
                    }
                    # 清理过期的用户完成时间
                    self.user_completion_times = {
                        k: v for k, v in self.user_completion_times.items()
                        if current_time - v <= 600
                    }
                logger.debug("[GiteeAIImage] 状态清理完成")
            except Exception as e:
                logger.error(f"[GiteeAIImage] 状态清理异常: {e}")

    async def _cleanup_loop(self):
        """缓存清理循环"""
        await asyncio.sleep(10)
        while True:
            try:
                # 使用线程池执行阻塞的文件操作
                await asyncio.to_thread(self._sync_cleanup)
            except Exception as e:
                logger.error(f"[GiteeAIImage] 清理任务异常: {e}")
            await asyncio.sleep(self.cache_cleanup_interval)

    def _get_image_dir(self) -> Path:
        """获取图片保存目录"""
        base_dir = StarTools.get_data_dir("astrbot_plugin_gitee_aiimg")
        image_dir = base_dir / "images"
        image_dir.mkdir(exist_ok=True)
        return image_dir

    def _parse_file_timestamp(self, filename: str) -> Optional[int]:
        """从文件名解析时间戳"""
        try:
            name_part = filename.rsplit(".", 1)[0]
            timestamp_str = name_part.split("_")[0]
            return int(timestamp_str)
        except (ValueError, IndexError):
            return None

    def _get_file_age(self, filepath: Path) -> float:
        """获取文件年龄（秒）"""
        timestamp = self._parse_file_timestamp(filepath.name)
        if timestamp is not None:
            return time.time() - timestamp
        try:
            return time.time() - filepath.stat().st_mtime
        except OSError:
            return 0

    def _is_image_file(self, filepath: Path) -> bool:
        """判断是否为图片文件"""
        return filepath.suffix.lower() in self.image_extensions

    def _sync_cleanup(self) -> Tuple[int, int, float]:
        """同步清理方法（在线程池中执行）"""
        image_dir = self._get_image_dir()
        if not image_dir.exists():
            return 0, 0, 0.0
        
        max_age_seconds = self.cache_max_age_hours * 3600
        protect_seconds = self.cache_protect_minutes * 60
        
        # 收集文件信息
        files_info = []
        for filepath in image_dir.iterdir():
            if filepath.is_file() and self._is_image_file(filepath):
                try:
                    age = self._get_file_age(filepath)
                    size = filepath.stat().st_size
                    files_info.append({"path": filepath, "age": age, "size": size})
                except OSError:
                    continue
        
        # 按年龄排序（从旧到新）
        files_info.sort(key=lambda x: x["age"], reverse=True)
        
        to_delete = []
        freed_bytes = 0
        
        # 删除超龄文件
        for info in files_info:
            if info["age"] > max_age_seconds and info["age"] > protect_seconds:
                to_delete.append(info)
                freed_bytes += info["size"]
        
        # 删除超量文件
        remaining = [f for f in files_info if f not in to_delete]
        while len(remaining) > self.cache_max_count:
            oldest = remaining[0]
            if oldest["age"] > protect_seconds:
                to_delete.append(oldest)
                freed_bytes += oldest["size"]
                remaining.pop(0)
            else:
                break
        
        # 执行删除
        deleted_count = 0
        for info in to_delete:
            try:
                info["path"].unlink()
                deleted_count += 1
            except OSError as e:
                logger.warning(f"[GiteeAIImage] 删除文件失败 {info['path'].name}: {e}")
        
        freed_mb = freed_bytes / (1024 * 1024)
        remaining_count = len(files_info) - deleted_count
        
        if deleted_count > 0:
            logger.info(
                f"[GiteeAIImage] 缓存清理: 删除 {deleted_count} 张, "
                f"剩余 {remaining_count} 张, 释放 {freed_mb:.2f} MB"
            )
        
        return deleted_count, remaining_count, freed_mb

    async def _do_cleanup(self) -> Tuple[int, int, float]:
        """异步清理接口（保持向后兼容）"""
        return await asyncio.to_thread(self._sync_cleanup)

    def _get_cache_stats(self) -> dict:
        """获取缓存统计信息"""
        image_dir = self._get_image_dir()
        if not image_dir.exists():
            return {"count": 0, "size_mb": 0.0, "oldest_hours": 0.0}
        
        total_size = 0
        oldest_age = 0
        count = 0
        
        for filepath in image_dir.iterdir():
            if filepath.is_file() and self._is_image_file(filepath):
                try:
                    total_size += filepath.stat().st_size
                    age = self._get_file_age(filepath)
                    oldest_age = max(oldest_age, age)
                    count += 1
                except OSError:
                    continue
        
        return {
            "count": count,
            "size_mb": total_size / (1024 * 1024),
            "oldest_hours": oldest_age / 3600
        }

    # HTTP Session 复用
    async def _get_http_session(self) -> aiohttp.ClientSession:
        """获取复用的 HTTP Session"""
        if self._http_session is None or self._http_session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._http_session = aiohttp.ClientSession(timeout=timeout)
        return self._http_session

    # OpenAI 客户端复用
    def _get_client(self) -> AsyncOpenAI:
        """获取复用的 AsyncOpenAI 客户端"""
        # 支持热重载配置
        if not self.api_keys:
            api_keys = self.config.get("api_key", [])
            if isinstance(api_keys, str):
                if api_keys:
                    self.api_keys = [k.strip() for k in api_keys.split(",") if k.strip()]
            elif isinstance(api_keys, list):
                self.api_keys = [str(k).strip() for k in api_keys if str(k).strip()]
        
        if not self.api_keys:
            raise ValueError("请先配置 API Key")
        
        # 轮询选择 API Key
        api_key = self.api_keys[self.current_key_index]
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        
        # 复用客户端
        if api_key not in self._openai_clients:
            self._openai_clients[api_key] = AsyncOpenAI(
                base_url=self.base_url,
                api_key=api_key,
                timeout=self.generation_timeout + 5,
            )
        
        return self._openai_clients[api_key]

    def _get_save_path(self, extension: str = ".jpg") -> str:
        """生成唯一的文件保存路径"""
        image_dir = self._get_image_dir()
        filename = f"{int(time.time())}_{os.urandom(4).hex()}{extension}"
        return str(image_dir / filename)

    # 使用 aiofiles 异步下载图片
    async def _download_image(self, url: str) -> str:
        """异步下载图片"""
        session = await self._get_http_session()
        
        async with session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"下载图片失败: HTTP {resp.status}")
            data = await resp.read()
        
        filepath = self._get_save_path()
        async with aiofiles.open(filepath, "wb") as f:
            await f.write(data)
        
        return filepath

    # 使用 aiofiles 异步保存 Base64 图片
    async def _save_base64_image(self, b64_data: str) -> str:
        """异步保存 Base64 图片"""
        filepath = self._get_save_path()
        image_bytes = base64.b64decode(b64_data)
        
        async with aiofiles.open(filepath, "wb") as f:
            await f.write(image_bytes)
        
        return filepath

    async def _generate_image(self, prompt: str, size: str = "") -> str:
        """生成图片"""
        # 自动注入人设前缀
        if self.auto_inject_persona and self.persona_prefix:
            prompt = self.persona_prefix + " " + prompt

        async with self._concurrent_limit:
            client = self._get_client()
            target_size = size if size else self.default_size
            
            kwargs = {
                "prompt": prompt,
                "model": self.model,
                "extra_body": {"num_inference_steps": self.num_inference_steps}
            }
            
            if self.negative_prompt:
                kwargs["extra_body"]["negative_prompt"] = self.negative_prompt
            if target_size:
                kwargs["size"] = target_size
            
            try:
                response = await asyncio.wait_for(
                    client.images.generate(**kwargs),
                    timeout=self.generation_timeout
                )
            except asyncio.TimeoutError:
                raise Exception(f"生成超时({self.generation_timeout}秒)，请稍后再试")
            except asyncio.CancelledError:
                raise Exception("生成被取消，请稍后再试")
            except Exception as e:
                error_msg = str(e)
                if "401" in error_msg:
                    raise Exception("API Key 无效或已过期")
                elif "429" in error_msg:
                    raise Exception("API 调用次数超限，请稍后再试")
                elif "500" in error_msg:
                    raise Exception("服务器内部错误，请稍后再试")
                else:
                    raise Exception(f"API调用失败: {error_msg}")
            
            if not response.data:
                raise Exception("生成失败：未返回数据")
            
            image_data = response.data[0]
            if image_data.url:
                return await self._download_image(image_data.url)
            elif image_data.b64_json:
                return await self._save_base64_image(image_data.b64_json)
            else:
                raise Exception("生成失败：未返回有效数据")

    def _get_message_id(self, event: AstrMessageEvent) -> str:
        """获取消息唯一 ID"""
        try:
            msg_id = event.message_obj.message_id
            if msg_id:
                return str(msg_id)
        except:
            pass
        user_id = event.get_sender_id()
        msg_str = event.message_str[:100] if event.message_str else ""
        return f"{user_id}_{hash(msg_str)}"

    @filter.llm_tool(name="draw_image")
    async def draw(self, event: AstrMessageEvent, prompt: str):
        """根据提示词生成图片。每条消息只能调用一次。

        【重要规则】
        如果是生成"自己"的图片，prompt 必须严格按照系统人设中的外貌描述来写，包括：
        - 年龄、国籍、身高等基本信息  
        - 发型、发色、眼睛、肤色等外貌特征
        - 当前的服装、场景、姿态、表情
        
        不要省略人设中的任何外貌细节！
        
        Args:
            prompt(string): 完整的图片描述，必须包含人设中的外貌特征
        """
        user_id = event.get_sender_id()
        message_id = self._get_message_id(event)
        current_time = time.time()
        
        async with self._state_lock:
            # 防止重复处理
            if message_id in self.processed_message_ids:
                logger.debug(f"[GiteeAIImage] 消息 {message_id} 已处理，跳过")
                return "图片已生成并发送，请直接用文字回复用户。"
            
            # 防抖检查
            if user_id in self.user_completion_times:
                time_since = current_time - self.user_completion_times[user_id]
                if time_since < 30.0:
                    return "请求过于频繁，请稍后再试。"
            
            # 并发控制
            if user_id in self.processing_users:
                return "图片正在生成中，请等待。"
            
            self.processed_message_ids[message_id] = current_time
            self.processing_users.add(user_id)
        
        try:
            # 自然语言对话调用时，自动注入穿搭
            outfit = await self._get_scheduler_outfit()
            if outfit:
                # 调用 LLM 智能清洗穿搭
                # 无需额外配置，复用生图的 API Key，模型写死为通用模型
                refined_outfit = await self._smart_filter_outfit(outfit, prompt)
                prompt = f"({refined_outfit}), {prompt}"
            
            logger.info(f"[GiteeAIImage] 开始生成: {prompt[:50]}...")
            image_path = await self._generate_image(prompt)
            logger.info(f"[GiteeAIImage] 生成完成: {image_path}")
            
            try:
                await event.send(event.chain_result([Image.fromFileSystem(image_path)]))
                logger.info("[GiteeAIImage] 图片已发送")
            except Exception as send_err:
                logger.error(f"[GiteeAIImage] 图片发送失败: {send_err}")
                return f"图片生成成功但发送失败: {send_err}"
            
            async with self._state_lock:
                self.user_completion_times[user_id] = time.time()
            
            return "图片已成功生成并发送。请用文字自然地回复用户，不要再调用工具。"
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[GiteeAIImage] 生成失败: {error_msg}")
            async with self._state_lock:
                self.processed_message_ids.pop(message_id, None)
            return f"生成失败: {error_msg}。请告诉用户稍后再试。"
            
        finally:
            async with self._state_lock:
                self.processing_users.discard(user_id)

    @filter.command("aiimg")
    async def generate_image_command(self, event: AstrMessageEvent, prompt: str):
        """生成图片指令。用法: /aiimg <提示词> [比例]"""
        if not prompt:
            yield event.plain_result("请提供提示词！用法：/aiimg <提示词> [比例]")
            return
        
        user_id = event.get_sender_id()
        
        async with self._state_lock:
            if user_id in self.processing_users:
                yield event.plain_result("您有正在进行的生图任务，请稍候...")
                return
            self.processing_users.add(user_id)
        
        # 解析比例参数
        ratio = "1:1"
        prompt_parts = prompt.rsplit(" ", 1)
        if len(prompt_parts) > 1 and prompt_parts[1] in self.supported_ratios:
            ratio = prompt_parts[1]
            prompt = prompt_parts[0]
        
        target_size = self.default_size
        if ratio != "1:1" or self.default_size not in self.supported_ratios["1:1"]:
            target_size = self.supported_ratios[ratio][0]
        
        try:
            # 注意：指令调用直接使用用户原提示词，不注入穿搭
            image_path = await self._generate_image(prompt, size=target_size)
            yield event.chain_result([Image.fromFileSystem(image_path)])
        except Exception as e:
            logger.error(f"[GiteeAIImage] 命令生图失败: {e}")
            yield event.plain_result(f"生成失败: {str(e)}")
        finally:
            async with self._state_lock:
                self.processing_users.discard(user_id)

    @filter.command("aiimg_clean")
    async def clean_cache_command(self, event: AstrMessageEvent):
        """清空所有图片缓存"""
        image_dir = self._get_image_dir()
        
        if not image_dir.exists():
            yield event.plain_result("缓存目录不存在")
            return
        
        before_stats = self._get_cache_stats()
        
        if before_stats["count"] == 0:
            yield event.plain_result("缓存为空，无需清理")
            return
        
        msg = "开始清理...\n当前: {} 张, {:.2f} MB".format(
            before_stats['count'], before_stats['size_mb']
        )
        yield event.plain_result(msg)
        
        # 使用线程池执行同步清理
        deleted_count, freed_bytes = await asyncio.to_thread(self._sync_clean_all)
        
        freed_mb = freed_bytes / (1024 * 1024)
        
        if deleted_count > 0:
            logger.info(f"[GiteeAIImage] 手动清理: 删除 {deleted_count} 张, 释放 {freed_mb:.2f} MB")
            result = "✅ 清理完成\n删除: {} 张\n释放: {:.2f} MB".format(deleted_count, freed_mb)
            yield event.plain_result(result)
        else:
            yield event.plain_result("没有成功删除任何文件")

    def _sync_clean_all(self) -> Tuple[int, int]:
        """同步清理所有文件（在线程池中执行）"""
        image_dir = self._get_image_dir()
        deleted_count = 0
        freed_bytes = 0
        
        for filepath in image_dir.iterdir():
            if filepath.is_file() and self._is_image_file(filepath):
                try:
                    freed_bytes += filepath.stat().st_size
                    filepath.unlink()
                    deleted_count += 1
                except OSError:
                    continue
        
        return deleted_count, freed_bytes

    @filter.command("aiimg_stats")
    async def cache_stats_command(self, event: AstrMessageEvent):
        """查看缓存统计"""
        stats = self._get_cache_stats()
        cleanup_status = "已启用" if self.cache_cleanup_enabled else "已禁用"
        
        lines = [
            "📊 图片缓存统计",
            "━━━━━━━━━━━━━━━",
            "缓存数量: {} 张".format(stats['count']),
            "占用空间: {:.2f} MB".format(stats['size_mb']),
            "最旧文件: {:.1f} 小时前".format(stats['oldest_hours']),
            "━━━━━━━━━━━━━━━",
            "自动清理: {}".format(cleanup_status),
            "保留时间: {} 小时".format(self.cache_max_age_hours),
            "数量上限: {} 张".format(self.cache_max_count),
            "并发限制: {}".format(self.max_concurrent),
            "生成超时: {} 秒".format(self.generation_timeout)
        ]
        yield event.plain_result("\n".join(lines))

    async def terminate(self):
        """插件卸载清理"""
        logger.info("[GiteeAIImage] 开始卸载插件...")
        
        # 1. 取消定时任务
        tasks_to_cancel = [
            ("缓存清理", self._cleanup_task),
            ("状态清理", self._state_cleanup_task)
        ]
        
        for task_name, task in tasks_to_cancel:
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logger.debug(f"[GiteeAIImage] {task_name}任务已取消")
        
        # 2. 取消所有后台任务
        if self._background_tasks:
            logger.debug(f"[GiteeAIImage] 取消 {len(self._background_tasks)} 个后台任务")
            for task in self._background_tasks:
                if not task.done():
                    task.cancel()
            
            # 等待任务完成（设置超时）
            if self._background_tasks:
                await asyncio.wait(self._background_tasks, timeout=5.0)
        
        # 3. 关闭 HTTP Session
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
            logger.debug("[GiteeAIImage] HTTP Session 已关闭")
        
        # 4. 关闭所有 OpenAI 客户端
        for api_key, client in self._openai_clients.items():
            try:
                await client.close()
            except Exception as e:
                logger.warning(f"[GiteeAIImage] 关闭客户端失败: {e}")
        self._openai_clients.clear()
        logger.debug(f"[GiteeAIImage] 已关闭所有 OpenAI 客户端")
        
        logger.info("[GiteeAIImage] 插件已完全卸载，所有资源已清理")
