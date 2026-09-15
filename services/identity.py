import re
from typing import Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
import astrbot.api.message_components as Comp


class IdentityMixin:
    """身份识别、昵称与权限判断。"""

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
        return str(user_id) == "1929647130"

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
