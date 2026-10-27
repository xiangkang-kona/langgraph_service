# -*- coding: utf-8 -*-
"""
测试公共配置。

关键：必须在导入 app 之前设置环境变量——app.config 在导入时就读取配置，
没有密钥/数据库地址会导致模型客户端初始化失败。测试全程不访问网络和数据库。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

os.environ.setdefault("DASHSCOPE_API_KEY", "test-key")
os.environ.setdefault("DATABASE_URL", "postgresql://support:support123@localhost:5432/support")
os.environ.setdefault("API_KEY", "test-api-key")
