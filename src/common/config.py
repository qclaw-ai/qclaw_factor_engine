#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import configparser
import logging

logger = logging.getLogger(__name__)


class Config:
    """配置加载类（从 ini 文件读取配置，支持 dev/prod 切换）

    约定：
    - 默认 config_file 传入模块内相对路径，例如：'data_ingest/config.ini'
    - 非 prod 环境（ENV != 'prod'）时，会自动替换为同目录下的 '*_dev.ini'
    """

    def __init__(self, config_file: str = "config.ini", default_section: str = "DEFAULT"):
        logger.info("初始化配置加载类")
        self.config = configparser.ConfigParser()
        self.default_section = default_section

        # 根据 ENV 选择配置文件（和老项目保持一致）
        if os.environ.get("ENV") != "prod":
            if config_file.endswith(".ini") and not config_file.endswith("_dev.ini"):
                config_file = config_file.replace(".ini", "_dev.ini")
            logger.info(f"开发环境，使用配置文件: {config_file}")

        # 从文件加载配置
        if os.path.exists(config_file):
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    self.config.read_file(f)
                logger.info(f"成功加载配置文件: {config_file}")
            except Exception as e:
                logger.error(f"读取配置文件失败: {str(e)}")
        else:
            logger.warning(f"配置文件不存在，创建默认配置: {config_file}")
            os.makedirs(os.path.dirname(config_file), exist_ok=True)
            with open(config_file, "w", encoding="utf-8") as f:
                self.config.write(f)

    def get(self, section, key, fallback=None):
        return self.config.get(section, key, fallback=fallback)

    def _strip_inline_comment(self, value):
        """去除行内注释，支持 # 和 ; 作为注释符号"""
        if not value:
            return value

        value = value.strip()

        # 如果值被引号括起来，先去除引号
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1].strip()

        # 去除行内注释（# 或 ; 后面的内容）
        # 检查 # 或 ; 是否在引号内（简单处理，不考虑转义）
        for comment_char in ["#", ";"]:
            if comment_char in value:
                # 查找第一个不在引号内的注释符号
                in_double_quotes = False
                in_single_quotes = False
                for i, char in enumerate(value):
                    if char == '"' and (i == 0 or value[i - 1] != "\\"):
                        in_double_quotes = not in_double_quotes
                    elif char == "'" and (i == 0 or value[i - 1] != "\\"):
                        in_single_quotes = not in_single_quotes
                    elif char == comment_char and not in_double_quotes and not in_single_quotes:
                        value = value[:i].strip()
                        break

        return value.strip()

    def getboolean(self, section, key, fallback=None):
        """获取布尔值，自动处理行内注释"""
        try:
            raw_value = self.config.get(section, key, fallback=None)
            if raw_value is None:
                return fallback
            # 去除注释后转换
            clean_value = self._strip_inline_comment(raw_value)
            # 使用 configparser 的标准布尔值转换
            if clean_value.lower() in ("1", "yes", "true", "on"):
                return True
            if clean_value.lower() in ("0", "no", "false", "off"):
                return False
            raise ValueError(f"无法将 '{clean_value}' 转换为布尔值")
        except (configparser.NoSectionError, configparser.NoOptionError):
            return fallback
        except (ValueError, TypeError):
            # 如果转换失败，返回 fallback
            return fallback

    def getint(self, section, key, fallback=None):
        """获取整数值，自动处理行内注释"""
        try:
            raw_value = self.config.get(section, key, fallback=None)
            if raw_value is None:
                return fallback
            # 去除注释后转换
            clean_value = self._strip_inline_comment(raw_value)
            return int(clean_value)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return fallback
        except (ValueError, TypeError):
            # 如果转换失败，返回 fallback
            return fallback

    def getfloat(self, section, key, fallback=None):
        """获取浮点数值，自动处理行内注释"""
        try:
            raw_value = self.config.get(section, key, fallback=None)
            if raw_value is None:
                return fallback
            # 去除注释后转换
            clean_value = self._strip_inline_comment(raw_value)
            return float(clean_value)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return fallback
        except (ValueError, TypeError):
            # 如果转换失败，返回 fallback
            return fallback

    def has_option(self, section, key):
        """检查配置项是否存在"""
        return self.config.has_option(section, key)

    def set_default_config(self, section, config_dict):
        """设置默认配置"""
        if section not in self.config:
            self.config[section] = {}
        for key, value in config_dict.items():
            if key not in self.config[section]:
                self.config[section][key] = str(value)

