/*
 * Zoe Touch UI Executor
 * Executes backend-issued UI actions with strict allowlist and sends acknowledgements.
 */
(function () {
    const ACTION_TYPES = new Set([
        'navigate',
        'open_panel',
        'focus',
        'fill',
        'submit',
        'create_record',
        'update_record',
        'delete_record',
        'highlight',
        'notify',
        'refresh',
        'click',
    ]);

    const state = {
        panelId: null,
        sessionId: null,
        processing: false,
        pollTimer: null,
        syncTimer: null,
        seenActions: new Set(),
    };

    function getSession() {
        try {
            if (window.zoeAuth && typeof window.zoeAuth.getCurrentSession === 'function') {
                return window.zoeAuth.getCurrentSession();
            }
            const raw = localStorage.getItem('zoe_session');
            return raw ? JSON.parse(raw) : null;
        } catch (e) {
            return null;
        }
    }

    function getPanelId() {
        const params = new URLSearchParams(window.location.search);
        const forced = params.get('panel_id');
        if (forced && forced.trim()) {
            localStorage.setItem('zoe_touch_panel_id', forced.trim());
            return forced.trim();
        }
        let panelId = localStorage.getItem('zoe_touch_panel_id');
        if (!panelId) {
            panelId = 'panel_' + Math.random().toString(36).slice(2, 10);
            localStorage.setItem('zoe_touch_panel_id', panelId);
        }
        return panelId;
    }

    async function api(path, options) {
        const session = getSession();
        const headers = Object.assign({ 'Content-Type': 'application/json' }, options && options.headers ? options.headers : {});
        if (session && session.session_id) {
            headers['X-Session-ID'] = session.session_id;
        }
        return fetch(path, Object.assign({}, options || {}, { headers }));
    }

    function buildContext() {
        return {
            page: location.pathname,
            title: document.title,
            hasModal: !!document.querySelector('[role="dialog"], .modal, .overlay'),
            activeElement: document.activeElement ? document.activeElement.id || document.activeElement.name || document.activeElement.tagName : null,
            timestamp: new Date().toISOString(),
        };
    }

    async function bindPanel() {
        await api('/api/ui/panel/bind', {
            method: 'POST',
            body: JSON.stringify({
                panel_id: state.panelId,
                session_id: state.sessionId || null,
                page: location.pathname,
                is_foreground: true,
                ui_context: buildContext(),
            }),
        });
    }

    async function syncState() {
        try {
            await api('/api/ui/state/sync', {
                method: 'POST',
                body: JSON.stringify({
                    panel_id: state.panelId,
                    session_id: state.sessionId || null,
                    page: location.pathname,
                    is_foreground: true,
                    ui_context: buildContext(),
                }),
            });
        } catch (e) {
            // Non-fatal; periodic sync retries.
        }
    }

    function showToast(message) {
        let toast = document.getElementById('zoeTouchActionToast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'zoeTouchActionToast';
            toast.style.position = 'fixed';
            toast.style.bottom = '18px';
            toast.style.right = '18px';
            toast.style.zIndex = '9999';
            toast.style.background = 'rgba(20,20,20,0.86)';
            toast.style.color = '#fff';
            toast.style.padding = '10px 12px';
            toast.style.borderRadius = '10px';
            toast.style.fontSize = '13px';
            toast.style.maxWidth = '320px';
            document.body.appendChild(toast);
        }
        toast.textContent = message;
        toast.style.display = 'block';
        clearTimeout(showToast._timer);
        showToast._timer = setTimeout(() => {
            toast.style.display = 'none';
        }, 2500);
    }

    function resolveSelector(selector) {
        if (!selector || typeof selector !== 'string') return null;
        try {
            return document.querySelector(selector);
        } catch (e) {
            return null;
        }
    }

    async function executeAction(action) {
        const actionType = action.action_type;
        const payload = action.payload || {};

        if (!ACTION_TYPES.has(actionType)) {
            return { status: 'blocked', error_code: 'unsupported_action', error_message: `Unsupported action: ${actionType}` };
        }

        try {
            if (actionType === 'navigate') {
                const page = payload.page || payload.path || payload.url;
                if (!page) return { status: 'failed', error_code: 'missing_page', error_message: 'Missing page/path for navigate' };
                showToast(`Navigating to ${page}`);
                setTimeout(() => { window.location.href = page; }, 150);
                return { status: 'success' };
            }

            if (actionType === 'notify') {
                showToast(payload.message || payload.title || 'Action completed');
                return { status: 'success' };
            }

            if (actionType === 'refresh') {
                showToast('Refreshing data');
                setTimeout(() => window.location.reload(), 120);
                return { status: 'success' };
            }

            if (actionType === 'focus') {
                const el = resolveSelector(payload.selector);
                if (!el) return { status: 'failed', error_code: 'selector_not_found', error_message: 'Focus selector not found' };
                el.focus();
                return { status: 'success' };
            }

            if (actionType === 'fill') {
                const el = resolveSelector(payload.selector);
                if (!el) return { status: 'failed', error_code: 'selector_not_found', error_message: 'Fill selector not found' };
                el.value = payload.value != null ? String(payload.value) : '';
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return { status: 'success' };
            }

            if (actionType === 'click' || actionType === 'submit') {
                const el = resolveSelector(payload.selector);
                if (!el) return { status: 'failed', error_code: 'selector_not_found', error_message: 'Click selector not found' };
                el.click();
                return { status: 'success' };
            }

            if (actionType === 'highlight' || actionType === 'open_panel' || actionType === 'create_record' || actionType === 'update_record' || actionType === 'delete_record') {
                // Deterministic/no-risk local behavior only. Unknown commands are blocked.
                if (actionType === 'highlight' && payload.selector) {
                    const el = resolveSelector(payload.selector);
                    if (el) {
                        const prev = el.style.outline;
                        el.style.outline = '2px solid #7B61FF';
                        setTimeout(() => { el.style.outline = prev; }, 1800);
                        return { status: 'success' };
                    }
                }
                return {
                    status: 'blocked',
                    error_code: 'manual_required',
                    error_message: `${actionType} requires module-specific executor mapping`,
                };
            }

            return { status: 'blocked', error_code: 'not_implemented', error_message: `${actionType} not implemented` };
        } catch (e) {
            return { status: 'failed', error_code: 'execution_error', error_message: String(e) };
        }
    }

    async function ackAction(actionId, result) {
        await api(`/api/ui/actions/${actionId}/ack`, {
            method: 'POST',
            body: JSON.stringify({
                status: result.status,
                error_code: result.error_code || null,
                error_message: result.error_message || null,
                ui_context: buildContext(),
            }),
        });
    }

    async function pollActions() {
        if (state.processing) return;
        state.processing = true;
        try {
            const res = await api(`/api/ui/actions/pending?panel_id=${encodeURIComponent(state.panelId)}&limit=10`);
            if (!res.ok) return;
            const data = await res.json();
            const actions = Array.isArray(data.actions) ? data.actions : [];
            for (const action of actions) {
                if (!action || !action.id) continue;
                const attemptKey = `${action.id}:${Number(action.retry_count || 0)}`;
                if (state.seenActions.has(attemptKey)) continue;
                state.seenActions.add(attemptKey);
                const result = await executeAction(action);
                await ackAction(action.id, result);
            }
        } catch (e) {
            // polling retries on next interval
        } finally {
            state.processing = false;
        }
    }

    function init() {
        state.panelId = getPanelId();
        const session = getSession();
        state.sessionId = session && session.session_id ? session.session_id : null;
        bindPanel().catch(() => {});
        syncState().catch(() => {});
        state.pollTimer = setInterval(pollActions, 2000);
        state.syncTimer = setInterval(syncState, 5000);
        window.addEventListener('beforeunload', () => {
            if (state.pollTimer) clearInterval(state.pollTimer);
            if (state.syncTimer) clearInterval(state.syncTimer);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
