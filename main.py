import asyncio
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, StarTools, register
import datetime

from .core.debouncer import Debouncer
from .core.image import ImageManager
from .core.service import ImageService, EDIT_TASK_TYPES


@register(
    "astrbot_plugin_gitee_aiimg", 
    "木有知 & 四次元未来", 
    "接入 Gitee AI 图像生成模型。支持 LLM 智能绘图、图生图、指令绘图、穿搭自动优化及多分辨率支持。", 
    "2.1.0"
)
class GiteeAIImage(Star):
    # Gitee AI 支持的图片比例
    SUPPORTED_RATIOS: dict[str, list[str]] = {
        "1:1": ["256x256", "512x512", "1024x1024", "2048x2048"],
        "4:3": ["1152x896", "2048x1536"],
        "3:4": ["768x1024", "1536x2048"],
        "3:2": ["2048x1360"],
        "2:3": ["1360x2048"],
        "16:9": ["1024x576", "2048x1152"],
        "9:16": ["576x1024", "1152x2048"],
    }

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_gitee_aiimg")
        
        # 状态管理
        self.processing_users: set[str] = set()
        self._background_tasks: set[asyncio.Task] = set()

    async def initialize(self):
        # 初始化各模块
        self.debouncer = Debouncer(self.config)
        self.imgr = ImageManager(self.config, self.data_dir)
        self.service = ImageService(self.config, self.imgr)
        
        # 启动缓存清理任务
        await self.imgr.start_cleanup_task()

    async def terminate(self):
        # 取消后台任务
        for task in self._background_tasks:
            task.cancel()
        self._background_tasks.clear()

        # 清理资源
        self.debouncer.clear_all()
        await self.imgr.close()
        await self.service.close()

    # ========== 辅助逻辑 ==========

    async def _get_scheduler_outfit(self) -> str:
        """尝试从 life_scheduler 插件获取今日穿搭 (新版逻辑)"""
        try:
            scheduler_plugin = None
            for plugin in self.context.get_all_stars():
                if "life_scheduler" in getattr(plugin, "name", ""):
                    scheduler_plugin = getattr(plugin, "star_cls", None)
                    break
            
            if not scheduler_plugin:
                return ""

            today_str = datetime.datetime.now().strftime("%Y-%m-%d")
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

    # ========== 文生图功能 ==========

    @filter.llm_tool(name="draw_image")
    async def draw_image_tool(self, event: AstrMessageEvent, prompt: str, is_self: bool = True):
        """根据提示词生成图片。每条消息只能调用一次。

        Args:
            prompt(string): 完整的图片描述。请直接使用中文描述。
            is_self(bool): 这张图是否是画你自己(Bot人格)？
                           - 如果是画你自己、自拍、你的穿搭，设为 True。
                           - 如果是画风景、动物、路人、其他角色、抽象概念，必须设为 False。
                           - 默认为 True。
        """
        request_id = event.get_sender_id()

        if self.debouncer.hit(request_id):
            return "操作太快了，请稍后再试。"

        if request_id in self.processing_users:
            return "您有正在进行的生图任务，请稍候..."

        self.processing_users.add(request_id)
        
        try:
            final_prompt = prompt
            # 人设与穿搭注入逻辑 
            if is_self:
                # 1. 穿搭注入
                outfit = await self._get_scheduler_outfit()
                if outfit:
                    # 智能清洗穿搭
                    refined_outfit = await self.service.smart_filter_outfit(outfit, prompt)
                    final_prompt = f"({refined_outfit}), {prompt}"
                
                # 2. 人设前缀注入 (通过 Service 层处理或在此处理，这里选择在此拼接)
                if self.config.get("auto_inject_persona") and self.config.get("persona_prefix"):
                    final_prompt = f"{self.config['persona_prefix']} {final_prompt}"

            logger.info(f"[draw_image] Prompts: {final_prompt[:50]}... (is_self={is_self})")
            
            # 使用配置的默认尺寸
            target_size = self.config.get("size", "1024x1024")
            image_path = await self.service.generate(final_prompt, size=target_size)
            
            await event.send(event.chain_result([Image.fromFileSystem(str(image_path))]))
            return "图片已成功生成并发送。请用文字自然地回复用户，不要再调用工具。"

        except Exception as e:
            logger.error(f"生图失败: {e}")
            return f"生成图片时遇到问题: {str(e)}"
        finally:
            self.processing_users.discard(request_id)

    @filter.command("aiimg")
    async def generate_image_command(self, event: AstrMessageEvent, prompt: str):
        """生成图片指令。用法: /aiimg <提示词> [比例]"""
        if not prompt:
            yield event.plain_result("请提供提示词！用法：/aiimg <提示词> [比例]")
            return

        request_id = event.get_sender_id()

        if self.debouncer.hit(request_id):
            yield event.plain_result("操作太快了，请稍后再试。")
            return
        
        if request_id in self.processing_users:
            yield event.plain_result("您有正在进行的生图任务，请稍候...")
            return
        
        self.processing_users.add(request_id)

        # 解析比例 
        ratio = "1:1"
        prompt_parts = prompt.rsplit(" ", 1)
        if len(prompt_parts) > 1 and prompt_parts[1] in self.SUPPORTED_RATIOS:
            ratio = prompt_parts[1]
            prompt = prompt_parts[0]

        default_size = self.config.get("size", "1024x1024")
        if ratio != "1:1" or default_size not in self.SUPPORTED_RATIOS["1:1"]:
            target_size = self.SUPPORTED_RATIOS[ratio][0]
        else:
            target_size = default_size

        try:
            # 指令模式不注入人设，保持纯净
            image_path = await self.service.generate(prompt, size=target_size)
            yield event.chain_result([Image.fromFileSystem(str(image_path))])
        except Exception as e:
            logger.error(f"命令生图失败: {e}")
            yield event.plain_result(f"生成失败: {str(e)}")
        finally:
            self.processing_users.discard(request_id)

    # ========== 图生图功能 ==========

    @filter.llm_tool(name="edit_image")
    async def edit_image_tool(
        self,
        event: AstrMessageEvent,
        prompt: str,
        use_message_images: bool = True,
        task_types: str = "id",
    ):
        """编辑用户发送的图片或引用的图片。当用户发送/引用了图片并希望修改、改图、换背景、换风格、换衣服、P图时调用此工具。

        Args:
            prompt(string): 图片编辑提示词，描述用户希望对图片做的修改。
            use_message_images(boolean): 是否自动获取用户消息中的图片，默认 true。
            task_types(string): 任务类型，逗号分隔。可选值: id(保持身份/默认), style(风格迁移), subject(主体替换), background(背景替换), element(元素编辑)。
        """
        user_id = event.get_sender_id()
        request_id = f"edit_{user_id}"

        if self.debouncer.hit(request_id):
            return "操作太快了，请稍后再试。"
        
        if request_id in self.processing_users:
            return "您有正在进行的图生图任务，请稍候..."

        # 提取图片
        image_data_list = []
        if use_message_images:
            image_data_list = await self.imgr.extract_images_from_event(event)

        if not image_data_list:
            return "请在消息中附带需要编辑的图片。提示：发送图片或引用图片后再发送修改指令。"

        self.processing_users.add(request_id)
        types = [t.strip() for t in task_types.split(",") if t.strip()]

        # 启动后台任务
        async def _background_edit():
            try:
                image_path = await self.service.edit_image(prompt, image_data_list, types)
                await event.send(event.chain_result([Image.fromFileSystem(str(image_path))]))
                logger.info(f"[edit_image] 完成: {prompt[:30]}")
            except Exception as e:
                logger.error(f"[edit_image] 失败: {e}")
                await event.send(event.plain_result(f"编辑图片失败: {str(e)}"))
            finally:
                self.processing_users.discard(request_id)

        task = asyncio.create_task(_background_edit())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return f"正在编辑图片，请稍候...（提示词: {prompt[:30]}...）"

    @filter.command("aiedit")
    async def edit_image_command(self, event: AstrMessageEvent, prompt: str):
        """图生图指令。用法: /aiedit <提示词> [任务类型]
        支持类型: id, style, subject, background, element
        """
        if not prompt:
            yield event.plain_result("请提供提示词！用法：/aiedit <提示词> [任务类型]")
            return

        user_id = event.get_sender_id()
        request_id = f"edit_{user_id}"

        if self.debouncer.hit(request_id):
            yield event.plain_result("操作太快了，请稍后再试。")
            return

        if request_id in self.processing_users:
            yield event.plain_result("您有正在进行的生图任务，请稍候...")
            return

        image_data_list = await self.imgr.extract_images_from_event(event)
        if not image_data_list:
            yield event.plain_result("请在消息中附带需要编辑的图片！(发送或引用)")
            return

        self.processing_users.add(request_id)
        
        # 解析任务类型
        task_types = ["id"]
        prompt_parts = prompt.rsplit(" ", 1)
        if len(prompt_parts) > 1:
            potential_types = prompt_parts[1]
            parsed_types = [t.strip() for t in potential_types.split(",")]
            if all(t in EDIT_TASK_TYPES for t in parsed_types):
                task_types = parsed_types
                prompt = prompt_parts[0]

        try:
            image_path = await self.service.edit_image(prompt, image_data_list, task_types)
            yield event.chain_result([Image.fromFileSystem(str(image_path))])
        except Exception as e:
            yield event.plain_result(f"编辑失败: {str(e)}")
        finally:
            self.processing_users.discard(request_id)

    # ========== 缓存管理 ==========

    @filter.command("aiimg_clean")
    async def clean_cache_command(self, event: AstrMessageEvent):
        """清空所有图片缓存"""
        stats = await self.imgr.get_cache_stats()
        if stats["count"] == 0:
            yield event.plain_result("缓存为空，无需清理")
            return

        msg = f"开始清理...\n当前: {stats['count']} 张, {stats['size_mb']:.2f} MB"
        yield event.plain_result(msg)

        deleted_count, freed_bytes = await self.imgr.clean_all_cache()
        freed_mb = freed_bytes / (1024 * 1024)
        
        yield event.plain_result(f"✅ 清理完成\n删除: {deleted_count} 张\n释放: {freed_mb:.2f} MB")

    @filter.command("aiimg_stats")
    async def cache_stats_command(self, event: AstrMessageEvent):
        """查看缓存统计"""
        stats = await self.imgr.get_cache_stats()
        cleanup_status = "已启用" if self.config.get("cache_cleanup_enabled") else "已禁用"
        
        lines = [
            "📊 图片缓存统计",
            "━━━━━━━━━━━━━━━",
            f"缓存数量: {stats['count']} 张",
            f"占用空间: {stats['size_mb']:.2f} MB",
            f"最旧文件: {stats['oldest_hours']:.1f} 小时前",
            "━━━━━━━━━━━━━━━",
            f"自动清理: {cleanup_status}",
            f"保留时间: {self.config.get('cache_max_age_hours')} 小时",
            f"数量上限: {self.config.get('cache_max_count')} 张",
        ]
        yield event.plain_result("\n".join(lines))
