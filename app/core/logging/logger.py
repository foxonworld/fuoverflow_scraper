import logging
import sys
import json
from datetime import datetime
from typing import Any, List

class WebSocketLogHandler(logging.Handler):
    """
    Custom handler to broadcast logs to connected WebSockets.
    """
    def __init__(self, broadcast_callback=None):
        super().__init__()
        self.broadcast_callback = broadcast_callback

    def emit(self, record):
        if self.broadcast_callback:
            log_entry = self.format(record)
            self.broadcast_callback(log_entry)

def setup_logging(broadcast_callback=None):
    logger = logging.getLogger("fuoverflow")
    logger.setLevel(logging.INFO)

    # Standard Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        '[% (asctime)s] % (levelname)s: % (message)s',
        datefmt='%H:%M:%S'
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # WebSocket Handler
    if broadcast_callback:
        ws_handler = WebSocketLogHandler(broadcast_callback)
        ws_handler.setFormatter(formatter)
        logger.addHandler(ws_handler)

    return logger

logger = logging.getLogger("fuoverflow")
