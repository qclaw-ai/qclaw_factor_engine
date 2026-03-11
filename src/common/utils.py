#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import os
from datetime import datetime


def setup_logger(name: str, log_file: str | None = None) -> logging.Logger:
    """创建 logger（对齐旧项目风格）

    - 始终输出到 stdout
    - 若提供了 log_file，则始终写入该文件（包括 prod），便于追踪
    """
    log_handlers: list[logging.Handler] = [logging.StreamHandler()]

    if log_file:
        # 在文件名中追加日期后缀，形如 xxx_20260311.log
        today_str = datetime.now().strftime("%Y%m%d")
        base, ext = os.path.splitext(log_file)
        if not ext:
            ext = ".log"
        dated_log_file = f"{base}_{today_str}{ext}"

        os.makedirs(os.path.dirname(dated_log_file), exist_ok=True)
        log_handlers.append(logging.FileHandler(dated_log_file, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=log_handlers,
    )

    return logging.getLogger(name)

