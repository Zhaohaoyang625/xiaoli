# ============================================
# 统一的路径管理（v2 结构重组 2026-08-22）
# 为什么需要它：重组后程序代码住在 xiaoli/ 包里，但"她记得的数据"（data/）
# 和"AI 模型"（models/）放在项目根——数据不该混在代码里（大厂惯例）。
# 所有模块都从这里拿路径，改位置只改这一个文件。
# ============================================

import os

# 项目根目录（xiaoli/ 的上一级）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 运行时数据：她记得的一切（聊天日记/心情/记忆/提醒…）
DATA_DIR = os.path.join(ROOT, "data")

# AI 模型：语义向量模型（bge）
MODELS_DIR = os.path.join(ROOT, "models")

# 网页（XiaoLi.html + Live2D 模型/引擎库）
WEB_DIR = os.path.join(ROOT, "web")
