from datetime import datetime

from astrbot.api.web import json_response, request


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _str(value):
    if value is None:
        return ""
    return str(value).strip()


def _has(body, key):
    value = body.get(key)
    return value is not None and str(value).strip() != ""


def _rows(cursor):
    if cursor.description is None:
        return []
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


async def _read_body():
    """尽量把 POST body 解析成 dict（优先 JSON，退回表单）。"""
    try:
        data = await request.json(default=None)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    try:
        form = await request.form()
        return {k: v for k, v in form.items()}
    except Exception:
        pass
    return {}


class WebApiMixin:
    """用户数据管理 WebUI 后端。"""

    def register_web_api_routes(self):
        prefix = "/astrbot_plugin_running_rank"
        routes = [
            ("GET", f"{prefix}/overview", self._api_overview, "数据概览"),
            ("GET", f"{prefix}/groups", self._api_groups, "群列表"),

            ("GET", f"{prefix}/running_records", self._api_list_running_records, "跑步记录列表"),
            ("POST", f"{prefix}/running_records/create", self._api_create_running_record, "新增跑步记录"),
            ("POST", f"{prefix}/running_records/update", self._api_update_running_record, "修改跑步记录"),
            ("POST", f"{prefix}/running_records/delete", self._api_delete_running_record, "删除跑步记录"),

            ("GET", f"{prefix}/newbie_users", self._api_list_newbie_users, "新手用户列表"),
            ("POST", f"{prefix}/newbie_users/create", self._api_create_newbie_user, "新增新手用户"),
            ("POST", f"{prefix}/newbie_users/update", self._api_update_newbie_user, "修改新手用户"),
            ("POST", f"{prefix}/newbie_users/delete", self._api_delete_newbie_user, "删除新手用户"),

            ("GET", f"{prefix}/newbie_running_points", self._api_list_newbie_points, "新手积分列表"),
            ("POST", f"{prefix}/newbie_running_points/create", self._api_create_newbie_point, "新增新手积分"),
            ("POST", f"{prefix}/newbie_running_points/update", self._api_update_newbie_point, "修改新手积分"),
            ("POST", f"{prefix}/newbie_running_points/delete", self._api_delete_newbie_point, "删除新手积分"),

            ("GET", f"{prefix}/newbie_training_records", self._api_list_newbie_training, "训练记录列表"),
            ("POST", f"{prefix}/newbie_training_records/create", self._api_create_newbie_training, "新增训练记录"),
            ("POST", f"{prefix}/newbie_training_records/update", self._api_update_newbie_training, "修改训练记录"),
            ("POST", f"{prefix}/newbie_training_records/delete", self._api_delete_newbie_training, "删除训练记录"),

            ("GET", f"{prefix}/newbies_admins", self._api_list_admins, "管理员列表"),
            ("POST", f"{prefix}/newbies_admins/create", self._api_create_admin, "新增管理员"),
            ("POST", f"{prefix}/newbies_admins/update", self._api_update_admin, "修改管理员"),
            ("POST", f"{prefix}/newbies_admins/delete", self._api_delete_admin, "删除管理员"),
        ]
        for method, route, handler, desc in routes:
            self.context.register_web_api(route, handler, [method], desc)

    # =============================================================
    # 概览 / 群列表
    # =============================================================

    async def _api_overview(self):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*), COALESCE(SUM(distance), 0) FROM running_records")
        r_count, r_distance = cursor.fetchone()
        conn.close()

        counts = {}
        nb = self.get_newbie_conn()
        nc = nb.cursor()
        for table in [
            "newbie_users",
            "newbies_admins",
            "newbie_running_records",
            "newbie_running_points",
            "newbie_training_records",
        ]:
            nc.execute(f"SELECT COUNT(*) FROM {table}")
            counts[table] = nc.fetchone()[0]
        nb.close()

        return json_response({"status": "ok", "data": {
            "running_records": {"count": r_count, "total_distance": round(r_distance or 0, 2)},
            "newbie_users": counts["newbie_users"],
            "newbies_admins": counts["newbies_admins"],
            "newbie_running_points": counts["newbie_running_points"],
            "newbie_training_records": counts["newbie_training_records"],
        }})

    async def _api_groups(self):
        groups = set()

        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT group_id FROM running_records")
        for row in cursor.fetchall():
            if row[0]:
                groups.add(str(row[0]))
        conn.close()

        nb = self.get_newbie_conn()
        nc = nb.cursor()
        for table in ["newbie_users", "newbies_admins", "newbie_running_points", "newbie_training_records"]:
            nc.execute(f"SELECT DISTINCT group_id FROM {table}")
            for row in nc.fetchall():
                if row["group_id"]:
                    groups.add(str(row["group_id"]))
        nb.close()

        return json_response({"status": "ok", "data": sorted(groups)})

    # =============================================================
    # 跑步记录 running_records（running.db）
    # =============================================================

    async def _api_list_running_records(self):
        q = request.query
        where = []
        params = []

        group_id = q.get("group_id")
        user_id = q.get("user_id")
        keyword = q.get("keyword")
        from_time = q.get("from")
        to_time = q.get("to")

        if group_id:
            where.append("group_id = ?")
            params.append(group_id)
        if user_id:
            where.append("user_id = ?")
            params.append(user_id)
        if keyword:
            where.append("(user_id LIKE ? OR user_name LIKE ?)")
            kw = f"%{keyword}%"
            params += [kw, kw]
        if from_time:
            where.append("run_time >= ?")
            params.append(from_time)
        if to_time:
            where.append("run_time <= ?")
            params.append(to_time)

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        limit = _int(q.get("limit"), 100)
        offset = _int(q.get("offset"), 0)

        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM running_records {where_sql}", params)
        total = cursor.fetchone()[0]
        cursor.execute(
            f"SELECT * FROM running_records {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        rows = _rows(cursor)
        conn.close()

        return json_response({"status": "ok", "data": {"total": total, "rows": rows}})

    async def _api_create_running_record(self):
        body = await _read_body()
        required = ["user_id", "user_name", "group_id", "distance", "run_time"]
        missing = [k for k in required if not _has(body, k)]
        if missing:
            return json_response({"status": "error", "message": f"缺少字段: {', '.join(missing)}"})

        created_at = _str(body.get("created_at")) or datetime.now().isoformat()
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO running_records (user_id, user_name, group_id, distance, run_time, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                _str(body["user_id"]),
                _str(body["user_name"]),
                _str(body["group_id"]),
                _float(body["distance"]),
                _str(body["run_time"]),
                created_at,
            ),
        )
        conn.commit()
        new_id = cursor.lastrowid
        conn.close()
        return json_response({"status": "ok", "data": {"id": new_id}})

    async def _api_update_running_record(self):
        body = await _read_body()
        record_id = _int(body.get("id"))
        if not record_id:
            return json_response({"status": "error", "message": "缺少记录 id"})

        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE running_records
            SET user_id = ?, user_name = ?, group_id = ?, distance = ?, run_time = ?, created_at = ?
            WHERE id = ?
            """,
            (
                _str(body.get("user_id")),
                _str(body.get("user_name")),
                _str(body.get("group_id")),
                _float(body.get("distance")),
                _str(body.get("run_time")),
                _str(body.get("created_at")),
                record_id,
            ),
        )
        conn.commit()
        conn.close()
        return json_response({"status": "ok", "data": {"id": record_id}})

    async def _api_delete_running_record(self):
        body = await _read_body()
        record_id = _int(body.get("id"))
        if not record_id:
            return json_response({"status": "error", "message": "缺少记录 id"})

        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM running_records WHERE id = ?", (record_id,))
        conn.commit()
        conn.close()
        return json_response({"status": "ok", "data": {"id": record_id}})

    # =============================================================
    # 新手用户 newbie_users（newbie_points.db）
    # =============================================================

    def _query_newbie_list(self, table, keyword_columns):
        q = request.query
        where = []
        params = []

        group_id = q.get("group_id")
        keyword = q.get("keyword")

        if group_id:
            where.append("group_id = ?")
            params.append(group_id)
        if keyword:
            kw = f"%{keyword}%"
            like = " OR ".join(f"{col} LIKE ?" for col in keyword_columns)
            where.append(f"({like})")
            params += [kw] * len(keyword_columns)

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        limit = _int(q.get("limit"), 100)
        offset = _int(q.get("offset"), 0)

        nb = self.get_newbie_conn()
        cursor = nb.cursor()
        cursor.execute(f"SELECT COUNT(*) AS c FROM {table} {where_sql}", params)
        total = cursor.fetchone()["c"]
        cursor.execute(
            f"SELECT * FROM {table} {where_sql} ORDER BY rowid DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        rows = _rows(cursor)
        nb.close()
        return total, rows

    async def _api_list_newbie_users(self):
        total, rows = self._query_newbie_list("newbie_users", ["user_id", "nickname"])
        return json_response({"status": "ok", "data": {"total": total, "rows": rows}})

    async def _api_create_newbie_user(self):
        body = await _read_body()
        required = ["group_id", "user_id"]
        missing = [k for k in required if not _has(body, k)]
        if missing:
            return json_response({"status": "error", "message": f"缺少字段: {', '.join(missing)}"})

        semester = _str(body.get("semester")) or "2026_fall"
        nb = self.get_newbie_conn()
        cursor = nb.cursor()
        cursor.execute(
            """
            INSERT INTO newbie_users (group_id, semester, user_id, nickname, gender, joined_at, points_started, points_started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _str(body["group_id"]),
                semester,
                _str(body["user_id"]),
                _str(body.get("nickname")) or None,
                _str(body.get("gender")) or "male",
                _str(body.get("joined_at")) or datetime.now().isoformat(),
                _int(body.get("points_started"), 0),
                _str(body.get("points_started_at")) or None,
            ),
        )
        nb.commit()
        nb.close()
        return json_response({"status": "ok", "data": {"group_id": body["group_id"], "user_id": body["user_id"]}})

    async def _api_update_newbie_user(self):
        body = await _read_body()
        group_id = _str(body.get("group_id"))
        semester = _str(body.get("semester")) or "2026_fall"
        user_id = _str(body.get("user_id"))
        if not group_id or not user_id:
            return json_response({"status": "error", "message": "缺少主键 group_id / user_id"})

        nb = self.get_newbie_conn()
        cursor = nb.cursor()
        cursor.execute(
            """
            UPDATE newbie_users
            SET nickname = ?, gender = ?, joined_at = ?, points_started = ?, points_started_at = ?
            WHERE group_id = ? AND semester = ? AND user_id = ?
            """,
            (
                _str(body.get("nickname")) or None,
                _str(body.get("gender")) or "male",
                _str(body.get("joined_at")),
                _int(body.get("points_started"), 0),
                _str(body.get("points_started_at")) or None,
                group_id,
                semester,
                user_id,
            ),
        )
        nb.commit()
        nb.close()
        return json_response({"status": "ok", "data": {"group_id": group_id, "user_id": user_id}})

    async def _api_delete_newbie_user(self):
        body = await _read_body()
        group_id = _str(body.get("group_id"))
        semester = _str(body.get("semester")) or "2026_fall"
        user_id = _str(body.get("user_id"))
        if not group_id or not user_id:
            return json_response({"status": "error", "message": "缺少主键 group_id / user_id"})

        nb = self.get_newbie_conn()
        cursor = nb.cursor()
        cursor.execute(
            "DELETE FROM newbie_users WHERE group_id = ? AND semester = ? AND user_id = ?",
            (group_id, semester, user_id),
        )
        nb.commit()
        nb.close()
        return json_response({"status": "ok", "data": {"group_id": group_id, "user_id": user_id}})

    # =============================================================
    # 新手积分 newbie_running_points（newbie_points.db）
    # =============================================================

    async def _api_list_newbie_points(self):
        total, rows = self._query_newbie_list("newbie_running_points", ["user_id"])
        return json_response({"status": "ok", "data": {"total": total, "rows": rows}})

    async def _api_create_newbie_point(self):
        body = await _read_body()
        required = ["group_id", "user_id", "year", "week", "points"]
        missing = [k for k in required if not _has(body, k)]
        if missing:
            return json_response({"status": "error", "message": f"缺少字段: {', '.join(missing)}"})

        nb = self.get_newbie_conn()
        cursor = nb.cursor()
        cursor.execute(
            """
            INSERT INTO newbie_running_points (group_id, semester, user_id, year, week, month, points, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _str(body["group_id"]),
                _str(body.get("semester")) or "2026_fall",
                _str(body["user_id"]),
                _int(body["year"]),
                _int(body["week"]),
                _int(body.get("month"), datetime.now().month),
                _int(body["points"]),
                _str(body.get("created_at")) or datetime.now().isoformat(),
            ),
        )
        nb.commit()
        new_id = cursor.lastrowid
        nb.close()
        return json_response({"status": "ok", "data": {"id": new_id}})

    async def _api_update_newbie_point(self):
        body = await _read_body()
        record_id = _int(body.get("id"))
        if not record_id:
            return json_response({"status": "error", "message": "缺少记录 id"})

        nb = self.get_newbie_conn()
        cursor = nb.cursor()
        cursor.execute(
            """
            UPDATE newbie_running_points
            SET group_id = ?, semester = ?, user_id = ?, year = ?, week = ?, month = ?, points = ?, created_at = ?
            WHERE id = ?
            """,
            (
                _str(body.get("group_id")),
                _str(body.get("semester")) or "2026_fall",
                _str(body.get("user_id")),
                _int(body.get("year")),
                _int(body.get("week")),
                _int(body.get("month")),
                _int(body.get("points")),
                _str(body.get("created_at")),
                record_id,
            ),
        )
        nb.commit()
        nb.close()
        return json_response({"status": "ok", "data": {"id": record_id}})

    async def _api_delete_newbie_point(self):
        body = await _read_body()
        record_id = _int(body.get("id"))
        if not record_id:
            return json_response({"status": "error", "message": "缺少记录 id"})

        nb = self.get_newbie_conn()
        cursor = nb.cursor()
        cursor.execute("DELETE FROM newbie_running_points WHERE id = ?", (record_id,))
        nb.commit()
        nb.close()
        return json_response({"status": "ok", "data": {"id": record_id}})

    # =============================================================
    # 训练记录 newbie_training_records（newbie_points.db）
    # =============================================================

    async def _api_list_newbie_training(self):
        total, rows = self._query_newbie_list("newbie_training_records", ["user_id", "admin_id"])
        return json_response({"status": "ok", "data": {"total": total, "rows": rows}})

    async def _api_create_newbie_training(self):
        body = await _read_body()
        required = ["group_id", "user_id"]
        missing = [k for k in required if not _has(body, k)]
        if missing:
            return json_response({"status": "error", "message": f"缺少字段: {', '.join(missing)}"})

        nb = self.get_newbie_conn()
        cursor = nb.cursor()
        cursor.execute(
            """
            INSERT INTO newbie_training_records (group_id, semester, user_id, admin_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                _str(body["group_id"]),
                _str(body.get("semester")) or "2026_fall",
                _str(body["user_id"]),
                _str(body.get("admin_id")) or "1929647130",
                _str(body.get("created_at")) or datetime.now().isoformat(),
            ),
        )
        nb.commit()
        new_id = cursor.lastrowid
        nb.close()
        return json_response({"status": "ok", "data": {"id": new_id}})

    async def _api_update_newbie_training(self):
        body = await _read_body()
        record_id = _int(body.get("id"))
        if not record_id:
            return json_response({"status": "error", "message": "缺少记录 id"})

        nb = self.get_newbie_conn()
        cursor = nb.cursor()
        cursor.execute(
            """
            UPDATE newbie_training_records
            SET group_id = ?, semester = ?, user_id = ?, admin_id = ?, created_at = ?
            WHERE id = ?
            """,
            (
                _str(body.get("group_id")),
                _str(body.get("semester")) or "2026_fall",
                _str(body.get("user_id")),
                _str(body.get("admin_id")),
                _str(body.get("created_at")),
                record_id,
            ),
        )
        nb.commit()
        nb.close()
        return json_response({"status": "ok", "data": {"id": record_id}})

    async def _api_delete_newbie_training(self):
        body = await _read_body()
        record_id = _int(body.get("id"))
        if not record_id:
            return json_response({"status": "error", "message": "缺少记录 id"})

        nb = self.get_newbie_conn()
        cursor = nb.cursor()
        cursor.execute("DELETE FROM newbie_training_records WHERE id = ?", (record_id,))
        nb.commit()
        nb.close()
        return json_response({"status": "ok", "data": {"id": record_id}})

    # =============================================================
    # 管理员 newbies_admins（newbie_points.db）
    # =============================================================

    async def _api_list_admins(self):
        total, rows = self._query_newbie_list("newbies_admins", ["user_id"])
        return json_response({"status": "ok", "data": {"total": total, "rows": rows}})

    async def _api_create_admin(self):
        body = await _read_body()
        required = ["group_id", "user_id"]
        missing = [k for k in required if not _has(body, k)]
        if missing:
            return json_response({"status": "error", "message": f"缺少字段: {', '.join(missing)}"})

        nb = self.get_newbie_conn()
        cursor = nb.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO newbies_admins (group_id, user_id, created_at)
            VALUES (?, ?, ?)
            """,
            (
                _str(body["group_id"]),
                _str(body["user_id"]),
                _str(body.get("created_at")) or datetime.now().isoformat(),
            ),
        )
        nb.commit()
        nb.close()
        return json_response({"status": "ok", "data": {"group_id": body["group_id"], "user_id": body["user_id"]}})

    async def _api_update_admin(self):
        body = await _read_body()
        group_id = _str(body.get("group_id"))
        user_id = _str(body.get("user_id"))
        if not group_id or not user_id:
            return json_response({"status": "error", "message": "缺少主键 group_id / user_id"})

        nb = self.get_newbie_conn()
        cursor = nb.cursor()
        cursor.execute(
            "UPDATE newbies_admins SET created_at = ? WHERE group_id = ? AND user_id = ?",
            (_str(body.get("created_at")) or datetime.now().isoformat(), group_id, user_id),
        )
        nb.commit()
        nb.close()
        return json_response({"status": "ok", "data": {"group_id": group_id, "user_id": user_id}})

    async def _api_delete_admin(self):
        body = await _read_body()
        group_id = _str(body.get("group_id"))
        user_id = _str(body.get("user_id"))
        if not group_id or not user_id:
            return json_response({"status": "error", "message": "缺少主键 group_id / user_id"})

        nb = self.get_newbie_conn()
        cursor = nb.cursor()
        cursor.execute(
            "DELETE FROM newbies_admins WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        )
        nb.commit()
        nb.close()
        return json_response({"status": "ok", "data": {"group_id": group_id, "user_id": user_id}})
