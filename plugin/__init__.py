from calibre.customize import InterfaceActionBase


class KCPlus(InterfaceActionBase):
    name = 'kc-ivy'
    description = 'kc-ivy：Kindle 收藏夹管理、Calibre 列同步与修改预览；MTP 和迁移分享为实验功能'
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'KC'
    version = (1, 0, 26)
    minimum_calibre_version = (9, 15, 0)
    actual_plugin = 'calibre_plugins.kc_plus.ui:KCPlusAction'
