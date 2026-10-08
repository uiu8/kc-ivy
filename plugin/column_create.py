"""Create a dedicated shelf column in the bound Calibre library."""
import re
from qt.core import (QVBoxLayout, QLabel, QLineEdit, QFormLayout, QTextBrowser,
                     QDialogButtonBox, QPushButton)
from .theme import InkDialog
from .protocol import Invalid


def create_column(host, lookup, title):
    if not host.same_library():
        raise Invalid('书库已切换，请重新打开 KC++ 后创建。')
    if host.busy:
        raise Invalid('请等待当前任务结束后创建。')
    if getattr(host.gui, 'must_restart_before_config', False):
        raise Invalid('Calibre 有等待重启生效的列设置，请先重启。')
    lookup = lookup.strip().removeprefix('#')
    title = title.strip()
    if not re.fullmatch('[a-z][a-z0-9_]*', lookup):
        raise Invalid('查找名称须以小写英文字母开头，只含小写字母、数字或下划线。')
    if not title:
        raise Invalid('请填写列标题。')
    fields = host.library_db.field_metadata
    if '#' + lookup in fields:
        raise Invalid('此查找名称已存在，请选择已有列或换一个名称。')
    if any(f.get('name') == title for f in fields.custom_field_metadata().values()):
        raise Invalid('此列标题已存在，请换一个标题。')
    # Same database API and GUI restart flag as Calibre's column preferences.
    host.library_db.create_custom_column(lookup, title, 'text', is_multiple=True)
    host.gui.must_restart_before_config = True
    return '#' + lookup


class CreateShelfColumn(InkDialog):
    def __init__(self, host):
        super().__init__(host)
        self.setWindowTitle('新建 Calibre 书架列')
        self.resize(620, 500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        heading = QLabel('新建书架列'); heading.setObjectName('heading')
        layout.addWidget(heading)
        intro = QLabel('在当前 Calibre 书库创建专用列。每个值对应一个收藏夹，一本书可有多个值。已有合适列时可直接关闭本窗口并选择它。')
        intro.setWordWrap(True); layout.addWidget(intro)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.title = QLineEdit('我的书架'); self.title.setObjectName('new_column_title')
        self.lookup = QLineEdit('bookshelves'); self.lookup.setObjectName('new_column_lookup')
        form.addRow('列标题', self.title); form.addRow('查找名称', self.lookup)
        layout.addLayout(form)
        hint = QLabel('名称可自定；查找名称例如 bookshelves，无须填写 #。\n类型固定为“逗号分隔的文本，类似标签”。')
        hint.setWordWrap(True); layout.addWidget(hint)
        help_text = QTextBrowser(); help_text.setObjectName('column_creation_instructions')
        help_text.setPlainText('创建后如何使用\n\n1. 完成当前操作，退出并重新打开 Calibre。\n2. 打开 KC++ → 同步设置 → 书架与同步设置，在“同步方式”选择新列并保存。\n3. 如需保留 Kindle 现有收藏归属，可另行预览导入此列。\n\n创建列不会移动书籍，也不会自动导入或覆盖收藏归属。若使用原生“标签”，无需创建列；整理结果会回填标签。')
        layout.addWidget(help_text, 1)
        self.notice = QLabel('创建后需重启 Calibre，新列才可选择。')
        self.notice.setWordWrap(True); self.notice.setObjectName('column_creation_status')
        layout.addWidget(self.notice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        self.create = QPushButton('创建书架列'); self.create.setObjectName('create_shelf_column')
        buttons.addButton(self.create, QDialogButtonBox.ButtonRole.ActionRole)
        self.create.clicked.connect(lambda: self.submit(host))
        layout.addWidget(buttons)

    def submit(self, host):
        try:
            field = create_column(host, self.lookup.text(), self.title.text())
        except Exception as exc:
            self.notice.setText(str(exc)); return
        self.create.setEnabled(False); self.title.setReadOnly(True); self.lookup.setReadOnly(True)
        self.notice.setText(f'已创建“{self.title.text().strip()}”（{field}）。请重启 Calibre，再在同步设置中选择此列。')
