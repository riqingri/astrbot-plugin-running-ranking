from datetime import datetime, timedelta
from typing import Optional

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Node, Plain


class RunningMixin:
    """跑步里程记录与排行榜。"""

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

            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 里程格式不正确。\n\n"
                    "正确用法：\n"
                    "/跑步 5\n"
                    "/跑步 5.2\n"
                    "/跑步 10.5")]
            )
            yield event.chain_result(self.adapt_reply(event, node))

            return

        # ---------------------------------------------------------
        # 检查距离
        # ---------------------------------------------------------

        if distance_value <= 0:

            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 跑步距离必须大于 0 km。")]
            )
            yield event.chain_result(self.adapt_reply(event, node))

            return

        if distance_value > 200:

            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 单次跑步距离不能超过 200 km。")]
            )
            yield event.chain_result(self.adapt_reply(event, node))

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

        node = Node(
            uin=0,
            name="柏柏子",
            content=[Plain(f"🏃 收到你的 {distance_value:.2f} km 跑步记录。\n\n"
                f"📸 请在 5 分钟内发送一张跑步证明图片。\n\n"
                f"⚠️ 收到图片后才会正式计入排行榜。\n"
                f"没有图片则不会记录本次跑步。")]
        )
        yield event.chain_result(self.adapt_reply(event, node))

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

        node = Node(
            uin=0,
            name="柏柏子",
            content=[Plain(f"🏃 {user_name} 的跑步数据\n\n"
                f"📅 今日：{today_total:.2f} km\n"
                f"📆 本周：{week_total:.2f} km\n"
                f"🗓️ 本月：{month_total:.2f} km\n"
                f"👑 总里程：{total:.2f} km")]
        )
        yield event.chain_result(self.adapt_reply(event, node))

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

        node = Node(
            uin=0,
            name="柏柏子",
            content=[Plain(message)]
        )
        yield event.chain_result(self.adapt_reply(event, node))

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

        node = Node(
            uin=0,
            name="柏柏子",
            content=[Plain(ranking_text)]
        )
        yield event.chain_result(self.adapt_reply(event, node))

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
                created_at

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

            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 你还没有跑步记录。")]
            )
            yield event.chain_result(self.adapt_reply(event, node))

            return

        record_id = record[0]

        distance = record[1]

        created_at = record[3]

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

        node = Node(
            uin=0,
            name="柏柏子",
            content=[Plain(f"🗑️ 已撤销最近一次跑步记录。\n\n"
                f"删除里程：{float(distance):.2f} km")]
        )
        yield event.chain_result(self.adapt_reply(event, node))
