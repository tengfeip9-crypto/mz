const launchBtn = document.getElementById("launchBtn");
const authBadge = document.getElementById("authBadge");
const authCurrentUser = document.getElementById("authCurrentUser");
const authFormBlock = document.getElementById("authFormBlock");
const authCn = document.getElementById("authCn");
const authPassword = document.getElementById("authPassword");
const registerBtn = document.getElementById("registerBtn");
const loginBtn = document.getElementById("loginBtn");
const logoutBtn = document.getElementById("logoutBtn");
const authStatus = document.getElementById("authStatus");
const recordsSection = document.getElementById("recordsSection");
const recordsSummary = document.getElementById("recordsSummary");
const recordsPath = document.getElementById("recordsPath");
const recordsOutput = document.getElementById("recordsOutput");
const statusBadge = document.getElementById("statusBadge");
const statusUrl = document.getElementById("statusUrl");
const sessionMeta = document.getElementById("sessionMeta");
const sessionLink = document.getElementById("sessionLink");
const closeSessionBtn = document.getElementById("closeSessionBtn");
const statusText = document.getElementById("statusText");
const mappingSection = document.getElementById("mappingSection");
const successSection = document.getElementById("successSection");
const autoLikeSection = document.getElementById("autoLikeSection");
const startAutoLikeBtn = document.getElementById("startAutoLikeBtn");
const stopAutoLikeBtn = document.getElementById("stopAutoLikeBtn");
const autoLikeStatus = document.getElementById("autoLikeStatus");
const autoLikeLogPath = document.getElementById("autoLikeLogPath");
const autoLikeMaxBigRounds = document.getElementById("autoLikeMaxBigRounds");
const autoLikeMaxLikes = document.getElementById("autoLikeMaxLikes");
const autoLikeWaitSeconds = document.getElementById("autoLikeWaitSeconds");
const autoLikeSkipTasks = document.getElementById("autoLikeSkipTasks");
const consoleOutput = document.getElementById("consoleOutput");
const threadSummary = document.getElementById("threadSummary");
const panelMeta = document.getElementById("panelMeta");
const loginSummary = document.getElementById("loginSummary");
const loginPhaseBadge = document.getElementById("loginPhaseBadge");
const loginStepGrid = document.getElementById("loginStepGrid");
const loginModeValue = document.getElementById("loginModeValue");
const loginVerifyValue = document.getElementById("loginVerifyValue");
const loginPageValue = document.getElementById("loginPageValue");
const loginNextValue = document.getElementById("loginNextValue");
const loginAlert = document.getElementById("loginAlert");
const frameImage = document.getElementById("frameImage");
const qqLoginStatus = document.getElementById("qqLoginStatus");
const focusKeyboardBtn = document.getElementById("focusKeyboardBtn");
const keyboardState = document.getElementById("keyboardState");
const textBridge = document.getElementById("textBridge");

let currentState = null;
let authState = { loggedIn: false, cn: "" };
let userRecords = {};
let sessionId = readSessionId();
let lastFrameToken = -1;
let pointerActive = false;
let composing = false;
let refreshTimer = 0;
let stateRequestSeq = 0;
let lastAppliedStateSeq = 0;
let frameLoadSeq = 0;
let currentFrameObjectUrl = "";

async function apiFetch(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  if (response.status === 204) {
    return null;
  }

  const contentType = response.headers.get("content-type") || "";
  if (!response.ok) {
    if (contentType.includes("application/json")) {
      const payload = await response.json();
      const error = new Error(payload.error || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    const text = await response.text();
    const error = new Error(text || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }

  if (contentType.includes("application/json")) {
    return response.json();
  }
  return response.text();
}

function readSessionId() {
  const url = new URL(window.location.href);
  return url.searchParams.get("session_id") || "";
}

function writeSessionId(nextSessionId) {
  sessionId = nextSessionId || "";
  const url = new URL(window.location.href);
  if (sessionId) {
    url.searchParams.set("session_id", sessionId);
  } else {
    url.searchParams.delete("session_id");
  }
  window.history.replaceState({}, "", url);
}

function buildSessionUrl(nextSessionId) {
  const url = new URL(window.location.href);
  if (nextSessionId) {
    url.searchParams.set("session_id", nextSessionId);
  } else {
    url.searchParams.delete("session_id");
  }
  return url.toString();
}

function setBadge(status) {
  statusBadge.className = `badge badge-${status}`;

  if (status === "idle") {
    statusBadge.textContent = "等待唤起";
  } else if (status === "starting") {
    statusBadge.textContent = "正在启动";
  } else if (status === "login_required") {
    statusBadge.textContent = "等待登录";
  } else if (status === "logged_in") {
    statusBadge.textContent = "登录成功";
  } else if (status === "expired") {
    statusBadge.textContent = "会话过期";
  } else if (status === "error") {
    statusBadge.textContent = "启动失败";
  } else {
    statusBadge.textContent = "状态未知";
  }
}

function setVisible(element, visible) {
  element.classList.toggle("hidden", !visible);
}

function renderLoginSteps(steps) {
  loginStepGrid.innerHTML = steps
    .map(
      (step, index) => `
        <article class="login-step-card" data-step-state="${step.state}">
          <span class="login-step-index">${index + 1}</span>
          <div class="login-step-title">${step.title}</div>
          <div class="login-step-copy">${step.copy}</div>
        </article>
      `
    )
    .join("");
}

function setKeyboardState(message) {
  keyboardState.textContent = message;
}

function describeVerifyKind(kind) {
  if (kind === "robot") {
    return "机器人验证";
  }
  if (kind === "phone_code") {
    return "手机或短信验证";
  }
  if (kind === "image_code") {
    return "图形验证码";
  }
  return "验证";
}

function queueRefresh(delay = 90) {
  if (refreshTimer) {
    window.clearTimeout(refreshTimer);
  }
  refreshTimer = window.setTimeout(() => {
    refreshTimer = 0;
    refreshState();
  }, delay);
}

function beginStateRequest() {
  stateRequestSeq += 1;
  return stateRequestSeq;
}

function applyStatePayload(state, requestSeq) {
  if (!state || requestSeq < lastAppliedStateSeq) {
    return;
  }
  lastAppliedStateSeq = requestSeq;
  if (state.sessionId && state.sessionId !== sessionId) {
    writeSessionId(state.sessionId);
  }
  updateUI(state);
}

async function updateFrameImage(resolvedSessionId, frameToken) {
  if (!resolvedSessionId) {
    return;
  }

  const loadSeq = ++frameLoadSeq;
  const response = await fetch(
    `/api/frame.png?session_id=${encodeURIComponent(resolvedSessionId)}&token=${frameToken}&t=${Date.now()}`,
    {
      cache: "no-store",
      credentials: "same-origin",
    }
  );

  if (response.status === 204) {
    return;
  }

  if (!response.ok) {
    throw new Error(`画面获取失败：HTTP ${response.status}`);
  }

  const blob = await response.blob();
  if (loadSeq !== frameLoadSeq) {
    return;
  }

  const nextObjectUrl = URL.createObjectURL(blob);
  const previousObjectUrl = currentFrameObjectUrl;
  currentFrameObjectUrl = nextObjectUrl;
  frameImage.src = nextObjectUrl;
  if (previousObjectUrl) {
    URL.revokeObjectURL(previousObjectUrl);
  }
}

function formatTime(timestamp) {
  if (!timestamp) {
    return "暂无";
  }
  try {
    return new Date(timestamp * 1000).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return "暂无";
  }
}

function renderUserRecords() {
  const loggedIn = Boolean(authState.loggedIn);
  setVisible(recordsSection, loggedIn);
  if (!loggedIn) {
    recordsSummary.textContent = "当前还没有可显示的用户记录。";
    recordsPath.textContent = "";
    recordsOutput.textContent = "等待登录网站账号。";
    return;
  }

  const sessions = Array.isArray(userRecords.recentSessions) ? userRecords.recentSessions : [];
  recordsSummary.textContent =
    `独立存储已分配给 ${authState.cn}。累计访问 ${userRecords.visitCount || 0} 次，最近一次网站登录时间：${formatTime(userRecords.lastWebsiteLoginAt)}。`;
  recordsPath.textContent = userRecords.storageRoot
    ? `存储目录：${userRecords.storageRoot}`
    : "还没有生成存储目录。";

  const lines = [
    `账号 CN: ${authState.cn}`,
    `存储目录: ${userRecords.storageRoot || "暂无"}`,
    `浏览器目录: ${userRecords.browserSessionRoot || "暂无"}`,
    `创建时间: ${formatTime(userRecords.createdAt)}`,
    `累计访问次数: ${userRecords.visitCount || 0}`,
    `最近网站登录: ${formatTime(userRecords.lastWebsiteLoginAt)}`,
    `最近网站退出: ${formatTime(userRecords.lastWebsiteLogoutAt)}`,
    `最近提交的 QQ 号: ${userRecords.lastQqNumber || "暂无"}`,
    `最近 QQ 提交时间: ${formatTime(userRecords.lastQqLoginAt)}`,
    `最近使用的登录会话: ${userRecords.lastLoginSessionId || "暂无"}`,
    "",
    "最近会话:",
  ];

  if (sessions.length === 0) {
    lines.push("暂无会话记录。");
  } else {
    sessions
      .slice()
      .reverse()
      .forEach((item) => {
        lines.push(
          `- ${item.sessionId || "unknown"} | ${item.sessionName || "unnamed"} | ${item.status || "unknown"} | ${formatTime(item.updatedAt || item.createdAt)}`
        );
      });
  }

  recordsOutput.textContent = lines.join("\n");
}

function updateAuthUI() {
  const loggedIn = Boolean(authState.loggedIn);
  authBadge.className = `badge ${loggedIn ? "badge-logged_in" : "badge-idle"}`;
  authBadge.textContent = loggedIn ? "已登录" : "未登录";

  if (loggedIn) {
    authCurrentUser.textContent = `当前网站账号：${authState.cn}`;
    authStatus.textContent = `已登录网站账号：${authState.cn}`;
  } else {
    authCurrentUser.textContent = "";
    authStatus.textContent = "请先注册或登录网站账号。";
  }

  setVisible(authCurrentUser, loggedIn);
  setVisible(authFormBlock, !loggedIn);
  setVisible(registerBtn, !loggedIn);
  setVisible(loginBtn, !loggedIn);
  setVisible(logoutBtn, loggedIn);
  renderUserRecords();

  if (!loggedIn) {
    launchBtn.disabled = true;
    launchBtn.textContent = "请先登录网站账号";
    setVisible(mappingSection, false);
    setVisible(successSection, false);
    setVisible(autoLikeSection, false);
    setVisible(closeSessionBtn, false);
    threadSummary.textContent = "未登录";
    consoleOutput.textContent = "请先登录网站账号。";
    statusText.textContent = "只有在登录网站账号后，才能继续使用远程浏览器整页操作和自动点赞功能。";
    setKeyboardState("请先登录网站账号，然后唤起会话。");
  }
}

async function refreshAuthState() {
  const result = await apiFetch("/api/auth/me");
  authState = {
    loggedIn: Boolean(result.loggedIn),
    cn: result.cn || "",
  };
  userRecords = result.records || {};
  updateAuthUI();
  return authState;
}

function readAuthPayload() {
  return {
    cn: authCn.value.trim(),
    password: authPassword.value.trim(),
  };
}

async function registerAccount() {
  registerBtn.disabled = true;
  try {
    const payload = readAuthPayload();
    const result = await apiFetch("/api/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    authState = { loggedIn: Boolean(result.loggedIn), cn: result.cn || "" };
    userRecords = result.records || {};
    authStatus.textContent = `注册成功，已自动登录网站账号：${authState.cn}`;
    authPassword.value = "";
    updateAuthUI();
    await refreshState();
  } catch (error) {
    if (error.status === 404) {
      authStatus.textContent = "注册失败：当前网站服务还是旧版本，请先重启网站服务后再试。";
    } else {
      authStatus.textContent = `注册失败：${error.message}`;
    }
  } finally {
    registerBtn.disabled = false;
  }
}

async function loginAccount() {
  loginBtn.disabled = true;
  try {
    const payload = readAuthPayload();
    const result = await apiFetch("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    authState = { loggedIn: Boolean(result.loggedIn), cn: result.cn || "" };
    userRecords = result.records || {};
    authStatus.textContent = `登录成功，当前网站账号：${authState.cn}`;
    authPassword.value = "";
    updateAuthUI();
    await refreshState();
  } catch (error) {
    if (error.status === 404) {
      authStatus.textContent = "登录失败：当前网站服务还是旧版本，请先重启网站服务后再试。";
    } else {
      authStatus.textContent = `登录失败：${error.message}`;
    }
  } finally {
    loginBtn.disabled = false;
  }
}

async function logoutAccount() {
  logoutBtn.disabled = true;
  try {
    await apiFetch("/api/auth/logout", {
      method: "POST",
      body: JSON.stringify({}),
    });
    authState = { loggedIn: false, cn: "" };
    userRecords = {};
    clearCurrentSessionLocally("已退出网站账号，关联会话和后台任务已开始清理。", false);
    authStatus.textContent = "你已退出网站账号。";
    updateAuthUI();
  } catch (error) {
    authStatus.textContent = `退出网站账号失败：${error.message}`;
  } finally {
    logoutBtn.disabled = false;
  }
}

function readOptionalNumber(input) {
  const value = input.value.trim();
  if (!value) {
    return null;
  }
  return Number(value);
}

function updateAutoLikeUI(state, resolvedSessionId) {
  const autoLike = state.autoLike || {};
  const hasSession = Boolean(resolvedSessionId);
  const loggedIn = state.status === "logged_in";
  const running = Boolean(autoLike.running);

  setVisible(autoLikeSection, hasSession);
  if (!hasSession) {
    return;
  }

  if (!loggedIn && !running) {
    autoLikeStatus.textContent = "请先在当前会话里完成登录，登录成功后才能启动自动点赞。";
  } else if (autoLike.status === "running") {
    autoLikeStatus.textContent = "自动点赞正在运行中。你可以保留页面观察状态，也可以稍后再回来。";
  } else if (autoLike.status === "completed") {
    autoLikeStatus.textContent = "自动点赞任务已经正常结束。你可以调整参数后再次启动。";
  } else if (autoLike.status === "stopped") {
    autoLikeStatus.textContent = "自动点赞任务已经停止。";
  } else if (autoLike.status === "error") {
    autoLikeStatus.textContent = autoLike.lastError || "自动点赞任务异常结束，请检查日志后重试。";
  } else {
    autoLikeStatus.textContent = "登录成功后，这里可以启动当前会话的自动点赞任务。";
  }

  if (autoLike.logPath) {
    autoLikeLogPath.textContent = `日志文件：${autoLike.logPath}`;
    setVisible(autoLikeLogPath, true);
  } else {
    autoLikeLogPath.textContent = "";
    setVisible(autoLikeLogPath, false);
  }

  startAutoLikeBtn.disabled = !loggedIn || running;
  stopAutoLikeBtn.disabled = !running;
}

function updateQqLoginUI(state, resolvedSessionId) {
  const qqLogin = state.qqLogin || {};
  const visible = Boolean(resolvedSessionId) && state.status === "login_required";
  if (!visible) {
    setKeyboardState("先点击上方登录板块中的账号或密码输入框，再直接键入内容。");
    return;
  }

  if (qqLogin.verificationRequired) {
    const label = describeVerifyKind(qqLogin.verificationKind);
    qqLoginStatus.textContent = `当前需要继续完成${label}。请直接在上面的登录板块中手动完成。`;
    setKeyboardState(
      document.activeElement === textBridge
        ? "键盘已接管。请继续在刚刚点击的验证输入框中直接键入。"
        : "如需输入验证码或短信码，请先点击对应输入框，再继续键入。"
    );
  } else if (qqLogin.mode === "password") {
    qqLoginStatus.textContent = "浏览器已自动切到 QQ 密码登录页。请先点击账号框或密码框，再直接输入。";
    setKeyboardState(
      document.activeElement === textBridge
        ? "键盘已接管。请继续在刚刚点击的浏览器输入框中直接键入。"
        : "先点选账号框或密码框。点完后可直接键入，也可以点“激活键盘输入”拉起软键盘。"
    );
  } else {
    qqLoginStatus.textContent = "浏览器正在切到 QQ 密码登录页，请稍候。";
    setKeyboardState("登录板块正在准备中，请稍候。");
  }
}

function updateLoginWorkbenchUI(state, resolvedSessionId) {
  const qqLogin = state.qqLogin || {};
  const hasSession = Boolean(resolvedSessionId);
  const loginRequired = state.status === "login_required";
  const loggedIn = state.status === "logged_in";
  const passwordReady = loginRequired && qqLogin.mode === "password";
  const verifying = Boolean(qqLogin.verificationRequired);

  let summary = "浏览器会自动切到 QQ 密码登录页，当前由用户在返回的登录板块中自行完成登录。";
  let modeLabel = "等待识别";
  let verifyLabel = "待确认";
  let pageLabel = "待加载";
  let nextLabel = "等待唤起浏览器";
  let alertText = "";
  let phaseClass = "badge-idle";
  let phaseText = "等待唤起";

  if (state.status === "starting") {
    summary = "浏览器会话正在启动，系统会自动把页面切到 QQ 密码登录页。";
    pageLabel = "浏览器启动中";
    nextLabel = "等待页面进入密码登录页";
    phaseClass = "badge-starting";
    phaseText = "启动中";
  } else if (loginRequired) {
    phaseClass = "badge-login_required";
    phaseText = "登录处理中";
    modeLabel = passwordReady ? "密码登录页" : "登录页识别中";
    pageLabel = qqLogin.bodyText ? "QQ 登录页面已返回" : "页面已返回";
    if (verifying) {
      verifyLabel = qqLogin.verificationKind === "robot" ? "需要机器人验证" : "需要图形验证码";
      nextLabel = "在整页浏览器画面中完成验证";
      summary = "浏览器已经进入登录流程，但当前需要你手动处理验证。";
      alertText = qqLogin.errorText
        ? `页面提示：${qqLogin.errorText}。请直接在上方返回的登录板块中继续完成验证。`
        : "当前登录流程要求继续验证，请直接在上方返回的登录板块中处理。";
    } else if (passwordReady) {
      verifyLabel = "未触发验证";
      nextLabel = "在返回的登录板块中点击输入框并登录";
      summary = "浏览器已自动切到 QQ 密码登录页，接下来由用户在返回的登录板块中自行输入并登录。";
      alertText = "如果页面里稍后出现验证码、机器人验证或手机验证，不需要切走，直接在同一块登录板块中继续完成即可。";
    } else {
      verifyLabel = "待确认";
      nextLabel = "等待切到密码登录页";
      summary = "浏览器正在识别并切换到 QQ 密码登录页，请稍候。";
    }
  } else if (loggedIn) {
    summary = "系统已检测到登录成功，前端登录工作台会自动收起浏览器登录页面。";
    modeLabel = "登录完成";
    verifyLabel = "无需验证";
    pageLabel = "已进入登录后状态";
    nextLabel = "可继续使用后续功能";
    phaseClass = "badge-logged_in";
    phaseText = "已登录";
    alertText = "当前登录流程已经结束，浏览器登录页面已自动关闭。";
  } else if (state.status === "error") {
    summary = state.lastError || "浏览器登录流程发生异常。";
    pageLabel = "流程异常";
    nextLabel = "关闭当前会话后重新唤起";
    phaseClass = "badge-error";
    phaseText = "异常";
    alertText = state.lastError || "请关闭当前会话后重新尝试。";
  } else if (state.status === "expired") {
    summary = state.lastError || "当前会话已失效，需要重新唤起浏览器。";
    pageLabel = "会话已失效";
    nextLabel = "重新唤起浏览器";
    phaseClass = "badge-expired";
    phaseText = "已失效";
    alertText = state.lastError || "请重新创建会话。";
  }

  loginSummary.textContent = summary;
  loginPhaseBadge.className = `badge ${phaseClass}`;
  loginPhaseBadge.textContent = phaseText;
  loginModeValue.textContent = modeLabel;
  loginVerifyValue.textContent = verifyLabel;
  loginPageValue.textContent = pageLabel;
  loginNextValue.textContent = nextLabel;
  loginAlert.textContent = alertText;
  setVisible(loginAlert, Boolean(alertText));

  renderLoginSteps([
    {
      title: "登录网站账号",
      state: authState.loggedIn ? "done" : "active",
      copy: authState.loggedIn
        ? `当前网站账号：${authState.cn}`
        : "先完成网站账号注册或登录，后续浏览器会话才会启用。",
    },
    {
      title: "唤起独立浏览器",
      state: hasSession ? "done" : authState.loggedIn ? "active" : "waiting",
      copy: hasSession
        ? `当前会话：${resolvedSessionId}`
        : "点击顶部按钮，创建只属于当前页面的浏览器会话。",
    },
    {
      title: "进入密码登录页",
      state: passwordReady || loggedIn ? "done" : loginRequired ? "active" : "waiting",
      copy: passwordReady || loggedIn
        ? "系统已自动把登录页切到 QQ 密码登录模式。"
        : "浏览器会自动识别并切换到 QQ 密码登录页。",
    },
    {
      title: "用户处理登录",
      state: loggedIn ? "done" : loginRequired ? "active" : "waiting",
      copy: verifying
        ? "当前需要你在登录板块里继续处理验证码、短信码或机器人验证。"
        : loggedIn
          ? "登录已经完成，登录板块会自动收起。"
          : "由用户直接在返回的登录板块中点击输入框、键入并完成登录。",
    },
  ]);
}

function updateConsoleUI(state) {
  const consoleState = state.console || {};
  const threadStatus = consoleState.threadStatus || {};
  const lines = Array.isArray(consoleState.lines) ? consoleState.lines : ["暂无控制台输出。"];

  const parts = [
    `刷新线程: ${threadStatus.refreshThreadAlive ? "运行中" : "未运行"}`,
    `自动点赞: ${threadStatus.autoLikeRunning ? "运行中" : "未运行"}`,
    `会话启用: ${threadStatus.sessionEnabled ? "是" : "否"}`,
    `会话时长: ${threadStatus.sessionAgeSeconds ?? 0}s`,
    `挂起关闭: ${threadStatus.closePending ? "是" : "否"}`,
  ];
  threadSummary.textContent = parts.join(" | ");

  const nextText = lines.join("\n");
  const shouldStickToBottom =
    consoleOutput.scrollTop + consoleOutput.clientHeight >= consoleOutput.scrollHeight - 24;
  if (consoleOutput.textContent !== nextText) {
    consoleOutput.textContent = nextText;
    if (shouldStickToBottom) {
      consoleOutput.scrollTop = consoleOutput.scrollHeight;
    }
  }
}

function updateUI(state) {
  if (!authState.loggedIn) {
    updateAuthUI();
    return;
  }

  currentState = state;
  const resolvedSessionId = state.sessionId || sessionId;

  setBadge(state.status);
  statusUrl.textContent = state.url || "尚未启动远程浏览器";

  if (resolvedSessionId) {
    sessionMeta.textContent = `当前独立会话：${resolvedSessionId}。这个页面只会控制这一个浏览器。`;
    sessionLink.href = buildSessionUrl(resolvedSessionId);
    sessionLink.textContent = "打开当前会话链接";
    setVisible(sessionLink, true);
    setVisible(closeSessionBtn, true);
  } else {
    sessionMeta.textContent = "当前还没有独立会话。每次点击按钮都会新建一个只属于当前页面的浏览器会话。";
    sessionLink.href = "#";
    setVisible(sessionLink, false);
    setVisible(closeSessionBtn, false);
  }

  if (state.status === "idle") {
    statusText.textContent = "点击“唤起浏览器”后，服务端会启动一个新的 Chrome 会话，并把登录相关板块映射回这个网页。";
    setVisible(mappingSection, false);
    setVisible(successSection, false);
  } else if (state.status === "starting") {
    statusText.textContent = "浏览器会话正在准备中，请稍候。";
    setVisible(mappingSection, false);
    setVisible(successSection, false);
  } else if (state.status === "login_required") {
    if (state.qqLogin?.verificationRequired) {
      const label = describeVerifyKind(state.qqLogin.verificationKind);
      statusText.textContent = `当前需要继续完成${label}。请直接在下方返回的登录板块中手动完成。`;
    } else {
      statusText.textContent = "浏览器已经启动，并且会自动切到密码登录页。现在直接在返回的登录板块里由用户自行处理登录即可。";
    }
    const surfaceMode = state.surface?.mode || "";
    const surfaceLabel =
      surfaceMode === "password_panel"
        ? "当前返回：密码登录板块"
        : surfaceMode === "login_frame"
          ? "当前返回：登录框/验证板块"
          : "当前返回：浏览器登录板块";
    panelMeta.textContent = `${surfaceLabel} | ${state.title || "QQ空间"} | ${state.url || ""}`;
    setVisible(mappingSection, true);
    setVisible(successSection, false);
  } else if (state.status === "logged_in") {
    statusText.textContent = "检测到登录成功，登录映射区域已自动关闭。";
    setVisible(mappingSection, false);
    setVisible(successSection, true);
  } else if (state.status === "expired") {
    statusText.textContent = state.lastError || "当前会话已失效，请重新点击按钮唤起新的浏览器。";
    setVisible(mappingSection, false);
    setVisible(successSection, false);
    lastFrameToken = -1;
    setVisible(closeSessionBtn, false);
  } else if (state.status === "error") {
    statusText.textContent = state.lastError || "浏览器会话启动失败，请稍后重试。";
    setVisible(mappingSection, false);
    setVisible(successSection, false);
  } else {
    statusText.textContent = "浏览器会话状态未知，请稍后重试。";
  }

  launchBtn.disabled = state.status === "starting";
  launchBtn.textContent = "新建并唤起独立浏览器";

  updateQqLoginUI(state, resolvedSessionId);
  updateLoginWorkbenchUI(state, resolvedSessionId);
  updateAutoLikeUI(state, resolvedSessionId);
  updateConsoleUI(state);

  if (state.imageAvailable && state.frameToken !== lastFrameToken && resolvedSessionId) {
    lastFrameToken = state.frameToken;
    updateFrameImage(resolvedSessionId, state.frameToken).catch((error) => {
      statusText.textContent = `画面刷新失败：${error.message}`;
    });
  }
}

async function refreshState() {
  const requestSeq = beginStateRequest();
  try {
    if (!authState.loggedIn) {
      updateAuthUI();
      return;
    }
    const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    const state = await apiFetch(`/api/state${query}`);
    applyStatePayload(state, requestSeq);
  } catch (error) {
    if (requestSeq < lastAppliedStateSeq) {
      return;
    }
    if (error.status === 401) {
      authState = { loggedIn: false, cn: "" };
      userRecords = {};
      clearCurrentSessionLocally("请先登录网站账号。", true);
      authStatus.textContent = "登录状态已失效，请重新登录网站账号。";
      updateAuthUI();
      return;
    }
    setBadge("error");
    statusUrl.textContent = "状态接口不可用";
    statusText.textContent = `状态刷新失败：${error.message}`;
    threadSummary.textContent = "状态接口不可用";
    consoleOutput.textContent = `状态刷新失败：${error.message}`;
  }
}

function getClientPoint(event) {
  if (event.touches && event.touches.length > 0) {
    return event.touches[0];
  }
  if (event.changedTouches && event.changedTouches.length > 0) {
    return event.changedTouches[0];
  }
  return event;
}

function buildPointerPayload(event, action, extra = {}) {
  const point = getClientPoint(event);
  const rect = frameImage.getBoundingClientRect();

  if (!point || rect.width <= 0 || rect.height <= 0) {
    return null;
  }

  const displayX = point.clientX - rect.left;
  const displayY = point.clientY - rect.top;
  if (displayX < 0 || displayY < 0 || displayX > rect.width || displayY > rect.height) {
    return null;
  }

  return {
    action,
    displayX,
    displayY,
    renderedWidth: rect.width,
    renderedHeight: rect.height,
    ...extra,
  };
}

async function sendPointer(event, action, extra = {}) {
  if (!currentState || currentState.status !== "login_required" || !sessionId) {
    return;
  }

  const payload = buildPointerPayload(event, action, extra);
  if (!payload) {
    return;
  }

  const response = await apiFetch("/api/input/pointer", {
    method: "POST",
    body: JSON.stringify({ sessionId, ...payload }),
  });
  applyStatePayload(response.state, beginStateRequest());
  if (action !== "move") {
    queueRefresh(70);
  }
}

async function sendText(text) {
  if (!text || !sessionId || currentState?.status !== "login_required") {
    return;
  }

  const response = await apiFetch("/api/input/text", {
    method: "POST",
    body: JSON.stringify({ sessionId, text }),
  });
  applyStatePayload(response.state, beginStateRequest());
  queueRefresh(60);
}

async function sendKey(key, ctrl = false) {
  if (!sessionId || currentState?.status !== "login_required") {
    return;
  }

  const response = await apiFetch("/api/input/key", {
    method: "POST",
    body: JSON.stringify({ sessionId, key, ctrl }),
  });
  applyStatePayload(response.state, beginStateRequest());
  queueRefresh(60);
}

async function wakeBrowser() {
  launchBtn.disabled = true;
  try {
    const result = await apiFetch("/api/session/start", {
      method: "POST",
      body: JSON.stringify({}),
    });
    writeSessionId(result.sessionId || "");
    lastFrameToken = -1;
    statusText.textContent = "正在启动远程浏览器，请稍候...";
  } catch (error) {
    statusText.textContent = `启动失败：${error.message}`;
  } finally {
    refreshState();
  }
}

async function startAutoLike() {
  if (!sessionId) {
    autoLikeStatus.textContent = "请先创建会话。";
    return;
  }

  startAutoLikeBtn.disabled = true;
  try {
    const payload = {
      sessionId,
      maxBigRounds: readOptionalNumber(autoLikeMaxBigRounds),
      maxNewLikesPerSmallRound: readOptionalNumber(autoLikeMaxLikes),
      waitBetweenBigRounds: readOptionalNumber(autoLikeWaitSeconds),
      skipExternalTasks: autoLikeSkipTasks.checked,
    };
    await apiFetch("/api/auto-like/start", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    autoLikeStatus.textContent = "自动点赞启动成功，正在切换到运行状态...";
  } catch (error) {
    autoLikeStatus.textContent = `启动自动点赞失败：${error.message}`;
  } finally {
    refreshState();
  }
}

async function stopAutoLike() {
  if (!sessionId) {
    return;
  }

  stopAutoLikeBtn.disabled = true;
  try {
    await apiFetch("/api/auto-like/stop", {
      method: "POST",
      body: JSON.stringify({ sessionId }),
    });
    autoLikeStatus.textContent = "正在停止自动点赞...";
  } catch (error) {
    autoLikeStatus.textContent = `停止自动点赞失败：${error.message}`;
  } finally {
    refreshState();
  }
}

function clearCurrentSessionLocally(message, preserveAuth = true) {
  writeSessionId("");
  currentState = null;
  lastFrameToken = -1;
  lastAppliedStateSeq = 0;
  frameLoadSeq += 1;
  if (currentFrameObjectUrl) {
    URL.revokeObjectURL(currentFrameObjectUrl);
    currentFrameObjectUrl = "";
  }
  frameImage.removeAttribute("src");
  setBadge("idle");
  statusUrl.textContent = "尚未启动远程浏览器";
  statusText.textContent = message || "当前会话已结束。";
  sessionMeta.textContent = "当前还没有独立会话。每次点击按钮都会新建一个只属于当前页面的浏览器会话。";
  setVisible(sessionLink, false);
  setVisible(closeSessionBtn, false);
  setVisible(mappingSection, false);
  setVisible(successSection, false);
  setVisible(autoLikeSection, false);
  threadSummary.textContent = "等待会话";
  consoleOutput.textContent = message || "当前会话已结束。";
  setKeyboardState("当前没有活动会话。重新唤起浏览器后，再点击登录板块中的输入框开始输入。");
  updateLoginWorkbenchUI(
    {
      status: "idle",
      qqLogin: {},
      lastError: "",
    },
    ""
  );
  if (!preserveAuth) {
    authState = { loggedIn: false, cn: "" };
  }
}

async function closeSession(immediate = true) {
  if (!sessionId || !authState.loggedIn) {
    return;
  }

  closeSessionBtn.disabled = true;
  try {
    await apiFetch("/api/session/close", {
      method: "POST",
      body: JSON.stringify({
        sessionId,
        immediate,
        reason: immediate ? "manual_close" : "pagehide",
      }),
    });
    if (immediate) {
      clearCurrentSessionLocally("当前会话已退出，浏览器和后台任务已开始清理。");
    }
  } catch (error) {
    if (immediate) {
      statusText.textContent = `退出会话失败：${error.message}`;
    }
  } finally {
    if (immediate) {
      refreshState();
    } else {
      closeSessionBtn.disabled = false;
    }
  }
}

function sendCloseBeacon() {
  if (!sessionId || !authState.loggedIn) {
    return;
  }

  const payload = JSON.stringify({
    sessionId,
    immediate: false,
    reason: "pagehide",
  });
  const blob = new Blob([payload], { type: "application/json" });
  navigator.sendBeacon("/api/session/close", blob);
}

function focusTextBridge() {
  textBridge.value = "";
  textBridge.focus({ preventScroll: true });
  setKeyboardState("键盘已接管。请继续在刚刚点击的浏览器输入框中直接键入。");
}

async function initializePage() {
  try {
    await refreshAuthState();
  } catch (error) {
    authState = { loggedIn: false, cn: "" };
    userRecords = {};
    updateAuthUI();
    if (error.status === 404) {
      authStatus.textContent = "当前网站服务还是旧版本，请先重启网站服务。";
    } else {
      authStatus.textContent = `登录状态检查失败：${error.message}`;
    }
  }
  await refreshState();
}

launchBtn.addEventListener("click", wakeBrowser);
registerBtn.addEventListener("click", registerAccount);
loginBtn.addEventListener("click", loginAccount);
logoutBtn.addEventListener("click", logoutAccount);
startAutoLikeBtn.addEventListener("click", startAutoLike);
stopAutoLikeBtn.addEventListener("click", stopAutoLike);
closeSessionBtn.addEventListener("click", () => closeSession(true));
focusKeyboardBtn.addEventListener("click", focusTextBridge);
window.addEventListener("pagehide", sendCloseBeacon);
authPassword.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    if (authState.loggedIn) {
      return;
    }
    loginAccount();
  }
});

frameImage.addEventListener("mousedown", async (event) => {
  event.preventDefault();
  pointerActive = true;
  focusTextBridge();
  await sendPointer(event, "press");
});

frameImage.addEventListener("contextmenu", (event) => {
  event.preventDefault();
});

window.addEventListener("mousemove", async (event) => {
  if (!pointerActive) {
    return;
  }
  await sendPointer(event, "move");
});

window.addEventListener("mouseup", async (event) => {
  if (!pointerActive) {
    return;
  }
  pointerActive = false;
  await sendPointer(event, "release");
});

frameImage.addEventListener("wheel", async (event) => {
  event.preventDefault();
  focusTextBridge();
  await sendPointer(event, "wheel", { deltaY: event.deltaY });
}, { passive: false });

frameImage.addEventListener("touchstart", async (event) => {
  event.preventDefault();
  pointerActive = true;
  focusTextBridge();
  await sendPointer(event, "press");
}, { passive: false });

frameImage.addEventListener("touchmove", async (event) => {
  if (!pointerActive) {
    return;
  }
  event.preventDefault();
  await sendPointer(event, "move");
}, { passive: false });

frameImage.addEventListener("touchend", async (event) => {
  if (!pointerActive) {
    return;
  }
  event.preventDefault();
  pointerActive = false;
  await sendPointer(event, "release");
}, { passive: false });

textBridge.addEventListener("compositionstart", () => {
  composing = true;
});

textBridge.addEventListener("compositionend", async () => {
  composing = false;
  const text = textBridge.value;
  textBridge.value = "";
  await sendText(text);
  setKeyboardState("已把输入内容发送到浏览器当前焦点输入框。");
});

textBridge.addEventListener("input", async () => {
  if (composing) {
    return;
  }
  const text = textBridge.value;
  textBridge.value = "";
  await sendText(text);
  setKeyboardState("输入内容已发送到浏览器当前焦点输入框。");
});

textBridge.addEventListener("paste", async (event) => {
  const text = event.clipboardData?.getData("text") || "";
  if (!text) {
    return;
  }
  event.preventDefault();
  textBridge.value = "";
  await sendText(text);
  setKeyboardState("粘贴内容已发送到浏览器当前焦点输入框。");
});

textBridge.addEventListener("keydown", async (event) => {
  if (!currentState || currentState.status !== "login_required") {
    return;
  }

  const key = event.key;
  const ctrlPressed = event.ctrlKey || event.metaKey;

  if (ctrlPressed && ["a", "c", "v", "x"].includes(key.toLowerCase())) {
    if (key.toLowerCase() === "v") {
      return;
    }
    event.preventDefault();
    await sendKey(key, true);
    setKeyboardState(`快捷键 Ctrl+${key.toUpperCase()} 已发送到浏览器。`);
    return;
  }

  if (["Backspace", "Tab", "Enter", "Escape", "Delete", "ArrowLeft", "ArrowUp", "ArrowRight", "ArrowDown", "Home", "End"].includes(key)) {
    event.preventDefault();
    await sendKey(key);
    setKeyboardState(`按键 ${key} 已发送到浏览器。`);
  }
});

textBridge.addEventListener("focus", () => {
  setKeyboardState("键盘已接管。请继续在刚刚点击的浏览器输入框中直接键入。");
});

textBridge.addEventListener("blur", () => {
  if (currentState?.status === "login_required") {
    setKeyboardState("如果还要继续输入，请先点击登录板块中的目标输入框，再次激活键盘输入。");
  }
});

initializePage();
setInterval(refreshState, 650);
