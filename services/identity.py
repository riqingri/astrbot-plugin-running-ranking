import re
from typing import Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
import astrbot.api.message_components as Comp


# =============================================================
# 超级管理员列表（可自行修改）
#
# 一行一个 ID，支持两种格式：
#   1. QQ 号   —— 用于 OneBot（aiocqhttp）平台
#   2. openid  —— 用于 QQ 官方机器人（qq_official）平台
#
# 如何获取 QQ 官方机器人上的 openid：
#   让管理员在机器人所在群里发一条消息，然后到 AstrBot 后台的
#   运行日志里查看该条消息的 sender / user_id 字段，即为 openid。
# =============================================================
SUPER_ADMINS = [
    "3123366945",  # OneBot 平台超级管理员 QQ 号
    # —— QQ 官方机器人（qq_official）——
    "8157C2A69B208B96383BB2BF58023633",  # 私聊 openid（user_openid）
    # 群聊里的管理员是另一个 openid（member_openid，每个群都不同）：
    # 让管理员在「群里」发条消息，按同样方法查日志得到后填在下面。
    # "xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
]


class IdentityMixin:
    """身份识别、昵称与权限判断。"""

    # =============================================================
    # 平台识别
    # =============================================================

    def get_platform_name(self, event: AstrMessageEvent) -> str:
        try:
            return str(event.get_platform_name())
        except Exception:
            return ""

    def is_qq_official(self, event: AstrMessageEvent) -> bool:
        """QQ 官方机器人（频道/群机器人），不含 OneBot。"""
        return self.get_platform_name(event) in (
            "qq_official",
            "qq_official_webhook",
        )

    def adapt_reply(self, event: AstrMessageEvent, node):
        """按平台适配回复消息链。

        QQ 官方机器人不支持合并转发（Node），把「柏柏子」转发节点
        降级为纯文本；其它平台（如 OneBot v11）保留原样。
        """
        if not self.is_qq_official(event):
            return [node]

        if isinstance(node, Comp.Node):
            texts = []
            for component in getattr(node, "content", None) or []:
                text = getattr(component, "text", None)
                if text is not None:
                    texts.append(str(text))
            return [Comp.Plain("".join(texts))]

        return [node]

    def reply_result(self, event: AstrMessageEvent, node):
        """构造按平台适配后的回复结果（消息链）。"""
        result = event.chain_result(self.adapt_reply(event, node))
        return self._force_plain_content(event, result)

    def reply_text(self, event: AstrMessageEvent, text: str):
        """构造按平台适配后的回复结果（纯文本）。"""
        result = event.plain_result(text)
        return self._force_plain_content(event, result)

    def _force_plain_content(self, event: AstrMessageEvent, result):
        """QQ 官方机器人：尽量强制纯文本 content 模式（msg_type 0）。

        群聊不接受原生 markdown（msg_type 2），否则插件回复会被平台
        拒绝、无法送达 QQ 客户端。较新版本提供 use_markdown() 方法，
        更旧版本只有 use_markdown_ 字段或完全不支持，这里做兼容，能设则设。
        """
        if not self.is_qq_official(event):
            return result
        setter = getattr(result, "use_markdown", None)
        if callable(setter):
            setter(False)
        elif hasattr(result, "use_markdown_"):
            result.use_markdown_ = False
        return result

    def get_group_id(self, event: AstrMessageEvent) -> str:
        try:
            return str(event.get_group_id())
        except Exception:
            return "private"

    def get_user_id(self, event: AstrMessageEvent) -> str:
        return str(event.get_sender_id())

    async def get_sender_nickname(self, event: AstrMessageEvent) -> str:
        try:
            name = event.get_sender_name()
            if name:
                return str(name)
        except Exception:
            pass
        return self.get_user_id(event)

    def is_super_admin(self, user_id: str) -> bool:
        return str(user_id) in SUPER_ADMINS

    def is_admin(self, group_id: str, user_id: str) -> bool:
        if self.is_super_admin(user_id):
            return True
        conn = self.get_newbie_conn()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT 1
        FROM newbies_admins
        WHERE group_id = ? AND user_id = ?
        """, (group_id, str(user_id)))
        result = cursor.fetchone()
        conn.close()
        return result is not None

    def extract_qq_id(self, text: str) -> Optional[str]:
        if not text:
            return None
        match = re.search(r"\d{5,15}", str(text))
        if match:
            return match.group()
        return None

    def extract_at_user_id(self, event: AstrMessageEvent) -> Optional[str]:
        try:
            message = event.message_obj.message
            for component in message:
                if isinstance(component, Comp.At):
                    for attr in ["qq", "user_id", "target", "id"]:
                        if hasattr(component, attr):
                            value = getattr(component, attr)
                            if value:
                                return str(value)
        except Exception:
            pass

        # QQ 官方机器人可能把 @ 以 <@!id> / <@id> 文本形式下发，
        # 其中 id 是 openid（字母数字混合），而非纯数字 QQ 号。
        try:
            text = self.extract_plain_text(event)
            match = re.search(r"<@!?([^>\s]+)>", text)
            if match:
                return match.group(1)
        except Exception:
            pass

        return None

    def get_target_user_id(self, event: AstrMessageEvent, argument: str) -> Optional[str]:
        at_user_id = self.extract_at_user_id(event)
        if at_user_id:
            return at_user_id
        return self.extract_qq_id(argument)

    def get_user_nickname(self, group_id: str, user_id: str) -> str:
        conn = self.get_newbie_conn()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT nickname FROM newbie_users
        WHERE group_id = ? AND semester = ? AND user_id = ?
        """, (group_id, "2026_fall", str(user_id)))
        row = cursor.fetchone()
        conn.close()
        if row and row["nickname"]:
            return row["nickname"]
        return str(user_id)

    def refresh_user_nickname_from_event(self, event: AstrMessageEvent) -> None:
        try:
            group_id = self.get_group_id(event)
            user_id = self.get_user_id(event)
            _, live_name, _ = self.get_user_info(event)
            if not live_name or str(live_name) == str(user_id):
                return

            conn = self.get_newbie_conn()
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE newbie_users
            SET nickname = ?
            WHERE group_id = ?
              AND semester = ?
              AND user_id = ?
              AND (nickname IS NULL OR nickname != ?)
            """, (str(live_name), group_id, "2026_fall", str(user_id), str(live_name)))
            conn.commit()
            conn.close()
        except Exception:
            pass

    def get_display_name(self, event: AstrMessageEvent, fallback_name: Optional[str] = None) -> str:
        try:
            user_id, user_name, _ = self.get_user_info(event)
            if user_name and user_name != user_id:
                return str(user_name)
        except Exception:
            pass

        if fallback_name:
            return str(fallback_name)

        try:
            return str(event.get_sender_name())
        except Exception:
            return "成员"

    # =============================================================
    # 获取用户信息
    #
    # 优先：
    #
    # 1. 群名片 card
    # 2. QQ昵称 nickname
    # 3. AstrBot sender_name
    # 4. QQ号
    # =============================================================

    def get_user_info(
        self,
        event: AstrMessageEvent
    ):

        user_id = str(
            event.get_sender_id()
        )

        user_name = None

        # ---------------------------------------------------------
        # 尝试从 OneBot sender 获取群名片
        # ---------------------------------------------------------

        try:

            message_obj = event.message_obj

            sender = getattr(
                message_obj,
                "sender",
                None
            )

            if sender:

                if not isinstance(
                    sender,
                    dict
                ):

                    user_name = (
                        getattr(
                            sender,
                            "card",
                            None
                        )
                        or getattr(
                            sender,
                            "nickname",
                            None
                        )
                    )

                else:

                    user_name = (
                        sender.get("card")
                        or sender.get("nickname")
                    )

        except Exception as e:

            logger.debug(
                "[RunningRank] "
                f"读取 sender 信息失败: {e}"
            )

        # ---------------------------------------------------------
        # AstrBot sender_name
        # ---------------------------------------------------------

        if not user_name:

            try:

                user_name = (
                    event.get_sender_name()
                )

            except Exception:

                user_name = None

        # ---------------------------------------------------------
        # QQ号兜底
        # ---------------------------------------------------------

        if not user_name:

            user_name = user_id

        # ---------------------------------------------------------
        # 群号
        # ---------------------------------------------------------

        try:

            group_id = (
                event.get_group_id()
            )

        except Exception:

            group_id = None

        if group_id is None:

            group_id = "private"

        return (
            user_id,
            str(user_name),
            str(group_id)
        )
