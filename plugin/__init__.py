from calibre.customize import InterfaceActionBase


class KCPlus(InterfaceActionBase):
    name = 'kc-ivy'
    description = 'kc-ivy-kpw3：面向 KPW3 soft-float 固件的完整 USB 版本'
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'KC'
    version = (1, 0, 2)
    minimum_calibre_version = (9, 15, 0)
    actual_plugin = 'calibre_plugins.kc_plus.ui:KCPlusAction'
