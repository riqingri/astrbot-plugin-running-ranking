import re
from datetime import datetime
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

# =============================================================
# 群号映射（QQ 官方机器人）
#
# qq_official 下 event.get_group_id() 返回的是 group_openid（十六进制串），
# 而旧数据库（OneBot 时代）的 group_id 存的是数字群号。把
#   「群 openid -> 数字群号」
# 填在下面，插件查询/写库时统一用数字群号，才能命中旧数据。
#
# 群 openid 的查法：在 AstrBot 后台运行日志里看任意一条群消息的
# group_id 字段（十六进制串）；数字群号就是你们原来的 QQ 群号。
# =============================================================
GROUP_ID_MAP = {
    "85B6BD2F856C47E7B3A42822EB984AA0": "1107851639"
}


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
            group_id = str(event.get_group_id())
        except Exception:
            return "private"
        if self.is_qq_official(event):
            if group_id not in GROUP_ID_MAP:
                logger.warning(
                    "[RunningRank] 未映射的 group_openid，请填入 GROUP_ID_MAP：%s",
                    group_id,
                )
            group_id = GROUP_ID_MAP.get(group_id, group_id)
        return group_id

    def get_user_id(self, event: AstrMessageEvent) -> str:
        return self.resolve_user_id(event)

    def log_identity_context(self, event: AstrMessageEvent, command_name: str = "") -> None:
        """打印一次身份解析上下文：原始 openid(qqid) 与解析后的 QQ号/数字群号。

        每次调用命令时打一行，用来排查「绑定不上 / 存的是 openid」问题。
        """
        platform = self.get_platform_name(event)
        try:
            raw_user = str(event.get_sender_id())
        except Exception:
            raw_user = "?"
        try:
            raw_group = str(event.get_group_id())
        except Exception:
            raw_group = "private"

        mapped = self.lookup_qq_id(raw_user) if self.is_qq_official(event) else None
        resolved_user = self.get_user_id(event)
        resolved_group = self.get_group_id(event)
        candidates = self._candidate_nicknames(event)

        logger.info(
            "[RunningRank] 身份解析 | 命令=%s | 平台=%s | "
            "原始群id=%s -> 解析群号=%s | "
            "原始用户id=%s -> 解析QQ号=%s | 映射表命中=%s | 昵称候选=%r",
            command_name,
            platform,
            raw_group,
            resolved_group,
            raw_user,
            resolved_user,
            mapped,
            candidates,
        )

    # =============================================================
    # openid ↔ QQ 号 映射
    #
    # qq_official 平台拿到的 sender id 是 openid，而旧数据库（OneBot
    # 时代）的 user_id 存的是 QQ 号。这里统一把 openid 解析回 QQ 号，
    # 让所有查询/写库都继续用 QQ 号作 key，而不是直接用 openid。
    # =============================================================

    def resolve_user_id(self, event: AstrMessageEvent) -> str:
        """把事件的 sender id 统一解析为 QQ 号。

        1. OneBot：本身就是 QQ 号，原样返回。
        2. qq_official：openid → qq_openid_map 查映射；
           查不到再按昵称（sender_name / 群名片 / QQ昵称 等多种候选）
           在 newbie_users 里找老 QQ 号并写入映射；都找不到（全新用户）
           则退回 openid 作为其 id。
        """
        raw_id = str(event.get_sender_id())

        if not self.is_qq_official(event):
            return raw_id

        qq_id = self.lookup_qq_id(raw_id)
        if qq_id:
            return qq_id

        group_id = self.get_group_id(event)
        candidates = self._candidate_nicknames(event)
        self._log_candidates_once(raw_id, candidates)

        for nickname in candidates:
            qq_id = self.search_qq_id_by_nickname(nickname, group_id)
            if qq_id:
                self.save_id_mapping(raw_id, qq_id)
                logger.info(
                    "[RunningRank] 已通过昵称 %r 绑定 openid %s -> QQ %s",
                    nickname, raw_id, qq_id,
                )
                return qq_id

        self._diagnose_binding(raw_id, candidates, group_id)
        return raw_id

    def _candidate_nicknames(self, event: AstrMessageEvent):
        """收集可能的昵称候选，群名片 card（群昵称）优先，再试 QQ昵称 / sender_name。"""
        names = []

        try:
            sender = getattr(event.message_obj, "sender", None)
            if isinstance(sender, dict):
                for key in ("card", "nickname", "user_name", "name"):
                    value = sender.get(key)
                    if value:
                        names.append(str(value))
            elif sender is not None:
                for key in ("card", "nickname"):
                    value = getattr(sender, key, None)
                    if value:
                        names.append(str(value))
        except Exception:
            pass

        try:
            name = event.get_sender_name()
            if name:
                names.append(str(name))
        except Exception:
            pass

        seen = set()
        result = []
        for name in names:
            if name not in seen:
                seen.add(name)
                result.append(name)
        return result

    def _log_candidates_once(self, openid: str, candidates) -> None:
        """把该 openid 当前能拿到的昵称候选打印一次，方便确认群名片是否可得。"""
        logged = getattr(self, "_logged_candidates", None)
        if logged is None:
            logged = set()
            self._logged_candidates = logged
        if openid in logged:
            return
        logged.add(openid)
        logger.warning(
            "[RunningRank] 诊断：openid %s 可用昵称候选=%r", openid, candidates)

    def _diagnose_binding(self, openid: str, candidates, group_id: str) -> None:
        """绑定失败时，用宽松条件查一遍 newbie_users，定位为什么按昵称找不到。

        每个 openid 只诊断一次，避免刷屏；重载插件后重新诊断。
        """
        diagnosed = getattr(self, "_diagnosed_openids", None)
        if diagnosed is None:
            diagnosed = set()
            self._diagnosed_openids = diagnosed
        if openid in diagnosed:
            return
        diagnosed.add(openid)

        conn = self.get_newbie_conn()
        cursor = conn.cursor()
        for nickname in candidates:
            cursor.execute(
                "SELECT user_id, group_id, semester FROM newbie_users WHERE nickname = ?",
                (nickname,),
            )
            rows = cursor.fetchall()
            if not rows:
                logger.warning(
                    "[RunningRank] 诊断：昵称 %r 在 newbie_users 里没有记录", nickname)
                continue
            for row in rows:
                logger.warning(
                    "[RunningRank] 诊断：昵称 %r 命中 user_id=%s group_id=%s semester=%s（当前解析 group_id=%s）",
                    nickname, row["user_id"], row["group_id"], row["semester"], group_id,
                )
        conn.close()

    def lookup_qq_id(self, openid: str) -> Optional[str]:
        conn = self.get_newbie_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT qq_id FROM qq_openid_map WHERE openid = ?",
            (openid,),
        )
        row = cursor.fetchone()
        conn.close()
        return row["qq_id"] if row else None

    def save_id_mapping(self, openid: str, qq_id: str) -> None:
        conn = self.get_newbie_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO qq_openid_map (openid, qq_id, created_at)
            VALUES (?, ?, ?)
            """,
            (openid, qq_id, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()

    def set_id_mapping(self, openid: str, qq_id: str) -> None:
        """强制写入/覆盖 openid → QQ 号映射（手动绑定用，可纠正错误的自动绑定）。"""
        conn = self.get_newbie_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO qq_openid_map (openid, qq_id, created_at)
            VALUES (?, ?, ?)
            """,
            (openid, qq_id, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()

    async def bind_qq(self, event: AstrMessageEvent, argument: str = ""):
        """手动绑定：/绑定 QQ号，把当前 openid 绑定到指定 QQ 号。

        用于 QQ 官方机器人拿不到群名片、自动按昵称绑定不到或绑错的情况。
        """
        openid = str(event.get_sender_id())
        qq_id = self.extract_qq_id(argument)

        if not qq_id:
            node = Comp.Node(
                uin=0,
                name="柏柏子",
                content=[Comp.Plain(
                    "❌ 请填写要绑定的 QQ 号。\n\n"
                    "例如：\n/绑定 123456789"
                )],
            )
            yield self.reply_result(event, node)
            return

        self.set_id_mapping(openid, qq_id)

        node = Comp.Node(
            uin=0,
            name="柏柏子",
            content=[Comp.Plain(
                f"✅ 绑定成功！\n\n"
                f"openid {openid} → QQ {qq_id}\n\n"
                f"之后你的跑步/积分都会记到这个 QQ 号名下。"
            )],
        )
        yield self.reply_result(event, node)

    def search_qq_id_by_nickname(self, nickname: Optional[str], group_id: str = "private") -> Optional[str]:
        """按群昵称 + 群号在 newbie_users 里查找老 QQ 号（纯数字 user_id）。

        只在恰好命中一条纯数字 QQ 号记录时返回，否则返回 None，
        避免昵称重名导致绑定到错误的人。
        """
        if not nickname:
            return None
        conn = self.get_newbie_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT user_id
            FROM newbie_users
            WHERE nickname = ?
              AND group_id = ?
              AND semester = '2026_fall'
              AND user_id NOT GLOB '*[^0-9]*'
              AND LENGTH(user_id) >= 5
            """,
            (nickname, group_id),
        )
        rows = cursor.fetchall()
        conn.close()
        ids = [row["user_id"] for row in rows]
        if len(ids) == 1:
            return ids[0]
        if len(ids) > 1:
            logger.warning(
                "[RunningRank] 群昵称 %s 匹配到多个 QQ 号 %s，跳过自动绑定",
                nickname, ids,
            )
        return None

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
            return self.lookup_qq_id(at_user_id) or at_user_id
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

        user_id = self.resolve_user_id(
            event
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
        # 群号（统一走 get_group_id，把 group_openid 映射回数字群号）
        # ---------------------------------------------------------

        group_id = self.get_group_id(event)

        return (
            user_id,
            str(user_name),
            str(group_id)
        )
