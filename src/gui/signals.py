from PySide6.QtCore import QObject, Signal

class AppSignals(QObject):
    # 扫描进度信号: (current, total)
    scan_progress = Signal(int, int)
    # AI 分析状态信号: (file_id, status)
    ai_status = Signal(int, str)
    # 数据库更新信号
    db_updated = Signal()

# 全局信号实例
signals = AppSignals()
