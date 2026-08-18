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
// ─────────────────────────────────────────────────────────────────────────────

const chatbox = document.getElementById('chatbox');
const messageInput = document.getElementById('message_input');
const sendBtn = document.getElementById('send_btn');
const statusMd = document.querySelector('#status_md p');

const imageInput = document.getElementById('image_input');
const imagePreview = document.getElementById('image_preview');
const clearImageBtn = document.getElementById('clear_image_btn');

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
messageInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

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

// Initial load
async function init() {
    try {
        const response = await fetch('/api/init');
        if (response.status === 401) {
            window.location.href = '/login';
            return;
        }
        const data = await response.json();
        chatHistory = data.chat_ui_state;
        userProfile.value = data.user_md;
        renderChat();
        updateButtonStates();
        setStatus("Ready.");
    } catch (e) {
        setStatus("Failed to load initial state.");
    }
}

init();
