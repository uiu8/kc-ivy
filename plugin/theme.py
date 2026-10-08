"""Plugin-scoped ink theme. Never changes Calibre's application palette."""
from qt.core import (Qt,QDialog,QMessageBox,QInputDialog,QFileDialog,QDialogButtonBox,
                     QPalette,QColor,QFont,QWidget,QPushButton,QVBoxLayout,QScrollArea,QLabel,QSizePolicy)

STYLE='''
QWidget { background-color:#f5f2eb; color:#302d29; font-family:"Microsoft YaHei UI","Noto Sans CJK SC",sans-serif; font-size:13px; }
QDialog { background:#f5f2eb; }
QLabel { background:transparent; }
QLabel#heading { font-family:"SimSun","Noto Serif CJK SC",serif; font-size:25px; color:#25231f; padding:8px 0; }
QLabel#status { background:#ece9e1; border:1px solid #d8d3c8; border-radius:5px; padding:8px; }
QLabel#status[level="info"] { background:#eaf0f3; color:#25485b; border:1px solid #a9bfcc; border-left:5px solid #537e95; }
QLabel#status[level="success"] { background:#e8f0e6; color:#315636; border:1px solid #aac1a5; border-left:5px solid #567b4c; }
QLabel#status[level="warning"] { background:#fff0d7; color:#704b12; border:1px solid #cfac6d; border-left:5px solid #af7926; }
QLabel#status[level="error"] { background:#fae4df; color:#812e23; border:1px solid #cb9083; border-left:5px solid #af4435; }
QLabel#workflow { color:#724333; font-weight:bold; padding:3px; }
QMessageBox QLabel#qt_msgbox_label { font-size:15px; min-width:380px; padding:12px 8px; }
QMessageBox QPushButton { min-width:80px; padding:9px 16px; }
QTextEdit#preview_notice[blocked="true"] { background:#fff0d7; color:#704b12; border:2px solid #af7926; font-size:14px; }
QWidget#navigation { background:#eae6dc; border-right:1px solid #d2ccc0; }
QPushButton { background:#faf8f2; border:1px solid #cfc7ba; border-radius:5px; padding:7px 12px; min-height:18px; }
QPushButton:hover { background:#eee7da; border-color:#a5947e; }
QPushButton:pressed,QPushButton:checked { background:#e8dbd0; border-color:#a64b3c; color:#87392e; }
QPushButton:focus { border:1px solid #a64b3c; }
QPushButton:disabled { color:#a49f95; background:#e9e5dd; border-color:#d9d3c9; }
QToolButton { background:#f7f3ea; color:#403a31; border:1px solid #cfc7ba; border-radius:4px; padding:5px; }
QToolButton:hover { background:#e9dfd1; }
QProgressBar { border:1px solid #d5cec1; border-radius:4px; background:#ece7dc; text-align:center; }
QProgressBar::chunk { background:#a34838; }
QPushButton#primary { background:#a34838; color:#fffaf1; border-color:#943c30; }
QPushButton#primary:hover { background:#b45341; }
QPushButton#primary:disabled { background:#ddd7ce; color:#a49f95; border-color:#d9d3c9; }
QPushButton#nav { text-align:left; border:0; background:transparent; padding:12px 14px; }
QPushButton#nav:checked { background:#ded8cc; color:#923f32; border-left:3px solid #a34838; border-radius:0; }
QLineEdit,QComboBox,QSpinBox { background:#fffdf7; border:1px solid #cec7ba; border-radius:4px; padding:6px; selection-background-color:#a34838; selection-color:white; }
QLineEdit:focus,QComboBox:focus { border-color:#a34838; }
QComboBox QAbstractItemView { background:#fffdf7; color:#302d29; selection-background-color:#e4d7c8; selection-color:#302d29; }
QAbstractItemView,QTextEdit,QPlainTextEdit { background:#fbf9f3; alternate-background-color:#f0ece3; border:1px solid #d7d0c3; border-radius:4px; selection-background-color:#e6d7c6; selection-color:#312d28; }
QAbstractItemView::item { padding:5px; }
QHeaderView::section { background:#eae5da; color:#514b41; padding:7px; border:0; border-bottom:1px solid #cfc7ba; }
QGroupBox { border:1px solid #d5cec1; border-radius:6px; margin-top:15px; padding:16px 9px 9px; font-weight:bold; }
QGroupBox::title { subcontrol-origin:margin; left:12px; padding:0 5px; color:#774a3c; }
QScrollArea,QStackedWidget,QTabWidget::pane { border:0; background:transparent; }
QTabBar::tab { background:#ece7dd; padding:8px 16px; border-bottom:2px solid transparent; }
QTabBar::tab:selected { color:#923f32; border-bottom-color:#a34838; }
QCheckBox { spacing:7px; padding:3px; }
QCheckBox::indicator { width:15px; height:15px; }
QScrollBar:vertical { width:10px; background:#eee9df; margin:0; }
QScrollBar::handle:vertical { background:#bdb4a4; min-height:25px; border-radius:4px; }
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
QScrollBar:horizontal { height:10px; background:#eee9df; }
QScrollBar::handle:horizontal { background:#bdb4a4; min-width:25px; border-radius:4px; }
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal { width:0; }
QSplitter::handle { background:#e0d9cc; }
QToolTip { color:#302d29; background:#fffaf0; border:1px solid #c4b9a8; padding:6px; }
QMenu { background:#faf7ef; border:1px solid #cfc7ba; padding:5px; }
QMenu::item { padding:7px 22px; }
QMenu::item:selected { background:#e4d7c8; }
'''

def apply_theme(widget):
    palette=widget.palette()
    for role,color in [(QPalette.ColorRole.Window,'#f5f2eb'),(QPalette.ColorRole.WindowText,'#302d29'),
                       (QPalette.ColorRole.Base,'#fbf9f3'),(QPalette.ColorRole.Text,'#302d29'),
                       (QPalette.ColorRole.Button,'#faf8f2'),(QPalette.ColorRole.ButtonText,'#302d29'),
                       (QPalette.ColorRole.Highlight,'#e6d7c6'),(QPalette.ColorRole.HighlightedText,'#302d29')]:palette.setColor(role,QColor(color))
    widget.setPalette(palette);widget.setStyleSheet(STYLE);widget.setFont(QFont('Microsoft YaHei UI',10))
    widget.setProperty('kc_ink_theme',True)

def polish_buttons(dialog):
    labels={QDialogButtonBox.StandardButton.Ok:'确定',QDialogButtonBox.StandardButton.Cancel:'取消',
            QDialogButtonBox.StandardButton.Close:'关闭',QDialogButtonBox.StandardButton.Save:'保存',
            QDialogButtonBox.StandardButton.Open:'打开'}
    for box in dialog.findChildren(QDialogButtonBox):
        for code,text in labels.items():
            button=box.button(code)
            if button:
                button.setText(text)
                if code in (QDialogButtonBox.StandardButton.Ok,QDialogButtonBox.StandardButton.Save,QDialogButtonBox.StandardButton.Open):button.setObjectName('primary')

class NoticeLabel(QLabel):
    """Visible severity with plain text; full content remains available to copy."""
    def __init__(self,text='',parent=None):
        super().__init__(parent);self.setObjectName('status');self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.fontMetrics().height()+22)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setText(text)
    def setText(self,text):self.show_message(text,'info')
    def text(self):return getattr(self,'full_text','')
    def show_message(self,text,level='info',summary=None):
        self.show()
        self.full_text=str(text);self.summary=summary or self.full_text.split('\n')[0]
        self.setProperty('level',level)
        self.render_summary();self.setToolTip(self.full_text)
        self.style().unpolish(self);self.style().polish(self);self.update()
    def render_summary(self):
        super().setText(self.fontMetrics().elidedText(getattr(self,'summary',''),Qt.TextElideMode.ElideRight,max(20,self.width()-28)))
    def resizeEvent(self,event):
        super().resizeEvent(event);self.render_summary()

class InkDialog(QDialog):
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);apply_theme(self)
    def showEvent(self,event):
        polish_buttons(self)
        if self.screen():
            area=self.screen().availableGeometry()
            if not self.property('kc_main') and not self.property('kc_scrolled') and self.layout() and self.height()>area.height()-64:
                content=QWidget();content.setLayout(self.layout());outer=QVBoxLayout(self)
                scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(content);outer.addWidget(scroll)
                for box in content.findChildren(QDialogButtonBox):
                    if content.layout().indexOf(box)>=0:
                        content.layout().removeWidget(box);outer.addWidget(box)
                self.setProperty('kc_scrolled',True)
            self.resize(min(self.width(),area.width()-32),min(self.height(),area.height()-64))
        super().showEvent(event)

class InkInputDialog(QInputDialog):
    def __init__(self,parent=None):
        super().__init__(parent);apply_theme(self);self.setOkButtonText('确定');self.setCancelButtonText('取消');self.setMinimumWidth(440)
    @staticmethod
    def getText(parent,title,label,text=''):
        d=InkInputDialog(parent);d.setWindowTitle(title);d.setLabelText(label);d.setTextValue(text)
        accepted=bool(d.exec());return d.textValue(),accepted
    @staticmethod
    def getItem(parent,title,label,items,current=0,editable=True):
        d=InkInputDialog(parent);d.setWindowTitle(title);d.setLabelText(label);d.setComboBoxItems(items);d.setComboBoxEditable(editable)
        if items:d.setTextValue(items[max(0,min(current,len(items)-1))])
        accepted=bool(d.exec());return d.textValue(),accepted

class InkMessageBox(QMessageBox):
    @staticmethod
    def _show(parent,title,text,icon,buttons,default):
        d=QMessageBox(parent);apply_theme(d);d.setWindowTitle(title);d.setTextFormat(Qt.TextFormat.PlainText);d.setText(text);d.setIcon(icon);d.setStandardButtons(buttons);d.setDefaultButton(default)
        for code,label in [(QMessageBox.StandardButton.Yes,'是'),(QMessageBox.StandardButton.No,'否'),(QMessageBox.StandardButton.Ok,'确定'),(QMessageBox.StandardButton.Cancel,'取消')]:
            if d.button(code):d.button(code).setText(label)
        return QMessageBox.StandardButton(d.exec())
    @staticmethod
    def question(parent,title,text,buttons=QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,defaultButton=QMessageBox.StandardButton.No):
        return InkMessageBox._show(parent,title,text,QMessageBox.Icon.Question,buttons,defaultButton)
    @staticmethod
    def information(parent,title,text):return InkMessageBox._show(parent,title,text,QMessageBox.Icon.Information,QMessageBox.StandardButton.Ok,QMessageBox.StandardButton.Ok)

class InkFileDialog(QFileDialog):
    @staticmethod
    def getExistingDirectory(parent,title,directory=''):
        d=QFileDialog(parent,title,directory);d.setOption(QFileDialog.Option.DontUseNativeDialog,True)
        d.setFileMode(QFileDialog.FileMode.Directory);d.setOption(QFileDialog.Option.ShowDirsOnly,True);apply_theme(d)
        d.setLabelText(QFileDialog.DialogLabel.Accept,'选择目录');d.setLabelText(QFileDialog.DialogLabel.Reject,'取消')
        return d.selectedFiles()[0] if d.exec() and d.selectedFiles() else ''
    @staticmethod
    def _choose(parent,title,directory,filter,save):
        d=QFileDialog(parent,title,directory,filter);d.setOption(QFileDialog.Option.DontUseNativeDialog,True);apply_theme(d)
        d.setAcceptMode(QFileDialog.AcceptMode.AcceptSave if save else QFileDialog.AcceptMode.AcceptOpen)
        d.setFileMode(QFileDialog.FileMode.AnyFile if save else QFileDialog.FileMode.ExistingFile)
        d.setLabelText(QFileDialog.DialogLabel.Accept,'保存' if save else '打开');d.setLabelText(QFileDialog.DialogLabel.Reject,'取消')
        d.setLabelText(QFileDialog.DialogLabel.FileName,'文件名');d.setLabelText(QFileDialog.DialogLabel.FileType,'文件类型');d.setLabelText(QFileDialog.DialogLabel.LookIn,'位置')
        d.resize(850,560)
        return (d.selectedFiles()[0],d.selectedNameFilter()) if d.exec() and d.selectedFiles() else ('','')
    @staticmethod
    def getOpenFileName(parent,title,directory='',filter=''):return InkFileDialog._choose(parent,title,directory,filter,False)
    @staticmethod
    def getSaveFileName(parent,title,directory='',filter=''):return InkFileDialog._choose(parent,title,directory,filter,True)

