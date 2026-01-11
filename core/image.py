import asyncio
import base64
import os
import time
from pathlib import Path
from typing import Tuple, Optional

import aiofiles
import aiohttp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image
from astrbot.core.message.components import Reply
from astrbot.core.utils.io import download_image_by_url

class ImageManager:
    def __init__(self, config: dict, data_dir: Path):
        self.config = config
        self.image_dir = data_dir / "images"
        self.image_dir.mkdir(parents=True, exist_ok=True)
        
        # 配置
        self.timeout = config.get("timeout", 60)
        self.image_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
        
        # Session 复用
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout)
        )
        self._cleanup_task: Optional[asyncio.Task] = None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
        if self._cleanup_task:
            self._cleanup_task.cancel()

    # ========== 文件操作 ==========

    def _get_save_path(self, extension: str = ".jpg") -> Path:
        filename = f"{int(time.time())}_{os.urandom(4).hex()}{extension}"
        return self.image_dir / filename

    async def save_image(self, data: bytes) -> Path:
        path = self._get_save_path()
        async with aiofiles.open(path, "wb") as f:
            await f.write(data)
        return path

    async def save_base64_image(self, b64: str) -> Path:
        try:
            data = base64.b64decode(b64)
            return await self.save_image(data)
        except Exception as e:
            raise ValueError(f"Base64 解码失败: {e}")

    async def download_image(self, url: str) -> Path:
        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise RuntimeError(f"下载失败 HTTP {resp.status}")
            data = await resp.read()
        return await self.save_image(data)

    # ========== 图片提取 ==========

    async def extract_images_from_event(self, event: AstrMessageEvent) -> list[bytes]:
        """从消息中提取图片数据，支持 Reply、URL、Base64、本地文件"""
        images: list[bytes] = []
        chain = event.message_obj.message

        # 1. 检查回复引用
        for seg in chain:
            if isinstance(seg, Reply) and hasattr(seg, "chain") and seg.chain:
                for item in seg.chain:
                    if isinstance(item, Image):
                        data = await self._load_image_data(item)
                        if data: images.append(data)

        # 2. 检查当前消息
        for seg in chain:
            if isinstance(seg, Image):
                data = await self._load_image_data(seg)
                if data: images.append(data)
        
        return images

    async def _load_image_data(self, img: Image) -> bytes | None:
        # 1. 本地文件 (NapCat/LLOneBot)
        file_path = getattr(img, "file", None)
        if file_path and not str(file_path).startswith("http"):
            path_obj = Path(file_path)
            # 尝试绝对路径
            if path_obj.is_file():
                return await asyncio.to_thread(path_obj.read_bytes)
            # 尝试相对 data 目录的常见缓存位置
            possible_dirs = [Path("data/Cache/Image"), Path("data/image_cache")]
            for d in possible_dirs:
                if (d / file_path).is_file():
                    return await asyncio.to_thread((d / file_path).read_bytes)

        # 2. Base64
        if getattr(img, "base64", None):
            try:
                return base64.b64decode(img.base64)
            except: pass

        # 3. URL
        url = getattr(img, "url", None)
        if url:
            # 优先使用 AstrBot 工具下载
            path = await download_image_by_url(url)
            if path:
                return await asyncio.to_thread(Path(path).read_bytes)
            # Fallback
            try:
                async with self._session.get(url, timeout=30) as resp:
                    if resp.status == 200: return await resp.read()
            except: pass
        
        return None

    # ========== 缓存清理与统计 ==========

    async def start_cleanup_task(self):
        if self.config.get("cache_cleanup_enabled", True) and not self._cleanup_task:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("[GiteeAIImage] 缓存清理任务已启动")

    async def _cleanup_loop(self):
        await asyncio.sleep(10)
        while True:
            try:
                deleted, _, _ = await asyncio.to_thread(self._sync_cleanup)
                if deleted > 0:
                    logger.info(f"[GiteeAIImage] 自动清理: 删除 {deleted} 张图片")
            except Exception as e:
                logger.error(f"[GiteeAIImage] 清理异常: {e}")
            # 每 30 分钟检查一次
            await asyncio.sleep(1800)

    def _sync_cleanup(self) -> Tuple[int, int, float]:
        """同步清理逻辑"""
        if not self.image_dir.exists(): return 0, 0, 0.0

        max_age = self.config.get("cache_max_age_hours", 24) * 3600
        max_count = self.config.get("cache_max_count", 200)
        now = time.time()
        
        files = []
        for p in self.image_dir.iterdir():
            if p.is_file() and p.suffix.lower() in self.image_extensions:
                files.append((p, p.stat().st_mtime, p.stat().st_size))
        
        # 按时间排序：旧 -> 新
        files.sort(key=lambda x: x[1])

        to_delete = []
        deleted_count = 0
        freed_bytes = 0

        # 1. 清理过期
        remaining = []
        for f in files:
            if now - f[1] > max_age:
                to_delete.append(f)
            else:
                remaining.append(f)
        
        # 2. 清理超量
        while len(remaining) > max_count:
            to_delete.append(remaining.pop(0)) # 删除最旧的

        for p, _, size in to_delete:
            try:
                p.unlink()
                deleted_count += 1
                freed_bytes += size
            except: pass
            
        return deleted_count, len(remaining), freed_bytes

    async def get_cache_stats(self) -> dict:
        """获取详细统计"""
        if not self.image_dir.exists():
            return {"count": 0, "size_mb": 0.0, "oldest_hours": 0.0}
        
        def _stats():
            count = 0
            size = 0
            oldest = time.time()
            now = time.time()
            for p in self.image_dir.iterdir():
                if p.is_file() and p.suffix.lower() in self.image_extensions:
                    count += 1
                    stat = p.stat()
                    size += stat.st_size
                    if stat.st_mtime < oldest: oldest = stat.st_mtime
            return {
                "count": count,
                "size_mb": size / (1024*1024),
                "oldest_hours": (now - oldest) / 3600 if count > 0 else 0
            }
        return await asyncio.to_thread(_stats)

    async def clean_all_cache(self) -> Tuple[int, int]:
        def _clean():
            count = 0
            bytes_ = 0
            for p in self.image_dir.iterdir():
                if p.is_file() and p.suffix.lower() in self.image_extensions:
                    bytes_ += p.stat().st_size
                    p.unlink()
                    count += 1
            return count, bytes_
        return await asyncio.to_thread(_clean)
