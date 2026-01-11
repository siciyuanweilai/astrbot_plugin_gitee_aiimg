import time

class Debouncer:
    def __init__(self, config: dict):
        self._interval = config.get("debounce_interval", 10)
        self._records = {}

    def hit(self, key: str) -> bool:
        now = time.time()
        # 简单清理
        if len(self._records) > 100:
            self._records = {k:v for k,v in self._records.items() if now-v < 300}
            
        last = self._records.get(key)
        if last and now - last < self._interval:
            return True
        self._records[key] = now
        return False

    def clear_all(self):
        self._records.clear()
