#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import time
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session

from common.config import Config

logger = logging.getLogger(__name__)


class DatabaseManager:
    """数据库管理类（PostgreSQL + SQLAlchemy）

    - 使用配置文件的 [database] 段创建 Engine
    - 提供 get_engine / get_session，内部带简单重试逻辑
    """

    def __init__(self, config_file: str):
        self._config_file = config_file
        self._engine: Optional[Engine] = None
        self._SessionLocal: Optional[sessionmaker] = None

    def _create_engine(self) -> Engine:
        cfg = Config(self._config_file)

        host = cfg.get("database", "db_host", fallback="localhost")
        port = cfg.getint("database", "db_port", fallback=5432)
        user = cfg.get("database", "db_user", fallback="postgres")
        password = cfg.get("database", "db_password", fallback="")
        db_name = cfg.get("database", "db_name", fallback="postgres")

        url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}"

        logger.info(f"创建数据库引擎: {url}")

        engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )

        return engine

    def get_engine(self, max_retries: int = 3, retry_delay: int = 5) -> Engine:
        """获取 SQLAlchemy Engine，带简单重试"""
        if self._engine is not None:
            return self._engine

        last_error: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                logger.info(f"尝试创建数据库引擎 (第{attempt + 1}次)")
                engine = self._create_engine()
                # 简单探活：获取一次连接
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                self._engine = engine
                self._SessionLocal = sessionmaker(bind=self._engine, autoflush=False, autocommit=False)
                logger.info("数据库引擎创建成功")
                return self._engine
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    logger.warning(f"创建数据库引擎失败 (第{attempt + 1}次): {e}, {retry_delay}秒后重试")
                    time.sleep(retry_delay)
                else:
                    logger.error(f"创建数据库引擎失败 (已达到最大重试次数): {e}")
        raise RuntimeError(f"无法创建数据库引擎: {last_error}")

    def get_session(self) -> Session:
        """获取一个新的 Session 实例"""
        if self._SessionLocal is None:
            self.get_engine()
        assert self._SessionLocal is not None
        return self._SessionLocal()


def get_db_manager(config_file: str) -> DatabaseManager:
    """方便模块内部按本地相对路径创建 DatabaseManager"""
    return DatabaseManager(config_file=config_file)

