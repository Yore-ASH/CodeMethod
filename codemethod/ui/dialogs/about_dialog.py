"""关于对话框。"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QTextBrowser, QVBoxLayout, QWidget

from ... import APP_NAME, APP_VERSION
from ...core.languages import all_languages
from ...storage.container import BINARY_EXTENSIONS, TEXT_EXTENSIONS, FORMAT_MAJOR, FORMAT_MINOR


class AboutDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"关于 {APP_NAME}")
        self.setMinimumSize(600, 460)

        root = QVBoxLayout(self)
        title = QLabel(f"{APP_NAME} {APP_VERSION}", self)
        title.setObjectName("TitleLabel")
        root.addWidget(title)

        subtitle = QLabel("多标签代码实现规划与知识库", self)
        subtitle.setObjectName("SubTitleLabel")
        root.addWidget(subtitle)

        browser = QTextBrowser(self)
        browser.setOpenExternalLinks(False)
        languages = "、".join(spec.name for spec in all_languages())
        browser.setHtml(
            f"""
            <h3>功能</h3>
            <ul>
              <li>一个功能条目 = 描述 + 前置要求 + 任意数量标签 + <b>多种语言实现</b></li>
              <li>多标签组合检索 (AND / OR / NOT / EXACT)、全文检索、
                  <code>tag:</code> <code>lang:</code> <code>status:</code> <code>is:</code> 限定符</li>
              <li>VSCode 风格代码预览, 语法高亮支持 {len(list(all_languages()))} 种语言:
                  {languages}</li>
              <li>一键复制到剪贴板 (单个实现 / 全部实现)</li>
              <li><b>全量修订历史</b>: 每一次修改都记录快照, 可随时回滚, 撤销/重做</li>
              <li>可移植容器格式: 二进制 <code>{", ".join(BINARY_EXTENSIONS)}</code>
                  (zlib + CRC32 + SHA-256 + 内嵌清单)
                  与文本 <code>{", ".join(TEXT_EXTENSIONS)}</code> (便于 git diff), 可互相转换</li>
              <li>原子写入 + 滚动备份 + 完整性自检</li>
            </ul>
            <h3>容器格式</h3>
            <p>当前实现版本 v{FORMAT_MAJOR}.{FORMAT_MINOR}。
            头部 64 字节固定结构 (魔数/版本/标记/长度/CRC32/时间/清单偏移),
            载荷为 zlib 压缩的 JSON, 尾部 32 字节含清单长度、文件长度与收尾魔数。</p>
            <h3>快捷操作</h3>
            <ul>
              <li><code>Ctrl+N</code> 新建条目 · <code>Ctrl+E</code> 编辑 · <code>Ctrl+F</code> 检索</li>
              <li><code>Ctrl+S</code> 保存 · <code>Ctrl+Shift+S</code> 另存为</li>
              <li><code>Ctrl+Z</code> 撤销 · <code>Ctrl+Y</code> 重做 (针对当前条目)</li>
              <li><code>Ctrl+Shift+C</code> 复制当前实现代码</li>
              <li><code>F5</code> 刷新 · <code>Ctrl+滚轮</code> 缩放代码字号</li>
            </ul>
            """.strip()
        )
        root.addWidget(browser, 1)

        box = QDialogButtonBox(self)
        box.addButton("关闭", QDialogButtonBox.ButtonRole.AcceptRole).clicked.connect(self.accept)
        root.addWidget(box)


__all__ = ["AboutDialog"]
