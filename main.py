import os
import re
import inspect
import sqlite3
from datetime import datetime

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
import astrbot.api.message_components as Comp
from astrbot.api.message_components import Node, Plain

from .database import DatabaseMixin
from .services.identity import IdentityMixin
from .services.time_utils import TimeMixin
from .services.newbie import NewbieMixin
from .services.running import RunningMixin
from .web_api import WebApiMixin


@register(
    "astrbot_plugin_running_rank",
    "QingriSun",
    "跑步里程记录、跑步证明和排行榜",
    "1.5.0",
)
class RunningRankPlugin(
    Star,
    DatabaseMixin,
    IdentityMixin,
    TimeMixin,
    NewbieMixin,
    RunningMixin,
    WebApiMixin,
):

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
        # 等待图片 / 等待跑步
        # =========================================================

        self.pending_images = {}
        self.pending_runs = {}

        self.PHOTO_WAIT_SECONDS = 300

        self.newbie_db_path = os.path.join(
            self.plugin_dir,
            "newbie_points.db"
        )

        self.newbie_conn = sqlite3.connect(
            self.newbie_db_path,
            check_same_thread=False
        )
        self.newbie_conn.row_factory = sqlite3.Row

        self.image_wait_seconds = 300

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
        }

        # WebUI 后端路由
        self.register_web_api_routes()

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
            r"^([一-龥A-Za-z]+)(?:\s+(.*))?$",
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
            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("⏰ 跑步凭证上传超时。\n本次跑步未记录。")]
            )
            yield self.reply_result(event, node)
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
        node = Node(
            uin=0,
            name="柏柏子",
            content=[Plain(message)]
        )
        yield self.reply_result(event, node)

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

            # 输出模型回复
            if response and response.completion_text:

                yield self.reply_text(event, response.completion_text)

        except Exception as e:
            logger.error("[RunningRank] 调用 LLM 分析跑步图片失败: %s", e)

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

            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("⌛ 图片上传已超时。\n\n"
                    "请重新发送对应命令。")]
            )
            yield self.reply_result(event, node)

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

            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 图片读取失败，请重新发送图片。")]
            )
            yield self.reply_result(event, node)

            return

        if not source_path:

            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 没有获取到图片文件，请重新发送图片。")]
            )
            yield self.reply_result(event, node)

            return

        if not os.path.exists(
            source_path
        ):

            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain("❌ 图片文件不存在，请重新发送图片。")]
            )
            yield self.reply_result(event, node)

            return

        # =========================================================
        # 跑步证明
        # =========================================================

        if pending["type"] == "running_proof":

            distance = float(
                pending["distance"]
            )

            # -----------------------------------------------------
            # 正式写入数据库（证明图片不落盘服务器）
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
                    now.isoformat()
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

            node = Node(
                uin=0,
                name="柏柏子",
                content=[Plain(f"✅ 跑步记录成功！\n\n"
                    f"🏃 本次：{distance:.2f} km\n"
                    f"📆 本周累计：{week_total:.2f} km\n"
                    f"🏆 当前周榜：第 {rank} 名\n\n"
                    f"📸 跑步证明已记录。"
                    f"\n━━━━━━━━━━\n"
                    f"{ranking_text}")]
            )
            yield self.reply_result(event, node)

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
                    prompt="你是一个爱水群爱吐槽的跑团组织者，跑团同学发送了一张图片，请看看这张图片是否是跑步证明？如果不是，就谴责他乱发图片，简单吐槽一下图片内容；如果是，就简单分析一下图片内容，找到简单夸奖或者聊聊图片的配速和地图或者其他图片信息。回复30个字以内，禁止使用markdown语法，使回复在一个QQ消息气泡中显得自然。示范：“数据型” → 这配速，今天偷偷开挂了？“地图型” → 这路线绕得，蚂蚁看了都迷路“时间型” → 这点还在跑，你是真不睡啊“跑团型” → 又一个不声不响开始卷的“细节型” → 这轨迹最后一下是迷路了吗“吐槽型” → 跑步证明呢？你这明显是在发旅游攻略“夸奖型” → 稳稳拿下，今天状态不错嘛。但是注意！不要直接套用示例，要根据图片内容进行分析和吐槽生成新鲜有趣的内容，回复中不要出现示例中的文字。",
                    image_urls=[
                        source_path
                    ]
                )

                # 输出模型回复
                if response and response.completion_text:

                    yield self.reply_text(event, response.completion_text)

            except Exception as e:

                logger.error(
                    "[RunningRank] "
                    f"调用 LLM 分析跑步图片失败: {e}"
                )

            return

    async def terminate(self):

        self.pending_images.clear()

        if hasattr(self, "newbie_conn") and self.newbie_conn:
            self.newbie_conn.close()

        logger.info(
            "[RunningRank] "
            "跑步排行榜插件已卸载"
        )
