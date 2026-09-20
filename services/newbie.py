import csv
import io
import re
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
import astrbot.api.message_components as Comp
from astrbot.api.message_components import Node, Plain

from .identity import SUPER_ADMINS


NEWBIE_EXPORT_COLUMNS = [
    ("group_id", "群号"),
    ("nickname", "用户名"),
    ("gender", "性别"),
    ("stage", "阶段"),
    ("week", "阶段周数"),
    ("this_week_runs", "本周跑步次数"),
    ("period_points", "新增跑步积分"),
    ("period_training", "训练次数"),
    ("training_points", "总训练积分"),
    ("total_points", "当前总积分"),
]


class NewbieMixin:
    """新手任务：报名、积分、训练、管理员管理。"""

    def get_running_stage(self, started_at: Optional[str], reference_time: Optional[datetime] = None) -> Tuple[int, int]:
        if not started_at:
            return (0, 0)

        try:
            # started at means the time user started
            start_time = datetime.fromisoformat(started_at)
        except (TypeError, ValueError):
            return (0, 0)

        if reference_time is None:
            reference_time = datetime.now()  # reference time is now

        start_week_start = start_time - timedelta(days=start_time.weekday()) # start_time - start_time

        # 周一开始积分：第 1 阶段就是 4 周；
        # 非周一开始积分：本周剩余天数 + 下四周 一起算第 1 阶段
        if start_time.weekday() == 0:
            stage1_end = start_week_start + timedelta(weeks=4)
        else:
            stage1_end = start_week_start + timedelta(weeks=5)

        days_to_stage2 = (stage1_end - reference_time).days
        if days_to_stage2 > 0:
            return 1, days_to_stage2

        # 第 1 阶段结束后，每 4 周一档升到第 2/3/4 阶段
        elapsed_after_stage1 = (reference_time - stage1_end).days
        if elapsed_after_stage1 < 4 * 7:
            return 2, 4 * 7 - elapsed_after_stage1
        if elapsed_after_stage1 < 8 * 7:
            return 3, 8 * 7 - elapsed_after_stage1
        if elapsed_after_stage1 < 12 * 7:
            return 4, 12 * 7 - elapsed_after_stage1
        return 4, 0

    def get_stage_week(self, started_at: Optional[str], reference_time: Optional[datetime] = None) -> Tuple[int, int]:
        """返回 (阶段, 阶段内周数)。未开始积分时返回 (0, 0)。"""
        if not started_at:
            return (0, 0)

        try:
            start_time = datetime.fromisoformat(started_at)
        except (TypeError, ValueError):
            return (0, 0)

        if reference_time is None:
            reference_time = datetime.now()

        start_week_start = start_time - timedelta(days=start_time.weekday())
        stage1_end = start_week_start + timedelta(weeks=4 if start_time.weekday() == 0 else 5)

        if reference_time < stage1_end:
            stage = 1
            stage_start = start_time
        else:
            elapsed = (reference_time - stage1_end).days
            if elapsed < 4 * 7:
                stage = 2
                stage_start = stage1_end
            elif elapsed < 8 * 7:
                stage = 3
                stage_start = stage1_end + timedelta(weeks=4)
            else:
                stage = 4
                stage_start = stage1_end + timedelta(weeks=8)

        week = (reference_time - stage_start).days // 7 + 1
        return (stage, week)

    def get_running_rule(self, gender: str, started_at: Optional[str], reference_time: Optional[datetime] = None) -> Optional[Tuple[Optional[Dict], int, int]]:
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
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("你已经参加了本学期的新手任务。\n无需重复报名。")]
            )
            yield self.reply_result(event, node)
            conn.close()
            return

        cursor.execute("""
        INSERT INTO newbie_users (
            group_id, semester, user_id, nickname, gender, joined_at, points_started, points_started_at
        ) VALUES (?, ?, ?, ?, ?, ?, 0, NULL)
        """, (group_id, "2026_fall", user_id, nickname, gender, datetime.now().isoformat()))
        conn.commit()
        conn.close()

        node = Node(
            uin=0,
            name="柏柏子",
            content=[Plain(f"🎉 新手任务报名成功！\n\n"
                f"成员：{nickname}\n"
                f"组别：{gender_name}\n"
                f"学期：2026_fall\n\n"
                "请等待管理员使用：\n"
                f"/开始积分 @{nickname}\n\n"
                "管理员开始积分后，你才能使用 /跑步积分 命令。")]
        )
        yield self.reply_result(event, node)

    async def cancel_newbie(self, event: AstrMessageEvent, argument: str = ""):
        group_id = self.get_group_id(event)
        admin_id = self.get_user_id(event)

        if not self.is_admin(group_id, admin_id):
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 只有管理员可以撤销报名。")]
            )
            yield self.reply_result(event, node)
            return

        target_user_id = self.get_target_user_id(event, argument)
        if not target_user_id:
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 请 @ 一位群友或输入 QQ 号。\n\n例如：\n/撤销报名 @张三\n或\n/撤销报名 123456789")]
            )
            yield self.reply_result(event, node)
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
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 该成员尚未报名新手任务，无法撤销报名。")]
            )
            yield self.reply_result(event, node)
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

        node = Node(
            uin=0,
            name="柏柏子",
            content=[Plain(f"✅ 已撤销 {user['nickname'] or target_user_id} 的报名\n\n"
                "该成员的本学期新手任务报名、跑步记录、训练记录和积分已清空。")]
        )
        yield self.reply_result(event, node)

    async def start_points(self, event: AstrMessageEvent, argument: str = ""):
        group_id = self.get_group_id(event)
        admin_id = self.get_user_id(event)

        if not self.is_admin(group_id, admin_id):
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 只有管理员可以开始积分。")]
            )
            yield self.reply_result(event, node)
            return

        target_user_id = self.get_target_user_id(event, argument)
        if not target_user_id:
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 请 @ 一位群友或输入 QQ 号。\n\n例如：\n/开始积分 @张三\n或\n/开始积分 123456789")]
            )
            yield self.reply_result(event, node)
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
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 该成员尚未加入新手任务。\n请先让该成员使用：\n/加入新手任务 男\n或\n/加入新手任务 女")]
            )
            yield self.reply_result(event, node)
            return

        if user["points_started"] == 1:
            conn.close()
            display_name = self.get_display_name(event, user["nickname"])
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain(f"⚠️ {display_name} 已经开始积分，无需重复操作。")]
            )
            yield self.reply_result(event, node)
            return

        cursor.execute("""
        UPDATE newbie_users
        SET points_started = 1, points_started_at = ?
        WHERE group_id = ? AND semester = ? AND user_id = ?
        """, (datetime.now().isoformat(), group_id, "2026_fall", target_user_id))
        conn.commit()
        conn.close()

        display_name = self.get_display_name(event, user["nickname"])

        node = Node(
            uin=0,
            name="柏柏子",
            content=[Plain(f"🎯 {display_name} 开始积分！\n\n"
                f"积分阶段按“开始积分后的周龄”计算：\n"
                f"第1阶段：0-4周\n"
                f"第2阶段：4-8周\n"
                f"第3阶段：8-12周\n"
                f"第4阶段：12-16周\n\n"
                f"从现在开始，该成员可以使用：\n/跑步积分 距离\n\n例如：\n/跑步积分 5km")]
        )
        yield self.reply_result(event, node)

    async def running_points_command(self, event: AstrMessageEvent, distance_text: str):
        group_id = self.get_group_id(event)
        user_id = self.get_user_id(event)

        match = re.search(r"(\d+(?:\.\d+)?)", str(distance_text))
        if not match:
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 距离格式错误。\n\n例如：\n/跑步积分 5km")]
            )
            yield self.reply_result(event, node)
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
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 你还没有加入新手任务。\n\n请先使用：\n/加入新手任务 男\n或\n/加入新手任务 女")]
            )
            yield self.reply_result(event, node)
            return

        if user["points_started"] != 1:
            conn.close()
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("⏳ 你已经报名新手任务，但管理员尚未为你开始积分。\n\n请等待管理员使用：\n/开始积分 @你")]
            )
            yield self.reply_result(event, node)
            return

        now = datetime.now()
        rule, stage, days_to_next_stage = self.get_running_rule(user["gender"], user["points_started_at"], now)
        if rule is None:
            conn.close()
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 该成员尚未开始积分，无法计算当前阶段规则。")]
            )
            yield self.reply_result(event, node)
            return

        required_distance = rule["distance"]
        if distance < required_distance:
            conn.close()
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain(f"⚠️ 你处于新手任务的第{stage}阶段，当前要求每次跑步距离至少 {required_distance}km。如果跑步距离未达到{required_distance}km，请使用 /跑步 命令跑量接龙，本次跑步记录不会计入新手任务。")]
            )
            yield self.reply_result(event, node)
            return

        key = f"{group_id}:{user_id}"
        self.pending_runs[key] = {"distance": distance, "created_at": datetime.now()}
        conn.close()

        node = Node(
            uin=0,
            name="柏柏子",
            content=[Plain(f"你处于新手任务的第{stage}阶段，距离下一阶段还有{days_to_next_stage}天\n"
                f"🏃 已提交 {distance}km 跑步任务。\n\n请在 {self.PHOTO_WAIT_SECONDS // 60} 分钟内上传跑步截图或照片。\n\n⚠️ 只有收到图片凭证后，本次跑步才会正式记录并计算积分。")]
        )
        yield self.reply_result(event, node)

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

        # 证明图片不落盘服务器，只取 AstrBot 自己的临时文件路径供后续 LLM 分析
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
        except Exception as e:
            logger.warning("[RunningRank] 处理新手任务图片证明失败: %s", e)

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
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                user_name,
                group_id,
                distance,
                now.isoformat(),
                now.isoformat(),
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

        if weekly_count < rule["point1"]:
            remaining = rule["point1"] - weekly_count
            progress = f"距离 1 分档还差 {remaining} 次"
        elif weekly_count < rule["point2"]:
            remaining = rule["point2"] - weekly_count
            progress = f"距离 2 分档还差 {remaining} 次"
        else:
            progress = "本周最高积分已完成！"

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
        conn.close()

        return message, source_path

    def recompute_week_points(self, group_id, semester, user_id, reference_time):
        """按 confirm_newbie_running 同款规则，重算某用户某周的跑步积分（供导入使用）。"""
        conn = self.get_newbie_conn()
        cursor = conn.cursor()

        cursor.execute("""
        SELECT gender, points_started_at FROM newbie_users
        WHERE group_id = ? AND semester = ? AND user_id = ?
        """, (group_id, semester, user_id))
        user = cursor.fetchone()
        if not user:
            conn.close()
            return

        rule, stage, _ = self.get_running_rule(user["gender"], user["points_started_at"], reference_time)
        if rule is None:
            conn.close()
            return

        week_start = reference_time - timedelta(days=reference_time.weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        week_end = week_start + timedelta(days=7)

        cursor.execute("""
        SELECT COUNT(*) AS count FROM newbie_running_records
        WHERE group_id = ? AND semester = ? AND user_id = ? AND created_at >= ? AND created_at < ?
        """, (group_id, semester, user_id, week_start.isoformat(), week_end.isoformat()))
        weekly_count = cursor.fetchone()["count"]

        year, week, _ = reference_time.isocalendar()

        if weekly_count >= rule["point2"]:
            new_points = 2
        elif weekly_count >= rule["point1"]:
            new_points = 1
        else:
            new_points = 0

        if new_points > 0:
            cursor.execute("""
            INSERT INTO newbie_running_points (group_id, semester, user_id, year, week, month, points, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(group_id, semester, user_id, year, week)
            DO UPDATE SET points = excluded.points, created_at = excluded.created_at
            """, (group_id, semester, user_id, year, week, reference_time.month, new_points, reference_time.isoformat()))
        else:
            cursor.execute("""
            DELETE FROM newbie_running_points
            WHERE group_id = ? AND semester = ? AND user_id = ? AND year = ? AND week = ?
            """, (group_id, semester, user_id, year, week))

        conn.commit()
        conn.close()

    async def add_training(self, event: AstrMessageEvent, argument: str = ""):
        argument = str(argument or "").strip()
        if argument.startswith("/"):
            argument = argument[1:].lstrip()

        group_id = self.get_group_id(event)
        admin_id = self.get_user_id(event)
        if not self.is_admin(group_id, admin_id):
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 只有管理员可以记录训练。")]
            )
            yield self.reply_result(event, node)
            return

        target_user_id = self.get_target_user_id(event, argument)
        if not target_user_id:
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 请 @ 一位群友或输入 QQ 号。\n\n例如：\n/参加训练 @张三")]
            )
            yield self.reply_result(event, node)
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
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 该成员尚未参加新手任务。")]
            )
            yield self.reply_result(event, node)
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
        conn.close()
        node = Node(
            uin=0,
            name="柏柏子",
            content=[Plain(message)]
        )
        yield self.reply_result(event, node)

    async def set_admin(self, event: AstrMessageEvent, argument: str = ""):
        group_id = self.get_group_id(event)
        user_id = self.get_user_id(event)
        if not self.is_super_admin(user_id):
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 只有超级管理员可以设置管理员。")]
            )
            yield self.reply_result(event, node)
            return

        target_user_id = self.get_target_user_id(event, argument)
        if not target_user_id:
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 请 @ 群友或输入 QQ 号。\n\n例如：\n/管理员设置 123456789")]
            )
            yield self.reply_result(event, node)
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
        node = Node(
            uin=0,
            name="柏柏子",
            content=[Plain(f"🎉 {nickname} 成为管理员\n\n👮 当前管理员：\n{admin_list}")]
        )
        yield self.reply_result(event, node)

    def get_admin_list(self, group_id: str) -> str:
        conn = self.get_newbie_conn()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT user_id FROM newbies_admins WHERE group_id = ? ORDER BY created_at ASC
        """, (group_id,))
        admin_ids = [row["user_id"] for row in cursor.fetchall()]
        conn.close()
        for super_admin in reversed(SUPER_ADMINS):
            if super_admin not in admin_ids:
                admin_ids.insert(0, super_admin)
        lines = []
        for index, admin_id in enumerate(admin_ids, start=1):
            nickname = self.get_user_nickname(group_id, admin_id)
            role = "超级管理员" if admin_id in SUPER_ADMINS else "管理员"
            lines.append(f"{index}. {nickname} （{role}）")
        return "\n".join(lines)

    async def show_admin_list(self, event: AstrMessageEvent):
        group_id = self.get_group_id(event)
        result = self.get_admin_list(group_id)
        node = Node(
            uin=0,
            name="柏柏子",
            content=[Plain("👮 当前管理员\n━━━━━━━━━━━━━━\n" + result)]
        )
        yield self.reply_result(event, node)

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

    def export_newbie_report(self, group_id: Optional[str] = None, start_iso: Optional[str] = None, end_iso: Optional[str] = None, semester: str = "2026_fall") -> Dict:
        """批量导出新手任务进度报表（WebUI 用）。返回 {columns, rows, csv, count}。"""
        conn = self.get_newbie_conn()
        cursor = conn.cursor()

        base_where = ["semester = ?"]
        base_params = [semester]
        if group_id:
            base_where.append("group_id = ?")
            base_params.append(group_id)

        cursor.execute(
            f"SELECT * FROM newbie_users WHERE {' AND '.join(base_where)} ORDER BY group_id, user_id",
            base_params,
        )
        users = cursor.fetchall()

        def aggregate(table, extra_where, extra_params, column, alias):
            w = base_where + extra_where
            p = base_params + extra_params
            cursor.execute(
                f"SELECT user_id, {column} AS {alias} FROM {table} WHERE {' AND '.join(w)} GROUP BY user_id",
                p,
            )
            result = {}
            for row in cursor.fetchall():
                result[row["user_id"]] = row[alias] or 0
            return result

        now = datetime.now()
        week_monday = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        week_runs = aggregate("newbie_running_records", ["created_at >= ?"], [week_monday.isoformat()], "COUNT(*)", "c")
        all_running_points = aggregate("newbie_running_points", [], [], "SUM(points)", "s")
        all_training = aggregate("newbie_training_records", [], [], "COUNT(*)", "c")

        period_where = []
        period_params = []
        if start_iso:
            period_where.append("created_at >= ?")
            period_params.append(start_iso)
        if end_iso:
            period_where.append("created_at <= ?")
            period_params.append(end_iso)
        period_points = aggregate("newbie_running_points", period_where, period_params, "SUM(points)", "s")
        period_training = aggregate("newbie_training_records", period_where, period_params, "COUNT(*)", "c")

        rows = []
        for user in users:
            user_id = user["user_id"]
            running_points_total = all_running_points.get(user_id, 0)
            training_total = all_training.get(user_id, 0)
            training_points = min((training_total // 4) * 3, 12)

            stage, week = self.get_stage_week(user["points_started_at"], now)

            rows.append({
                "group_id": str(user["group_id"]),
                "nickname": user["nickname"] or str(user_id),
                "gender": "女" if user["gender"] == "female" else "男",
                "stage": "未开始" if stage == 0 else f"第{stage}阶段",
                "week": "" if stage == 0 else f"第{week}周",
                "this_week_runs": week_runs.get(user_id, 0),
                "period_points": period_points.get(user_id, 0),
                "period_training": period_training.get(user_id, 0),
                "training_points": training_points,
                "total_points": running_points_total + training_points,
            })

        conn.close()

        rows.sort(key=lambda r: (r["group_id"], -r["total_points"]))

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([label for _, label in NEWBIE_EXPORT_COLUMNS])
        for r in rows:
            writer.writerow([r[key] for key, _ in NEWBIE_EXPORT_COLUMNS])
        csv_text = "﻿" + buffer.getvalue()

        return {
            "columns": [{"key": key, "label": label} for key, label in NEWBIE_EXPORT_COLUMNS],
            "rows": rows,
            "csv": csv_text,
            "count": len(rows),
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
        node = Node(
            uin=0,
            name="柏柏子",
            content=[Plain("📊 新手任务积分排行榜\n━━━━━━━━━━━━━━\n" + leaderboard)]
        )
        yield self.reply_result(event, node)

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
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("你还没有参加新手任务。")]
            )
            yield self.reply_result(event, node)
            return
        stats = self.get_user_stats(group_id, user_id)
        status = "🟢 已开始积分" if user["points_started"] == 1 else "🟡 等待管理员开始积分"
        conn.close()
        display_name = self.get_display_name(event, user["nickname"])
        node = Node(
            uin=0,
            name="柏柏子",
            content=[Plain(f"📊 {display_name} 的新手任务\n━━━━━━━━━━━━━━\n{status}\n\n"
                f"🏃 跑步次数：{stats['running_count']}\n"
                f"🏋️ 训练次数：{stats['training_count']}\n"
                f"📌 总次数：{stats['total_count']}\n\n"
                f"🏃 跑步积分：{stats['running_points']} 分\n"
                f"🏋️ 训练积分：{stats['training_points']} 分\n"
                f"⭐ 总积分：{stats['total_points']} 分")]
        )
        yield self.reply_result(event, node)

    async def undo_training(self, event: AstrMessageEvent, argument: str = ""):
        group_id = self.get_group_id(event)
        admin_id = self.get_user_id(event)
        if not self.is_admin(group_id, admin_id):
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 只有管理员可以撤销训练记录。")]
            )
            yield self.reply_result(event, node)
            return

        target_user_id = self.get_target_user_id(event, argument)
        if not target_user_id:
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 请 @ 一位群友或输入 QQ 号。\n\n例如：\n/撤销训练 @张三")]
            )
            yield self.reply_result(event, node)
            return

        before_stats = self.get_user_stats(group_id, target_user_id)
        if before_stats["training_count"] <= 0:
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 该成员没有可以撤销的训练记录。")]
            )
            yield self.reply_result(event, node)
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
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 没有找到训练记录。")]
            )
            yield self.reply_result(event, node)
            return

        cursor.execute("DELETE FROM newbie_training_records WHERE id = ?", (training["id"],))
        conn.commit()
        after_stats = self.get_user_stats(group_id, target_user_id)
        point_change = after_stats["total_points"] - before_stats["total_points"]
        nickname = self.get_display_name(event, self.get_user_nickname(group_id, target_user_id))
        leaderboard = self.get_newbie_leaderboard(group_id, changed_user=target_user_id, point_change=point_change)
        conn.close()
        node = Node(
            uin=0,
            name="柏柏子",
            content=[Plain(f"↩️ 已撤销 {nickname} 最近一次训练记录\n\n"
                f"🏋️ 训练次数：{before_stats['training_count']} → {after_stats['training_count']}\n"
                f"🏋️ 训练积分：{before_stats['training_points']} → {after_stats['training_points']}\n"
                f"⭐ 总积分变化：{point_change:+d}\n\n"
                f"━━━━━━━━━━━━━━\n📊 新手任务积分排行榜\n━━━━━━━━━━━━━━\n{leaderboard}")]
        )
        yield self.reply_result(event, node)

    async def undo_newbie_running(self, event: AstrMessageEvent, argument: str = ""):
        group_id = self.get_group_id(event)
        admin_id = self.get_user_id(event)
        if not self.is_admin(group_id, admin_id):
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 只有管理员可以撤销跑步记录。")]
            )
            yield self.reply_result(event, node)
            return

        target_user_id = self.get_target_user_id(event, argument)
        if not target_user_id:
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 请 @ 一位群友或输入 QQ 号。\n\n例如：\n/撤销跑步 @张三")]
            )
            yield self.reply_result(event, node)
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
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 该成员没有可以撤销的跑步记录。")]
            )
            yield self.reply_result(event, node)
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
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 找不到该成员的新手任务信息。")]
            )
            yield self.reply_result(event, node)
            return

        rule, stage, days_to_next_stage = self.get_running_rule(user["gender"], user["points_started_at"], running_time)
        if rule is None:
            conn.close()
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 该成员尚未开始积分，无法计算撤销后的阶段规则。")]
            )
            yield self.reply_result(event, node)
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
        node = Node(
            uin=0,
            name="柏柏子",
            content=[Plain(f"↩️ 已撤销 {nickname} 最近一次跑步记录\n\n"
                f"🏃 撤销距离：{running['distance']}km\n"
                f"📅 该周当前跑步次数：{weekly_count}\n"
                f"🏃 跑步积分：{before_stats['running_points']} → {after_stats['running_points']}\n"
                f"⭐ 总积分变化：{point_change:+d}\n\n"
                f"━━━━━━━━━━━━━━\n📊 新手任务积分排行榜\n━━━━━━━━━━━━━━\n{leaderboard}")]
        )
        yield self.reply_result(event, node)

    def build_unified_help_message(self, user_id: str, group_id: str) -> str:
        lines = [
            "🏃 跑团命令总览",
            "━━━━━━━━━━━━━━",
            "",
            "【账号绑定】",
            "/绑定 QQ号   绑定",
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
        node = Node(
            uin=0,
            name="柏柏子",
            content=[Plain(message)]
        )
        yield self.reply_result(event, node)
