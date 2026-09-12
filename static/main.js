// ── Theme Toggle ──────────────────────────────────────────────────────────────
const themeToggleBtn = document.getElementById('theme_toggle');
const themeIcon = document.getElementById('theme_icon');

function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    themeIcon.textContent = theme === 'dark' ? '☀️' : '🌙';
}

// Initialise icon to match the theme already applied by the inline <head> script
applyTheme(document.documentElement.getAttribute('data-theme') || 'light');

themeToggleBtn.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    localStorage.setItem('theme', next); // manual override
    applyTheme(next);
});

// Follow OS preference changes only when there is no manual override
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
    if (!localStorage.getItem('theme')) {
        applyTheme(e.matches ? 'dark' : 'light');
    }
});

// ── i18n ───────────────────────────────────────────────────────────────────────
const I18N_DICT = {
    en: {
        theme_title: "Toggle dark / light mode",
        lang_title: "Switch English / 中文",
        app_title: "# Virtual Life Chat",
        model_prefix: "Model: ",
        message_placeholder: "Type your message... (Click Send to send)",
        send_btn: "Send",
        edit_last_btn: "📝 Edit Last",
        regenerate_btn: "🔄 Regenerate",
        status_ready: "Ready.",
        upload_label: "Upload Image (optional)",
        remove_image_btn: "Remove Image",
        model_select_label: "Model",
        retained_prompt_header: "Retained Prompt",
        retained_prompt_placeholder: "Last sent prompt will be kept here...",
        resend_btn: "Resend",
        fill_input_btn: "Fill Input",
        clear_btn: "Clear",
        user_profile_header: "User Profile & Context",
        save_profile_btn: "Save Profile",
        memory_management_header: "Memory Management",
        manual_compress_btn: "Manual Compress",
        memory_warning: "Warning: Consolidating memory.md irreversibly rewrites the memory file.",
        consolidate_confirm_placeholder: "Type COMPRESS",
        consolidate_btn: "Consolidate Memory",
        danger_zone_header: "Danger Zone",
        danger_warning: "WARNING: This action permanently deletes ALL history, memory, and uploaded images.",
        clear_history_confirm_placeholder: "Type CLEAR ALL HISTORY",
        clear_history_btn: "Clear All History",
        logout_link: "Logout",
    },
    zh: {
        theme_title: "切换深色 / 浅色模式",
        lang_title: "切换语言 (English / 中文)",
        app_title: "# 虚拟人生 Virtual Life",
        model_prefix: "模型: ",
        message_placeholder: "输入消息...（点击发送按钮发送）",
        send_btn: "发送",
        edit_last_btn: "📝 编辑上一条",
        regenerate_btn: "🔄 重新生成",
        status_ready: "就绪。",
        upload_label: "上传图片（可选）",
        remove_image_btn: "移除图片",
        model_select_label: "模型选择",
        retained_prompt_header: "保留提示词",
        retained_prompt_placeholder: "上一次发送的文本将保留在此...",
        resend_btn: "重新发送",
        fill_input_btn: "填入输入框",
        clear_btn: "清空",
        user_profile_header: "用户设定与画像",
        save_profile_btn: "保存设定",
        memory_management_header: "记忆管理",
        manual_compress_btn: "手动压缩",
        memory_warning: "警告：整理 memory.md 将永久重写记忆文件。",
        consolidate_confirm_placeholder: "输入 COMPRESS",
        consolidate_btn: "合并整理记忆",
        danger_zone_header: "危险区域",
        danger_warning: "警告：此操作将永久删除所有对话历史、记忆和上传的图片。",
        clear_history_confirm_placeholder: "输入 CLEAR ALL HISTORY",
        clear_history_btn: "清空所有历史",
        logout_link: "退出登录",
    }
};

let currentLang = localStorage.getItem('vl_lang') || ((navigator.language && navigator.language.startsWith('zh')) ? 'zh' : 'en');

function applyLanguage(lang) {
    currentLang = lang;
    localStorage.setItem('vl_lang', lang);
    document.documentElement.lang = lang;
    const t = I18N_DICT[lang] || I18N_DICT.en;

    const langToggleBtn = document.getElementById('lang_toggle');
    if (langToggleBtn) langToggleBtn.title = t.lang_title;
    if (themeToggleBtn) themeToggleBtn.title = t.theme_title;

    const appTitleText = document.getElementById('app_title_text');
    if (appTitleText) appTitleText.textContent = t.app_title;

    if (modelLabel && currentModel) {
        modelLabel.textContent = `${t.model_prefix}${currentModel}`;
    }

    if (messageInput) messageInput.placeholder = t.message_placeholder;
    if (sendBtn) sendBtn.textContent = t.send_btn;
    if (editLastBtn) editLastBtn.textContent = t.edit_last_btn;
    if (regenerateBtn) regenerateBtn.textContent = t.regenerate_btn;

    const uploadImageLabel = document.getElementById('upload_image_label');
    if (uploadImageLabel) uploadImageLabel.textContent = t.upload_label;
    if (clearImageBtn) clearImageBtn.textContent = t.remove_image_btn;

    const modelSelectLabel = document.getElementById('model_select_label');
    if (modelSelectLabel) modelSelectLabel.textContent = t.model_select_label;

    const retainedPromptHeader = document.getElementById('retained_prompt_header');
    if (retainedPromptHeader) retainedPromptHeader.textContent = t.retained_prompt_header;
    if (retainedPromptText) retainedPromptText.placeholder = t.retained_prompt_placeholder;
    if (resendRetainedBtn) resendRetainedBtn.textContent = t.resend_btn;
    if (fillRetainedBtn) fillRetainedBtn.textContent = t.fill_input_btn;
    if (clearRetainedBtn) clearRetainedBtn.textContent = t.clear_btn;

    const userProfileHeader = document.getElementById('user_profile_header');
    if (userProfileHeader) userProfileHeader.textContent = t.user_profile_header;
    if (saveUserBtn) saveUserBtn.textContent = t.save_profile_btn;

    const memoryManagementHeader = document.getElementById('memory_management_header');
    if (memoryManagementHeader) memoryManagementHeader.textContent = t.memory_management_header;
    if (manualCompressBtn) manualCompressBtn.textContent = t.manual_compress_btn;
    const memoryWarningText = document.getElementById('memory_warning_text');
    if (memoryWarningText) memoryWarningText.textContent = t.memory_warning;
    if (advancedCompressConfirm) advancedCompressConfirm.placeholder = t.consolidate_confirm_placeholder;
    if (advancedCompressBtn) advancedCompressBtn.textContent = t.consolidate_btn;

    const dangerZoneHeader = document.getElementById('danger_zone_header');
    if (dangerZoneHeader) dangerZoneHeader.textContent = t.danger_zone_header;
    const dangerWarningText = document.getElementById('danger_warning_text');
    if (dangerWarningText) dangerWarningText.innerHTML = `<strong>${lang === 'zh' ? '警告：' : 'WARNING:'}</strong> ${t.danger_warning}`;
    if (confirmClear) confirmClear.placeholder = t.clear_history_confirm_placeholder;
    if (clearBtn) clearBtn.textContent = t.clear_history_btn;

    const logoutLink = document.getElementById('logout_link');
    if (logoutLink) logoutLink.textContent = t.logout_link;
}

const langToggleBtn = document.getElementById('lang_toggle');
if (langToggleBtn) {
    langToggleBtn.addEventListener('click', () => {
        const next = currentLang === 'zh' ? 'en' : 'zh';
        applyLanguage(next);
    });
}
// ─────────────────────────────────────────────────────────────────────────────

const chatbox = document.getElementById('chatbox');
const messageInput = document.getElementById('message_input');
const sendBtn = document.getElementById('send_btn');
const statusMd = document.querySelector('#status_md p');

const imageInput = document.getElementById('image_input');
const imagePreview = document.getElementById('image_preview');
const clearImageBtn = document.getElementById('clear_image_btn');

const modelSelect = document.getElementById('model_select');
const modelLabel = document.getElementById('model_label');

// Retained Prompt elements & persistence
const RETAINED_PROMPT_KEY = 'vl_retained_prompt';
const retainedPromptText = document.getElementById('retained_prompt_text');
const resendRetainedBtn = document.getElementById('resend_retained_btn');
const fillRetainedBtn = document.getElementById('fill_retained_btn');
const clearRetainedBtn = document.getElementById('clear_retained_btn');

function saveRetainedPrompt(text) {
    if (!text || !text.trim()) return;
    const cleanText = text.trim();
    localStorage.setItem(RETAINED_PROMPT_KEY, cleanText);
    if (retainedPromptText) {
        retainedPromptText.value = cleanText;
    }
}

function loadRetainedPrompt(history) {
    let saved = (localStorage.getItem(RETAINED_PROMPT_KEY) || '').trim();
    if (!saved && history && Array.isArray(history) && history.length > 0) {
        for (let i = history.length - 1; i >= 0; i--) {
            if (history[i].role === 'user') {
                const parts = history[i].content.split('\n\n![uploaded image](');
                const userText = (parts[0] || '').trim();
                if (userText) {
                    saved = userText;
                    localStorage.setItem(RETAINED_PROMPT_KEY, saved);
                    break;
                }
            }
        }
    }
    if (retainedPromptText) {
        retainedPromptText.value = saved;
    }
}

// Accordion elements
const userProfile = document.getElementById('user_profile');
const saveUserBtn = document.getElementById('save_user_btn');
const manualCompressBtn = document.getElementById('manual_compress_btn');
const advancedCompressConfirm = document.getElementById('advanced_compress_confirm');
const advancedCompressBtn = document.getElementById('advanced_compress_btn');
const confirmClear = document.getElementById('confirm_clear');
const clearBtn = document.getElementById('clear_btn');

const editLastBtn = document.getElementById('edit_last_btn');
const regenerateBtn = document.getElementById('regenerate_btn');

let chatHistory = [];
let isStreaming = false;
let pendingBase64Image = null;
let currentModel = null;

function updateButtonStates() {
    const hasHistory = chatHistory.length > 0;
    const disabled = isStreaming || !hasHistory;
    editLastBtn.disabled = disabled;
    regenerateBtn.disabled = disabled;
    sendBtn.disabled = isStreaming;
    messageInput.disabled = isStreaming;
}

// Helpers
function setStatus(text) {
    statusMd.textContent = text;
}

function scrollToBottom() {
    chatbox.scrollTop = chatbox.scrollHeight;
}

// Rendering
function renderMarkdown(text) {
    return marked.parse(text);
}

function renderChat() {
    chatbox.innerHTML = '';
    chatHistory.forEach((msg, idx) => {
        const div = document.createElement('div');
        div.className = `message message-${msg.role}`;

        let contentHtml = '';
        if (msg.role === 'user') {
            // Check for image
            const parts = msg.content.split('\n\n![uploaded image](');
            const textContent = parts[0];
            contentHtml = `<p style="margin:0">${textContent.replace(/\n/g, '<br>')}</p>`;
            if (parts.length > 1) {
                const imgData = parts[1].replace(')', '');
                contentHtml += `<img src="${imgData}" style="max-width:200px; border-radius:8px; margin-top:8px;">`;
            }
        } else {
            contentHtml = renderMarkdown(msg.content);
        }

        div.innerHTML = contentHtml;
        chatbox.appendChild(div);
    });
    scrollToBottom();
    updateButtonStates();
}

function updateLastMessage(content) {
    if (chatHistory.length === 0 || chatHistory[chatHistory.length - 1].role !== 'assistant') {
        chatHistory.push({ role: 'assistant', content: content });
        renderChat();
    } else {
        chatHistory[chatHistory.length - 1].content = content;
        // Optimization: update DOM directly for streaming instead of full re-render
        const lastDiv = chatbox.lastElementChild;
        if (lastDiv && lastDiv.classList.contains('message-assistant')) {
            lastDiv.innerHTML = renderMarkdown(content);
            scrollToBottom();
        } else {
            renderChat();
        }
    }
}

// Base64 encode image
function getBase64Image(file) {
    return new Promise((resolve, reject) => {
        if (!file) {
            resolve(null);
            return;
        }
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = error => reject(error);
        reader.readAsDataURL(file);
    });
}

// Core Chat Action
async function internalSendMessage(text, base64Image) {
    if (isStreaming) return;
    if (!text && !base64Image) return;

    if (text) {
        saveRetainedPrompt(text);
    }

    setStatus("Sending...");
    isStreaming = true;
    updateButtonStates();

    // Build immediate UI
    let userDisplay = text;
    if (base64Image) {
        userDisplay += `\n\n![uploaded image](${base64Image})`;
    }
    chatHistory.push({ role: 'user', content: userDisplay });
    messageInput.value = '';

    // Clear image
    imageInput.value = '';
    pendingBase64Image = null;
    imagePreview.innerHTML = '';
    clearImageBtn.style.display = 'none';

    chatHistory.push({ role: 'assistant', content: '' });
    renderChat();

    setStatus("Streaming...");

    const payload = {
        message: text,
        image_data: base64Image
    };

    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            if (response.status === 401) {
                window.location.href = '/login';
                return;
            }
            throw new Error(`Server returned ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let done = false;
        let buffer = '';

        while (!done) {
            const { value, done: readerDone } = await reader.read();
            done = readerDone;
            if (value) {
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop(); // keep the last partial line in the buffer

                for (let line of lines) {
                    if (line.startsWith('data: ')) {
                        const dataStr = line.substring(6);
                        if (dataStr === '[DONE]') {
                            break;
                        }
                        try {
                            const data = JSON.parse(dataStr);
                            if (data.content !== undefined) {
                                updateLastMessage(data.content);
                            }
                            if (data.status) {
                                setStatus(data.status);
                            }
                        } catch (e) {
                            console.error('JSON parse error:', e, "Raw data:", dataStr);
                        }
                    }
                }
            }
        }

        // Sync final state with the server once streaming is finished to guarantee match
        try {
            const syncResponse = await fetch('/api/init');
            if (syncResponse.ok) {
                const data = await syncResponse.json();
                if (data.chat_ui_state) {
                    chatHistory = data.chat_ui_state;
                    renderChat();
                }
            }
        } catch (e) {
            console.error('Final sync error:', e);
        }
    } catch (err) {
        updateLastMessage(`**Error:** ${err.message}`);
        setStatus("Error");
    } finally {
        isStreaming = false;
        updateButtonStates();
        messageInput.focus();
    }
}

async function sendMessage() {
    const text = messageInput.value.trim();
    let base64Image = pendingBase64Image;
    if (imageInput.files[0] && !base64Image) {
        base64Image = await getBase64Image(imageInput.files[0]);
    }
    await internalSendMessage(text, base64Image);
}

// Events
sendBtn.addEventListener('click', sendMessage);
// Note: Bare Enter key listener is completely removed. Pressing Enter will only insert a newline.
// Messages can only be sent by explicitly clicking the Send button (or Resend in Retained Prompt).

// Retained Prompt actions
if (retainedPromptText) {
    retainedPromptText.addEventListener('input', () => {
        localStorage.setItem(RETAINED_PROMPT_KEY, retainedPromptText.value);
    });
}

if (resendRetainedBtn) {
    resendRetainedBtn.addEventListener('click', async () => {
        if (isStreaming) return;
        const text = (retainedPromptText ? retainedPromptText.value : '').trim();
        if (!text) return;
        saveRetainedPrompt(text);
        await internalSendMessage(text, null);
    });
}

if (fillRetainedBtn) {
    fillRetainedBtn.addEventListener('click', () => {
        const text = (retainedPromptText ? retainedPromptText.value : '').trim();
        if (!text) return;
        messageInput.value = text;
        messageInput.focus();
    });
}

if (clearRetainedBtn) {
    clearRetainedBtn.addEventListener('click', () => {
        localStorage.removeItem(RETAINED_PROMPT_KEY);
        if (retainedPromptText) {
            retainedPromptText.value = '';
        }
    });
}

// Image preview
imageInput.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (file) {
        pendingBase64Image = await getBase64Image(file);
        imagePreview.innerHTML = `<img src="${pendingBase64Image}">`;
        clearImageBtn.style.display = 'block';
    } else {
        pendingBase64Image = null;
        imagePreview.innerHTML = '';
        clearImageBtn.style.display = 'none';
    }
});

clearImageBtn.addEventListener('click', () => {
    imageInput.value = '';
    imagePreview.innerHTML = '';
    clearImageBtn.style.display = 'none';
});

// Accordions
document.querySelectorAll('.accordion-header').forEach(header => {
    header.addEventListener('click', () => {
        header.classList.toggle('active');
        const content = header.nextElementSibling;
        content.classList.toggle('show');
    });
});

// API Calls
async function apiCall(endpoint, payload) {
    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {})
        });
        if (response.status === 401) {
            window.location.href = '/login';
            return { status: "Unauthorized" };
        }
        return await response.json();
    } catch (e) {
        return { status: "Request failed: " + e.message };
    }
}

async function doPopTurn() {
    setStatus("Removing last turn...");
    const res = await apiCall('/api/pop_last_turn', {});
    if (!res.success) {
        setStatus(res.error || "Failed to pop turn.");
        return null;
    }
    // Remove last two messages from chatHistory
    if (chatHistory.length >= 2) {
        chatHistory.splice(-2, 2);
    }
    renderChat();
    return res;
}

editLastBtn.addEventListener('click', async () => {
    if (isStreaming || chatHistory.length === 0) return;
    const res = await doPopTurn();
    if (!res) return;

    // Set text
    messageInput.value = res.user_text;

    // Reset image if available
    if (res.image_data) {
        pendingBase64Image = res.image_data;
        imagePreview.innerHTML = `<img src="${pendingBase64Image}">`;
        clearImageBtn.style.display = 'block';
    } else {
        pendingBase64Image = null;
    }
    setStatus("Ready to edit.");
    messageInput.focus();
});

regenerateBtn.addEventListener('click', async () => {
    if (isStreaming || chatHistory.length === 0) return;
    const res = await doPopTurn();
    if (!res) return;

    // Re-send instantly without editing
    await internalSendMessage(res.user_text, res.image_data);
});

saveUserBtn.addEventListener('click', async () => {
    setStatus("Saving profile...");
    const res = await apiCall('/api/save_profile', { content: userProfile.value });
    setStatus(res.status);
});

manualCompressBtn.addEventListener('click', async () => {
    setStatus("Compressing...");
    const res = await apiCall('/api/manual_compress');
    setStatus(res.status);
});

advancedCompressBtn.addEventListener('click', async () => {
    setStatus("Compressing...");
    const res = await apiCall('/api/advanced_compress', { confirm_text: advancedCompressConfirm.value });
    setStatus(res.status);
    advancedCompressConfirm.value = '';
});

clearBtn.addEventListener('click', async () => {
    setStatus("Clearing...");
    const res = await apiCall('/api/clear_history', { confirm_text: confirmClear.value });
    setStatus(res.status);
    confirmClear.value = '';
    if (res.cleared) {
        chatHistory = [];
        renderChat();
    }
});

// Model selector (persisted server-side in model_state.json)
async function loadModels() {
    try {
        const response = await fetch('/api/model');
        if (response.status === 401) {
            window.location.href = '/login';
            return;
        }
        const data = await response.json();
        currentModel = data.model;
        modelSelect.innerHTML = '';
        (data.choices || []).forEach(m => {
            const opt = document.createElement('option');
            opt.value = m;
            opt.textContent = m;
            modelSelect.appendChild(opt);
        });
        modelSelect.value = currentModel;
        updateModelLabel(currentModel);
    } catch (e) {
        console.error('Failed to load models:', e);
    }
}

function updateModelLabel(modelId) {
    if (modelLabel) {
        const prefix = (I18N_DICT[currentLang] || I18N_DICT.en).model_prefix;
        modelLabel.textContent = `${prefix}${modelId}`;
    }
}

modelSelect.addEventListener('change', async () => {
    const m = modelSelect.value;
    if (!m) return;
    setStatus(`Switching model to ${m}...`);
    const res = await apiCall('/api/model', { model: m });
    if (res && res.ok) {
        currentModel = res.model;
        updateModelLabel(res.model);
        setStatus(`Model switched to ${res.model}`);
    } else {
        setStatus((res && res.status) || 'Failed to switch model.');
        modelSelect.value = currentModel || '';
    }
});

// Initial load
async function init() {
    try {
        loadRetainedPrompt();
        applyLanguage(currentLang);
        const response = await fetch('/api/init');
        if (response.status === 401) {
            window.location.href = '/login';
            return;
        }
        const data = await response.json();
        chatHistory = data.chat_ui_state;
        userProfile.value = data.user_md;
        loadRetainedPrompt(chatHistory);
        renderChat();
        updateButtonStates();
        const t = I18N_DICT[currentLang] || I18N_DICT.en;
        setStatus(t.status_ready);
        loadModels();
    } catch (e) {
        setStatus("Failed to load initial state.");
    }
}

init();
