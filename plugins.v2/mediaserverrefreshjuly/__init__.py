import time
from pathlib import Path
from hashlib import sha1
from typing import Any, List, Dict, Tuple, Optional

from app.core.context import MediaInfo
from app.core.event import eventmanager, Event
from app.helper.mediaserver import MediaServerHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import TransferInfo, RefreshMediaItem, ServiceInfo
from app.schemas.types import EventType
from app.core.config import settings


class MediaServerRefreshJuly(_PluginBase):
    # 插件名称
    plugin_name = "媒体库服务器刷新"
    # 插件描述
    plugin_desc = "入库后自动刷新Emby/Jellyfin/Plex服务器海报墙。"
    # 插件图标
    plugin_icon = "refresh2.png"
    # 插件版本
    plugin_version = "3.1.9"
    # 插件作者
    plugin_author = "jxxghp,july"
    # 作者主页
    author_url = "https://github.com/jxxghp"
    # 插件配置项ID前缀
    plugin_config_prefix = "mediaserverrefresh_"
    # 加载顺序
    plugin_order = 14
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    mediaserver_helper = None
    _enabled = False
    _delay = 0
    _mediaservers = None

    def init_plugin(self, config: dict = None):
        self.mediaserver_helper = MediaServerHelper()
        if config:
            self._enabled = config.get("enabled")
            self._delay = config.get("delay") or 0
            self._mediaservers = config.get("mediaservers") or []

    @property
    def service_infos(self) -> Optional[Dict[str, ServiceInfo]]:
        """
        服务信息
        """
        if not self._mediaservers:
            logger.warning("尚未配置媒体服务器，请检查配置")
            return None

        services = self.mediaserver_helper.get_services(name_filters=self._mediaservers)
        if not services:
            logger.warning("获取媒体服务器实例失败，请检查配置")
            return None

        active_services = {}
        for service_name, service_info in services.items():
            if service_info.instance.is_inactive():
                logger.warning(f"媒体服务器 {service_name} 未连接，请检查配置")
            else:
                active_services[service_name] = service_info

        if not active_services:
            logger.warning("没有已连接的媒体服务器，请检查配置")
            return None

        return active_services

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'multiple': True,
                                            'chips': True,
                                            'clearable': True,
                                            'model': 'mediaservers',
                                            'label': '媒体服务器',
                                            'items': [{"title": config.name, "value": config.name}
                                                      for config in self.mediaserver_helper.get_configs().values()]
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'delay',
                                            'label': '延迟时间（秒）',
                                            'placeholder': '0'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "delay": 0
        }

    def get_page(self) -> List[dict]:
        pass

    @eventmanager.register(EventType.TransferComplete)
    def refresh(self, event: Event):
        """
        发送通知消息
        """
        if not self._enabled:
            return

        event_info: dict = event.event_data
        if not event_info:
            return

        # 刷新媒体库
        if not self.service_infos:
            return

        # 入库数据
        transferinfo: TransferInfo = event_info.get("transferinfo")
        if not transferinfo or not transferinfo.target_diritem or not transferinfo.target_diritem.path:
            return

        if self._delay:
            delay = float(self._delay)
            target_path = Path(transferinfo.target_diritem.path)
            target_path_hash = sha1(str(target_path).encode()).hexdigest()
            
            temp_path = Path(settings.CONFIG_PATH)
            lock_path = temp_path / "media_refresh_lock" / f"{target_path_hash}.lock"
            logger.info(f"锁定文件路径: {lock_path}")

            try:
                # 如果存在该文件，检查是否达到定时任务执行时间，如果没有达到，则说明未来某一时刻这个任务将被执行，直接返回
                if lock_path.exists():
                    with lock_path.open("r") as f:
                        content = f.read()
                        if content:
                            lock_time = float(content)
                            if time.time() < lock_time:
                                lock_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(lock_time))
                                logger.info(f"当前目录 [{target_path}] 已有任务等待执行，将在 {lock_time_str} 进行刷新，本次取消.")
                                return

                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.touch(exist_ok=True)
                run_time = time.time() + delay
                run_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run_time))
                with lock_path.open("w") as f:
                    f.write(str(run_time))
                logger.info(f"任务将于 {run_time_str} 执行")
            except Exception as e:
                logger.info(f"锁定失败，刷新任务继续执行，失败原因: {e}")

            logger.info(f"延迟 {self._delay} 秒后刷新媒体库... ")
            time.sleep(float(self._delay))

        mediainfo: MediaInfo = event_info.get("mediainfo")
        items = [
            RefreshMediaItem(
                title=mediainfo.title,
                year=mediainfo.year,
                type=mediainfo.type,
                category=mediainfo.category,
                target_path=Path(transferinfo.target_diritem.path)
            )
        ]

        for name, service in self.service_infos.items():
            if hasattr(service.instance, 'refresh_library_by_items'):
                service.instance.refresh_library_by_items(items)
            elif hasattr(service.instance, 'refresh_root_library'):
                # FIXME Jellyfin未找到刷新单个项目的API
                service.instance.refresh_root_library()
            else:
                logger.warning(f"{name} 不支持刷新")

    def stop_service(self):
        """
        退出插件
        """
        pass
