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
      requiredFields: ["user_id", "user_name", "group_id", "distance", "run_time"],
      columns: [
        { key: "id", label: "ID" },
        { key: "user_id", label: "用户 QQ" },
        { key: "user_name", label: "昵称", autofill: ["user_id", "group_id"] },
        { key: "group_id", label: "群号" },
        { key: "distance", label: "距离(km)", type: "number" },
        { key: "run_time", label: "跑步时间", type: "datetime" },
        { key: "created_at", label: "录入时间", type: "datetime" },
      ],
    },
    newbie_users: {
      label: "新手用户",
      endpoint: "newbie_users",
      pkFields: ["group_id", "semester", "user_id"],
      autoFields: [],
      requiredFields: ["group_id", "user_id", "gender"],
      columns: [
        { key: "group_id", label: "群号" },
        { key: "semester", label: "学期", default: "2026_fall" },
        { key: "user_id", label: "用户 QQ" },
        { key: "nickname", label: "昵称", autofill: ["user_id", "group_id"] },
        { key: "gender", label: "性别", type: "select", options: [{ value: "male", label: "男" }, { value: "female", label: "女" }] },
        { key: "joined_at", label: "加入时间", type: "datetime" },
        { key: "points_started", label: "开始积分", type: "number" },
        { key: "points_started_at", label: "积分开始时间", type: "datetime" },
      ],
    },
    newbie_running_points: {
      label: "积分",
      endpoint: "newbie_running_points",
      pkFields: ["id"],
      autoFields: ["id"],
      requiredFields: ["group_id", "user_id", "year", "week", "points"],
      columns: [
        { key: "_nickname", label: "昵称", autofill: ["user_id", "group_id"], helper: true, placeholder: "输入昵称，自动填充 QQ 和群号" },
        { key: "id", label: "ID" },
        { key: "group_id", label: "群号" },
        { key: "semester", label: "学期", default: "2026_fall" },
        { key: "user_id", label: "用户 QQ" },
        { key: "year", label: "年", type: "number" },
        { key: "week", label: "周", type: "number" },
        { key: "month", label: "月", type: "number" },
        { key: "points", label: "积分", type: "number" },
        { key: "created_at", label: "时间", type: "datetime" },
      ],
    },
    newbie_training_records: {
      label: "训练",
      endpoint: "newbie_training_records",
      pkFields: ["id"],
      autoFields: ["id"],
      requiredFields: ["group_id", "user_id"],
      columns: [
        { key: "_nickname", label: "昵称", autofill: ["user_id", "group_id"], helper: true, placeholder: "输入昵称，自动填充 QQ 和群号" },
        { key: "id", label: "ID" },
        { key: "group_id", label: "群号" },
        { key: "semester", label: "学期", default: "2026_fall" },
        { key: "user_id", label: "用户 QQ" },
        { key: "admin_id", label: "管理员 QQ" },
        { key: "created_at", label: "时间", type: "datetime" },
      ],
    },
    newbies_admins: {
      label: "管理员",
      endpoint: "newbies_admins",
      pkFields: ["group_id", "user_id"],
      autoFields: [],
      requiredFields: ["group_id", "user_id"],
      columns: [
        { key: "_nickname", label: "昵称", autofill: ["user_id", "group_id"], helper: true, placeholder: "输入昵称，自动填充 QQ 和群号" },
        { key: "group_id", label: "群号" },
        { key: "user_id", label: "管理员 QQ" },
        { key: "created_at", label: "设置时间", type: "datetime" },
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
    exportCsv: "",
    exportFilename: "",
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
    exportBtn: $("exportBtn"),
    exportModal: $("exportModal"),
    exportClose: $("exportClose"),
    exportGroup: $("exportGroup"),
    exportFrom: $("exportFrom"),
    exportTo: $("exportTo"),
    exportRun: $("exportRun"),
    exportDownload: $("exportDownload"),
    exportInfo: $("exportInfo"),
    exportThead: $("exportThead"),
    exportBody: $("exportBody"),
    exportEmpty: $("exportEmpty"),
    confirmModal: $("confirmModal"),
    confirmText: $("confirmText"),
    confirmOk: $("confirmOk"),
    confirmCancel: $("confirmCancel"),
    importBtn: $("importBtn"),
    importModal: $("importModal"),
    importClose: $("importClose"),
    importTip: $("importTip"),
    importTemplateBtn: $("importTemplateBtn"),
    importFile: $("importFile"),
    importRun: $("importRun"),
    importInfo: $("importInfo"),
    importTableWrap: $("importTableWrap"),
    importBody: $("importBody"),
  };

  let toastTimer = null;
  function toast(msg) {
    els.toast.textContent = msg;
    els.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (els.toast.hidden = true), 2500);
  }

  // 沙箱 iframe 屏蔽了 window.confirm，改用 DOM 确认框
  let confirmResolve = null;
  function confirmDialog(message) {
    return new Promise((resolve) => {
      els.confirmText.textContent = message;
      els.confirmModal.hidden = false;
      confirmResolve = resolve;
    });
  }
  function resolveConfirm(value) {
    els.confirmModal.hidden = true;
    if (confirmResolve) {
      confirmResolve(value);
      confirmResolve = null;
    }
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
    c.columns.filter((col) => !col.helper).forEach((col) => {
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
    let s = String(v);
    if (col.type === "datetime") s = s.replace(/\.\d+$/, ""); // 去掉微秒尾数
    return s;
  }

  function renderBody() {
    const c = config();
    els.tbody.innerHTML = "";
    els.empty.hidden = state.rows.length > 0;

    state.rows.forEach((row) => {
      const tr = document.createElement("tr");
      c.columns.filter((col) => !col.helper).forEach((col) => {
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

  function openExport() {
    els.exportGroup.innerHTML = '<option value="">全部群</option>';
    state.groups.forEach((g) => {
      const opt = document.createElement("option");
      opt.value = g;
      opt.textContent = g;
      els.exportGroup.appendChild(opt);
    });
    els.exportFrom.value = "";
    els.exportTo.value = "";
    els.exportInfo.textContent = "";
    els.exportThead.innerHTML = "";
    els.exportBody.innerHTML = "";
    els.exportEmpty.hidden = false;
    els.exportEmpty.textContent = "暂无数据，先选择时间生成报表";
    els.exportDownload.disabled = true;
    state.exportCsv = "";
    state.exportFilename = "";
    els.exportModal.hidden = false;
  }

  function renderExport(data) {
    const columns = (data && data.columns) || [];
    const rows = (data && data.rows) || [];
    state.exportCsv = (data && data.csv) || "";
    state.exportFilename = (data && data.filename) || "新手任务导出.csv";

    els.exportInfo.textContent = `共 ${rows.length} 条`;
    els.exportThead.innerHTML = "";
    const tr = document.createElement("tr");
    columns.forEach((col) => {
      const th = document.createElement("th");
      th.textContent = col.label;
      tr.appendChild(th);
    });
    els.exportThead.appendChild(tr);

    els.exportBody.innerHTML = "";
    els.exportEmpty.hidden = rows.length > 0;
    els.exportEmpty.textContent = "该时间段内暂无数据";
    rows.forEach((row) => {
      const r = document.createElement("tr");
      columns.forEach((col) => {
        const td = document.createElement("td");
        const v = row[col.key];
        td.textContent = v === null || v === undefined || v === "" ? "—" : String(v);
        td.title = td.textContent;
        r.appendChild(td);
      });
      els.exportBody.appendChild(r);
    });

    els.exportDownload.disabled = rows.length === 0 || !state.exportCsv;
  }

  async function runExport() {
    const params = {};
    const gid = els.exportGroup.value;
    const fromVal = els.exportFrom.value;
    const toVal = els.exportTo.value;
    if (gid) params.group_id = gid;
    if (fromVal) params.from = fromVal;
    if (toVal) params.to = toVal;

    els.exportRun.disabled = true;
    try {
      const data = await bridge.apiGet("newbie_export", params);
      renderExport(data);
    } catch (e) {
      toast("导出失败：" + (e && e.message ? e.message : e));
    } finally {
      els.exportRun.disabled = false;
    }
  }

  function downloadTextFile(text, filename) {
    const blob = new Blob([text], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function downloadCsv() {
    if (!state.exportCsv) return;
    downloadTextFile(state.exportCsv, state.exportFilename);
  }

  function openImport() {
    const c = config();
    els.importTip.textContent = `当前表：${c.label}。先下载模板，按表头填写（每行一条），再上传导入。`;
    els.importFile.value = "";
    els.importInfo.textContent = "";
    els.importBody.innerHTML = "";
    els.importTableWrap.hidden = true;
    els.importModal.hidden = false;
  }

  async function downloadImportTemplate() {
    const c = config();
    try {
      const data = await bridge.apiGet("import_template", { table: c.endpoint });
      const csv = (data && data.csv) || "";
      const filename = (data && data.filename) || `${c.label}_导入模板.csv`;
      if (!csv) {
        toast("生成模板失败");
        return;
      }
      downloadTextFile(csv, filename);
    } catch (e) {
      toast("生成模板失败：" + (e && e.message ? e.message : e));
    }
  }

  async function runImport() {
    const c = config();
    const file = els.importFile.files && els.importFile.files[0];
    if (!file) {
      toast("请先选择 CSV 文件");
      return;
    }
    let text;
    try {
      text = await file.text();
    } catch (e) {
      toast("读取文件失败");
      return;
    }
    els.importRun.disabled = true;
    try {
      const data = await bridge.apiPost("import", { table: c.endpoint, csv_text: text });
      renderImport(data);
    } catch (e) {
      toast("导入失败：" + (e && e.message ? e.message : e));
    } finally {
      els.importRun.disabled = false;
    }
  }

  function renderImport(data) {
    const success = (data && data.success) || 0;
    const failed = (data && data.failed) || 0;
    const errors = (data && data.errors) || [];
    els.importInfo.textContent = `成功 ${success} 条 · 失败 ${failed} 条`;
    els.importBody.innerHTML = "";
    if (errors.length) {
      els.importTableWrap.hidden = false;
      errors.forEach((e) => {
        const tr = document.createElement("tr");
        const td1 = document.createElement("td");
        td1.textContent = e.line;
        const td2 = document.createElement("td");
        td2.textContent = e.reason;
        tr.appendChild(td1);
        tr.appendChild(td2);
        els.importBody.appendChild(tr);
      });
    } else {
      els.importTableWrap.hidden = true;
    }
    if (success > 0) {
      load();
      loadOverview();
    }
  }

  function toDatetimeLocal(iso) {
    if (!iso) return "";
    const m = String(iso).match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/);
    return m ? `${m[1]}T${m[2]}` : "";
  }

  function attachUserAutocomplete(input, autofillKeys) {
    let dropdown = null;
    let timer = null;
    let seq = 0;

    function hideDropdown() {
      if (dropdown) {
        dropdown.remove();
        dropdown = null;
      }
    }

    function applySuggestion(s) {
      input.value = s.user_name || "";
      autofillKeys.forEach((key) => {
        const el = els.modalForm.querySelector(`input[data-key="${key}"]`);
        if (el && s[key] != null) el.value = s[key];
      });
      hideDropdown();
    }

    input.addEventListener("input", () => {
      clearTimeout(timer);
      const kw = input.value.trim();
      const mySeq = ++seq;
      if (!kw) {
        hideDropdown();
        return;
      }
      timer = setTimeout(async () => {
        try {
          const list = (await bridge.apiGet("suggest", { keyword: kw })) || [];
          if (mySeq !== seq) return;
          hideDropdown();
          if (!list.length) return;
          dropdown = document.createElement("div");
          dropdown.className = "autofill";
          list.slice(0, 20).forEach((s) => {
            const item = document.createElement("div");
            item.className = "autofill-item";
            item.textContent = `${s.user_name}（QQ ${s.user_id} · 群 ${s.group_id}）`;
            item.onclick = () => applySuggestion(s);
            dropdown.appendChild(item);
          });
          input.parentElement.appendChild(dropdown);
        } catch (e) {
          /* 自动补全失败，静默忽略 */
        }
      }, 250);
    });

    input.addEventListener("blur", () => {
      setTimeout(hideDropdown, 150);
    });
  }

  function openModal(mode, row) {
    state.editing = row ? { mode, row } : { mode };
    const c = config();
    const isCreate = mode === "create";
    els.modalTitle.textContent = (isCreate ? "新增" : "编辑") + c.label;

    els.modalForm.innerHTML = "";
    c.columns.forEach((col) => {
      if (col.helper && !isCreate) return; // 辅助字段仅在新增时显示
      const isAuto = c.autoFields.includes(col.key);
      const isPk = c.pkFields.includes(col.key);
      if (isCreate && isAuto) return; // 自动 id 不显示

      const readonly = isAuto || (!isCreate && isPk);
      const required = (c.requiredFields || []).includes(col.key);
      const wrap = document.createElement("div");
      wrap.className = "field";
      const label = document.createElement("label");
      label.textContent = col.label + (readonly ? "（不可改）" : (isCreate && required ? " *" : ""));
      const el = col.type === "select" ? document.createElement("select") : document.createElement("input");
      el.dataset.key = col.key;

      if (col.type === "select") {
        (col.options || []).forEach((opt) => {
          const option = document.createElement("option");
          option.value = opt.value;
          option.textContent = opt.label;
          el.appendChild(option);
        });
        if (row && row[col.key] !== null && row[col.key] !== undefined) {
          el.value = row[col.key];
        }
        el.disabled = readonly;
      } else {
        if (col.type === "number") {
          el.type = "number";
        } else if (col.type === "datetime") {
          el.type = "datetime-local";
        } else {
          el.type = "text";
        }
        if (row && row[col.key] !== null && row[col.key] !== undefined) {
          el.value = col.type === "datetime" ? toDatetimeLocal(row[col.key]) : row[col.key];
        } else if (col.default !== undefined && col.default !== null) {
          el.value = col.default;
        } else if (col.placeholder) {
          el.placeholder = col.placeholder;
        } else if (isCreate && col.type !== "number" && !required) {
          el.placeholder = "选填";
        }
        el.readOnly = readonly;
      }
      wrap.appendChild(label);
      wrap.appendChild(el);
      els.modalForm.appendChild(wrap);
    });

    els.modalForm.querySelectorAll("input").forEach((input) => {
      const col = c.columns.find((x) => x.key === input.dataset.key);
      if (col && col.autofill) {
        attachUserAutocomplete(input, col.autofill);
      }
    });

    // 新增时：表里有多个时间字段时，填一个自动同步到其余空的时间字段
    if (isCreate) {
      const timeInputs = Array.from(els.modalForm.querySelectorAll('input[type="datetime-local"]'));
      timeInputs.forEach((input) => {
        input.addEventListener("change", () => {
          if (!input.value) return;
          timeInputs.forEach((other) => {
            if (other !== input && !other.value) other.value = input.value;
          });
        });
      });
    }

    els.modal.hidden = false;
  }

  function closeModal() {
    els.modal.hidden = true;
    state.editing = null;
  }

  function collectForm() {
    const c = config();
    const body = {};
    const inputs = els.modalForm.querySelectorAll("input, select");
    inputs.forEach((el) => {
      const key = el.dataset.key;
      const col = c.columns.find((x) => x.key === key);
      if (col && col.helper) return; // 辅助字段不提交
      let value = el.value;
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
    const ok = await confirmDialog(`确认删除这条${c.label}？\n${key}`);
    if (!ok) return;

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
    els.exportBtn.onclick = openExport;
    els.exportClose.onclick = () => {
      els.exportModal.hidden = true;
    };
    els.exportRun.onclick = runExport;
    els.exportDownload.onclick = downloadCsv;
    els.exportModal.addEventListener("click", (e) => {
      if (e.target === els.exportModal) els.exportModal.hidden = true;
    });
    els.confirmOk.onclick = () => resolveConfirm(true);
    els.confirmCancel.onclick = () => resolveConfirm(false);
    els.confirmModal.addEventListener("click", (e) => {
      if (e.target === els.confirmModal) resolveConfirm(false);
    });
    els.importBtn.onclick = openImport;
    els.importClose.onclick = () => {
      els.importModal.hidden = true;
    };
    els.importTemplateBtn.onclick = downloadImportTemplate;
    els.importRun.onclick = runImport;
    els.importModal.addEventListener("click", (e) => {
      if (e.target === els.importModal) els.importModal.hidden = true;
    });
  }

  await bridge.ready();
  applyTheme();
  renderTabs();
  renderThead();
  bindEvents();
  await Promise.all([loadGroups(), loadOverview(), load()]);
})();
