(async function () {
  const bridge = window.AstrBotPluginPage;
  if (!bridge) {
    document.body.innerHTML = '<p style="padding:24px">未检测到 AstrBot 插件页面桥接。</p>';
    return;
  }

  const TABLES = {
    running_records: {
      label: "跑步记录",
      endpoint: "running_records",
      pkFields: ["id"],
      autoFields: ["id"],
      columns: [
        { key: "id", label: "ID" },
        { key: "user_id", label: "用户 QQ" },
        { key: "user_name", label: "昵称" },
        { key: "group_id", label: "群号" },
        { key: "distance", label: "距离(km)", type: "number" },
        { key: "run_time", label: "跑步时间" },
        { key: "created_at", label: "录入时间" },
      ],
    },
    newbie_users: {
      label: "新手用户",
      endpoint: "newbie_users",
      pkFields: ["group_id", "semester", "user_id"],
      autoFields: [],
      columns: [
        { key: "group_id", label: "群号" },
        { key: "semester", label: "学期" },
        { key: "user_id", label: "用户 QQ" },
        { key: "nickname", label: "昵称" },
        { key: "gender", label: "性别" },
        { key: "joined_at", label: "加入时间" },
        { key: "points_started", label: "开始积分", type: "number" },
        { key: "points_started_at", label: "积分开始时间" },
      ],
    },
    newbie_running_points: {
      label: "积分",
      endpoint: "newbie_running_points",
      pkFields: ["id"],
      autoFields: ["id"],
      columns: [
        { key: "id", label: "ID" },
        { key: "group_id", label: "群号" },
        { key: "semester", label: "学期" },
        { key: "user_id", label: "用户 QQ" },
        { key: "year", label: "年", type: "number" },
        { key: "week", label: "周", type: "number" },
        { key: "month", label: "月", type: "number" },
        { key: "points", label: "积分", type: "number" },
        { key: "created_at", label: "时间" },
      ],
    },
    newbie_training_records: {
      label: "训练",
      endpoint: "newbie_training_records",
      pkFields: ["id"],
      autoFields: ["id"],
      columns: [
        { key: "id", label: "ID" },
        { key: "group_id", label: "群号" },
        { key: "semester", label: "学期" },
        { key: "user_id", label: "用户 QQ" },
        { key: "admin_id", label: "管理员 QQ" },
        { key: "created_at", label: "时间" },
      ],
    },
    newbies_admins: {
      label: "管理员",
      endpoint: "newbies_admins",
      pkFields: ["group_id", "user_id"],
      autoFields: [],
      columns: [
        { key: "group_id", label: "群号" },
        { key: "user_id", label: "管理员 QQ" },
        { key: "created_at", label: "设置时间" },
      ],
    },
  };

  const state = {
    table: "running_records",
    rows: [],
    total: 0,
    limit: 50,
    offset: 0,
    groups: [],
    editing: null, // null | { mode: "create" } | { mode: "edit", row }
  };

  const $ = (id) => document.getElementById(id);
  const els = {
    tabs: $("tabs"),
    overview: $("overview"),
    groupFilter: $("groupFilter"),
    keyword: $("keyword"),
    searchBtn: $("searchBtn"),
    resetBtn: $("resetBtn"),
    createBtn: $("createBtn"),
    thead: $("thead"),
    tbody: $("tbody"),
    empty: $("empty"),
    pageInfo: $("pageInfo"),
    prevBtn: $("prevBtn"),
    nextBtn: $("nextBtn"),
    modal: $("modal"),
    modalTitle: $("modalTitle"),
    modalForm: $("modalForm"),
    modalClose: $("modalClose"),
    modalCancel: $("modalCancel"),
    modalSave: $("modalSave"),
    toast: $("toast"),
  };

  let toastTimer = null;
  function toast(msg) {
    els.toast.textContent = msg;
    els.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (els.toast.hidden = true), 2500);
  }

  // 尽量跟随 Dashboard 主题
  function applyTheme() {
    let dark = false;
    try {
      if (bridge.getContext && typeof bridge.getContext === "function") {
        const ctx = bridge.getContext();
        if (ctx && typeof ctx.isDark === "boolean") dark = ctx.isDark;
      }
    } catch (e) {}
    if (!dark && window.matchMedia) {
      dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    }
    document.documentElement.dataset.theme = dark ? "dark" : "light";
  }

  function config() {
    return TABLES[state.table];
  }

  function renderTabs() {
    els.tabs.innerHTML = "";
    Object.entries(TABLES).forEach(([key, t]) => {
      const btn = document.createElement("button");
      btn.className = "tab" + (key === state.table ? " active" : "");
      btn.textContent = t.label;
      btn.onclick = () => {
        state.table = key;
        state.offset = 0;
        renderTabs();
        load();
      };
      els.tabs.appendChild(btn);
    });
  }

  function renderThead() {
    const c = config();
    const tr = document.createElement("tr");
    c.columns.forEach((col) => {
      const th = document.createElement("th");
      th.textContent = col.label;
      tr.appendChild(th);
    });
    const th = document.createElement("th");
    th.textContent = "操作";
    tr.appendChild(th);
    els.thead.innerHTML = "";
    els.thead.appendChild(tr);
  }

  function cellText(col, row) {
    const v = row[col.key];
    if (v === null || v === undefined || v === "") return "—";
    return String(v);
  }

  function renderBody() {
    const c = config();
    els.tbody.innerHTML = "";
    els.empty.hidden = state.rows.length > 0;

    state.rows.forEach((row) => {
      const tr = document.createElement("tr");
      c.columns.forEach((col) => {
        const td = document.createElement("td");
        td.textContent = cellText(col, row);
        td.title = cellText(col, row);
        tr.appendChild(td);
      });

      const actions = document.createElement("td");
      actions.className = "row-actions";
      const editBtn = document.createElement("button");
      editBtn.className = "btn";
      editBtn.textContent = "编辑";
      editBtn.onclick = () => openModal("edit", row);
      const delBtn = document.createElement("button");
      delBtn.className = "btn danger";
      delBtn.textContent = "删除";
      delBtn.onclick = () => remove(row);
      actions.appendChild(editBtn);
      actions.appendChild(delBtn);
      tr.appendChild(actions);
      els.tbody.appendChild(tr);
    });

    renderPager();
  }

  function renderPager() {
    const totalPages = Math.max(1, Math.ceil(state.total / state.limit));
    const current = Math.floor(state.offset / state.limit) + 1;
    els.pageInfo.textContent = `共 ${state.total} 条 · 第 ${current}/${totalPages} 页`;
    els.prevBtn.disabled = state.offset <= 0;
    els.nextBtn.disabled = state.offset + state.limit >= state.total;
  }

  async function load() {
    const c = config();
    const params = { limit: state.limit, offset: state.offset };
    const gid = els.groupFilter.value;
    const kw = els.keyword.value.trim();
    if (gid) params.group_id = gid;
    if (kw) params.keyword = kw;

    try {
      const data = await bridge.apiGet(c.endpoint, params);
      state.rows = (data && data.rows) || [];
      state.total = (data && data.total) || 0;
      renderBody();
    } catch (e) {
      toast("加载失败：" + (e && e.message ? e.message : e));
    }
  }

  async function loadGroups() {
    try {
      state.groups = (await bridge.apiGet("groups", {})) || [];
      els.groupFilter.innerHTML = '<option value="">全部群</option>';
      state.groups.forEach((g) => {
        const opt = document.createElement("option");
        opt.value = g;
        opt.textContent = g;
        els.groupFilter.appendChild(opt);
      });
    } catch (e) {
      /* 群列表加载失败不影响主流程 */
    }
  }

  async function loadOverview() {
    try {
      const d = await bridge.apiGet("overview", {});
      const rr = d.running_records || {};
      els.overview.textContent =
        `跑步 ${rr.count ?? 0} 条 / ${rr.total_distance ?? 0} km` +
        ` · 新手用户 ${d.newbie_users ?? 0}` +
        ` · 积分 ${d.newbie_running_points ?? 0}` +
        ` · 训练 ${d.newbie_training_records ?? 0}` +
        ` · 管理员 ${d.newbies_admins ?? 0}`;
    } catch (e) {
      els.overview.textContent = "";
    }
  }

  function openModal(mode, row) {
    state.editing = row ? { mode, row } : { mode };
    const c = config();
    const isCreate = mode === "create";
    els.modalTitle.textContent = (isCreate ? "新增" : "编辑") + c.label;

    els.modalForm.innerHTML = "";
    c.columns.forEach((col) => {
      const isAuto = c.autoFields.includes(col.key);
      const isPk = c.pkFields.includes(col.key);
      if (isCreate && isAuto) return; // 自动 id 不显示

      const readonly = isAuto || (!isCreate && isPk);
      const wrap = document.createElement("div");
      wrap.className = "field";
      const label = document.createElement("label");
      label.textContent = col.label + (readonly ? "（不可改）" : "");
      const input = document.createElement("input");
      input.type = col.type === "number" ? "number" : "text";
      input.dataset.key = col.key;
      input.readOnly = readonly;
      if (row && row[col.key] !== null && row[col.key] !== undefined) {
        input.value = row[col.key];
      } else if (isCreate && col.type !== "number") {
        input.placeholder = "选填";
      }
      wrap.appendChild(label);
      wrap.appendChild(input);
      els.modalForm.appendChild(wrap);
    });

    els.modal.hidden = false;
  }

  function closeModal() {
    els.modal.hidden = true;
    state.editing = null;
  }

  function collectForm() {
    const c = config();
    const body = {};
    const inputs = els.modalForm.querySelectorAll("input");
    inputs.forEach((input) => {
      const key = input.dataset.key;
      const col = c.columns.find((x) => x.key === key);
      let value = input.value;
      if (col && col.type === "number") {
        value = value === "" ? "" : Number(value);
      }
      body[key] = value;
    });
    return body;
  }

  async function save() {
    if (!state.editing) {
      return;
    }
    const c = config();
    const body = collectForm();
    const isCreate = state.editing.mode === "create";

    try {
      if (isCreate) {
        await bridge.apiPost(`${c.endpoint}/create`, body);
      } else {
        await bridge.apiPost(`${c.endpoint}/update`, body);
      }
      toast(isCreate ? "已新增" : "已保存");
      closeModal();
      load();
      loadOverview();
    } catch (e) {
      toast("保存失败：" + (e && e.message ? e.message : e));
    }
  }

  async function remove(row) {
    const c = config();
    const key = c.pkFields.map((f) => `${f}: ${row[f]}`).join(", ");
    if (!window.confirm(`确认删除这条${c.label}？\n${key}`)) return;

    const body = {};
    c.pkFields.forEach((f) => (body[f] = row[f]));
    if (c.pkFields.includes("semester") && !body.semester) body.semester = "2026_fall";

    try {
      await bridge.apiPost(`${c.endpoint}/delete`, body);
      toast("已删除");
      load();
      loadOverview();
    } catch (e) {
      toast("删除失败：" + (e && e.message ? e.message : e));
    }
  }

  function bindEvents() {
    els.searchBtn.onclick = () => {
      state.offset = 0;
      load();
    };
    els.resetBtn.onclick = () => {
      els.keyword.value = "";
      els.groupFilter.value = "";
      state.offset = 0;
      load();
    };
    els.keyword.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        state.offset = 0;
        load();
      }
    });
    els.createBtn.onclick = () => openModal("create", null);
    els.prevBtn.onclick = () => {
      state.offset = Math.max(0, state.offset - state.limit);
      load();
    };
    els.nextBtn.onclick = () => {
      state.offset = state.offset + state.limit;
      load();
    };
    els.modalClose.onclick = closeModal;
    els.modalCancel.onclick = closeModal;
    els.modalSave.onclick = save;
    els.modal.addEventListener("click", (e) => {
      if (e.target === els.modal) closeModal();
    });
  }

  await bridge.ready();
  applyTheme();
  renderTabs();
  renderThead();
  bindEvents();
  await Promise.all([loadGroups(), loadOverview(), load()]);
})();
