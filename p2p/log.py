import json
import logging
import os
import sys
from datetime import datetime, timezone

class JSONFormatter(logging.Formatter):
    def format(self, record):
        data = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage()
        }
        
        # Merge in any extra dictionary passed via the `extra` kwarg 
        # (e.g., logger.info("...", extra={"extra_data": {"peer_id": "X"}}))
        if hasattr(record, "extra_data"):
            data.update(record.extra_data)
            
        if record.exc_info:
            data["error"] = self.formatException(record.exc_info)
            
        return json.dumps(data)

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.hasHandlers():
        logger.propagate = False
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        
        level_name = os.environ.get("DC_LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)
        logger.setLevel(level)
        
    return logger
