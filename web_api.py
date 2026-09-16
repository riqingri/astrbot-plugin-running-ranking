import csv
import io
import sqlite3
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


def _normalize_datetime(value):
    """把前端传来的时间字符串统一成 ISO 秒级格式（去掉微秒），保证字符串比较正确。"""
    if not value:
        return ""
    value = str(value).strip()
    try:
        return datetime.fromisoformat(value).replace(microsecond=0).isoformat()
    except (TypeError, ValueError):
        return value


def _now_iso():
    """当前时间的秒级 ISO 字符串（不含微秒）。"""
    return datetime.now().replace(microsecond=0).isoformat()


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


# =============================================================
# 一键导入：各表模板规格
# =============================================================

IMPORT_SPECS = {
    "running_records": {
        "db": "running",
        "table": "running_records",
        "label": "跑步记录",
        "columns": [
            {"key": "user_id", "label": "用户QQ", "required": True, "default": None, "example": "10001", "type": "str"},
            {"key": "user_name", "label": "昵称", "required": True, "default": None, "example": "小明", "type": "str"},
            {"key": "group_id", "label": "群号", "required": True, "default": None, "example": "123456789", "type": "str"},
            {"key": "distance", "label": "距离(km)", "required": True, "default": None, "example": "5.2", "type": "float"},
            {"key": "run_time", "label": "跑步时间", "required": True, "default": None, "example": "2026-09-16T14:30", "type": "datetime"},
            {"key": "created_at", "label": "录入时间", "required": False, "default": "now", "example": "2026-09-16T14:30", "type": "datetime"},
        ],
    },
    "newbie_users": {
        "db": "newbie",
        "table": "newbie_users",
        "label": "新手用户",
        "columns": [
            {"key": "group_id", "label": "群号", "required": True, "default": None, "example": "123456789", "type": "str"},
            {"key": "semester", "label": "学期", "required": False, "default": "2026_fall", "example": "2026_fall", "type": "str"},
            {"key": "user_id", "label": "用户QQ", "required": True, "default": None, "example": "10001", "type": "str"},
            {"key": "nickname", "label": "昵称", "required": False, "default": None, "example": "小明", "type": "str"},
            {"key": "gender", "label": "性别", "required": True, "default": None, "example": "male", "type": "str"},
            {"key": "joined_at", "label": "加入时间", "required": False, "default": "now", "example": "2026-09-16T14:30", "type": "datetime"},
            {"key": "points_started", "label": "开始积分", "required": False, "default": "0", "example": "1", "type": "int"},
            {"key": "points_started_at", "label": "积分开始时间", "required": False, "default": None, "example": "2026-09-16T14:30", "type": "datetime"},
        ],
    },
    "newbie_running_records": {
        "db": "newbie",
        "table": "newbie_running_records",
        "label": "新手跑步",
        "columns": [
            {"key": "group_id", "label": "群号", "required": True, "default": None, "example": "123456789", "type": "str"},
            {"key": "semester", "label": "学期", "required": False, "default": "2026_fall", "example": "2026_fall", "type": "str"},
            {"key": "user_id", "label": "用户QQ", "required": True, "default": None, "example": "10001", "type": "str"},
            {"key": "distance", "label": "距离(km)", "required": True, "default": None, "example": "5.2", "type": "float"},
            {"key": "created_at", "label": "时间", "required": False, "default": "now", "example": "2026-09-16T14:30", "type": "datetime"},
        ],
    },
    "newbie_running_points": {
        "db": "newbie",
        "table": "newbie_running_points",
        "label": "积分",
        "columns": [
            {"key": "group_id", "label": "群号", "required": True, "default": None, "example": "123456789", "type": "str"},
            {"key": "semester", "label": "学期", "required": False, "default": "2026_fall", "example": "2026_fall", "type": "str"},
            {"key": "user_id", "label": "用户QQ", "required": True, "default": None, "example": "10001", "type": "str"},
            {"key": "year", "label": "年", "required": True, "default": None, "example": "2026", "type": "int"},
            {"key": "week", "label": "周", "required": True, "default": None, "example": "38", "type": "int"},
            {"key": "month", "label": "月", "required": False, "default": "month", "example": "9", "type": "int"},
            {"key": "points", "label": "积分", "required": True, "default": None, "example": "3", "type": "int"},
            {"key": "created_at", "label": "时间", "required": False, "default": "now", "example": "2026-09-16T14:30", "type": "datetime"},
        ],
    },
    "newbie_training_records": {
        "db": "newbie",
        "table": "newbie_training_records",
        "label": "训练",
        "columns": [
            {"key": "group_id", "label": "群号", "required": True, "default": None, "example": "123456789", "type": "str"},
            {"key": "semester", "label": "学期", "required": False, "default": "2026_fall", "example": "2026_fall", "type": "str"},
            {"key": "user_id", "label": "用户QQ", "required": True, "default": None, "example": "10001", "type": "str"},
            {"key": "admin_id", "label": "管理员QQ", "required": False, "default": "3123366945", "example": "3123366945", "type": "str"},
            {"key": "created_at", "label": "时间", "required": False, "default": "now", "example": "2026-09-16T14:30", "type": "datetime"},
        ],
    },
    "newbies_admins": {
        "db": "newbie",
        "table": "newbies_admins",
        "label": "管理员",
        "columns": [
            {"key": "group_id", "label": "群号", "required": True, "default": None, "example": "123456789", "type": "str"},
            {"key": "user_id", "label": "管理员QQ", "required": True, "default": None, "example": "10001", "type": "str"},
            {"key": "created_at", "label": "设置时间", "required": False, "default": "now", "example": "2026-09-16T14:30", "type": "datetime"},
        ],
    },
}


def _import_default(default, now):
    """把空值转换成该列的默认值。"""
    if default is None:
        return None
    if default == "now":
        return _now_iso()
    if default == "month":
        return now.month
    if default == "0":
        return 0
    return default


def _parse_ref_time(raw, now):
    """把导入记录的时间解析成 datetime 作为积分重算的参考时间，失败回退 now。"""
    raw = (raw or "").strip()
    if not raw:
        return now
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return now


class WebApiMixin:
    """用户数据管理 WebUI 后端。"""

    def register_web_api_routes(self):
        prefix = "/astrbot_plugin_running_rank"
        routes = [
            ("GET", f"{prefix}/overview", self._api_overview, "数据概览"),
            ("GET", f"{prefix}/groups", self._api_groups, "群列表"),
            ("GET", f"{prefix}/suggest", self._api_suggest_users, "昵称联想用户"),
            ("GET", f"{prefix}/newbie_export", self._api_export_newbie_report, "新手任务批量导出"),
            ("GET", f"{prefix}/import_template", self._api_import_template, "导入模板下载"),
            ("POST", f"{prefix}/import", self._api_import_csv, "批量导入"),

            ("GET", f"{prefix}/running_records", self._api_list_running_records, "跑步记录列表"),
            ("POST", f"{prefix}/running_records/create", self._api_create_running_record, "新增跑步记录"),
            ("POST", f"{prefix}/running_records/update", self._api_update_running_record, "修改跑步记录"),
            ("POST", f"{prefix}/running_records/delete", self._api_delete_running_record, "删除跑步记录"),

            ("GET", f"{prefix}/newbie_users", self._api_list_newbie_users, "新手用户列表"),
            ("POST", f"{prefix}/newbie_users/create", self._api_create_newbie_user, "新增新手用户"),
            ("POST", f"{prefix}/newbie_users/update", self._api_update_newbie_user, "修改新手用户"),
            ("POST", f"{prefix}/newbie_users/delete", self._api_delete_newbie_user, "删除新手用户"),

            ("GET", f"{prefix}/newbie_running_records", self._api_list_newbie_running_records, "新手跑步列表"),
            ("POST", f"{prefix}/newbie_running_records/create", self._api_create_newbie_running_record, "新增新手跑步"),
            ("POST", f"{prefix}/newbie_running_records/update", self._api_update_newbie_running_record, "修改新手跑步"),
            ("POST", f"{prefix}/newbie_running_records/delete", self._api_delete_newbie_running_record, "删除新手跑步"),

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

    async def _api_suggest_users(self):
        """按昵称模糊联想出 (QQ, 群号)，用于新增表单自动补全。"""
        keyword = _str(request.query.get("keyword"))
        if not keyword:
            return json_response({"status": "ok", "data": []})
        kw = f"%{keyword}%"
        results = {}

        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT user_id, user_name, group_id
            FROM running_records
            WHERE user_name LIKE ?
            ORDER BY id DESC
            """,
            (kw,),
        )
        for user_id, user_name, group_id in cursor.fetchall():
            key = (str(group_id), str(user_id))
            if key not in results:
                results[key] = {
                    "user_id": str(user_id),
                    "user_name": str(user_name),
                    "group_id": str(group_id),
                }
        conn.close()

        nb = self.get_newbie_conn()
        nc = nb.cursor()
        nc.execute(
            """
            SELECT user_id, nickname, group_id
            FROM newbie_users
            WHERE nickname LIKE ?
            ORDER BY rowid DESC
            """,
            (kw,),
        )
        for row in nc.fetchall():
            key = (str(row["group_id"]), str(row["user_id"]))
            if key not in results:
                results[key] = {
                    "user_id": str(row["user_id"]),
                    "user_name": str(row["nickname"] or row["user_id"]),
                    "group_id": str(row["group_id"]),
                }
        nb.close()

        return json_response({"status": "ok", "data": list(results.values())[:20]})

    async def _api_export_newbie_report(self):
        group_id = _str(request.query.get("group_id")) or None
        start_iso = _normalize_datetime(request.query.get("from")) or None
        end_iso = _normalize_datetime(request.query.get("to")) or None

        report = self.export_newbie_report(
            group_id=group_id,
            start_iso=start_iso,
            end_iso=end_iso,
        )
        report["filename"] = f"新手任务导出_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return json_response({"status": "ok", "data": report})

    # =============================================================
    # 一键导入
    # =============================================================

    def _import_template_csv(self, table):
        spec = IMPORT_SPECS.get(table)
        if not spec:
            return None
        header = [c["label"] for c in spec["columns"]]
        example = [c["example"] for c in spec["columns"]]
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(header)
        writer.writerow(example)
        return "﻿" + buf.getvalue()

    def _insert_import_row(self, cursor, spec, row, now):
        cols = spec["columns"]
        missing = [c["label"] for c in cols if c["required"] and not row.get(c["key"])]
        if missing:
            return "缺少必填项: " + ", ".join(missing)

        values = []
        for c in cols:
            key = c["key"]
            raw = (row.get(key) or "").strip()
            if raw == "":
                val = _import_default(c["default"], now)
            else:
                try:
                    if c["type"] == "int":
                        val = int(raw)
                    elif c["type"] == "float":
                        val = float(raw)
                    elif c["type"] == "datetime":
                        val = _normalize_datetime(raw)
                    else:
                        val = raw
                    if key == "gender":
                        val = {"男": "male", "女": "female"}.get(val, val)
                except (TypeError, ValueError):
                    return f"{c['label']} 格式错误: {raw}"
            values.append(val)

        col_list = ", ".join(c["key"] for c in cols)
        qmarks = ", ".join("?" for _ in cols)
        try:
            cursor.execute(
                f"INSERT INTO {spec['table']} ({col_list}) VALUES ({qmarks})",
                values,
            )
        except sqlite3.IntegrityError:
            return "数据已存在（主键/唯一键重复）"
        except Exception as e:
            return f"插入失败: {e}"
        return values

    def _mirror_newbie_running_to_main(self, rows):
        """把导入/新增的新手跑步记录镜像到主排行榜 running_records（复刻 confirm_newbie_running 双写）。"""
        if not rows:
            return

        nicknames = {}
        nb = self.get_newbie_conn()
        nc = nb.cursor()
        for r in rows:
            key = (r["group_id"], r["semester"], r["user_id"])
            if key in nicknames:
                continue
            nc.execute(
                "SELECT nickname FROM newbie_users WHERE group_id = ? AND semester = ? AND user_id = ?",
                key,
            )
            row = nc.fetchone()
            nicknames[key] = (row["nickname"] if row and row["nickname"] else r["user_id"])
        nb.close()

        conn = self.get_conn()
        cursor = conn.cursor()
        for r in rows:
            cursor.execute(
                """
                INSERT INTO running_records (user_id, user_name, group_id, distance, run_time, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    r["user_id"],
                    nicknames[(r["group_id"], r["semester"], r["user_id"])],
                    r["group_id"],
                    r["distance"],
                    r["created_at"],
                    r["created_at"],
                ),
            )
        conn.commit()
        conn.close()

    def _import_table(self, table, csv_text):
        spec = IMPORT_SPECS.get(table)
        if not spec:
            return {"success": 0, "failed": 1, "errors": [{"line": 0, "reason": f"未知表: {table}"}]}

        label_to_key = {c["label"]: c["key"] for c in spec["columns"]}
        key_set = {c["key"] for c in spec["columns"]}

        text = (csv_text or "").lstrip("﻿")
        lines = list(csv.reader(io.StringIO(text)))
        if not lines:
            return {"success": 0, "failed": 0, "errors": []}

        header = [str(h).strip() for h in lines[0]]
        col_keys = []
        for h in header:
            if h in label_to_key:
                col_keys.append(label_to_key[h])
            elif h in key_set:
                col_keys.append(h)
            else:
                col_keys.append(None)

        conn = self.get_conn() if spec["db"] == "running" else self.get_newbie_conn()
        cursor = conn.cursor()
        now = datetime.now()

        success = 0
        failed = 0
        errors = []
        recompute = {}  # (group_id, semester, user_id, year, week) -> 参考时间
        mirror_rows = []  # 新手跑步导入后需要镜像到主排行榜的行
        for idx, raw in enumerate(lines[1:], start=2):
            if not raw or all(str(c).strip() == "" for c in raw):
                continue
            row = {}
            for ci, key in enumerate(col_keys):
                if key is None:
                    continue
                row[key] = str(raw[ci]).strip() if ci < len(raw) else ""

            result = self._insert_import_row(cursor, spec, row, now)
            if isinstance(result, str):
                failed += 1
                errors.append({"line": idx, "reason": result})
                continue

            success += 1
            if spec["table"] == "newbie_running_records":
                vmap = {c["key"]: v for c, v in zip(spec["columns"], result)}
                ref = _parse_ref_time(vmap.get("created_at"), now)
                semester = _str(vmap.get("semester")) or "2026_fall"
                y, w, _ = ref.isocalendar()
                key = (_str(vmap.get("group_id")), semester, _str(vmap.get("user_id")), y, w)
                if key not in recompute or ref > recompute[key]:
                    recompute[key] = ref
                mirror_rows.append({
                    "group_id": _str(vmap.get("group_id")),
                    "semester": semester,
                    "user_id": _str(vmap.get("user_id")),
                    "distance": vmap.get("distance"),
                    "created_at": vmap.get("created_at"),
                })

        conn.commit()
        conn.close()

        # 复刻 confirm_newbie_running 的双写：镜像到主排行榜
        if mirror_rows:
            self._mirror_newbie_running_to_main(mirror_rows)

        # 导入新手跑步记录后，按 confirm_newbie_running 同款规则重算并更新积分
        for (group_id, semester, user_id, _y, _w), ref in recompute.items():
            self.recompute_week_points(group_id, semester, user_id, ref)

        return {"success": success, "failed": failed, "errors": errors}

    async def _api_import_template(self):
        table = _str(request.query.get("table"))
        csv_text = self._import_template_csv(table)
        if csv_text is None:
            return json_response({"status": "error", "message": f"未知表: {table}"})
        filename = f"{IMPORT_SPECS[table]['label']}_导入模板.csv"
        return json_response({"status": "ok", "data": {"csv": csv_text, "filename": filename}})

    async def _api_import_csv(self):
        body = await _read_body()
        table = _str(body.get("table"))
        csv_text = body.get("csv_text") or body.get("csv") or ""
        result = self._import_table(table, csv_text)
        return json_response({"status": "ok", "data": result})

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

        run_time = _normalize_datetime(body.get("run_time"))
        created_at = _normalize_datetime(body.get("created_at")) or _now_iso()
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
                run_time,
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
                _normalize_datetime(body.get("run_time")),
                _normalize_datetime(body.get("created_at")),
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
        required = ["group_id", "user_id", "gender"]
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
                _str(body.get("joined_at")) or _now_iso(),
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
    # 新手跑步 newbie_running_records（newbie_points.db）
    # =============================================================

    async def _api_list_newbie_running_records(self):
        total, rows = self._query_newbie_list("newbie_running_records", ["user_id"])
        return json_response({"status": "ok", "data": {"total": total, "rows": rows}})

    async def _api_create_newbie_running_record(self):
        body = await _read_body()
        required = ["group_id", "user_id", "distance"]
        missing = [k for k in required if not _has(body, k)]
        if missing:
            return json_response({"status": "error", "message": f"缺少字段: {', '.join(missing)}"})

        group_id = _str(body["group_id"])
        semester = _str(body.get("semester")) or "2026_fall"
        user_id = _str(body["user_id"])
        distance = _float(body["distance"])
        created_at = _normalize_datetime(body.get("created_at")) or _now_iso()

        nb = self.get_newbie_conn()
        cursor = nb.cursor()
        cursor.execute(
            "INSERT INTO newbie_running_records (group_id, semester, user_id, distance, created_at) VALUES (?, ?, ?, ?, ?)",
            (group_id, semester, user_id, distance, created_at),
        )
        nb.commit()
        new_id = cursor.lastrowid
        nb.close()

        self._mirror_newbie_running_to_main([
            {"group_id": group_id, "semester": semester, "user_id": user_id, "distance": distance, "created_at": created_at},
        ])
        self.recompute_week_points(group_id, semester, user_id, _parse_ref_time(created_at, datetime.now()))

        return json_response({"status": "ok", "data": {"id": new_id}})

    async def _api_update_newbie_running_record(self):
        body = await _read_body()
        record_id = _int(body.get("id"))
        if not record_id:
            return json_response({"status": "error", "message": "缺少记录 id"})

        nb = self.get_newbie_conn()
        cursor = nb.cursor()
        cursor.execute(
            "SELECT group_id, semester, user_id, created_at FROM newbie_running_records WHERE id = ?",
            (record_id,),
        )
        old = cursor.fetchone()
        if not old:
            nb.close()
            return json_response({"status": "error", "message": "记录不存在"})

        group_id = _str(body.get("group_id"))
        semester = _str(body.get("semester")) or "2026_fall"
        user_id = _str(body.get("user_id"))
        distance = _float(body.get("distance"))
        created_at = _normalize_datetime(body.get("created_at")) or _now_iso()

        cursor.execute(
            "UPDATE newbie_running_records SET group_id = ?, semester = ?, user_id = ?, distance = ?, created_at = ? WHERE id = ?",
            (group_id, semester, user_id, distance, created_at, record_id),
        )
        nb.commit()
        nb.close()

        # 时间或归属可能变了：重算旧周与新周
        now = datetime.now()
        recompute = {}
        for g, s, u, ref in [
            (old["group_id"], old["semester"], old["user_id"], _parse_ref_time(old["created_at"], now)),
            (group_id, semester, user_id, _parse_ref_time(created_at, now)),
        ]:
            y, w, _ = ref.isocalendar()
            key = (g, s, u, y, w)
            if key not in recompute or ref > recompute[key]:
                recompute[key] = (g, s, u, ref)
        for g, s, u, ref in recompute.values():
            self.recompute_week_points(g, s, u, ref)

        return json_response({"status": "ok", "data": {"id": record_id}})

    async def _api_delete_newbie_running_record(self):
        body = await _read_body()
        record_id = _int(body.get("id"))
        if not record_id:
            return json_response({"status": "error", "message": "缺少记录 id"})

        nb = self.get_newbie_conn()
        cursor = nb.cursor()
        cursor.execute(
            "SELECT group_id, semester, user_id, distance, created_at FROM newbie_running_records WHERE id = ?",
            (record_id,),
        )
        old = cursor.fetchone()
        if not old:
            nb.close()
            return json_response({"status": "error", "message": "记录不存在"})

        cursor.execute("DELETE FROM newbie_running_records WHERE id = ?", (record_id,))
        nb.commit()
        nb.close()

        # 同步删除主排行榜镜像（与撤销逻辑一致）
        main_conn = self.get_conn()
        main_cursor = main_conn.cursor()
        main_cursor.execute(
            "DELETE FROM running_records WHERE group_id = ? AND user_id = ? AND distance = ? AND created_at = ?",
            (old["group_id"], old["user_id"], float(old["distance"]), old["created_at"]),
        )
        main_conn.commit()
        main_conn.close()

        self.recompute_week_points(old["group_id"], old["semester"], old["user_id"], _parse_ref_time(old["created_at"], datetime.now()))

        return json_response({"status": "ok", "data": {"id": record_id}})

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
                _str(body.get("created_at")) or _now_iso(),
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
                _str(body.get("admin_id")) or "3123366945",
                _str(body.get("created_at")) or _now_iso(),
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
                _str(body.get("created_at")) or _now_iso(),
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
            (_str(body.get("created_at")) or _now_iso(), group_id, user_id),
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
