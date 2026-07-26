from os import path
import sys
from datetime import datetime, timedelta
import re
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.message.message_event_result import MessageChain, MessageEventResult
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.message_session import MessageSession, MessageSesion
from astrbot.core.platform.message_type import MessageType
from astrbot.core.star.star_tools import StarTools
import pillowmd
from .database import BlacklistDatabase


@register(
    "astrbot_plugin_blacklist_tools",
    "ctrlkk",
    "允许管理员和 LLM 将用户添加到黑名单中，阻止他们的消息，自动拉黑！",
    "1.6",
)
class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        data_dir = StarTools.get_data_dir()
        self.db_path = path.join(data_dir, "blacklist.db")

        # 黑名单最长时长
        self.max_blacklist_duration = config.get(
            "max_blacklist_duration", 1 * 24 * 60 * 60
        )
        # 是否允许永久黑名单
        self.allow_permanent_blacklist = config.get("allow_permanent_blacklist", True)
        # 是否向被拉黑用户显示拉黑状态
        self.show_blacklist_status = config.get("show_blacklist_status", True)
        # 黑名单提示消息
        self.blacklist_message = config.get("blacklist_message", "[连接已中断]")
        # 自动删除过期多久的黑名单
        self.auto_delete_expired_after = config.get("auto_delete_expired_after", 86400)
        # 是否允许拉黑管理员
        self.allow_blacklist_admin = config.get("allow_blacklist_admin", False)

        self.db = BlacklistDatabase(self.db_path, self.auto_delete_expired_after)

        # ------------------------------------------------------------
        # Monkey-patch Context.send_message 以阻止向黑名单用户的主动消息
        # ------------------------------------------------------------
        _original_send_message = self.context.send_message

        async def _patched_send_message(session, message_chain):
            # 解析 session -> MessageSession
            if isinstance(session, str):
                try:
                    parsed = MessageSession.from_str(session)
                except Exception:
                    return await _original_send_message(session, message_chain)
            elif isinstance(session, MessageSesion):
                parsed = session
            else:
                return await _original_send_message(session, message_chain)

            try:
                # — 私聊：目标 = session_id —
                if parsed.message_type == MessageType.FRIEND_MESSAGE:
                    target_uid = parsed.session_id
                    if await self.db.is_user_blacklisted(target_uid):
                        logger.info(
                            f"[黑名单·主动拦截] 阻止向黑名单用户 {target_uid} 发送私聊消息"
                        )
                        return True

                # — 群聊：扫描消息链中的 @ 组件 —
                elif parsed.message_type == MessageType.GROUP_MESSAGE:
                    for comp in message_chain.chain:
                        if isinstance(comp, Comp.At):
                            # qq 可能为 int（用户 ID）或 str（"all"=全体成员）
                            if isinstance(comp.qq, int) or (
                                isinstance(comp.qq, str) and comp.qq.isdigit()
                            ):
                                target_uid = str(comp.qq)
                                if await self.db.is_user_blacklisted(target_uid):
                                    logger.info(
                                        f"[黑名单·主动拦截] 阻止向群聊中的黑名单用户 {target_uid} 发送消息"
                                    )
                                    return True
            except Exception as e:
                logger.error(f"[黑名单·主动拦截] 检查失败：{e}")

            return await _original_send_message(session, message_chain)

        self.context.send_message = _patched_send_message

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        await self.db.initialize()

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        await self.db.terminate()

    def _format_datetime(
        self, iso_datetime_str, show_remaining=False, check_expire=False
    ):
        """统一格式化日期时间字符串
        Args:
            iso_datetime_str: ISO格式的日期时间字符串
            show_remaining: 是否显示剩余时间
            check_expire: 是否检查是否过期（仅对过期时间有效）
        """
        if not iso_datetime_str:
            return "永久"
        try:
            datetime_obj = datetime.fromisoformat(iso_datetime_str)
            formatted_time = datetime_obj.strftime("%Y-%m-%d %H:%M:%S")

            if check_expire:
                if datetime.now() > datetime_obj:
                    return "已过期"

            if show_remaining:
                if datetime.now() > datetime_obj:
                    return "已过期"
                else:
                    remaining_time = datetime_obj - datetime.now()
                    days = remaining_time.days
                    hours, remainder = divmod(remaining_time.seconds, 3600)
                    minutes, _ = divmod(remainder, 60)
                    return (
                        f"{formatted_time} (剩余: {days}天 {hours}小时 {minutes}分钟)"
                    )
            else:
                return formatted_time
        except Exception as e:
            logger.error(f"格式化日期时间时出错：{e}")
            return "格式错误"

    @filter.event_message_type(filter.EventMessageType.ALL, priority=sys.maxsize - 1)
    async def on_all_message(self, event: AstrMessageEvent):
        sender_id = event.get_sender_id()
        try:
            if event.is_admin() and not self.allow_blacklist_admin:
                return

            if await self.db.is_user_blacklisted(sender_id):
                event.stop_event()

                # 同时 patch 该 event 的 send 方法，防止其他路径发出回复
                _orig_send = event.send

                async def _blocked_send(message):
                    logger.info(
                        f"[黑名单·回复拦截] 阻止向黑名单用户 {sender_id} 发送回复"
                    )
                    # 黑名单用户仍应收到一条提示（如果开启了展示）
                    if self.show_blacklist_status:
                        return await _orig_send(
                            MessageChain().message(self.blacklist_message)
                        )
                    return

                event.send = _blocked_send

                # 如果开启展示，发送黑名单提示
                if self.show_blacklist_status and event.get_messages():
                    await event.send(MessageChain().message(self.blacklist_message))

        except Exception as e:
            logger.error(f"检查黑名单时出错：{e}")



    @staticmethod
    def _display_len(s) -> int:
        """计算字符串的显示宽度：CJK字符算2，ASCII算1"""
        count = 0
        for ch in str(s):
            if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f' or '\uff00' <= ch <= '\uffef':
                count += 2
            else:
                count += 1
        return count
    
    def _resolve_target_id(self, event: AstrMessageEvent, fallback: str = "") -> str:
        """提取被@的用户ID（参考画像插件）。
        优先顺序:
        1. At组件 → comp.qq
        2. 正则提取 <@OPENID>
        3. raw_message.mentions
        4. 回退到参数传进来的 fallback
        """
        from astrbot.core.message.components import At
        
        # 方式1: At组件
        message_obj = getattr(event, "message_obj", None)
        if message_obj and hasattr(message_obj, "message"):
            for comp in message_obj.message:
                if isinstance(comp, At) and str(comp.qq) not in ("", "all"):
                    return str(comp.qq)
        
        # 方式2: 正则提取 <@OPENID>（QQ原生格式）
        text = event.message_str
        m = re.search(r'<@(\w+)>', text)
        if m:
            return m.group(1)
        
        # 方式3: raw_message.mentions
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if isinstance(raw, dict):
            mentions = raw.get("mentions", [])
            if mentions and isinstance(mentions, list) and len(mentions) > 0:
                first = mentions[0]
                if isinstance(first, dict):
                    uid = first.get("member_openid", "") or first.get("id", "") or ""
                    if uid:
                        return str(uid)
        
        return fallback
    
    def _resolve_nickname(self, event: AstrMessageEvent, target_id: str) -> str:
        """获取目标用户昵称（参考画像插件）。
        优先顺序:
        1. At组件.name
        2. 发送者昵称（如果target就是发送者）
        3. raw_message.mentions.username
        """
        from astrbot.core.message.components import At
        
        # 方式1: At组件name属性
        message_obj = getattr(event, "message_obj", None)
        if message_obj and hasattr(message_obj, "message"):
            for comp in message_obj.message:
                if isinstance(comp, At) and str(comp.qq) == target_id:
                    name = getattr(comp, "name", None) or ""
                    if name and name != target_id:
                        return str(name)
        
        # 方式2: 发送者昵称（自添加）
        if target_id == event.get_sender_id():
            name = event.get_sender_name() or ""
            if name and name != target_id:
                return str(name)
        
        # 方式3: raw_message.mentions.username
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if raw is not None:
            if hasattr(raw, "mentions"):
                mentions = getattr(raw, "mentions", None) or []
                for m in mentions:
                    mid = str(getattr(m, "member_openid", "") or getattr(m, "id", "") or "")
                    if mid == target_id:
                        username = str(getattr(m, "username", "") or "")
                        if username:
                            return username
            elif isinstance(raw, dict):
                mentions = raw.get("mentions", [])
                for m in mentions:
                    if isinstance(m, dict):
                        mid = str(m.get("member_openid", "") or m.get("id", "") or "")
                        if mid == target_id:
                            username = str(m.get("username", "") or "")
                            if username:
                                return username
        
        return ""
    @filter.command_group("blacklist", alias=["black", "bl"])
    def blacklist():
        pass

    @filter.permission_type(filter.PermissionType.ADMIN)
    @blacklist.command("ls")
    async def ls(self, event: AstrMessageEvent, page: int = 1, page_size: int = 10):
        """列出黑名单中的所有用户（支持分页）
        Args:
            page: 页码，从1开始
            page_size: 每页显示的数量
        """
        try:
            total_count = await self.db.get_blacklist_count()

            if total_count == 0:
                yield event.plain_result("黑名单为空。")
                return

            # 计算分页参数
            total_pages = (total_count + page_size - 1) // page_size
            if page < 1:
                page = 1
            elif page > total_pages:
                page = total_pages

            users = await self.db.get_blacklist_users(page, page_size)

            # 构造 Markdown 表格
            table_rows = ["| ID | 昵称 | 加入时间 | 过期时间 | 原因 |"]
            table_rows.append("|----|------|----------|----------|------|")
            for user in users:
                user_id, ban_time, expire_time, reason, nickname = user
                nick = nickname if nickname else "?"
                ban_time_str = self._format_datetime(ban_time, check_expire=False)
                expire_time_str = self._format_datetime(expire_time, check_expire=True)
                reason_str = reason if reason else ""
                # 转义 Markdown 表格中的竖线
                user_id_str = str(user_id).replace("|", "\\|")
                nick_str = str(nick).replace("|", "\\|")
                reason_str = reason_str.replace("|", "\\|")
                table_rows.append(
                    f"| {user_id_str} | {nick_str} | {ban_time_str} | {expire_time_str} | {reason_str} |"
                )

            md_content = "\n".join(table_rows)

            # 分页信息
            page_info = (
                f"第 {page}/{total_pages} 页 | 共 {total_count} 条记录 | "
                f"每页 {page_size} 条"
            )
            if page > 1:
                page_info += f" | ← `/bl ls {page - 1} {page_size}`"
            if page < total_pages:
                page_info += f" | `/bl ls {page + 1} {page_size}` →"

            md_footer = f"\n\n> {page_info}"

            try:
                # 使用 pillowmd 渲染 Markdown 为图片，自适应宽度
                style = pillowmd.MdStyle(
                    name="blacklist",
                    fontSize=22,
                    title1FontSize=50,
                    title2FontSize=40,
                    title3FontSize=32,
                    xSizeMax=1200,
                    formLineDistance=15,
                    lineDistance=8,
                    formTextColor=(255, 255, 255),
                    formUnderpainting=(10, 20, 40, 0),
                    formTitleUnderpainting=(30, 60, 120, 0),
                    textColor=(220, 230, 255),
                    linkColor=(180, 200, 255),
                )
                render_result = await pillowmd.MdToImage(
                    text=md_content + md_footer,
                    title="黑名单列表",
                    autoPage=True,
                    style=style,
                    showLink=False,
                )
                import io, base64

                buf = io.BytesIO()
                render_result.image.save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode()
                yield event.chain_result([Comp.Image.fromBase64(b64)])
            except Exception as render_err:
                logger.error(f"pillowmd 渲染失败，回退到文本：{render_err}")
                yield event.plain_result(md_content + "\n" + page_info)
        except Exception as e:
            logger.error(f"列出黑名单时出错：{e}")
            yield event.plain_result("列出黑名单时出错。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @blacklist.command("rm")
    async def rm(self, event: AstrMessageEvent, user_id: str):
        """从黑名单中移除用户"""
        try:
            # 优先从消息组件提取 @ 的用户（参考画像插件三层提取）
            final_user_id = self._resolve_target_id(event, user_id)

            user = await self.db.get_user_info(final_user_id)

            if not user:
                yield event.plain_result(f"用户 {final_user_id} 不在黑名单中。")
                return

            if await self.db.remove_user(final_user_id):
                yield event.plain_result(f"用户 {final_user_id} 已从黑名单中移除。")
            else:
                yield event.plain_result("从黑名单移除用户时出错。")
        except Exception as e:
            logger.error(f"从黑名单移除用户时出错：{e}")
            yield event.plain_result("从黑名单移除用户时出错。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @blacklist.command("add")
    async def add(
        self, event: AstrMessageEvent, user_id: str, duration: int = 0, reason: str = ""
    ):
        """添加用户到黑名单"""
        try:
            # 优先从消息组件提取 @ 的用户（参考画像插件三层提取）
            final_user_id = self._resolve_target_id(event, user_id)
            nickname = self._resolve_nickname(event, final_user_id) if final_user_id else ""

            if not final_user_id:
                yield event.plain_result("请指定要拉黑的用户（通过 @ 或输入用户 ID）")
                return

            ban_time = datetime.now().isoformat()
            expire_time = None

            if duration > 0:
                expire_time = (datetime.now() + timedelta(seconds=duration)).isoformat()

            if await self.db.add_user(final_user_id, ban_time, expire_time, reason, nickname):
                nick_disp = f" ({nickname})" if nickname else ""
                if duration > 0:
                    yield event.plain_result(
                        f"用户 {final_user_id}{nick_disp} 已被加入黑名单，时长 {duration} 秒。"
                    )
                else:
                    yield event.plain_result(f"用户 {final_user_id}{nick_disp} 已被永久加入黑名单。")
            else:
                yield event.plain_result("添加用户到黑名单时出错。")

        except Exception as e:
            logger.error(f"添加用户到黑名单时出错：{e}")
            yield event.plain_result("添加用户到黑名单时出错。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @blacklist.command("clear")
    async def clear(self, event: AstrMessageEvent):
        """清空黑名单"""
        try:
            count = await self.db.get_blacklist_count()

            if count == 0:
                yield event.plain_result("黑名单已经为空。")
                return

            if await self.db.clear_blacklist():
                yield event.plain_result(f"黑名单已清空，共移除 {count} 个用户。")
            else:
                yield event.plain_result("清空黑名单时出错。")
        except Exception as e:
            logger.error(f"清空黑名单时出错：{e}")
            yield event.plain_result("清空黑名单时出错。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @blacklist.command("info")
    async def info(self, event: AstrMessageEvent, user_id: str):
        """查看特定用户的黑名单信息"""
        try:
            # 优先从消息组件提取 @ 的用户（参考画像插件三层提取）
            final_user_id = self._resolve_target_id(event, user_id)

            user = await self.db.get_user_info(final_user_id)

            if not user:
                yield event.plain_result(f"用户 {final_user_id} 不在黑名单中。")
                return

            rec_id, ban_time, expire_time, reason, nickname = user
            ban_time_str = self._format_datetime(ban_time, check_expire=False)
            expire_time_str = self._format_datetime(
                expire_time, show_remaining=True, check_expire=True
            )
            reason_str = reason if reason else "无"
            nick = nickname if nickname else "?"

            md_content = (
            f"## 用户信息\n\n"
            f"| 项目 | 内容 |\n"
            f"|---|---|\n"
            f"| **昵称** | {nick} |\n"
            f"| **ID** | `{final_user_id}` |\n"
            f"| **加入时间** | {ban_time_str} |\n"
            f"| **过期时间** | {expire_time_str} |\n"
            f"| **原因** | {reason_str} |\n"
        )

            try:
                style = pillowmd.MdStyle(
                    name="blacklist_info",
                    fontSize=22,
                    title1FontSize=40,
                    title2FontSize=32,
                    xSizeMax=800,
                    formLineDistance=12,
                    lineDistance=6,
                    formTextColor=(255, 255, 255),
                    formUnderpainting=(10, 20, 40, 0),
                    formTitleUnderpainting=(30, 60, 120, 0),
                    textColor=(220, 230, 255),
                )
                render_result = await pillowmd.MdToImage(
                    text=md_content,
                    title="黑名单详情",
                    autoPage=False,
                    style=style,
                    showLink=False,
                )
                import io, base64

                buf = io.BytesIO()
                render_result.image.save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode()
                yield event.chain_result([Comp.Image.fromBase64(b64)])
            except Exception as render_err:
                logger.error(f"pillowmd 渲染失败，回退到文本：{render_err}")
                yield event.plain_result(md_content)
        except Exception as e:
            logger.error(f"查看用户 {final_user_id} 黑名单信息时出错：{e}")
            yield event.plain_result("查看用户黑名单信息时出错。")

    @filter.llm_tool(name="block_user")
    async def add_to_block_user(
        self, event: AstrMessageEvent, duration: int = 0, reason: str = ""
    ) -> MessageEventResult:
        """
        Block a user. All messages from this user will be ignored immediately.
        Use this function when you decide to blacklist a user and cease all contact.

        Args:
            duration (number): The block duration in seconds. Use 0 to make it permanent.
            reason (string): The reason for blocking this user.
        """
        try:
            user_id = event.get_sender_id()
            ban_time = datetime.now().isoformat()
            expire_time = None
            actual_duration = duration

            # 如果不允许永久黑名单，则使用默认时长
            if duration == 0 and not self.allow_permanent_blacklist:
                actual_duration = self.max_blacklist_duration

            # 超出使用最大时间
            if actual_duration > self.max_blacklist_duration:
                actual_duration = self.max_blacklist_duration

            if actual_duration > 0:
                expire_time = (
                    datetime.now() + timedelta(seconds=actual_duration)
                ).isoformat()

            # 获取黑名单用户的昵称
            nickname = getattr(event.message_obj.sender, "nickname", "") or ""

            await self.db.add_user(user_id, ban_time, expire_time, reason, nickname)

            nick_disp = f" ({nickname})" if nickname else ""
            if actual_duration > 0:
                return f"用户 {user_id}{nick_disp} 已被加入黑名单，时长 {actual_duration} 秒"
            else:
                return f"用户 {user_id}{nick_disp} 已被永久加入黑名单"

        except Exception as e:
            logger.error(f"添加用户 {user_id} 到黑名单时出错：{e}")
            return "添加用户到黑名单时出错"
