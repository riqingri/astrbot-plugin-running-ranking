import sqlite3


class DatabaseMixin:
    """数据库初始化与连接。"""

    def init_database(self):

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

    def get_conn(self):

        return sqlite3.connect(
            self.db_path
        )

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

        # ---------------------------------------------------------
        # openid ↔ QQ 号 映射
        # qq_official 平台拿到的是 openid，旧数据库（OneBot）存的是 QQ 号，
        # 这里用一张表把两者联系起来，查询/写库时统一解析回 QQ 号。
        # ---------------------------------------------------------

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS qq_openid_map (
            openid TEXT PRIMARY KEY,
            qq_id TEXT NOT NULL,
            created_at TEXT NOT NULL
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

        conn = sqlite3.connect(
            self.newbie_db_path
        )
        conn.row_factory = sqlite3.Row
        return conn
