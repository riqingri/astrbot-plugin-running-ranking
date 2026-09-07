import os
import re
import shutil
import base64
import inspect
import mimetypes
import urllib.request
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from PIL import Image

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
import astrbot.api.message_components as Comp


@register(
    "astrbot_plugin_running_rank",
    "QingriSun",
    "跑步里程记录、跑步证明、排行榜和榜首展示图片",
    "1.5.0",
)
class RunningRankPlugin(Star):

    def __init__(self, context: Context):
        super().__init__(context)

        logger.info(
            "[RunningRank] llm_generate signature: "
            f"{inspect.signature(self.context.llm_generate)}"
        )

        logger.info(
            "[RunningRank] get_using_provider signature: "
            f"{inspect.signature(self.context.get_using_provider)}"
        )

        # =========================================================
        # 基础目录
        # =========================================================

        self.plugin_dir = os.path.dirname(
            os.path.abspath(__file__)
        )

        self.db_path = os.path.join(
            self.plugin_dir,
            "running.db"
        )

        # =========================================================
        # 图片目录
        # =========================================================

        self.images_dir = os.path.join(
            self.plugin_dir,
            "images"
        )

        self.users_image_dir = os.path.join(
            self.images_dir,
            "users"
        )

        self.proofs_dir = os.path.join(
            self.images_dir,
            "proofs"
        )

        self.cards_dir = os.path.join(
            self.plugin_dir,
            "cards"
        )

        os.makedirs(
            self.users_image_dir,
            exist_ok=True
        )

        os.makedirs(
            self.proofs_dir,
            exist_ok=True
        )

        os.makedirs(
            self.cards_dir,
            exist_ok=True
        )

        # =========================================================
        # 等待图片
        #
        # key:
        #     group_id:user_id
        #
        # type:
        #     running_proof
        #     leader_image
        # =========================================================

        self.pending_images = {}
        self.pending_runs = {}

        self.SUPER_ADMIN = "1929647130"
        self.PHOTO_WAIT_SECONDS = 300
        self.CURRENT_SEMESTER = "2026_fall"
        self.DISTANCE_TOLERANCE = 0.3

        self.newbie_db_path = os.path.join(
            self.plugin_dir,
            "newbie_points.db"
        )

        self.newbie_conn = __import__("sqlite3").connect(
            self.newbie_db_path,
            check_same_thread=False
        )
        self.newbie_conn.row_factory = __import__("sqlite3").Row

        self.image_wait_seconds = 300

        # =========================================================
        # Pillow 图片压缩配置
        # =========================================================

        # JPEG 压缩质量
        #
        # 85：
        #     清晰度和文件大小比较平衡
        #
        self.image_quality = 85

        # 图片最长边
        #
        # 超过 1600 px 自动缩小
        #
        self.max_image_size = 1600

        # =========================================================
        # 初始化数据库
        # =========================================================

        self.init_database()
        self.init_newbie_database()

        self.command_map = {
            "加入新手任务": "join_newbie",
            "撤销报名": "cancel_newbie",
            "退出新手任务": "cancel_newbie",
            "开始积分": "start_points",
            "跑步积分": "running_points_command",
            "参加训练": "add_training",
            "训练": "add_training",
            "训练记录": "add_training",
            "管理员设置": "set_admin",
            "管理员列表": "show_admin_list",
            "新手积分榜": "show_leaderboard",
            "我的新手积分": "my_points",
            "撤销训练": "undo_training",
            "撤销跑步": "undo_newbie_running",
            "新手任务帮助": "newbie_help_command",
            "跑步": "running_command_answer_qq",
            "我的里程": "my_distance",
            "跑量接龙帮助": "running_help",
            "今日榜": "today_rank",
            "日榜": "day_rank",
            "周榜": "week_rank",
            "月榜": "month_rank",
            "总榜": "total_rank",
            "撤销": "undo_self_running",
            "榜首预备": "prepare_leader_image",
            "取消榜首预备": "cancel_leader_image",
        }

        logger.info(
            "[RunningRank] 跑步排行榜插件 1.5.0 已加载"
        )

    def extract_plain_text(self, event: AstrMessageEvent) -> str:
        try:
            message = getattr(event, "message_obj", None)
            if message is None:
                return ""
            payload = getattr(message, "message", None)
            if isinstance(payload, str):
                return payload.strip()
            if isinstance(payload, (list, tuple)):
                parts = []
                for component in payload:
                    if isinstance(component, str):
                        parts.append(component)
                    else:
                        text = getattr(component, "text", None)
                        if text is None:
                            text = getattr(component, "content", None)
                        if text is not None:
                            parts.append(str(text))
                return "".join(parts).strip()
        except Exception:
            pass
        return ""

    def parse_command(self, event: AstrMessageEvent):
        text = self.extract_plain_text(event)
        if not text:
            return None, ""

        cleaned = text.strip()
        if cleaned.startswith("/"):
            cleaned = cleaned[1:].strip()

        if not cleaned:
            return None, ""

        match = re.match(
            r"^([\u4e00-\u9fa5A-Za-z]+)(?:\s+(.*))?$",
            cleaned
        )
        if not match:
            return None, ""

        command_name = match.group(1).strip()
        argument = (match.group(2) or "").strip()
        return command_name, argument

    async def _dispatch_command(self, event: AstrMessageEvent, handler, argument: str):
        try:
            result = handler(event, argument) if argument else handler(event)
        except TypeError:
            result = handler(event)

        if hasattr(result, "__aiter__"):
            async for item in result:
                yield item
            return

        if inspect.isawaitable(result):
            result = await result

        if result is not None:
            yield result

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_all_messages(self, event: AstrMessageEvent):
        self.refresh_user_nickname_from_event(event)

        command_name, argument = self.parse_command(event)
        if not command_name:
            return

        handler_name = self.command_map.get(command_name)
        if not handler_name:
            return

        handler = getattr(self, handler_name, None)
        if not callable(handler):
            return

        async for item in self._dispatch_command(event, handler, argument):
            yield item

    # =============================================================
    # 数据库
    # =============================================================

    def init_database(self):

        import sqlite3

        conn = sqlite3.connect(
            self.db_path
        )

        cursor = conn.cursor()

        # ---------------------------------------------------------
        # 跑步记录
        # ---------------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS running_records (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                user_id TEXT NOT NULL,

                user_name TEXT NOT NULL,

                group_id TEXT NOT NULL,

                distance REAL NOT NULL,

                run_time TEXT NOT NULL,

                created_at TEXT NOT NULL,

                proof_path TEXT

            )
            """
        )

        # ---------------------------------------------------------
        # 榜首图片
        # ---------------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS leader_images (

                user_id TEXT NOT NULL,

                group_id TEXT NOT NULL,

                image_path TEXT NOT NULL,

                updated_at TEXT NOT NULL,

                PRIMARY KEY (
                    user_id,
                    group_id
                )

            )
            """
        )

        # ---------------------------------------------------------
        # 兼容旧数据库
        # ---------------------------------------------------------

        cursor.execute(
            "PRAGMA table_info(running_records)"
        )

        columns = [
            row[1]
            for row in cursor.fetchall()
        ]

        if "proof_path" not in columns:

            cursor.execute(
                """
                ALTER TABLE running_records
                ADD COLUMN proof_path TEXT
                """
            )

        # ---------------------------------------------------------
        # 索引
        # ---------------------------------------------------------

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_running_group_time

            ON running_records (
                group_id,
                run_time
            )
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_running_user_group

            ON running_records (
                user_id,
                group_id
            )
            """
        )

        conn.commit()
        conn.close()

    # =============================================================
    # 数据库连接
    # =============================================================

    def get_conn(self):

        import sqlite3

        return sqlite3.connect(
            self.db_path
        )

    # =============================================================
    # 新手任务数据库
    # =============================================================

    def init_newbie_database(self):

        cursor = self.newbie_conn.cursor()

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS newbie_users (
            group_id TEXT NOT NULL,
            semester TEXT NOT NULL,
            user_id TEXT NOT NULL,
            nickname TEXT,
            gender TEXT NOT NULL DEFAULT 'male',
            joined_at TEXT NOT NULL,
            points_started INTEGER NOT NULL DEFAULT 0,
            points_started_at TEXT,
            PRIMARY KEY (group_id, semester, user_id)
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS newbies_admins (
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (group_id, user_id)
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS newbie_running_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            semester TEXT NOT NULL,
            user_id TEXT NOT NULL,
            distance REAL NOT NULL,
            created_at TEXT NOT NULL
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS newbie_running_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            semester TEXT NOT NULL,
            user_id TEXT NOT NULL,
            year INTEGER NOT NULL,
            week INTEGER NOT NULL,
            month INTEGER NOT NULL,
            points INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(group_id, semester, user_id, year, week)
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS newbie_training_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            semester TEXT NOT NULL,
            user_id TEXT NOT NULL,
            admin_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)

        self.newbie_conn.commit()

    def get_newbie_conn(self):

        conn = __import__("sqlite3").connect(
            self.newbie_db_path
        )
        conn.row_factory = __import__("sqlite3").Row
        return conn

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

    def get_running_stage(self, started_at: Optional[str], reference_time: Optional[datetime] = None) -> int:
        if not started_at:
            return 0

        try:
            start_time = datetime.fromisoformat(started_at)
        except (TypeError, ValueError):
            return 0

        if reference_time is None:
            reference_time = datetime.now()

        start_week_start = start_time - timedelta(days=start_time.weekday())
        ref_week_start = reference_time - timedelta(days=reference_time.weekday())
        weeks_since_start = max(0, (ref_week_start - start_week_start).days // 7)

        # 如果不是从周一开始积分，就把“本周”和“下一周”都算成第 1 阶段
        if start_time.weekday() != 0 and weeks_since_start < 2:
            days_to_next_stage = (start_week_start + timedelta(weeks=2) - reference_time).days
            return 1, max(0, days_to_next_stage)

        elapsed_weeks = max(0, (reference_time - start_time).days // 7)

        if elapsed_weeks < 4:
            return 1, 4 * 7 - (reference_time - start_time).days
        if elapsed_weeks < 8:
            return 2, 8 * 7 - (reference_time - start_time).days
        if elapsed_weeks < 12:
            return 3, 12 * 7 - (reference_time - start_time).days
        if elapsed_weeks < 16:
            return 4, 16 * 7 - (reference_time - start_time).days
        return 4, 0

    def get_running_rule(self, gender: str, started_at: Optional[str], reference_time: Optional[datetime] = None) -> Optional[Dict]:
        stage, days_to_next_stage = self.get_running_stage(started_at, reference_time)
        if stage == 0:
            return None

        male_rules = {
            1: {"distance": 4, "point1": 3, "point2": 4},
            2: {"distance": 5, "point1": 4, "point2": 5},
            3: {"distance": 5, "point1": 5, "point2": 6},
            4: {"distance": 6, "point1": 5, "point2": 6},
        }
        female_rules = {
            1: {"distance": 2, "point1": 3, "point2": 4},
            2: {"distance": 3, "point1": 4, "point2": 5},
            3: {"distance": 4, "point1": 5, "point2": 6},
            4: {"distance": 4, "point1": 5, "point2": 6},
        }

        if gender == "female":
            return female_rules.get(stage), stage, days_to_next_stage
        return male_rules.get(stage), stage, days_to_next_stage

    async def join_newbie(self, event: AstrMessageEvent, gender_text: str = "男"):
        group_id = self.get_group_id(event)
        user_id = self.get_user_id(event)
        nickname = await self.get_sender_nickname(event)
        gender_text = str(gender_text).strip().lower()

        if gender_text in ["女", "女生", "female", "girl"]:
            gender = "female"
            gender_name = "女生"
        else:
            gender = "male"
            gender_name = "男生"

        conn = self.get_newbie_conn()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT *
        FROM newbie_users
        WHERE group_id = ? AND semester = ? AND user_id = ?
        """, (group_id, "2026_fall", user_id))
        old_user = cursor.fetchone()
        if old_user:
            yield event.plain_result("你已经参加了本学期的新手任务。\n无需重复报名。")
            conn.close()
            return

        cursor.execute("""
        INSERT INTO newbie_users (
            group_id, semester, user_id, nickname, gender, joined_at, points_started, points_started_at
        ) VALUES (?, ?, ?, ?, ?, ?, 0, NULL)
        """, (group_id, "2026_fall", user_id, nickname, gender, datetime.now().isoformat()))
        conn.commit()
        conn.close()

        yield event.plain_result(
            f"🎉 新手任务报名成功！\n\n"
            f"成员：{nickname}\n"
            f"组别：{gender_name}\n"
            f"学期：2026_fall\n\n"
            "请等待管理员使用：\n"
            f"/开始积分 @{nickname}\n\n"
            "管理员开始积分后，你才能使用 /跑步积分 命令。"
        )

    async def cancel_newbie(self, event: AstrMessageEvent, argument: str = ""):
        group_id = self.get_group_id(event)
        admin_id = self.get_user_id(event)

        if not self.is_admin(group_id, admin_id):
            yield event.plain_result("❌ 只有管理员可以撤销报名。")
            return

        target_user_id = self.get_target_user_id(event, argument)
        if not target_user_id:
            yield event.plain_result(
                "❌ 请 @ 一位群友或输入 QQ 号。\n\n例如：\n/撤销报名 @张三\n或\n/撤销报名 123456789"
            )
            return

        conn = self.get_newbie_conn()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT * FROM newbie_users
        WHERE group_id = ? AND semester = ? AND user_id = ?
        """, (group_id, "2026_fall", target_user_id))
        user = cursor.fetchone()

        if not user:
            conn.close()
            yield event.plain_result("❌ 该成员尚未报名新手任务，无法撤销报名。")
            return

        cursor.execute("""
        DELETE FROM newbie_users
        WHERE group_id = ? AND semester = ? AND user_id = ?
        """, (group_id, "2026_fall", target_user_id))
        cursor.execute("""
        DELETE FROM newbie_running_records
        WHERE group_id = ? AND semester = ? AND user_id = ?
        """, (group_id, "2026_fall", target_user_id))
        cursor.execute("""
        DELETE FROM newbie_training_records
        WHERE group_id = ? AND semester = ? AND user_id = ?
        """, (group_id, "2026_fall", target_user_id))
        cursor.execute("""
        DELETE FROM newbie_running_points
        WHERE group_id = ? AND semester = ? AND user_id = ?
        """, (group_id, "2026_fall", target_user_id))
        conn.commit()
        conn.close()

        self.pending_runs.pop(f"{group_id}:{target_user_id}", None)

        yield event.plain_result(
            f"✅ 已撤销 {user['nickname'] or target_user_id} 的报名\n\n"
            "该成员的本学期新手任务报名、跑步记录、训练记录和积分已清空。"
        )

    async def start_points(self, event: AstrMessageEvent, argument: str = ""):
        group_id = self.get_group_id(event)
        admin_id = self.get_user_id(event)

        if not self.is_admin(group_id, admin_id):
            yield event.plain_result("❌ 只有管理员可以开始积分。")
            return

        target_user_id = self.get_target_user_id(event, argument)
        if not target_user_id:
            yield event.plain_result(
                "❌ 请 @ 一位群友或输入 QQ 号。\n\n例如：\n/开始积分 @张三\n或\n/开始积分 123456789"
            )
            return

        conn = self.get_newbie_conn()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT * FROM newbie_users
        WHERE group_id = ? AND semester = ? AND user_id = ?
        """, (group_id, "2026_fall", target_user_id))
        user = cursor.fetchone()
        if not user:
            conn.close()
            yield event.plain_result(
                "❌ 该成员尚未加入新手任务。\n请先让该成员使用：\n/加入新手任务 男\n或\n/加入新手任务 女"
            )
            return

        if user["points_started"] == 1:
            conn.close()
            display_name = self.get_display_name(event, user["nickname"])
            yield event.plain_result(f"⚠️ {display_name} 已经开始积分，无需重复操作。")
            return

        cursor.execute("""
        UPDATE newbie_users
        SET points_started = 1, points_started_at = ?
        WHERE group_id = ? AND semester = ? AND user_id = ?
        """, (datetime.now().isoformat(), group_id, "2026_fall", target_user_id))
        conn.commit()
        conn.close()

        display_name = self.get_display_name(event, user["nickname"])

        yield event.plain_result(
            f"🎯 {display_name} 开始积分！\n\n"
            f"积分阶段按“开始积分后的周龄”计算：\n"
            f"第1阶段：0-4周\n"
            f"第2阶段：4-8周\n"
            f"第3阶段：8-12周\n"
            f"第4阶段：12-16周\n\n"
            f"从现在开始，该成员可以使用：\n/跑步积分 距离\n\n例如：\n/跑步积分 5km"
        )

    async def running_points_command(self, event: AstrMessageEvent, distance_text: str):
        group_id = self.get_group_id(event)
        user_id = self.get_user_id(event)

        match = re.search(r"(\d+(?:\.\d+)?)", str(distance_text))
        if not match:
            yield event.plain_result("❌ 距离格式错误。\n\n例如：\n/跑步积分 5km")
            return

        distance = float(match.group(1))
        conn = self.get_newbie_conn()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT * FROM newbie_users
        WHERE group_id = ? AND semester = ? AND user_id = ?
        """, (group_id, "2026_fall", user_id))
        user = cursor.fetchone()
        if not user:
            conn.close()
            yield event.plain_result("❌ 你还没有加入新手任务。\n\n请先使用：\n/加入新手任务 男\n或\n/加入新手任务 女")
            return

        if user["points_started"] != 1:
            conn.close()
            yield event.plain_result("⏳ 你已经报名新手任务，但管理员尚未为你开始积分。\n\n请等待管理员使用：\n/开始积分 @你")
            return

        now = datetime.now()
        rule, stage, days_to_next_stage = self.get_running_rule(user["gender"], user["points_started_at"], now)
        if rule is None:
            conn.close()
            yield event.plain_result("❌ 该成员尚未开始积分，无法计算当前阶段规则。")
            return

        required_distance = rule["distance"]
        if distance < required_distance:
            conn.close()
            yield event.plain_result(f"⚠️ 你处于新手任务的第{stage}阶段，当前要求每次跑步距离至少 {required_distance}km。如果跑步距离未达到{required_distance}km，请使用 /跑步 命令跑量接龙，本次跑步记录不会计入新手任务。")
            return

        key = f"{group_id}:{user_id}"
        self.pending_runs[key] = {"distance": distance, "created_at": datetime.now()}
        conn.close()

        yield event.plain_result(
            f"你处于新手任务的第{stage}阶段，距离下一阶段还有{days_to_next_stage}天\n"
            f"🏃 已提交 {distance}km 跑步任务。\n\n请在 {self.PHOTO_WAIT_SECONDS // 60} 分钟内上传跑步截图或照片。\n\n⚠️ 只有收到图片凭证后，本次跑步才会正式记录并计算积分。"
        )

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def receive_newbie_image(self, event: AstrMessageEvent):
        group_id = self.get_group_id(event)
        user_id = self.get_user_id(event)
        key = f"{group_id}:{user_id}"

        if key not in self.pending_runs:
            return

        pending = self.pending_runs[key]
        elapsed = (datetime.now() - pending["created_at"]).total_seconds()
        if elapsed > self.PHOTO_WAIT_SECONDS:
            del self.pending_runs[key]
            yield event.plain_result("⏰ 跑步凭证上传超时。\n本次跑步未记录。")
            return

        has_image = False
        try:
            for component in event.message_obj.message:
                if isinstance(component, Comp.Image):
                    has_image = True
                    break
        except Exception:
            pass

        if not has_image:
            return

        distance = pending["distance"]
        del self.pending_runs[key]
        message, source_path = await self.confirm_newbie_running(event, distance)
        yield event.plain_result(message)

        if not source_path or not os.path.exists(source_path):
            return

        try:
            provider = self.context.get_using_provider(event.unified_msg_origin)
            if provider is None:
                logger.warning("[RunningRank] 未找到当前会话的 LLM Provider")
                return

            chat_provider_id = provider.meta().id
            logger.info("[RunningRank] 准备将跑步证明图片发送给 LLM: %s", chat_provider_id)

            response = await self.context.llm_generate(
                chat_provider_id=chat_provider_id,
                prompt="你是一个爱水群爱吐槽的跑团组织者，跑团同学发送了一张图片，请看看这张图片是否是跑步证明？如果不是，就谴责他乱发图片，简单吐槽一下图片内容；如果是，就简单分析一下图片内容，找到简单夸奖或者聊聊图片的配速和地图或者其他图片信息。回复30个字以内，禁止使用markdown语法，使回复在一个QQ消息气泡中显得自然",
                image_urls=[source_path],
            )

            if response and response.completion_text:
                llm_reply = str(response.completion_text).strip()
                if llm_reply:
                    yield event.plain_result(llm_reply)

        except Exception as e:
            logger.error("[RunningRank] 调用 LLM 分析跑步图片失败: %s", e)

    async def confirm_newbie_running(self, event: AstrMessageEvent, distance: float) -> Tuple[str, str]:
        group_id = self.get_group_id(event)
        user_id = self.get_user_id(event)
        now = datetime.now()

        conn = self.get_newbie_conn()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT * FROM newbie_users
        WHERE group_id = ? AND semester = ? AND user_id = ?
        """, (group_id, "2026_fall", user_id))
        user = cursor.fetchone()
        if not user:
            conn.close()
            return "❌ 用户不存在。", ""

        proof_path = None
        source_path = None
        try:
            message_obj = getattr(event, "message_obj", None)
            message_chain = getattr(message_obj, "message", None)
            image_component = None

            if message_chain:
                for component in message_chain:
                    if isinstance(component, Comp.Image):
                        image_component = component
                        break

            if image_component is not None:
                try:
                    source_path = await image_component.convert_to_file_path()
                except Exception as e:
                    logger.error("[RunningRank] 获取新手任务图片失败: %s", e)

                if source_path and os.path.exists(source_path):
                    timestamp = now.strftime("%Y%m%d_%H%M%S_%f")
                    proof_filename = f"{group_id}_{user_id}_{timestamp}.jpg"
                    proof_path = os.path.join(self.proofs_dir, proof_filename)

                    try:
                        success = self.compress_image(source_path, proof_path)
                        if not success:
                            shutil.copy2(source_path, proof_path)
                    except Exception as e:
                        logger.error("[RunningRank] 保存新手任务跑步证明失败: %s", e)
                        proof_path = None
        except Exception as e:
            logger.warning("[RunningRank] 处理新手任务图片证明失败: %s", e)
            proof_path = None

        cursor.execute("""
        INSERT INTO newbie_running_records (group_id, semester, user_id, distance, created_at)
        VALUES (?, ?, ?, ?, ?)
        """, (group_id, "2026_fall", user_id, distance, now.isoformat()))

        user_name = self.get_user_info(event)[1]
        main_conn = self.get_conn()
        main_cursor = main_conn.cursor()
        main_cursor.execute(
            """
            INSERT INTO running_records (
                user_id,
                user_name,
                group_id,
                distance,
                run_time,
                created_at,
                proof_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                user_name,
                group_id,
                distance,
                now.isoformat(),
                now.isoformat(),
                proof_path,
            )
        )
        main_conn.commit()
        main_conn.close()

        week_start = now - timedelta(days=now.weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)

        cursor.execute("""
        SELECT COUNT(*) AS count
        FROM newbie_running_records
        WHERE group_id = ? AND semester = ? AND user_id = ? AND created_at >= ?
        """, (group_id, "2026_fall", user_id, week_start.isoformat()))
        weekly_count = cursor.fetchone()["count"]

        rule, stage, days_to_next_stage = self.get_running_rule(user["gender"], user["points_started_at"], now)
        if rule is None:
            conn.close()
            return "❌ 该成员尚未开始积分，无法计算当前阶段规则。", ""

        year, week, _ = now.isocalendar()

        cursor.execute("""
        SELECT points FROM newbie_running_points
        WHERE group_id = ? AND semester = ? AND user_id = ? AND year = ? AND week = ?
        """, (group_id, "2026_fall", user_id, year, week))
        old_record = cursor.fetchone()
        old_points = old_record["points"] if old_record else 0

        if weekly_count >= rule["point2"]:
            new_points = 2
        elif weekly_count >= rule["point1"]:
            new_points = 1
        else:
            new_points = 0

        point_change = new_points - old_points

        if new_points > 0:
            cursor.execute("""
            INSERT INTO newbie_running_points (group_id, semester, user_id, year, week, month, points, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(group_id, semester, user_id, year, week)
            DO UPDATE SET points = excluded.points, created_at = excluded.created_at
            """, (group_id, "2026_fall", user_id, year, week, now.month, new_points, now.isoformat()))

        conn.commit()
        leaderboard = self.get_newbie_leaderboard(group_id, changed_user=user_id, point_change=point_change)

        if weekly_count < rule["point1"]:
            remaining = rule["point1"] - weekly_count
            progress = f"距离 1 分档还差 {remaining} 次"
        elif weekly_count < rule["point2"]:
            remaining = rule["point2"] - weekly_count
            progress = f"距离 2 分档还差 {remaining} 次"
        else:
            progress = "本周最高积分已完成！"

        week_ranking = self.query_ranking(group_id, "week")
        if week_ranking:
            week_lines = []
            for index, row in enumerate(week_ranking[:], start=1):
                week_lines.append(f"{index}. {row[1]} ｜ {float(row[2]):.2f} km")
            week_text = "\n".join(week_lines)
        else:
            week_text = "暂无周榜数据"
            

        display_name = self.get_display_name(event, user["nickname"])

        message = (
            f"🏃 跑步记录成功！\n\n"
            f"成员：{display_name}\n"
            f"本次距离：{distance}km\n"
            f"本周完成：{weekly_count} 次\n"
            f"本周积分：{new_points} 分\n"
            f"{progress}\n"
        )

        message += f"\n🎉 本次积分变化：+{point_change}\n" if point_change > 0 else "\n📌 本次积分变化：+0\n"
        message += "\n\n📊 新手任务积分排行榜\n━━━━━━━━━━━━━━\n" + leaderboard
        message += "\n\n"
        message += "\n\n📊 本周跑量榜\n━━━━━━━━━━━━━━\n" + week_text
        conn.close()

        return message, source_path
        
    async def add_training(self, event: AstrMessageEvent, argument: str = ""):
        argument = str(argument or "").strip()
        if argument.startswith("/"):
            argument = argument[1:].lstrip()

        group_id = self.get_group_id(event)
        admin_id = self.get_user_id(event)
        if not self.is_admin(group_id, admin_id):
            yield event.plain_result("❌ 只有管理员可以记录训练。")
            return

        target_user_id = self.get_target_user_id(event, argument)
        if not target_user_id:
            yield event.plain_result("❌ 请 @ 一位群友或输入 QQ 号。\n\n例如：\n/参加训练 @张三")
            return

        conn = self.get_newbie_conn()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT * FROM newbie_users
        WHERE group_id = ? AND semester = ? AND user_id = ?
        """, (group_id, "2026_fall", target_user_id))
        user = cursor.fetchone()
        if not user:
            conn.close()
            yield event.plain_result("❌ 该成员尚未参加新手任务。")
            return

        cursor.execute("""
        INSERT INTO newbie_training_records (group_id, semester, user_id, admin_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        """, (group_id, "2026_fall", target_user_id, admin_id, datetime.now().isoformat()))
        conn.commit()

        cursor.execute("""
        SELECT COUNT(*) AS count FROM newbie_training_records WHERE group_id = ? AND semester = ? AND user_id = ?
        """, (group_id, "2026_fall", target_user_id))
        training_count = cursor.fetchone()["count"]
        old_count = training_count - 1
        old_points = min((old_count // 4) * 3, 12)
        new_points = min((training_count // 4) * 3, 12)
        point_change = new_points - old_points

        leaderboard = self.get_newbie_leaderboard(group_id, changed_user=target_user_id, point_change=point_change)
        display_name = self.get_display_name(event, user["nickname"])
        message = (f"🏋️ 训练记录成功！\n\n成员：{display_name}\n累计训练：{training_count} 次\n训练积分：{new_points} 分\n")
        if point_change > 0:
            message += f"\n🎉 本次积分变化：+{point_change}\n"
        else:
            remaining = 4 - (training_count % 4)
            if training_count >= 16:
                remaining = 0
            message += "\n📌 本次积分变化：+0\n"
            if remaining > 0:
                message += f"再完成 {remaining} 次训练可获得 +3 分\n"
            else:
                message += "训练积分已达到上限 12 分\n"
        message += "\n━━━━━━━━━━━━━━\n📊 新手任务积分排行榜\n━━━━━━━━━━━━━━\n" + leaderboard
        conn.close()
        yield event.plain_result(message)

    async def set_admin(self, event: AstrMessageEvent, argument: str = ""):
        group_id = self.get_group_id(event)
        user_id = self.get_user_id(event)
        if not self.is_super_admin(user_id):
            yield event.plain_result("❌ 只有超级管理员可以设置管理员。")
            return

        target_user_id = self.get_target_user_id(event, argument)
        if not target_user_id:
            yield event.plain_result("❌ 请 @ 群友或输入 QQ 号。\n\n例如：\n/管理员设置 123456789")
            return

        conn = self.get_newbie_conn()
        cursor = conn.cursor()
        cursor.execute("""
        INSERT OR IGNORE INTO newbies_admins (group_id, user_id, created_at)
        VALUES (?, ?, ?)
        """, (group_id, target_user_id, datetime.now().isoformat()))
        conn.commit()
        nickname = self.get_user_nickname(group_id, target_user_id)
        admin_list = self.get_admin_list(group_id)
        conn.close()
        yield event.plain_result(f"🎉 {nickname} 成为管理员\n\n👮 当前管理员：\n{admin_list}")

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

    def get_admin_list(self, group_id: str) -> str:
        conn = self.get_newbie_conn()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT user_id FROM newbies_admins WHERE group_id = ? ORDER BY created_at ASC
        """, (group_id,))
        admin_ids = [row["user_id"] for row in cursor.fetchall()]
        conn.close()
        if "1929647130" not in admin_ids:
            admin_ids.insert(0, "1929647130")
        lines = []
        for index, admin_id in enumerate(admin_ids, start=1):
            nickname = self.get_user_nickname(group_id, admin_id)
            role = "超级管理员" if admin_id == "1929647130" else "管理员"
            lines.append(f"{index}. {nickname} （{role}）")
        return "\n".join(lines)

    async def show_admin_list(self, event: AstrMessageEvent):
        group_id = self.get_group_id(event)
        result = self.get_admin_list(group_id)
        yield event.plain_result("👮 当前管理员\n━━━━━━━━━━━━━━\n" + result)

    def get_user_stats(self, group_id: str, user_id: str) -> Dict:
        conn = self.get_newbie_conn()
        cursor = conn.cursor()

        cursor.execute("""
        SELECT COUNT(*) AS count FROM newbie_running_records
        WHERE group_id = ? AND semester = ? AND user_id = ?
        """, (group_id, "2026_fall", user_id))
        running_count = cursor.fetchone()["count"]

        cursor.execute("""
        SELECT COUNT(*) AS count FROM newbie_training_records
        WHERE group_id = ? AND semester = ? AND user_id = ?
        """, (group_id, "2026_fall", user_id))
        training_count = cursor.fetchone()["count"]

        cursor.execute("""
        SELECT COALESCE(SUM(points), 0) AS points FROM newbie_running_points
        WHERE group_id = ? AND semester = ? AND user_id = ?
        """, (group_id, "2026_fall", user_id))
        running_points = cursor.fetchone()["points"]

        training_points = min((training_count // 4) * 3, 12)
        total_points = running_points + training_points
        conn.close()

        return {
            "running_count": running_count,
            "training_count": training_count,
            "total_count": running_count + training_count,
            "running_points": running_points,
            "training_points": training_points,
            "total_points": total_points,
        }

    def get_newbie_leaderboard(self, group_id: str, changed_user: Optional[str] = None, point_change: int = 0) -> str:
        conn = self.get_newbie_conn()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT * FROM newbie_users
        WHERE group_id = ? AND semester = ? AND points_started = 1
        """, (group_id, "2026_fall"))
        users = cursor.fetchall()
        data = []
        for user in users:
            stats = self.get_user_stats(group_id, user["user_id"])
            data.append({"user_id": user["user_id"], "nickname": user["nickname"] or user["user_id"], **stats})
        data.sort(key=lambda x: (-x["total_points"], -x["total_count"]))
        conn.close()
        if not data:
            return "暂无已经开始积分的成员。"

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for index, user in enumerate(data, start=1):
            rank = medals[index - 1] if index <= 3 else f"{index}."
            line = f"{rank} {user['nickname']} ｜ 积分 {user['total_points']}"
            if user["user_id"] == changed_user and point_change != 0:
                sign = "+" if point_change > 0 else ""
                line += f" ｜ 本次 {sign}{point_change}"
            lines.append(line)
        return "\n".join(lines)

    async def show_leaderboard(self, event: AstrMessageEvent):
        group_id = self.get_group_id(event)
        leaderboard = self.get_newbie_leaderboard(group_id)
        yield event.plain_result("📊 新手任务积分排行榜\n━━━━━━━━━━━━━━\n" + leaderboard)

    async def my_points(self, event: AstrMessageEvent):
        group_id = self.get_group_id(event)
        user_id = self.get_user_id(event)
        conn = self.get_newbie_conn()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT * FROM newbie_users WHERE group_id = ? AND semester = ? AND user_id = ?
        """, (group_id, "2026_fall", user_id))
        user = cursor.fetchone()
        if not user:
            conn.close()
            yield event.plain_result("你还没有参加新手任务。")
            return
        stats = self.get_user_stats(group_id, user_id)
        status = "🟢 已开始积分" if user["points_started"] == 1 else "🟡 等待管理员开始积分"
        conn.close()
        display_name = self.get_display_name(event, user["nickname"])
        yield event.plain_result(
            f"📊 {display_name} 的新手任务\n━━━━━━━━━━━━━━\n{status}\n\n"
            f"🏃 跑步次数：{stats['running_count']}\n"
            f"🏋️ 训练次数：{stats['training_count']}\n"
            f"📌 总次数：{stats['total_count']}\n\n"
            f"🏃 跑步积分：{stats['running_points']} 分\n"
            f"🏋️ 训练积分：{stats['training_points']} 分\n"
            f"⭐ 总积分：{stats['total_points']} 分"
        )

    async def undo_training(self, event: AstrMessageEvent, argument: str = ""):
        group_id = self.get_group_id(event)
        admin_id = self.get_user_id(event)
        if not self.is_admin(group_id, admin_id):
            yield event.plain_result("❌ 只有管理员可以撤销训练记录。")
            return

        target_user_id = self.get_target_user_id(event, argument)
        if not target_user_id:
            yield event.plain_result("❌ 请 @ 一位群友或输入 QQ 号。\n\n例如：\n/撤销训练 @张三")
            return

        before_stats = self.get_user_stats(group_id, target_user_id)
        if before_stats["training_count"] <= 0:
            yield event.plain_result("❌ 该成员没有可以撤销的训练记录。")
            return

        conn = self.get_newbie_conn()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT * FROM newbie_training_records
        WHERE group_id = ? AND semester = ? AND user_id = ?
        ORDER BY created_at DESC LIMIT 1
        """, (group_id, "2026_fall", target_user_id))
        training = cursor.fetchone()
        if not training:
            conn.close()
            yield event.plain_result("❌ 没有找到训练记录。")
            return

        cursor.execute("DELETE FROM newbie_training_records WHERE id = ?", (training["id"],))
        conn.commit()
        after_stats = self.get_user_stats(group_id, target_user_id)
        point_change = after_stats["total_points"] - before_stats["total_points"]
        nickname = self.get_display_name(event, self.get_user_nickname(group_id, target_user_id))
        leaderboard = self.get_newbie_leaderboard(group_id, changed_user=target_user_id, point_change=point_change)
        conn.close()
        yield event.plain_result(
            f"↩️ 已撤销 {nickname} 最近一次训练记录\n\n"
            f"🏋️ 训练次数：{before_stats['training_count']} → {after_stats['training_count']}\n"
            f"🏋️ 训练积分：{before_stats['training_points']} → {after_stats['training_points']}\n"
            f"⭐ 总积分变化：{point_change:+d}\n\n"
            f"━━━━━━━━━━━━━━\n📊 新手任务积分排行榜\n━━━━━━━━━━━━━━\n{leaderboard}"
        )

    async def undo_newbie_running(self, event: AstrMessageEvent, argument: str = ""):
        group_id = self.get_group_id(event)
        admin_id = self.get_user_id(event)
        if not self.is_admin(group_id, admin_id):
            yield event.plain_result("❌ 只有管理员可以撤销跑步记录。")
            return

        target_user_id = self.get_target_user_id(event, argument)
        if not target_user_id:
            yield event.plain_result("❌ 请 @ 一位群友或输入 QQ 号。\n\n例如：\n/撤销跑步 @张三")
            return

        conn = self.get_newbie_conn()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT * FROM newbie_running_records
        WHERE group_id = ? AND semester = ? AND user_id = ?
        ORDER BY created_at DESC LIMIT 1
        """, (group_id, "2026_fall", target_user_id))
        running = cursor.fetchone()
        if not running:
            conn.close()
            yield event.plain_result("❌ 该成员没有可以撤销的跑步记录。")
            return

        before_stats = self.get_user_stats(group_id, target_user_id)
        running_time = datetime.fromisoformat(running["created_at"])
        year, week, _ = running_time.isocalendar()
        cursor.execute("DELETE FROM newbie_running_records WHERE id = ?", (running["id"],))
        conn.commit()

        main_conn = self.get_conn()
        main_cursor = main_conn.cursor()
        main_cursor.execute(
            """
            DELETE FROM running_records
            WHERE group_id = ?
              AND user_id = ?
              AND distance = ?
              AND created_at = ?
            """,
            (
            group_id,
            target_user_id,
            float(running["distance"]),
            running["created_at"],
            )
        )
        main_conn.commit()
        main_conn.close()

        week_start = running_time - timedelta(days=running_time.weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        week_end = week_start + timedelta(days=7)
        cursor.execute("""
        SELECT COUNT(*) AS count FROM newbie_running_records
        WHERE group_id = ? AND semester = ? AND user_id = ? AND created_at >= ? AND created_at < ?
        """, (group_id, "2026_fall", target_user_id, week_start.isoformat(), week_end.isoformat()))
        weekly_count = cursor.fetchone()["count"]

        cursor.execute("""
        SELECT * FROM newbie_users WHERE group_id = ? AND semester = ? AND user_id = ?
        """, (group_id, "2026_fall", target_user_id))
        user = cursor.fetchone()
        if not user:
            conn.close()
            yield event.plain_result("❌ 找不到该成员的新手任务信息。")
            return

        rule, stage, days_to_next_stage = self.get_running_rule(user["gender"], user["points_started_at"], running_time)
        if rule is None:
            conn.close()
            yield event.plain_result("❌ 该成员尚未开始积分，无法计算撤销后的阶段规则。")
            return

        if weekly_count >= rule["point2"]:
            new_week_points = 2
        elif weekly_count >= rule["point1"]:
            new_week_points = 1
        else:
            new_week_points = 0

        if new_week_points == 0:
            cursor.execute("""
            DELETE FROM newbie_running_points
            WHERE group_id = ? AND semester = ? AND user_id = ? AND year = ? AND week = ?
            """, (group_id, "2026_fall", target_user_id, year, week))
        else:
            cursor.execute("""
            INSERT INTO newbie_running_points (group_id, semester, user_id, year, week, month, points, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(group_id, semester, user_id, year, week)
            DO UPDATE SET points = excluded.points, created_at = excluded.created_at
            """, (group_id, "2026_fall", target_user_id, year, week, running_time.month, new_week_points, datetime.now().isoformat()))
        conn.commit()
        after_stats = self.get_user_stats(group_id, target_user_id)
        point_change = after_stats["total_points"] - before_stats["total_points"]
        nickname = self.get_display_name(event, user["nickname"])
        leaderboard = self.get_newbie_leaderboard(group_id, changed_user=target_user_id, point_change=point_change)
        conn.close()
        yield event.plain_result(
            f"↩️ 已撤销 {nickname} 最近一次跑步记录\n\n"
            f"🏃 撤销距离：{running['distance']}km\n"
            f"📅 该周当前跑步次数：{weekly_count}\n"
            f"🏃 跑步积分：{before_stats['running_points']} → {after_stats['running_points']}\n"
            f"⭐ 总积分变化：{point_change:+d}\n\n"
            f"━━━━━━━━━━━━━━\n📊 新手任务积分排行榜\n━━━━━━━━━━━━━━\n{leaderboard}"
        )

    def build_unified_help_message(self, user_id: str, group_id: str) -> str:
        lines = [
            "🏃 跑团命令总览",
            "━━━━━━━━━━━━━━",
            "",
            "【新手任务】",
            "/加入新手任务  报名",
            "/开始积分     开始",
            "/跑步积分     提交",
            "/我的新手积分  查看",
            "/新手积分榜   排行",
            "",
            "【跑量接龙】",
            "/跑步         记录",
            "/我的里程     统计",
            "/日榜         排行",
            "/周榜         排行",
            "/月榜         排行",
            "/总榜         排行",
            "/撤销         撤销",
            "",
            "【管理员】",
            "/管理员列表  列表",
        ]

        if self.is_admin(group_id, user_id):
            lines.extend([
                "/开始积分     开始",
                "/撤销报名     撤销",
                "/参加训练     记录",
                "/撤销跑步     撤销",
                "/撤销训练     撤销",
            ])

        if self.is_super_admin(user_id):
            lines.extend([
                "",
                "【超级管理员】",
                "/管理员设置   设置",
            ])

        return "\n".join(lines)

    async def newbie_help_command(self, event: AstrMessageEvent):
        user_id = self.get_user_id(event)
        group_id = self.get_group_id(event)
        message = self.build_unified_help_message(user_id, group_id)
        yield event.plain_result(message)

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

    # =============================================================
    # 时间
    # =============================================================

    def get_today_start(self):

        now = datetime.now()

        return now.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

    def get_week_start(self):

        now = datetime.now()

        monday = (
            now
            - timedelta(
                days=now.weekday()
            )
        )

        return monday.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

    def get_month_start(self):

        now = datetime.now()

        return now.replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

    # =============================================================
    # 图片压缩
    #
    # 使用 Pillow
    #
    # 规则：
    #
    # 1. 最大边 1600 px
    # 2. 普通图片保存为 JPEG
    # 3. JPEG quality = 85
    # 4. 有透明通道的图片保留 PNG
    # 5. GIF 只取第一帧
    # =============================================================

    def compress_image(
        self,
        source_path: str,
        target_path: str
    ):

        try:

            with Image.open(
                source_path
            ) as image:

                # -------------------------------------------------
                # 尺寸
                # -------------------------------------------------

                original_width, original_height = (
                    image.size
                )

                # -------------------------------------------------
                # GIF / 动图
                #
                # 排行榜展示不需要动画，
                # 直接取第一帧
                # -------------------------------------------------

                try:

                    if getattr(
                        image,
                        "is_animated",
                        False
                    ):

                        image.seek(0)

                except Exception:

                    pass

                # -------------------------------------------------
                # 缩放
                # -------------------------------------------------

                max_size = self.max_image_size

                if (
                    original_width > max_size
                    or original_height > max_size
                ):

                    image.thumbnail(
                        (
                            max_size,
                            max_size
                        ),
                        Image.Resampling.LANCZOS
                    )

                # -------------------------------------------------
                # 判断透明通道
                # -------------------------------------------------

                has_alpha = (
                    image.mode in (
                        "RGBA",
                        "LA"
                    )
                    or (
                        image.mode == "P"
                        and "transparency"
                        in image.info
                    )
                )

                # -------------------------------------------------
                # 有透明通道
                #
                # 保存 PNG
                # -------------------------------------------------

                if has_alpha:

                    if image.mode != "RGBA":

                        image = image.convert(
                            "RGBA"
                        )

                    image.save(
                        target_path,
                        format="PNG",
                        optimize=True
                    )

                # -------------------------------------------------
                # 普通图片
                #
                # 保存 JPEG
                # -------------------------------------------------

                else:

                    if image.mode != "RGB":

                        image = image.convert(
                            "RGB"
                        )

                    image.save(
                        target_path,
                        format="JPEG",
                        quality=self.image_quality,
                        optimize=True,
                        progressive=True
                    )

                # -------------------------------------------------
                # 输出压缩日志
                # -------------------------------------------------

                try:

                    original_size = (
                        os.path.getsize(
                            source_path
                        )
                    )

                    compressed_size = (
                        os.path.getsize(
                            target_path
                        )
                    )

                    if original_size > 0:

                        ratio = (
                            compressed_size
                            / original_size
                            * 100
                        )

                        logger.info(
                            "[RunningRank] "
                            f"图片压缩完成："
                            f"{original_size / 1024:.1f} KB "
                            f"→ "
                            f"{compressed_size / 1024:.1f} KB "
                            f"({ratio:.1f}%)"
                        )

                except Exception:

                    pass

                return True

        except Exception as e:

            logger.warning(
                "[RunningRank] "
                f"Pillow 图片压缩失败: {e}"
            )

            return False

    # =============================================================
    # /跑步
    #
    # /跑步 5.2
    #
    # 必须上传图片后才真正记录
    # =============================================================

    async def running_command_answer_qq(
        self,
        event: AstrMessageEvent,
        distance: str
    ):
        # ---------------------------------------------------------
        # 解析距离
        # ---------------------------------------------------------

        try:

            distance_value = float(
                distance
            )

        except (ValueError, TypeError):

            yield event.plain_result(
                "❌ 里程格式不正确。\n\n"
                "正确用法：\n"
                "/跑步 5\n"
                "/跑步 5.2\n"
                "/跑步 10.5"
            )

            return

        # ---------------------------------------------------------
        # 检查距离
        # ---------------------------------------------------------

        if distance_value <= 0:

            yield event.plain_result(
                "❌ 跑步距离必须大于 0 km。"
            )

            return

        if distance_value > 200:

            yield event.plain_result(
                "❌ 单次跑步距离不能超过 200 km。"
            )

            return

        (
            user_id,
            user_name,
            group_id
        ) = self.get_user_info(
            event
        )

        key = (
            f"{group_id}:"
            f"{user_id}"
        )

        # ---------------------------------------------------------
        # 创建等待任务
        # ---------------------------------------------------------

        self.pending_images[key] = {

            "type": "running_proof",

            "distance": distance_value,

            "expire_time": (
                datetime.now()
                + timedelta(
                    seconds=self.image_wait_seconds
                )
            )
        }

        yield event.plain_result(
            f"🏃 收到你的 {distance_value:.2f} km 跑步记录。\n\n"
            f"📸 请在 5 分钟内发送一张跑步证明图片。\n\n"
            f"⚠️ 收到图片后才会正式计入排行榜。\n"
            f"没有图片则不会记录本次跑步。"
        )


    async def running_command(
        self,
        event: AstrMessageEvent,
        distance: str
    ):

        # ---------------------------------------------------------
        # 解析距离
        # ---------------------------------------------------------

        try:

            distance_value = float(
                distance
            )

        except (ValueError, TypeError):

            yield event.plain_result(
                "❌ 里程格式不正确。\n\n"
                "正确用法：\n"
                "/跑步 5\n"
                "/跑步 5.2\n"
                "/跑步 10.5"
            )

            return

        # ---------------------------------------------------------
        # 检查距离
        # ---------------------------------------------------------

        if distance_value <= 0:

            yield event.plain_result(
                "❌ 跑步距离必须大于 0 km。"
            )

            return

        if distance_value > 200:

            yield event.plain_result(
                "❌ 单次跑步距离不能超过 200 km。"
            )

            return

        (
            user_id,
            user_name,
            group_id
        ) = self.get_user_info(
            event
        )

        key = (
            f"{group_id}:"
            f"{user_id}"
        )

        # ---------------------------------------------------------
        # 创建等待任务
        # ---------------------------------------------------------

        self.pending_images[key] = {

            "type": "running_proof",

            "distance": distance_value,

            "expire_time": (
                datetime.now()
                + timedelta(
                    seconds=self.image_wait_seconds
                )
            )
        }

        yield event.plain_result(
            f"🏃 收到你的 {distance_value:.2f} km 跑步记录。\n\n"
            f"📸 请在 5 分钟内发送一张跑步证明图片。\n\n"
            f"⚠️ 收到图片后才会正式计入排行榜。\n"
            f"没有图片则不会记录本次跑步。"
        )

    # =============================================================
    # /我的里程
    # =============================================================

    async def my_distance(
        self,
        event: AstrMessageEvent
    ):

        (
            user_id,
            user_name,
            group_id
        ) = self.get_user_info(
            event
        )

        today_start = (
            self.get_today_start()
        )

        week_start = (
            self.get_week_start()
        )

        month_start = (
            self.get_month_start()
        )

        conn = self.get_conn()

        cursor = conn.cursor()

        # ---------------------------------------------------------
        # 查询函数
        # ---------------------------------------------------------

        def query_total(
            start_time: Optional[datetime] = None
        ):

            if start_time:

                cursor.execute(
                    """
                    SELECT
                        COALESCE(
                            SUM(distance),
                            0
                        )

                    FROM running_records

                    WHERE
                        user_id = ?
                        AND group_id = ?
                        AND run_time >= ?
                    """,
                    (
                        user_id,
                        group_id,
                        start_time.isoformat()
                    )
                )

            else:

                cursor.execute(
                    """
                    SELECT
                        COALESCE(
                            SUM(distance),
                            0
                        )

                    FROM running_records

                    WHERE
                        user_id = ?
                        AND group_id = ?
                    """,
                    (
                        user_id,
                        group_id
                    )
                )

            result = cursor.fetchone()

            return (
                result[0]
                if result
                else 0
            )

        today_total = query_total(
            today_start
        )

        week_total = query_total(
            week_start
        )

        month_total = query_total(
            month_start
        )

        total = query_total()

        conn.close()

        yield event.plain_result(
            f"🏃 {user_name} 的跑步数据\n\n"
            f"📅 今日：{today_total:.2f} km\n"
            f"📆 本周：{week_total:.2f} km\n"
            f"🗓️ 本月：{month_total:.2f} km\n"
            f"👑 总里程：{total:.2f} km"
        )

        pass

    # =============================================================
    # /跑量接龙帮助
    # =============================================================

    async def running_help(
        self,
        event: AstrMessageEvent
    ):

        user_id = self.get_user_id(event)
        group_id = self.get_group_id(event)
        message = self.build_unified_help_message(user_id, group_id)

        yield event.plain_result(
            message
        )

    # =============================================================
    # 查询排行榜
    # =============================================================

    def query_ranking(
        self,
        group_id: str,
        ranking_type: str
    ):

        conn = self.get_conn()

        cursor = conn.cursor()

        # ---------------------------------------------------------
        # 时间范围
        # ---------------------------------------------------------

        if ranking_type == "today":

            start_time = (
                self.get_today_start()
            )

        elif ranking_type == "week":

            start_time = (
                self.get_week_start()
            )

        elif ranking_type == "month":

            start_time = (
                self.get_month_start()
            )

        elif ranking_type == "total":

            start_time = None

        else:

            conn.close()

            return []

        # ---------------------------------------------------------
        # 查询
        # ---------------------------------------------------------

        if start_time:

            cursor.execute(
                """
                SELECT
                    r.user_id,
                    (
                        SELECT r2.user_name
                        FROM running_records r2
                        WHERE r2.group_id = ?
                          AND r2.user_id = r.user_id
                          AND r2.run_time >= ?
                        ORDER BY r2.run_time DESC, r2.id DESC
                        LIMIT 1
                    ) AS user_name,
                    SUM(r.distance) AS total_distance,
                    MIN(r.run_time) AS first_run_time

                FROM running_records r

                WHERE
                    r.group_id = ?
                    AND r.run_time >= ?

                GROUP BY r.user_id

                ORDER BY
                    total_distance DESC,
                    first_run_time ASC
                """,
                (
                    group_id,
                    start_time.isoformat(),
                    group_id,
                    start_time.isoformat()
                )
            )

        else:

            cursor.execute(
                """
                SELECT
                    r.user_id,
                    (
                        SELECT r2.user_name
                        FROM running_records r2
                        WHERE r2.group_id = ?
                          AND r2.user_id = r.user_id
                        ORDER BY r2.run_time DESC, r2.id DESC
                        LIMIT 1
                    ) AS user_name,
                    SUM(r.distance) AS total_distance,
                    MIN(r.run_time) AS first_run_time

                FROM running_records r

                WHERE
                    r.group_id = ?

                GROUP BY r.user_id

                ORDER BY
                    total_distance DESC,
                    first_run_time ASC
                """,
                (
                    group_id,
                    group_id,
                )
            )

        ranking = cursor.fetchall()

        conn.close()

        return ranking

    # =============================================================
    # 获取榜首自定义图片
    # =============================================================

    # def get_custom_leader_image(
    #     self,
    #     user_id: str,
    #     group_id: str
    # ):

    #     conn = self.get_conn()

    #     cursor = conn.cursor()

    #     cursor.execute(
    #         """
    #         SELECT
    #             image_path

    #         FROM leader_images

    #         WHERE
    #             user_id = ?
    #             AND group_id = ?
    #         """,
    #         (
    #             user_id,
    #             group_id
    #         )
    #     )

    #     result = cursor.fetchone()

    #     conn.close()

    #     if not result:

    #         return None

    #     image_path = result[0]

    #     if not image_path:

    #         return None

    #     if not os.path.exists(
    #         image_path
    #     ):

    #         return None

    #     return image_path

    # # =============================================================
    # # 下载 QQ 头像
    # #
    # # QQ头像接口失败不会影响排行榜
    # # =============================================================

    # async def download_qq_avatar(
    #     self,
    #     user_id: str
    # ):

    #     avatar_path = os.path.join(
    #         self.cards_dir,
    #         f"avatar_{user_id}.jpg"
    #     )

    #     # ---------------------------------------------------------
    #     # 临时原图
    #     # ---------------------------------------------------------

    #     temp_avatar_path = os.path.join(
    #         self.cards_dir,
    #         f"avatar_{user_id}_original"
    #     )

    #     # ---------------------------------------------------------
    #     # 多个 QQ 头像地址
    #     # ---------------------------------------------------------

    #     avatar_urls = [

    #         (
    #             "https://q1.qlogo.cn/g"
    #             f"?b=qq&nk={user_id}&s=640"
    #         ),

    #         (
    #             "https://q2.qlogo.cn/g"
    #             f"?b=qq&nk={user_id}&s=640"
    #         ),

    #         (
    #             "https://q3.qlogo.cn/g"
    #             f"?b=qq&nk={user_id}&s=640"
    #         ),

    #         (
    #             "https://thirdqq.qlogo.cn/g"
    #             f"?b=qq&nk={user_id}&s=640"
    #         ),

    #     ]

    #     for avatar_url in avatar_urls:

    #         try:

    #             logger.info(
    #                 "[RunningRank] "
    #                 f"尝试获取 QQ 头像：{avatar_url}"
    #             )

    #             request = urllib.request.Request(
    #                 avatar_url,
    #                 headers={
    #                     "User-Agent":
    #                         "Mozilla/5.0 "
    #                         "(X11; Linux x86_64) "
    #                         "AppleWebKit/537.36 "
    #                         "Chrome/120 Safari/537.36",

    #                     "Accept":
    #                         "image/avif,image/webp,"
    #                         "image/apng,image/svg+xml,"
    #                         "image/*,*/*;q=0.8",
    #                 }
    #             )

    #             with urllib.request.urlopen(
    #                 request,
    #                 timeout=10
    #             ) as response:

    #                 data = response.read()

    #             # -------------------------------------------------
    #             # 检查数据
    #             # -------------------------------------------------

    #             if not data:

    #                 continue

    #             if len(data) < 100:

    #                 logger.warning(
    #                     "[RunningRank] "
    #                     f"QQ头像返回数据过小："
    #                     f"{len(data)} bytes"
    #                 )

    #                 continue

    #             # -------------------------------------------------
    #             # 保存临时原图
    #             # -------------------------------------------------

    #             with open(
    #                 temp_avatar_path,
    #                 "wb"
    #             ) as f:

    #                 f.write(data)

    #             # -------------------------------------------------
    #             # Pillow 压缩
    #             # -------------------------------------------------

    #             success = self.compress_image(
    #                 temp_avatar_path,
    #                 avatar_path
    #             )

    #             # -------------------------------------------------
    #             # 压缩失败
    #             #
    #             # 使用原图
    #             # -------------------------------------------------

    #             if not success:

    #                 shutil.copy2(
    #                     temp_avatar_path,
    #                     avatar_path
    #                 )

    #             # -------------------------------------------------
    #             # 删除临时文件
    #             # -------------------------------------------------

    #             try:

    #                 os.remove(
    #                     temp_avatar_path
    #                 )

    #             except Exception:

    #                 pass

    #             logger.info(
    #                 "[RunningRank] "
    #                 f"QQ头像获取成功：{user_id}"
    #             )

    #             return avatar_path

    #         except Exception as e:

    #             logger.warning(
    #                 "[RunningRank] "
    #                 f"QQ头像接口失败 "
    #                 f"{user_id}: {e}"
    #             )

    #             # -------------------------------------------------
    #             # 清理临时文件
    #             # -------------------------------------------------

    #             try:

    #                 if os.path.exists(
    #                     temp_avatar_path
    #                 ):

    #                     os.remove(
    #                         temp_avatar_path
    #                     )

    #             except Exception:

    #                 pass

    #     logger.warning(
    #         "[RunningRank] "
    #         f"所有 QQ头像接口均失败：{user_id}"
    #     )

    #     return None

    # # =============================================================
    # # 获取榜首图片
    # #
    # # 自定义图片 > QQ头像
    # # =============================================================

    # async def get_leader_image(
    #     self,
    #     user_id: str,
    #     group_id: str
    # ):

    #     custom_image = (
    #         self.get_custom_leader_image(
    #             user_id,
    #             group_id
    #         )
    #     )

    #     if custom_image:

    #         return custom_image

    #     return await self.download_qq_avatar(
    #         user_id
    #     )

    # # =============================================================
    # # 图片转 Data URI
    # # =============================================================

    # def get_image_data_uri(
    #     self,
    #     image_path: str
    # ):

    #     if not image_path:

    #         return None

    #     if not os.path.exists(
    #         image_path
    #     ):

    #         return None

    #     try:

    #         mime_type, _ = (
    #             mimetypes.guess_type(
    #                 image_path
    #             )
    #         )

    #         if not mime_type:

    #             mime_type = "image/jpeg"

    #         with open(
    #             image_path,
    #             "rb"
    #         ) as f:

    #             encoded = (
    #                 base64.b64encode(
    #                     f.read()
    #                 ).decode(
    #                     "utf-8"
    #                 )
    #             )

    #         return (
    #             f"data:{mime_type};base64,"
    #             f"{encoded}"
    #         )

    #     except Exception as e:

    #         logger.error(
    #             "[RunningRank] "
    #             f"读取图片失败: {e}"
    #         )

    #         return None

    # # =============================================================
    # # HTML 转义
    # # =============================================================

    # def escape_html(
    #     self,
    #     text
    # ):

    #     text = str(text)

    #     return (
    #         text
    #         .replace(
    #             "&",
    #             "&amp;"
    #         )
    #         .replace(
    #             "<",
    #             "&lt;"
    #         )
    #         .replace(
    #             ">",
    #             "&gt;"
    #         )
    #         .replace(
    #             '"',
    #             "&quot;"
    #         )
    #         .replace(
    #             "'",
    #             "&#39;"
    #         )
    #     )

#     # =============================================================
#     # 生成排行榜卡片
#     # =============================================================

#     async def generate_ranking_card(
#         self,
#         ranking_type: str,
#         ranking,
#         group_id: str
#     ):

#         if not ranking:

#             return None

#         now = datetime.now()

#         # ---------------------------------------------------------
#         # 标题
#         # ---------------------------------------------------------

#         if ranking_type == "today":

#             title = "今日跑步排行榜"

#             date_text = (
#                 now.strftime(
#                     "%Y年%m月%d日"
#                 )
#             )

#         elif ranking_type == "week":

#             title = "本周跑步排行榜"

#             start = (
#                 self.get_week_start()
#             )

#             end = (
#                 start
#                 + timedelta(days=6)
#             )

#             date_text = (
#                 f"{start.strftime('%m月%d日')}"
#                 f" — "
#                 f"{end.strftime('%m月%d日')}"
#             )

#         elif ranking_type == "month":

#             title = "本月跑步排行榜"

#             start = (
#                 self.get_month_start()
#             )

#             date_text = (
#                 start.strftime(
#                     "%Y年%m月"
#                 )
#             )

#         else:

#             title = "跑团历史总榜"

#             date_text = "累计跑量"

#         # ---------------------------------------------------------
#         # 榜首
#         # ---------------------------------------------------------

#         leader_id = ranking[0][0]

#         leader_name = (
#             ranking[0][1]
#         )

#         leader_distance = (
#             ranking[0][2]
#         )

#         leader_image_path = (
#             await self.get_leader_image(
#                 leader_id,
#                 group_id
#             )
#         )

#         leader_image_uri = None

#         if leader_image_path:

#             leader_image_uri = (
#                 self.get_image_data_uri(
#                     leader_image_path
#                 )
#             )

#         # ---------------------------------------------------------
#         # 默认占位图
#         # ---------------------------------------------------------

#         if not leader_image_uri:

#             placeholder_svg = """
#             <svg
#                 xmlns="http://www.w3.org/2000/svg"
#                 width="900"
#                 height="600">

#                 <rect
#                     width="100%"
#                     height="100%"
#                     fill="#eeeeee"/>

#                 <text
#                     x="50%"
#                     y="50%"
#                     text-anchor="middle"
#                     dominant-baseline="middle"
#                     font-size="120">
#                     🏃
#                 </text>

#             </svg>
#             """

#             leader_image_uri = (
#                 "data:image/svg+xml;base64,"
#                 + base64.b64encode(
#                     placeholder_svg.encode(
#                         "utf-8"
#                     )
#                 ).decode(
#                     "utf-8"
#                 )
#             )

#         # ---------------------------------------------------------
#         # 排名列表
#         # ---------------------------------------------------------

#         ranking_html = ""

#         for index, row in enumerate(
#             ranking,
#             start=1
#         ):

#             user_name = (
#                 self.escape_html(
#                     row[1]
#                 )
#             )

#             distance = float(
#                 row[2]
#             )

#             # -----------------------------------------------------
#             # 排名
#             # -----------------------------------------------------

#             if index == 1:

#                 rank = "🥇"

#                 rank_class = "top-rank"

#             elif index == 2:

#                 rank = "🥈"

#                 rank_class = "top-rank"

#             elif index == 3:

#                 rank = "🥉"

#                 rank_class = "top-rank"

#             else:

#                 rank = str(index)

#                 rank_class = "normal-rank"

#             # -----------------------------------------------------
#             # HTML
#             # -----------------------------------------------------

#             ranking_html += f"""
#             <div class="rank-row">

#                 <div class="rank-number {rank_class}">
#                     {rank}
#                 </div>

#                 <div class="rank-name">
#                     {user_name}
#                 </div>

#                 <div class="rank-distance">
#                     {distance:.2f} km
#                 </div>

#             </div>
#             """

#         # ---------------------------------------------------------
#         # 总跑量
#         # ---------------------------------------------------------

#         total_distance = sum(
#             float(row[2])
#             for row in ranking
#         )

#         # =========================================================
#         # HTML
#         # =========================================================

#         html = f"""
# <!DOCTYPE html>

# <html>

# <head>

# <meta charset="UTF-8">

# <style>

# * {{
#     box-sizing: border-box;
# }}

# body {{

#     margin: 0;

#     padding: 0;

#     background: #f3f4f6;

#     font-family:
#         "Noto Sans CJK SC",
#         "Noto Sans SC",
#         "Microsoft YaHei",
#         Arial,
#         sans-serif;

# }}

# .card {{

#     width: 900px;

#     background: white;

#     border-radius: 32px;

#     overflow: hidden;

#     padding-bottom: 50px;

#     box-shadow:
#         0 10px 40px
#         rgba(0, 0, 0, 0.08);

# }}

# .header {{

#     padding:
#         55px
#         60px
#         35px;

#     text-align: center;

# }}

# .club-title {{

#     font-size: 30px;

#     font-weight: 700;

#     color: #666;

#     margin-bottom: 15px;

# }}

# .title {{

#     font-size: 48px;

#     font-weight: 800;

#     color: #222;

# }}

# .date {{

#     font-size: 25px;

#     color: #999;

#     margin-top: 15px;

# }}

# .leader-section {{

#     margin:
#         20px
#         60px
#         45px;

#     background:
#         linear-gradient(
#             135deg,
#             #fff7d6,
#             #fff
#         );

#     border-radius: 30px;

#     padding:
#         35px
#         35px
#         45px;

#     text-align: center;

# }}

# .leader-crown {{

#     font-size: 50px;

#     margin-bottom: 20px;

# }}

# /*
#  * 榜首展示图片
#  *
#  * 不再是头像小圆图。
#  *
#  * 直接使用大图。
#  */

# .leader-image {{

#     display: block;

#     width: 100%;

#     max-width: 760px;

#     max-height: 650px;

#     margin: 0 auto;

#     border-radius: 24px;

#     object-fit: contain;

#     background: #f5f5f5;

#     border:
#         6px solid white;

#     box-shadow:
#         0 10px 35px
#         rgba(0,0,0,0.15);

# }}

# .leader-name {{

#     margin-top: 25px;

#     font-size: 42px;

#     font-weight: 800;

#     color: #222;

# }}

# .leader-distance {{

#     margin-top: 10px;

#     font-size: 34px;

#     font-weight: 700;

#     color: #d97706;

# }}

# .ranking {{

#     margin:
#         0
#         60px;

# }}

# .rank-row {{

#     display: flex;

#     align-items: center;

#     min-height: 76px;

#     border-bottom:
#         1px solid #eeeeee;

#     padding:
#         0
#         15px;

# }}

# .rank-number {{

#     width: 85px;

#     text-align: center;

#     font-size: 25px;

#     color: #888;

# }}

# .top-rank {{

#     font-size: 34px;

# }}

# .rank-name {{

#     flex: 1;

#     font-size: 28px;

#     font-weight: 600;

#     color: #333;

#     white-space: nowrap;

#     overflow: hidden;

#     text-overflow: ellipsis;

# }}

# .rank-distance {{

#     width: 180px;

#     text-align: right;

#     font-size: 27px;

#     font-weight: 600;

#     color: #444;

# }}

# .footer {{

#     margin:
#         45px
#         60px
#         0;

#     padding-top: 35px;

#     border-top:
#         2px solid #eeeeee;

#     display: flex;

#     justify-content:
#         space-between;

#     font-size: 24px;

#     color: #777;

# }}

# </style>

# </head>

# <body>

# <div class="card">

#     <!-- ===================================================== -->
#     <!-- 标题 -->
#     <!-- ===================================================== -->

#     <div class="header">

#         <div class="club-title">
#             🏃 南科跑团
#         </div>

#         <div class="title">
#             {title}
#         </div>

#         <div class="date">
#             {date_text}
#         </div>

#     </div>


#     <!-- ===================================================== -->
#     <!-- 榜首 -->
#     <!-- ===================================================== -->

#     <div class="leader-section">

#         <div class="leader-crown">
#             👑
#         </div>

#         <img
#             class="leader-image"
#             src="{leader_image_uri}"
#         >

#         <div class="leader-name">
#             {self.escape_html(leader_name)}
#         </div>

#         <div class="leader-distance">
#             {leader_distance:.2f} km
#         </div>

#     </div>


#     <!-- ===================================================== -->
#     <!-- 全部排名 -->
#     <!-- ===================================================== -->

#     <div class="ranking">

#         {ranking_html}

#     </div>


#     <!-- ===================================================== -->
#     <!-- 底部 -->
#     <!-- ===================================================== -->

#     <div class="footer">

#         <div>
#             👥 {len(ranking)} 人参与
#         </div>

#         <div>
#             🏃 {total_distance:.2f} km
#         </div>

#     </div>

# </div>

# </body>

# </html>
# """

#         # ---------------------------------------------------------
#         # HTML → 图片
#         # ---------------------------------------------------------

#         try:

#             image_url = await self.html_render(
#                 tmpl=html,
#                 data={},
#                 return_url=True,
#                 options={
#                     "type": "png",
#                     "full_page": True,
#                     "animations": "disabled",
#                 }
#             )

#             logger.info(
#                 "[RunningRank] "
#                 f"排行榜卡片生成成功: {image_url}"
#             )

#             return image_url

#         except Exception as e:

#             logger.exception(
#                 "[RunningRank] "
#                 "生成排行榜卡片失败"
#             )

#             return None

    # =============================================================
    # 显示排行榜
    # =============================================================

    def get_ranking_text(
        self,
        event: AstrMessageEvent,
        ranking_type: str
    ) -> str:

        (
            current_user_id,
            current_user_name,
            group_id
        ) = self.get_user_info(event)

        ranking = self.query_ranking(
            group_id,
            ranking_type
        )

        # 没有记录
        if not ranking:

            return (
                "🏃 目前还没有人完成跑步打榜。\n\n"
                "使用：\n"
                "/跑步 5\n"
                "然后发送跑步证明图片。"
            )

        # 标题
        if ranking_type == "today":

            title = "📅 今日跑步排行榜"

        elif ranking_type == "week":

            title = "🏆 本周跑步排行榜"

        elif ranking_type == "month":

            title = "🗓️ 本月跑步排行榜"

        else:

            title = "👑 总跑步排行榜"

        lines = [title]
        lines.append("")
        lines.append("榜  群昵称  跑量")

        total_distance = 0.0

        for index, row in enumerate(
            ranking,
            start=1
        ):

            distance = float(row[2])
            total_distance += distance

            if index == 1:

                rank = "🥇"

            elif index == 2:

                rank = "🥈"

            elif index == 3:

                rank = "🥉"

            else:

                rank = str(index)

            lines.append(
                f"{rank:<2}  {row[1]}  {distance:.2f} km"
            )

        lines.append("")
        lines.append(
            f"累计：{total_distance:.2f} km"
        )

        return "\n".join(lines)

    async def show_ranking(
        self,
        event: AstrMessageEvent,
        ranking_type: str
    ):

        ranking_text = self.get_ranking_text(
            event,
            ranking_type
        )

        yield event.plain_result(
            ranking_text
        )

    # =============================================================
    # /今日榜
    # =============================================================

    async def today_rank(
        self,
        event: AstrMessageEvent
    ):

        async for result in self.show_ranking(
            event,
            "today"
        ):

            yield result

    # =============================================================
    # /日榜
    # =============================================================

    async def day_rank(
        self,
        event: AstrMessageEvent
    ):

        async for result in self.show_ranking(
            event,
            "today"
        ):

            yield result

    # =============================================================
    # /周榜
    # =============================================================

    async def week_rank(
        self,
        event: AstrMessageEvent
    ):

        async for result in self.show_ranking(
            event,
            "week"
        ):

            yield result

    # =============================================================
    # /月榜
    # =============================================================

    async def month_rank(
        self,
        event: AstrMessageEvent
    ):

        async for result in self.show_ranking(
            event,
            "month"
        ):

            yield result

    # =============================================================
    # /总榜
    # =============================================================

    async def total_rank(
        self,
        event: AstrMessageEvent
    ):

        async for result in self.show_ranking(
            event,
            "total"
        ):

            yield result

    # =============================================================
    # /撤销跑步
    # =============================================================

    async def undo_self_running(
        self,
        event: AstrMessageEvent
    ):

        (
            user_id,
            user_name,
            group_id
        ) = self.get_user_info(
            event
        )

        conn = self.get_conn()

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                id,
                distance,
                run_time,
                created_at,
                proof_path

            FROM running_records

            WHERE
                user_id = ?
                AND group_id = ?

            ORDER BY
                run_time DESC

            LIMIT 1
            """,
            (
                user_id,
                group_id
            )
        )

        record = cursor.fetchone()

        if not record:

            conn.close()

            yield event.plain_result(
                "❌ 你还没有跑步记录。"
            )

            return

        record_id = record[0]

        distance = record[1]

        created_at = record[3]

        proof_path = record[4]

        # ---------------------------------------------------------
        # 删除记录
        # ---------------------------------------------------------

        cursor.execute(
            """
            DELETE FROM running_records

            WHERE id = ?
            """,
            (
                record_id,
            )
        )

        conn.commit()

        newbie_conn = self.get_newbie_conn()
        newbie_cursor = newbie_conn.cursor()
        newbie_cursor.execute(
            """
            DELETE FROM newbie_running_records
            WHERE group_id = ?
              AND user_id = ?
              AND distance = ?
              AND created_at = ?
            """,
            (
            group_id,
            user_id,
            float(distance),
            created_at,
            )
        )
        newbie_conn.commit()
        newbie_conn.close()

        conn.close()

        # ---------------------------------------------------------
        # 删除证据
        # ---------------------------------------------------------

        if proof_path:

            try:

                if os.path.exists(
                    proof_path
                ):

                    os.remove(
                        proof_path
                    )

            except Exception as e:

                logger.warning(
                    "[RunningRank] "
                    f"删除跑步证明失败: {e}"
                )

        yield event.plain_result(
            f"🗑️ 已撤销最近一次跑步记录。\n\n"
            f"删除里程：{float(distance):.2f} km"
        )

    # =============================================================
    # /榜首预备
    # =============================================================

    async def prepare_leader_image(
        self,
        event: AstrMessageEvent
    ):

        (
            user_id,
            user_name,
            group_id
        ) = self.get_user_info(
            event
        )

        key = (
            f"{group_id}:"
            f"{user_id}"
        )

        self.pending_images[key] = {

            "type": "leader_image",

            "expire_time": (
                datetime.now()
                + timedelta(
                    seconds=self.image_wait_seconds
                )
            )
        }

        yield event.plain_result(
            "👑 榜首预备！\n\n"
            "请在 5 分钟内发送一张图片。\n\n"
            "这张图片会成为你的榜首展示图。\n"
            "以后当你成为日榜、周榜、月榜或总榜第一名时，"
            "排行榜卡片会展示它。\n\n"
            "如果没有设置图片，则自动使用你的 QQ 头像。\n\n"
            "再次使用 /榜首预备可以更换。"
        )

    # =============================================================
    # /取消榜首预备
    # =============================================================

    async def cancel_leader_image(
        self,
        event: AstrMessageEvent
    ):

        (
            user_id,
            user_name,
            group_id
        ) = self.get_user_info(
            event
        )

        key = (
            f"{group_id}:"
            f"{user_id}"
        )

        # ---------------------------------------------------------
        # 取消等待
        # ---------------------------------------------------------

        if key in self.pending_images:

            del self.pending_images[key]

        # ---------------------------------------------------------
        # 查询图片
        # ---------------------------------------------------------

        conn = self.get_conn()

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                image_path

            FROM leader_images

            WHERE
                user_id = ?
                AND group_id = ?
            """,
            (
                user_id,
                group_id
            )
        )

        result = cursor.fetchone()

        # ---------------------------------------------------------
        # 删除数据库
        # ---------------------------------------------------------

        cursor.execute(
            """
            DELETE FROM leader_images

            WHERE
                user_id = ?
                AND group_id = ?
            """,
            (
                user_id,
                group_id
            )
        )

        conn.commit()

        conn.close()

        # ---------------------------------------------------------
        # 删除图片
        # ---------------------------------------------------------

        if result:

            path = result[0]

            if path:

                try:

                    if os.path.exists(
                        path
                    ):

                        os.remove(
                            path
                        )

                except Exception:

                    pass

        yield event.plain_result(
            "🗑️ 已删除你的榜首展示图。\n\n"
            "以后如果你成为榜首，将自动使用 QQ 头像。"
        )

    # =============================================================
    # 统一图片监听
    #
    # /跑步 5.2
    #       ↓
    # 等待图片
    #       ↓
    # 图片
    #       ↓
    # 正式记录
    #
    # /榜首预备
    #       ↓
    # 等待图片
    #       ↓
    # 图片
    #       ↓
    # 保存榜首图片
    # =============================================================

    @filter.event_message_type(
        filter.EventMessageType.ALL
    )
    async def handle_pending_image(
        self,
        event: AstrMessageEvent
    ):

        (
            user_id,
            user_name,
            group_id
        ) = self.get_user_info(
            event
        )

        key = (
            f"{group_id}:"
            f"{user_id}"
        )

        # ---------------------------------------------------------
        # 没有等待任务
        # ---------------------------------------------------------

        if key not in self.pending_images:

            return

        pending = (
            self.pending_images[key]
        )

        # ---------------------------------------------------------
        # 超时
        # ---------------------------------------------------------

        if (
            datetime.now()
            > pending["expire_time"]
        ):

            del self.pending_images[key]

            yield event.plain_result(
                "⌛ 图片上传已超时。\n\n"
                "请重新发送对应命令。"
            )

            return

        # ---------------------------------------------------------
        # 获取消息对象
        # ---------------------------------------------------------

        message_obj = (
            event.message_obj
        )

        if message_obj is None:

            return

        message_chain = getattr(
            message_obj,
            "message",
            None
        )

        if not message_chain:

            return

        # ---------------------------------------------------------
        # 查找图片
        # ---------------------------------------------------------

        image_component = None

        for component in message_chain:

            if isinstance(
                component,
                Comp.Image
            ):

                image_component = component

                break

        # ---------------------------------------------------------
        # 不是图片
        # ---------------------------------------------------------

        if image_component is None:

            return

        # ---------------------------------------------------------
        # 获取本地图片
        # ---------------------------------------------------------

        try:

            source_path = (
                await image_component
                .convert_to_file_path()
            )

        except Exception as e:

            logger.error(
                "[RunningRank] "
                f"获取图片文件失败: {e}"
            )

            yield event.plain_result(
                "❌ 图片读取失败，请重新发送图片。"
            )

            return

        if not source_path:

            yield event.plain_result(
                "❌ 没有获取到图片文件，请重新发送图片。"
            )

            return

        if not os.path.exists(
            source_path
        ):

            yield event.plain_result(
                "❌ 图片文件不存在，请重新发送图片。"
            )

            return

        # =========================================================
        # 跑步证明
        # =========================================================

        if pending["type"] == "running_proof":

            distance = float(
                pending["distance"]
            )

            timestamp = (
                datetime.now()
                .strftime(
                    "%Y%m%d_%H%M%S_%f"
                )
            )

            # -----------------------------------------------------
            # 统一保存为 JPG
            # -----------------------------------------------------

            proof_filename = (
                f"{group_id}_"
                f"{user_id}_"
                f"{timestamp}.jpg"
            )

            proof_path = os.path.join(
                self.proofs_dir,
                proof_filename
            )

            # -----------------------------------------------------
            # Pillow 压缩保存
            # -----------------------------------------------------

            try:

                success = self.compress_image(
                    source_path,
                    proof_path
                )

                # -------------------------------------------------
                # 压缩失败
                #
                # 直接复制原图
                # -------------------------------------------------

                if not success:

                    shutil.copy2(
                        source_path,
                        proof_path
                    )

            except Exception as e:

                logger.error(
                    "[RunningRank] "
                    f"保存跑步证明失败: {e}"
                )

                yield event.plain_result(
                    "❌ 跑步证明保存失败。\n"
                    "本次跑步没有记录，请重新发送图片。"
                )

                return

            # -----------------------------------------------------
            # 正式写入数据库
            # -----------------------------------------------------

            now = datetime.now()

            conn = self.get_conn()

            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO running_records (

                    user_id,
                    user_name,
                    group_id,
                    distance,
                    run_time,
                    created_at,
                    proof_path

                )

                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    user_name,
                    group_id,
                    distance,
                    now.isoformat(),
                    now.isoformat(),
                    proof_path
                )
            )

            conn.commit()

            conn.close()

            # -----------------------------------------------------
            # 清除等待
            # -----------------------------------------------------

            del self.pending_images[key]

            # -----------------------------------------------------
            # 查询本周排名
            # -----------------------------------------------------

            ranking = self.query_ranking(
                group_id,
                "week"
            )

            rank = 0

            week_total = 0

            for index, row in enumerate(
                ranking,
                start=1
            ):

                if str(row[0]) == str(
                    user_id
                ):

                    rank = index

                    week_total = float(
                        row[2]
                    )

                    break


            ranking_text = self.get_ranking_text(
                event,
                "week"
            )

            yield event.plain_result(
                f"✅ 跑步记录成功！\n\n"
                f"🏃 本次：{distance:.2f} km\n"
                f"📆 本周累计：{week_total:.2f} km\n"
                f"🏆 当前周榜：第 {rank} 名\n\n"
                f"📸 跑步证明已保存。"
                f"\n━━━━━━━━━━\n"
                f"{ranking_text}"
            )

            # =====================================================
            # 将当前图片交给 Agent / LLM
            # =====================================================

            try:

                # 获取当前会话使用的模型
                provider = self.context.get_using_provider(
                    event.unified_msg_origin
                )

                if provider is None:

                    logger.warning(
                        "[RunningRank] 未找到当前会话的 LLM Provider"
                    )

                    return

                # 获取 Provider ID
                chat_provider_id = provider.meta().id

                logger.info(
                    "[RunningRank] "
                    f"准备将跑步证明图片发送给 LLM: "
                    f"{chat_provider_id}"
                )

                # 调用多模态模型
                response = await self.context.llm_generate(
                    chat_provider_id=chat_provider_id,
                    prompt="你是一个爱水群爱吐槽的跑团组织者，跑团同学发送了一张图片，请看看这张图片是否是跑步证明？如果不是，就谴责他乱发图片，简单吐槽一下图片内容；如果是，就简单分析一下图片内容，找到简单夸奖或者聊聊图片的配速和地图或者其他图片信息。回复30个字以内，禁止使用markdown语法，使回复在一个QQ消息气泡中显得自然",
                    image_urls=[
                        source_path
                    ]
                )

                # 输出模型回复
                if response and response.completion_text:

                    yield event.plain_result(
                        response.completion_text
                    )

            except Exception as e:

                logger.error(
                    "[RunningRank] "
                    f"调用 LLM 分析跑步图片失败: {e}"
                )

            return

        # =========================================================
        # 榜首图片
        # =========================================================

        if pending["type"] == "leader_image":

            # -----------------------------------------------------
            # 固定保存成 JPG
            # -----------------------------------------------------

            filename = (
                f"{group_id}_"
                f"{user_id}.jpg"
            )

            save_path = os.path.join(
                self.users_image_dir,
                filename
            )

            # -----------------------------------------------------
            # 删除旧图片
            #
            # 兼容 jpg/png/webp/gif 等旧文件
            # -----------------------------------------------------

            prefix = (
                f"{group_id}_"
                f"{user_id}."
            )

            try:

                for old_filename in os.listdir(
                    self.users_image_dir
                ):

                    if old_filename.startswith(
                        prefix
                    ):

                        old_path = os.path.join(
                            self.users_image_dir,
                            old_filename
                        )

                        try:

                            os.remove(
                                old_path
                            )

                        except Exception:

                            pass

            except Exception:

                pass

            # -----------------------------------------------------
            # Pillow 压缩
            # -----------------------------------------------------

            try:

                success = self.compress_image(
                    source_path,
                    save_path
                )

                if not success:

                    shutil.copy2(
                        source_path,
                        save_path
                    )

            except Exception as e:

                logger.error(
                    "[RunningRank] "
                    f"保存榜首图片失败: {e}"
                )

                yield event.plain_result(
                    "❌ 榜首图片保存失败，请重新发送。"
                )

                return

            # -----------------------------------------------------
            # 写入数据库
            # -----------------------------------------------------

            conn = self.get_conn()

            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO leader_images (

                    user_id,
                    group_id,
                    image_path,
                    updated_at

                )

                VALUES (?, ?, ?, ?)

                ON CONFLICT (
                    user_id,
                    group_id
                )

                DO UPDATE SET

                    image_path =
                        excluded.image_path,

                    updated_at =
                        excluded.updated_at
                """,
                (
                    user_id,
                    group_id,
                    save_path,
                    datetime.now().isoformat()
                )
            )

            conn.commit()

            conn.close()

            # -----------------------------------------------------
            # 清除等待
            # -----------------------------------------------------

            del self.pending_images[key]

            yield event.plain_result(
                "👑 榜首展示图设置成功！\n\n"
                "以后你成为日榜、周榜、月榜或总榜第一时，"
                "排行榜卡片会自动展示这张图片。\n\n"
                "如果以后想恢复 QQ 头像：\n"
                "/取消榜首预备"
            )

            return

    # =============================================================
    # 插件卸载
    # =============================================================

    async def terminate(self):

        self.pending_images.clear()

        if hasattr(self, "newbie_conn") and self.newbie_conn:
            self.newbie_conn.close()

        logger.info(
            "[RunningRank] "
            "跑步排行榜插件已卸载"
        )
