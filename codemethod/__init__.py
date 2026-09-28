"""CodeMethod - 多标签代码实现规划与知识库.

一个用于记录"具有特定功能的代码实现"的多标签规划工具:

* 库里有三类**互相独立**的实体 —— **模块 (Entry)**、**独立空间 (Space)**、
  **函数体 (Function)**, 它们共用标签、检索、历史与容器;
* 模块与函数体都允许挂载 **多种语言实现**, 每种语言各有**自己的前置要求**
  (函数体的每种实现还额外带一张**变量含义表**);
* 空间是库内的一个完整项目, 目录树与文件内容全部存进同一个文件;
* 实体可附加任意数量的 **标签 (Tag)**, 支持多标签组合检索;
* 所有修改都会写入 **修订历史 (Revision)**, 支持全量回滚;
* 数据保存为自描述的 **可移植容器格式** (``.cmdb`` 二进制 / ``.cmj`` 文本).

GUI 基于 PySide6, 视觉风格模拟 VSCode.
"""

from __future__ import annotations

__all__ = ["__version__", "__app_name__", "APP_NAME", "APP_VERSION"]

APP_NAME = "CodeMethod"
APP_VERSION = "1.7.0"

__app_name__ = APP_NAME
__version__ = APP_VERSION