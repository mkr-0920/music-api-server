/**
 * 全局工具函数
 */
const Utils = {
    // 显示/隐藏加载状态
    showLoading(btnElement, loaderElement, btnTextElement, isLoading, loadingText, defaultText) {
        if (loaderElement) loaderElement.classList.toggle('hidden', !isLoading);
        if (btnElement) btnElement.disabled = isLoading;
        if (btnTextElement) btnTextElement.textContent = isLoading ? loadingText : defaultText;
    },

    // 统一结果展示
    displayResult(container, htmlContent) {
        if (container) container.innerHTML = htmlContent;
    },

    // 格式化错误消息
    errorAlert(message) {
        return `<div class="bg-red-100 border-red-400 text-red-700 px-4 py-3 rounded-lg" role="alert"><strong>错误:</strong> ${message}</div>`;
    },

    // 格式化成功消息
    successAlert(message) {
        return `<div class="bg-green-100 border-green-400 text-green-700 px-4 py-3 rounded-lg" role="alert"><strong>成功:</strong> ${message}</div>`;
    },

    // 从 localStorage 获取 API Key
    getApiKey() {
        return localStorage.getItem('api_key') || '';
    },

    // 保存 API Key 到 localStorage
    saveApiKey(key) {
        if (key) localStorage.setItem('api_key', key);
    }
};
