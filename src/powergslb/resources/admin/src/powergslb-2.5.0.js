// ====================================================
// Theme
// ====================================================

const themeStorageKey = 'powergslb.theme';

// Resolve and apply the theme synchronously during head parse
(function () {
    const stored = localStorage.getItem(themeStorageKey);
    const theme = stored || (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    document.documentElement.setAttribute('data-theme', theme);
})();

const themeToolbarItem = () => ({ id: 'theme', type: 'button', icon: 'pg-icon-theme', tooltip: 'Toggle theme' });
const logoutToolbarItem = () => ({ id: 'logout', type: 'button', text: 'Logout', tooltip: 'Logout' });

const toggleTheme = () => {
    const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem(themeStorageKey, next);
};

const logoutToolbarClick = () => {
    // Logout: send DELETE request to server to clear HTTP-only cookie
    fetch('/logout', { 
        method: 'DELETE',
        credentials: 'include'
    })
    .then(() => {
        window.location.href = '/login';
    })
    .catch(err => {
        console.error('Logout failed:', err);
        window.location.href = '/login';
    });
};

const themeToolbarClick = (event) => {
    const targetId = event.detail?.item?.id || event.target;

    if (targetId === 'theme') toggleTheme();
    if (targetId === 'logout') logoutToolbarClick();
};

// ====================================================
// Request handling
// ====================================================

// Note: HTTP-only cookies are automatically sent by the browser
// with all requests (GET, POST, etc.) so no need to manually add
// Authorization header with Bearer token.


// ====================================================
// Auto Reload Management for Status Grid
// ====================================================

let reloadIntervalId = 0;
const reloadInterval = 5000; // 5 seconds

const startAutoReload = function () {
    if (reloadIntervalId === 0) {
        reloadIntervalId = setInterval(function () {
            if (w2ui.gridStatus) {
                w2ui.gridStatus.reload();
            }
        }, reloadInterval);
        if (w2ui.gridStatus && w2ui.gridStatus.toolbar) {
            w2ui.gridStatus.toolbar.check('reload');
        }
        if (w2ui.gridStatus) {
            w2ui.gridStatus.reload();
        }
    }
};

const stopAutoReload = function () {
    if (reloadIntervalId !== 0) {
        clearInterval(reloadIntervalId);
        reloadIntervalId = 0;
        if (w2ui.gridStatus && w2ui.gridStatus.toolbar) {
            w2ui.gridStatus.toolbar.uncheck('reload');
        }
    }
};

// Note: HTTP-only cookies are automatically sent by the browser with all requests.


// ====================================================
// Response Format Transformer for w2ui 2.0 (Grids & Lists)
// ====================================================

const originalJSON = Response.prototype.json;
Response.prototype.json = function() {
    return originalJSON.call(this).then(data => {
        if (this.url && this.url.includes('/admin/w2ui')) {
            // Unwrapping list items for w2ui 2.0 drop-downs
            if (data && data.status === 'success' && data.records && !data.total) {
                const items = data.records.map(r => {
                    const val = r.domain || r.name_type || r.policy || r.monitor || r.view || r.user || r.name || r.recid;
                    return { id: String(val), text: String(val) };
                });
                return { status: 'success', items: items };
            }
            // Unwrapping grid records
            if (data && data.status && data.total !== undefined && data.records !== undefined) {
                return {
                    total: data.total,
                    records: data.records
                };
            }
        }
        return data;
    });
};

// ====================================================
// Helper functions
// ====================================================

const w2uiUrl = '/admin/w2ui';

// Custom date/time picker styled for w2ui 2.0 (Clean HTML)
function openDateTimePicker(inputElement, form = null) {
    const currentValue = inputElement.value || '';
    const [currentDate, currentTime] = currentValue.split(' ');
    const [year, month, day] = currentDate ? currentDate.split('-') : [
        new Date().getFullYear().toString(),
        String(new Date().getMonth() + 1).padStart(2, '0'),
        String(new Date().getDate()).padStart(2, '0')
    ];
    const [hours, mins] = currentTime ? currentTime.split(':') : ['00', '00'];

    const overlay = document.createElement('div');
    overlay.className = 'custom-dt-overlay';
    overlay.setAttribute('tabindex', '0');

    const modal = document.createElement('div');
    modal.className = 'custom-dt-modal';

    modal.innerHTML = `
        <div class="custom-dt-title">
            Select Date & Time
        </div>
        
        <div class="custom-dt-body">
            <div class="custom-dt-row">
                <label class="custom-dt-label">Year:</label>
                <div class="custom-dt-input-wrap">
                    <select class="w2ui-input" id="picker-year" style="width: 100%; height: 30px;">
                        ${Array.from({length: 31}, (_, i) => 2026 + i)
                            .map(y => `<option value="${y}" ${y === parseInt(year) ? 'selected' : ''}>${y}</option>`)
                            .join('')}
                    </select>
                </div>
            </div>
            
            <div class="custom-dt-row">
                <label class="custom-dt-label">Month:</label>
                <div class="custom-dt-input-wrap">
                    <select class="w2ui-input" id="picker-month" style="width: 100%; height: 30px;">
                        ${['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
                            .map((m, i) => `<option value="${String(i + 1).padStart(2, '0')}" ${(i + 1) === parseInt(month) ? 'selected' : ''}>${m}</option>`)
                            .join('')}
                    </select>
                </div>
            </div>
            
            <div class="custom-dt-row">
                <label class="custom-dt-label">Day:</label>
                <div class="custom-dt-input-wrap">
                    <select class="w2ui-input" id="picker-day" style="width: 100%; height: 30px;">
                        ${Array.from({length: 31}, (_, i) => i + 1)
                            .map(d => `<option value="${String(d).padStart(2, '0')}" ${d === parseInt(day) ? 'selected' : ''}>${d}</option>`)
                            .join('')}
                    </select>
                </div>
            </div>
            
            <div class="custom-dt-row">
                <label class="custom-dt-label">Time:</label>
                <div class="custom-dt-time-wrap">
                    <select class="w2ui-input" id="picker-hour" style="flex: 1; height: 30px;">
                        ${Array.from({length: 24}, (_, i) => String(i).padStart(2, '0'))
                            .map(h => `<option value="${h}" ${h === hours ? 'selected' : ''}>${h}</option>`)
                            .join('')}
                    </select>
                    <span>:</span>
                    <select class="w2ui-input" id="picker-minute" style="flex: 1; height: 30px;">
                        ${Array.from({length: 60}, (_, i) => String(i).padStart(2, '0'))
                            .map(m => `<option value="${m}" ${m === mins ? 'selected' : ''}>${m}</option>`)
                            .join('')}
                    </select>
                </div>
            </div>
        </div>
        
        <div class="custom-dt-footer">
            <button class="w2ui-btn" id="picker-cancel">Cancel</button>
            <button class="w2ui-btn w2ui-btn-blue" id="picker-ok">OK</button>
        </div>
    `;

    overlay.appendChild(modal);
    document.body.appendChild(overlay);
    overlay.focus();

    const closeDatePicker = () => overlay.remove();

    overlay.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            e.stopPropagation(); 
            e.preventDefault(); 
            closeDatePicker();
        }
    }, true);

    document.getElementById('picker-ok').addEventListener('click', () => {
        const y = document.getElementById('picker-year').value;
        const m = document.getElementById('picker-month').value;
        const d = document.getElementById('picker-day').value;
        const h = document.getElementById('picker-hour').value;
        const min = document.getElementById('picker-minute').value;
        const result = `${y}-${m}-${d} ${h}:${min}:00`;
        
        inputElement.value = result;
        
        if (form) {
            form.record['expires_at'] = result;
        }
        
        inputElement.dispatchEvent(new Event('change', { bubbles: true }));
        closeDatePicker();
    });
    
    document.getElementById('picker-cancel').addEventListener('click', closeDatePicker);
    
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) closeDatePicker();
    });
}

const prepareFormRecord = (form, rawRecord) => {
    if (!rawRecord) return {};
    const rec = structuredClone(rawRecord);
    
    form.fields.forEach(field => {
        const val = rec[field.field];
        if ((field.type === 'list' || field.type === 'combo') && val !== undefined && val !== null && typeof val !== 'object') {
            rec[field.field] = { id: String(val), text: String(val) };
        }
    });
    return rec;
};

// Load dropdown list items
const loadListItems = async (formName) => {
    const form = w2ui[formName];
    
    const listFieldsMap = {
        'formRecords': [
            { field: 'domain', cmd: 'domains' },
            { field: 'name_type', cmd: 'types' },
            { field: 'policy', cmd: 'routings' },
            { field: 'monitor', cmd: 'monitors' },
            { field: 'view', cmd: 'views' }
        ],
        'formJwtTokens': [
            { field: 'user_id', cmd: 'users', queryField: 'user' }
        ]
    };
    
    const fieldsToLoad = listFieldsMap[formName];
    if (!fieldsToLoad) return;
    
    const timeoutPromise = new Promise((_, reject) => 
        setTimeout(() => reject(new Error('Items load timeout')), 3000)
    );
    
    try {
        await Promise.race([
            Promise.all(fieldsToLoad.map(async ({ field, cmd, queryField }) => {
                const fieldDef = form.fields.find(f => f.field === field);
                if (!fieldDef) return;
                
                try {
                    const params = new URLSearchParams();
                    params.set('cmd', 'get-items');
                    params.set('data', cmd);
                    params.set('field', queryField || field);
                    
                    const response = await fetch(w2uiUrl, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded'
                        },
                        credentials: 'include',
                        body: params.toString()
                    });
                    
                    const data = await response.json();
                    
                    if (data.items && Array.isArray(data.items)) {
                        fieldDef.options = fieldDef.options || {};
                        fieldDef.options.items = data.items.map(item => {
                            if (typeof item === 'object' && item !== null) return item;
                            return { id: String(item), text: String(item) };
                        });
                        fieldDef.options.match = 'contains';
                        fieldDef.options.filter = true;
                    }
                } catch (err) {
                    console.error(`Failed to load items for ${field}:`, err.message);
                }
            })),
            timeoutPromise
        ]);
    } catch (err) {
        console.warn(`Items loading issue for ${formName}:`, err.message);
    }
};

const openPopupForm = (event, recordName, popupWidth, popupHeight, formName) => {
    const form = w2ui[formName];
    const grid = w2ui[event.target];
    let formTitle = '';
    let currentRecord = {};
    let isEditMode = false;

    switch (event.type) {
        case 'add':
            formTitle = `PowerGSLB: add ${recordName}`;
            form.clear();
            currentRecord = {};
            form.recid = 0;
            isEditMode = false;
            break;
            
        case 'dblClick':
        case 'edit': {
            formTitle = `PowerGSLB: edit ${recordName}`;
            const recid = event.recid || grid.getSelection()[0];
            if (!recid) return;
            
            const rawRec = grid.get(recid);
            if (!rawRec) return;
            
            form.recid = recid;
            const rec = JSON.parse(JSON.stringify(rawRec));
            
            if (typeof prepareFormRecord === 'function') {
                currentRecord = prepareFormRecord(form, rec);
            } else {
                currentRecord = rec;
            }
            
            form.fields.forEach(field => {
                if ((field.type === 'list' || field.type === 'combo') && currentRecord[field.field]) {
                    const val = currentRecord[field.field];
                    field.options = field.options || {};
                    if (typeof val === 'object' && val !== null && val.id !== undefined) {
                        field.options.items = [val];
                    } else if (val !== null && val !== undefined) {
                        field.options.items = [{ id: String(val), text: String(val) }];
                    }
                    field.options.match = 'contains';
                    field.options.filter = true;
                }
            });
            isEditMode = true;
            break;
        }
    }

    form.record = currentRecord;
    const savedRecord = structuredClone(currentRecord);

    w2popup.open({
        title: formTitle,
        body: `<div id="popup-form-container" style="width: 100%; height: 100%; opacity: 0; transition: opacity 0.15s ease-in-out;"></div>`,
        style: 'padding: 0px;',
        width: popupWidth,
        height: popupHeight
    });
    
    setTimeout(async () => {
        const container = document.getElementById('popup-form-container');
        if (!container) return;

        await loadListItems(formName);
        
        form.box = '#popup-form-container';
        form.render('#popup-form-container');
        
        if (Object.keys(savedRecord).length > 0) {
            form.record = structuredClone(savedRecord);
        }
        
        // Refresh form to apply dropdown items (must be after render)
        form.refresh();

        if (formName === 'formJwtTokens') {
            if (!isEditMode) {
                setupJwtTokensCreateMode(form, container);
            } else {
                setupJwtTokensEditMode(form, savedRecord, container);
            }
        } else {
            // For normal forms, show immediately
            container.style.opacity = '1';
        }
        
        form.show.spinner = false;
        form.unlock();
    }, 50);
};


let jwtEditModeActive = false;

const setupJwtTokensCreateMode = (form, container) => {
    jwtEditModeActive = false;
    
    setTimeout(() => {
        const tokenInput = document.querySelector('input[name="token"]');
        const recidInput = document.querySelector('input[name="recid"]');
        const userNameInput = document.querySelector('input[name="userName"]');
        const nameInput = document.querySelector('input[name="name"]');
        const userInput = document.querySelector('input[name="user_id"]');
        const expiresInput = document.querySelector('input[name="expires_at"]');
        
        if (tokenInput) {
            let fieldRow = tokenInput.closest('.w2ui-field');
            if (fieldRow) fieldRow.style.display = 'none';
        }
        if (recidInput) {
            let fieldRow = recidInput.closest('.w2ui-field');
            if (fieldRow) fieldRow.style.display = 'none';
        }
        if (userNameInput) {
            let fieldRow = userNameInput.closest('.w2ui-field');
            if (fieldRow) fieldRow.style.display = 'none';
        }
        
        if (nameInput) {
            nameInput.readOnly = false;
            nameInput.focus();
        }
        if (userInput) {
            userInput.disabled = false;
        }
        if (expiresInput) {
            expiresInput.style.cursor = 'pointer';
            expiresInput.readOnly = true;
            if (!expiresInput.dataset.calendarAttached) {
                expiresInput.addEventListener('click', () => {
                    openDateTimePicker(expiresInput, form);
                });
                expiresInput.dataset.calendarAttached = 'true';
            }
        }
        
        if (container) container.style.opacity = '1';
    }, 50);
};

const setupJwtTokensEditMode = (form, savedRecord, container) => {
    jwtEditModeActive = true;
    
    setTimeout(() => {
        const page = document.querySelector('#popup-form-container .w2ui-page');
        if (!page) {
            if (container) container.style.opacity = '1';
            return;
        }
        
        form.record = { ...savedRecord };
        form.fields.forEach(f => f.required = false);

        page.querySelectorAll('.w2ui-field').forEach(el => {
            el.style.display = 'none';
        });

        let customUi = document.getElementById('jwt-custom-ui');
        if (!customUi) {
            customUi = document.createElement('div');
            customUi.id = 'jwt-custom-ui';
            page.prepend(customUi);
        }

        const isEnabled = savedRecord.enabled == 1 || savedRecord.enabled === true || savedRecord.enabled === '1';
        form.record.enabled = isEnabled ? 1 : 0;

        customUi.innerHTML = `
            <div class="jwt-custom-panel">
                <div class="jwt-row">
                    <strong class="jwt-label">Token Name:</strong> 
                    <span class="jwt-value">${savedRecord.name || 'N/A'}</span>
                </div>
                <div class="jwt-row">
                    <strong class="jwt-label">User:</strong> 
                    <span class="jwt-value">${savedRecord.userName || savedRecord.user_id || 'N/A'}</span>
                </div>
                <div class="jwt-row jwt-row-margin">
                    <strong class="jwt-label">Expires:</strong> 
                    <span class="jwt-value">${savedRecord.expires_at || 'Never'}</span>
                </div>
                
                <div class="jwt-checkbox-row">
                    <label for="jwt-real-enabled" class="jwt-label pointer">Enabled:</label>
                    <input type="checkbox" id="jwt-real-enabled" ${isEnabled ? 'checked' : ''}>
                </div>

                <div class="jwt-token-row">
                    <input type="text" readonly value="${savedRecord.token || ''}" class="w2ui-input jwt-token-input">
                    <button id="jwt-copy-btn" type="button" class="w2ui-btn">📋 Copy</button>
                </div>
            </div>
        `;

        const copyBtn = document.getElementById('jwt-copy-btn');
        if (copyBtn) {
            copyBtn.onclick = (e) => {
                e.preventDefault();
                navigator.clipboard.writeText(savedRecord.token || '');
                copyBtn.textContent = '✅ Copied!';
                setTimeout(() => copyBtn.textContent = '📋 Copy', 2000);
            };
        }

        const cb = document.getElementById('jwt-real-enabled');
        if (cb) {
            cb.onchange = (e) => {
                form.record.enabled = e.target.checked ? 1 : 0;
            };
        }
        
        if (container) container.style.opacity = '1';
    }, 50);
};

const gridPopupForm = (event) => {
    const map = {
        gridDomains: ['domain', 400, 200, 'formDomains'],
        gridJwtTokens: ['token', 400, 300, 'formJwtTokens'],
        gridMonitors: ['monitor', 400, 250, 'formMonitors'],
        gridRecords: ['record', 400, 500, 'formRecords'],
        gridRoutings: ['routing', 400, 250, 'formRoutings'],
        gridTypes: ['type', 400, 245, 'formTypes'],
        gridUsers: ['user', 400, 245, 'formUsers'],
        gridViews: ['view', 400, 250, 'formViews']
    };

    if (map[event.target]) {
        openPopupForm(event, ...map[event.target]);
    }
};

const reformatJson = (value, indent) => {
    try {
        const parsed = JSON.parse(value);
        if (indent) return JSON.stringify(parsed, null, indent);
        return JSON.stringify(parsed, null, 1).replace(/,\n\s*/g, ', ').replace(/\n\s*/g, '');
    } catch {
        return value;
    }
};

const expandJson = (value) => reformatJson(value, 2);
const collapseJson = (value) => reformatJson(value, 0);
const expandTokens = (value) => (value ? value.split(/\s+/).filter(Boolean).join('\n') : '');
const collapseTokens = (value) => (value ? value.split(/\s+/).filter(Boolean).join(' ') : '');

const textareaFields = {
    formMonitors: { field: 'monitor_json', expand: expandJson, collapse: collapseJson },
    formRoutings: { field: 'policy_json', expand: expandJson, collapse: collapseJson },
    formViews: { field: 'rule', expand: expandTokens, collapse: collapseTokens }
};

const transformField = (record, field, transform) => {
    if (typeof record[field] === 'string') {
        record[field] = transform(record[field]);
    }
};

const panelStyle = 'border: 1px solid var(--w2ui-border-color, #dfdfdf); padding: 5px;';

// ====================================================
// Config Definition
// ====================================================

const config = {
    layout: {
        name: 'layout',
        panels: [
            { type: 'left', size: 140, style: panelStyle },
            { type: 'main', style: panelStyle }
        ]
    },

    sidebar: {
        name: 'sidebar',
        nodes: [
            {
                id: 'status', text: 'Status', expanded: true, group: true,
                nodes: [{ id: 'gridStatus', text: 'Status', icon: 'w2ui-icon-page', selected: true }]
            },
            {
                id: 'gslb', text: 'GSLB', expanded: true, group: true,
                nodes: [
                    { id: 'gridDomains', text: 'Domains', icon: 'w2ui-icon-page' },
                    { id: 'gridMonitors', text: 'Monitors', icon: 'w2ui-icon-page' },
                    { id: 'gridRecords', text: 'Records', icon: 'w2ui-icon-page' },
                    { id: 'gridRoutings', text: 'Routings', icon: 'w2ui-icon-page' },
                    { id: 'gridTypes', text: 'Types', icon: 'w2ui-icon-page' },
                    { id: 'gridViews', text: 'Views', icon: 'w2ui-icon-page' }
                ]
            },
            {
                id: 'admin', text: 'Admin', expanded: true, group: true,
                nodes: [
                    { id: 'gridUsers', text: 'Users', icon: 'w2ui-icon-page' },
                    { id: 'gridJwtTokens', text: 'Tokens', icon: 'w2ui-icon-page' },
                    { id: 'gridAudit', text: 'Audit', icon: 'w2ui-icon-page' }
                ]
            }
        ],
        onClick(event) {
            if (event.target !== 'gridStatus') stopAutoReload();
            if (w2ui[event.target]) {
                w2ui.layout.html('main', w2ui[event.target]);
            }
        }
    },

    gridStatus: {
        name: 'gridStatus',
        postData: { cmd: 'get-records', data: 'status' },
        url: w2uiUrl,
        columns: [
            { field: 'recid', text: 'ID', size: '50px', sortable: true },
            { 
                field: 'status', 
                text: 'Status', 
                size: '70px', 
                sortable: true, 
                render: (record) => {
                    const status = record.status || 'Unknown';
                    // Динамічно формуємо назву класу: 'status-On', 'status-Off'
                    return `
                        <div class="pg-status-cell status-${status}">
                            <div class="status-indicator"></div>
                            ${status}
                        </div>
                    `;
                }
            },
            { field: 'domain', text: 'Domain', size: '100px', sortable: true },
            { field: 'name', text: 'Name', size: '100px', sortable: true },
            { field: 'name_type', text: 'Type', size: '60px', sortable: true },
            { field: 'content', text: 'Content', size: '510px', sortable: true },
            { field: 'ttl', text: 'TTL', size: '55px', sortable: true },
            { field: 'disabled', text: 'Disabled', size: '65px', sortable: true },
            { field: 'weight', text: 'Weight', size: '55px', sortable: true },
            { field: 'policy', text: 'Routing', size: '150px', sortable: true },
            { field: 'monitor', text: 'Monitor', size: '150px', sortable: true },
            { field: 'view', text: 'View', size: '100px', sortable: true }
        ],
        searches: [
            { field: 'recid', label: 'ID', type: 'int' },
            { field: 'status', label: 'Status', type: 'text' },
            { field: 'domain', label: 'Domain', type: 'text' },
            { field: 'name', label: 'Name', type: 'text' },
            { field: 'name_type', label: 'Type', type: 'text' },
            { field: 'content', label: 'Content', type: 'text' },
            { field: 'ttl', label: 'TTL', type: 'int' },
            { field: 'disabled', label: 'Disabled', type: 'int' },
            { field: 'weight', label: 'Weight', type: 'int' },
            { field: 'policy', label: 'Routing', type: 'text' },
            { field: 'monitor', label: 'Monitor', type: 'text' },
            { field: 'view', label: 'View', type: 'text' }
        ],
        show: { footer: true, toolbar: true, toolbarReload: true, toolbarColumns: true, toolbarSearch: true },
        sortData: [{ field: 'status', direction: 'asc' }, { field: 'recid', direction: 'asc' }],
        toolbar: {
            items: [
                { id: 'break', type: 'break' },
                { id: 'reload', type: 'check', text: 'Auto', icon: 'w2ui-icon-reload', checked: false, tooltip: 'Auto reload' },
                { type: 'spacer' },
                themeToolbarItem(),
                logoutToolbarItem()
            ],
            onClick(event) {
                if (event.target === 'reload') {
                    event.preventDefault();
                    reloadIntervalId === 0 ? startAutoReload() : stopAutoReload();
                } else {
                    themeToolbarClick(event);
                }
            }
        },
        onSelect(event) { event.preventDefault(); },
        onRequest(event) {
            currentGridDataType = 'status';
            const params = new URLSearchParams(event.postData);
            params.set('cmd', 'get-records');
            params.set('data', 'status');
            event.postData = params.toString();
            event.method = 'POST';
        }
    },

    gridDomains: {
        name: 'gridDomains',
        postData: { cmd: 'get-records', data: 'domains' },
        url: w2uiUrl,
        columns: [
            { field: 'recid', text: 'ID', size: '50px', sortable: true },
            { field: 'domain', text: 'Domain', size: '250px', sortable: true },
            { field: 'description', text: 'Description', size: '750px', sortable: true }
        ],
        searches: [
            { field: 'recid', label: 'ID', type: 'int' },
            { field: 'domain', label: 'Domain', type: 'text' },
            { field: 'description', label: 'Description', type: 'text' }
        ],
        onRequest(event) {
            currentGridDataType = 'domains';
            const params = new URLSearchParams(event.postData);
            params.set('cmd', 'get-records');
            params.set('data', 'domains');
            event.postData = params.toString();
            event.method = 'POST';
        }
    },

    formDomains: {
        name: 'formDomains',
        url: w2uiUrl,
        fields: [
            { field: 'domain', type: 'text', required: true, html: { label: 'Domain' } },
            { field: 'description', type: 'text', required: false, html: { label: 'Description' } }
        ],
        onLoad(event) {
            event.preventDefault();
            return false;
        }
    },

    gridMonitors: {
        name: 'gridMonitors',
        postData: { cmd: 'get-records', data: 'monitors' },
        url: w2uiUrl,
        columns: [
            { field: 'recid', text: 'ID', size: '50px', sortable: true },
            { field: 'monitor', text: 'Monitor', size: '250px', sortable: true },
            { field: 'monitor_json', text: 'Parameters', size: '750px', sortable: true }
        ],
        searches: [
            { field: 'recid', label: 'ID', type: 'int' },
            { field: 'monitor', label: 'Monitor', type: 'text' },
            { field: 'monitor_json', label: 'Parameters', type: 'text' }
        ],
        onRequest(event) {
            currentGridDataType = 'monitors';
            const params = new URLSearchParams(event.postData);
            params.set('cmd', 'get-records');
            params.set('data', 'monitors');
            event.postData = params.toString();
            event.method = 'POST';
        }
    },

    formMonitors: {
        name: 'formMonitors',
        url: w2uiUrl,
        fields: [
            { field: 'monitor', type: 'text', required: true, html: { label: 'Monitor' } },
            { field: 'monitor_json', type: 'textarea', required: true, html: { label: 'Parameters' } }
        ],
        onLoad(event) {
            event.preventDefault();
            return false;
        }
    },

    gridRecords: {
        name: 'gridRecords',
        postData: { cmd: 'get-records', data: 'records' },
        url: w2uiUrl,
        columns: [
            { field: 'recid', text: 'ID', size: '50px', sortable: true },
            { field: 'domain', text: 'Domain', size: '100px', sortable: true },
            { field: 'name', text: 'Name', size: '100px', sortable: true },
            { field: 'name_type', text: 'Type', size: '60px', sortable: true },
            { field: 'content', text: 'Content', size: '510px', sortable: true },
            { field: 'ttl', text: 'TTL', size: '55px', sortable: true },
            { field: 'disabled', text: 'Disabled', size: '65px', sortable: true },
            { field: 'weight', text: 'Weight', size: '55px', sortable: true },
            { field: 'policy', text: 'Routing', size: '150px', sortable: true },
            { field: 'monitor', text: 'Monitor', size: '150px', sortable: true },
            { field: 'view', text: 'View', size: '100px', sortable: true }
        ],
        searches: [
            { field: 'recid', label: 'ID', type: 'int' },
            { field: 'domain', label: 'Domain', type: 'text' },
            { field: 'name', label: 'Name', type: 'text' },
            { field: 'name_type', label: 'Type', type: 'text' },
            { field: 'content', label: 'Content', type: 'text' },
            { field: 'ttl', label: 'TTL', type: 'int' },
            { field: 'disabled', label: 'Disabled', type: 'int' },
            { field: 'weight', label: 'Weight', type: 'int' },
            { field: 'policy', label: 'Routing', type: 'text' },
            { field: 'monitor', label: 'Monitor', type: 'text' },
            { field: 'view', label: 'View', type: 'text' }
        ],
        onRequest(event) {
            currentGridDataType = 'records';
            const params = new URLSearchParams(event.postData);
            params.set('cmd', 'get-records');
            params.set('data', 'records');
            event.postData = params.toString();
            event.method = 'POST';
        }
    },

    formRecords: {
        name: 'formRecords',
        url: w2uiUrl,
        focus: 1,
        onLoad(event) {
            event.preventDefault();
            return false;
        },
        fields: [
            {
                field: 'domain', type: 'list', required: true, html: { label: 'Domain' },
                options: { items: [], openOnFocus: false, filter: true, match: 'contains' }
            },
            { field: 'name', type: 'text', required: true, html: { label: 'Name:' } },
            {
                field: 'name_type', type: 'list', required: true, html: { label: 'Type' },
                options: { items: [], openOnFocus: false, filter: true, match: 'contains' }
            },
            { field: 'content', type: 'text', required: true, html: { label: 'Content' } },
            { field: 'ttl', type: 'int', required: true, html: { label: 'TTL' } },
            { field: 'disabled', type: 'toggle', required: false, html: { label: 'Disabled' } },
            { field: 'weight', type: 'int', required: false, html: { label: 'Weight' } },
            {
                field: 'policy', type: 'list', required: true, html: { label: 'Routing' },
                options: { items: [], openOnFocus: false, filter: true, match: 'contains' }
            },
            {
                field: 'monitor', type: 'list', required: true, html: { label: 'Monitor' },
                options: { items: [], openOnFocus: false, filter: true, match: 'contains' }
            },
            {
                field: 'view', type: 'list', required: true, html: { label: 'View' },
                options: { items: [], openOnFocus: false, filter: true, match: 'contains' }
            }
        ]
    },

    gridRoutings: {
        name: 'gridRoutings',
        postData: { cmd: 'get-records', data: 'routings' },
        url: w2uiUrl,
        columns: [
            { field: 'recid', text: 'ID', size: '50px', sortable: true },
            { field: 'policy', text: 'Policy', size: '250px', sortable: true },
            { field: 'policy_json', text: 'Parameters', size: '750px', sortable: true }
        ],
        searches: [
            { field: 'recid', label: 'ID', type: 'int' },
            { field: 'policy', label: 'Policy', type: 'text' },
            { field: 'policy_json', label: 'Parameters', type: 'text' }
        ],
        onRequest(event) {
            currentGridDataType = 'routings';
            const params = new URLSearchParams(event.postData);
            params.set('cmd', 'get-records');
            params.set('data', 'routings');
            event.postData = params.toString();
            event.method = 'POST';
        }
    },

    formRoutings: {
        name: 'formRoutings',
        url: w2uiUrl,
        fields: [
            { field: 'policy', type: 'text', required: true, html: { label: 'Policy' } },
            { field: 'policy_json', type: 'textarea', required: true, html: { label: 'Parameters' } }
        ],
        onLoad(event) {
            event.preventDefault();
            return false;
        }
    },

    gridTypes: {
        name: 'gridTypes',
        postData: { cmd: 'get-records', data: 'types' },
        url: w2uiUrl,
        columns: [
            { field: 'recid', text: 'Value', size: '50px', sortable: true },
            { field: 'name_type', text: 'Type', size: '250px', sortable: true },
            { field: 'description', text: 'Description', size: '750px', sortable: true }
        ],
        searches: [
            { field: 'recid', label: 'Value', type: 'int' },
            { field: 'name_type', label: 'Type', type: 'text' },
            { field: 'description', label: 'Description', type: 'text' }
        ],
        onRequest(event) {
            currentGridDataType = 'types';
            const params = new URLSearchParams(event.postData);
            params.set('cmd', 'get-records');
            params.set('data', 'types');
            event.postData = params.toString();
            event.method = 'POST';
        }
    },

    formTypes: {
        name: 'formTypes',
        url: w2uiUrl,
        onLoad(event) {
            event.preventDefault();
            return false;
        },
        fields: [
            { field: 'recid', type: 'int', required: true, html: { label: 'Value' } },
            { field: 'name_type', type: 'text', required: true, html: { label: 'Type' } },
            { field: 'description', type: 'text', required: true, html: { label: 'Description' } }
        ]
    },

    gridViews: {
        name: 'gridViews',
        postData: { cmd: 'get-records', data: 'views' },
        url: w2uiUrl,
        columns: [
            { field: 'recid', text: 'ID', size: '50px', sortable: true },
            { field: 'view', text: 'View', size: '250px', sortable: true },
            { field: 'rule', text: 'Rule', size: '750px', sortable: true }
        ],
        searches: [
            { field: 'recid', label: 'ID', type: 'int' },
            { field: 'view', label: 'View', type: 'text' },
            { field: 'rule', label: 'Rule', type: 'text' }
        ],
        onRequest(event) {
            currentGridDataType = 'views';
            const params = new URLSearchParams(event.postData);
            params.set('cmd', 'get-records');
            params.set('data', 'views');
            event.postData = params.toString();
            event.method = 'POST';
        }
    },

    formViews: {
        name: 'formViews',
        url: w2uiUrl,
        fields: [
            { field: 'view', type: 'text', required: true, html: { label: 'View' } },
            { field: 'rule', type: 'textarea', required: true, html: { label: 'Rule' } }
        ],
        onLoad(event) {
            event.preventDefault();
            return false;
        }
    },

    gridUsers: {
        name: 'gridUsers',
        postData: { cmd: 'get-records', data: 'users' },
        url: w2uiUrl,
        columns: [
            { field: 'recid', text: 'ID', size: '50px', sortable: true },
            { field: 'user', text: 'User', size: '250px', sortable: true },
            { field: 'name', text: 'Name', size: '750px', sortable: true }
        ],
        searches: [
            { field: 'recid', label: 'ID', type: 'int' },
            { field: 'user', label: 'User', type: 'text' },
            { field: 'name', label: 'Name', type: 'text' }
        ],
        onRequest(event) {
            currentGridDataType = 'users';
            const params = new URLSearchParams(event.postData);
            params.set('cmd', 'get-records');
            params.set('data', 'users');
            event.postData = params.toString();
            event.method = 'POST';
        }
    },

    formUsers: {
        name: 'formUsers',
        url: w2uiUrl,
        onLoad(event) {
            event.preventDefault();
            return false;
        },
        fields: [
            { field: 'user', type: 'text', required: true, html: { label: 'User' } },
            { field: 'name', type: 'text', required: true, html: { label: 'Name' } },
            { field: 'password', type: 'password', required: true, html: { label: 'Password' } }
        ]
    },

    // ====================================================
    // JWT Tokens
    // ====================================================

    gridJwtTokens: {
        name: 'gridJwtTokens',
        postData: { cmd: 'get-records', data: 'jwt_tokens' },
        url: w2uiUrl,
        columns: [
            { field: 'recid', text: 'ID', size: '50px', sortable: true },
            { field: 'name', text: 'Name', size: '200px', sortable: true },
            { field: 'userName', text: 'User', size: '120px', sortable: true },
            { field: 'created_at', text: 'Created', size: '145px', sortable: true },
            { field: 'expires_at', text: 'Expires', size: '165px', sortable: true },
            { field: 'last_used', text: 'Last Used', size: '145px', sortable: true },
            { field: 'enabled', text: 'Enabled', size: '70px', sortable: true }
        ],
        searches: [
            { field: 'recid', label: 'ID', type: 'int' },
            { field: 'name', label: 'Name', type: 'text' },
            { field: 'userName', label: 'User', type: 'text' },
            { field: 'created_at', label: 'Created', type: 'date' },
            { field: 'expires_at', label: 'Expires', type: 'datetime' },
            { field: 'last_used', label: 'Last Used', type: 'date' },
            { field: 'enabled', label: 'Enabled', type: 'int' }
        ],
        onRequest(event) {
            currentGridDataType = 'jwt_tokens';
            const params = new URLSearchParams(event.postData);
            params.set('cmd', 'get-records');
            params.set('data', 'jwt_tokens');
            event.postData = params.toString();
            event.method = 'POST';
        }
    },

    formJwtTokens: {
        name: 'formJwtTokens',
        url: w2uiUrl,
        onLoad(event) {
            event.preventDefault();
            return false;
        },
        fields: [
            { field: 'recid', type: 'hidden' },
            { field: 'token', type: 'text', readonly: true, html: { label: 'Token' } },
            { field: 'name', type: 'text', required: true, html: { label: 'Name' } },
            {
                field: 'user_id', type: 'list', required: true, html: { label: 'User' },
                options: { items: [], openOnFocus: false, filter: true, match: 'contains' }
            },
            {
                field: 'expires_at', 
                type: 'text', 
                required: false, 
                html: { 
                    label: 'Expires:', 
                    attr: 'placeholder="YYYY-MM-DD HH:mm" style="width: 150px"'
                }
            },
            { field: 'enabled', type: 'checkbox', required: false, html: { label: 'Enabled' } },
            { field: 'userName', type: 'text', readonly: true, html: { label: 'User Name' } }
        ],
        onSubmit(event) {
            const record = event.postData?.record || event.record;
            
            const isCreate = !record.recid || record.recid === 0;
            if (isCreate && record.name) {
                const grid = w2ui.gridJwtTokens;
                const existingToken = grid.records.find(r => r.name === record.name);
                if (existingToken) {
                    event.onError({ status: 'error', message: `Token with name "${record.name}" already exists!` });
                    return false;
                }
            }
            
            if (typeof record.user_id === 'object' && record.user_id !== null) {
                record.user_id = record.user_id.id;
            }
            
            record.enabled = (record.enabled === 'true' || record.enabled === true || record.enabled === 1) ? 1 : 0;
            return record;
        },
        onSave(event) {
            const oldToken = this.record.token;
            const recid = this.record.recid;
            const recordName = this.record.name;
            const grid = w2ui.gridJwtTokens;

            setTimeout(() => {
                const gridRecord = grid.records.find(r => r.recid === recid || (!recid && r.name === recordName));
                
                if (gridRecord?.token && gridRecord.token !== oldToken) {
                    w2popup.open({
                        title: 'Token Generated',
                        width: 480,
                        height: 280,
                        body: `
                            <div style="padding: 20px; text-align: center; font-size: 13px;">
                                <div style="margin-bottom: 15px; font-size: 15px; font-weight: bold; color: var(--pg-text);">
                                    JWT Token saved successfully!
                                </div>
                                <div style="margin-bottom: 15px; font-family: monospace; background-color: var(--pg-bg); padding: 12px; border-radius: 4px; border: 1px solid var(--pg-border); word-break: break-all; user-select: all; cursor: text;">
                                    ${gridRecord.token}
                                </div>
                                <div style="color: var(--pg-muted);">
                                    Please copy and save it in a secure location.
                                </div>
                            </div>
                        `,
                        actions: {
                            Close: () => w2popup.close()
                        }
                    });
                } else if (recid && gridRecord) {
                    w2popup.open({
                        title: 'Notification',
                        width: 350,
                        height: 180,
                        body: `
                            <div style="padding: 30px; text-align: center; font-size: 14px; color: var(--pg-text);">
                                ✅ JWT Token settings updated successfully!
                            </div>
                        `,
                        actions: {
                            Close: () => w2popup.close()
                        }
                    });
                }
                jwtEditModeActive = false;
            }, 400);
        },

        onError(event) {
            console.error('❌ Form error:', event);
            const errorMsg = event.message || 'An error occurred';
            w2alert(`Error: ${errorMsg}`);
        }
    },

    gridAudit: {
        name: 'gridAudit',
        url: w2uiUrl,
        postData: { cmd: 'get-records', data: 'audit' },
        columns: [
            { field: 'recid', text: 'ID', size: '50px', sortable: true },
            { field: 'logged', text: 'Logged', size: '145px', sortable: true },
            { field: 'user', text: 'User', size: '100px', sortable: true },
            { field: 'client_ip', text: 'Client IP', size: '120px', sortable: true },
            { field: 'action', text: 'Action', size: '55px', sortable: true },
            { field: 'data', text: 'Table', size: '70px', sortable: true },
            { field: 'record_id', text: 'Record ID', size: '70px', sortable: true },
            { field: 'record_before', text: 'Before', size: '370px', sortable: true },
            { field: 'record_after', text: 'After', size: '370px', sortable: true }
        ],
        searches: [
            { field: 'recid', label: 'ID', type: 'int' },
            { field: 'logged', label: 'Logged', type: 'date' },
            { field: 'user', label: 'User', type: 'text' },
            { field: 'client_ip', label: 'Client IP', type: 'text' },
            { field: 'action', label: 'Action', type: 'text' },
            { field: 'data', label: 'Table', type: 'text' },
            { field: 'record_id', label: 'Record ID', type: 'int' },
            { field: 'record_before', label: 'Before', type: 'text' },
            { field: 'record_after', label: 'After', type: 'text' }
        ],
        show: { footer: true, toolbar: true, toolbarReload: true, toolbarColumns: true, toolbarSearch: true },
        sortData: [{ field: 'recid', direction: 'desc' }],
        toolbar: { items: [{ type: 'spacer' }, themeToolbarItem(), logoutToolbarItem()], onClick: themeToolbarClick },
        onRequest(event) {
            currentGridDataType = 'audit';
            const params = new URLSearchParams(event.postData);
            params.set('cmd', 'get-records');
            params.set('data', 'audit');
            event.postData = params.toString();
            event.method = 'POST';
        }
    }
};

// Cell rendering
const escapeCell = function (field, index, colIndex) {
    const value = String(this.getCellValue(index, colIndex) || '');
    const escaped = w2utils.encodeTags(value);
    return `<div title="${escaped}">${escaped}</div>`;
};

// Cell rendering and search defaults
Object.keys(config).forEach(name => {
    const gridConfig = config[name];
    
    // Захист кастомних рендерів (те, що ми робили раніше)
    if (gridConfig.columns) {
        gridConfig.columns.forEach(column => {
            if (!column.render) {
                column.render = function(record, index, columnIndex) {
                    const value = String(record[column.field] || '');
                    const escaped = w2utils.encodeTags(value);
                    return `<div title="${escaped}">${escaped}</div>`;
                };
            }
        });
    }
    
    // 🌟 ДОДАНО: Примусово змінюємо оператор пошуку з 'begins' на 'contains'
    if (gridConfig.searches) {
        gridConfig.searches.forEach(search => {
            if (search.type === 'text') {
                search.operator = 'contains';
            }
        });
    }
});


Object.keys(textareaFields).forEach(name => {
    const spec = textareaFields[name];
    config[name].onLoad = function (event) {
        event.preventDefault();
        if (this.record && this.record[spec.field]) {
            event.done(() => transformField(this.record, spec.field, spec.expand));
        }
        return false;
    };
    config[name].onSubmit = function (event) {
        const record = event.postData?.record || this.record || {};
        
        for (let k in record) {
            if (typeof record[k] === 'object' && record[k] !== null && record[k].id) {
                record[k] = record[k].id;
            }
        }
        
        transformField(record, spec.field, spec.collapse);
        
        if (event.postData) {
            event.postData.record = record;
        }
    };
});

// Auto-init for grids and forms
['Domains', 'JwtTokens', 'Monitors', 'Records', 'Routings', 'Types', 'Users', 'Views'].forEach(base => {
    const grid = config[`grid${base}`];
    grid.show = {
        footer: true, selectColumn: true, toolbar: true, toolbarReload: true,
        toolbarColumns: true, toolbarSearch: true, toolbarAdd: true, toolbarEdit: true, toolbarDelete: true
    };
    grid.sortData = [{ field: 'recid', direction: 'asc' }];
    grid.url = w2uiUrl;
    grid.toolbar = { items: [{ type: 'spacer' }, themeToolbarItem(), logoutToolbarItem()], onClick: themeToolbarClick };
    grid.onAdd = gridPopupForm;
    grid.onDblClick = gridPopupForm;
    grid.onEdit = gridPopupForm;
    
    grid.onDelete = function(event) {
        event.preventDefault();
        
        const selectedRows = this.getSelection();
        if (!selectedRows || selectedRows.length === 0) {
            return;
        }
        
        if (!confirm(`Are you sure you want to delete ${selectedRows.length} record(s)?`)) {
            return;
        }
        
        // Determine data parameter based on grid name
        let dataParam = 'records';
        if (this.name.includes('Domain')) dataParam = 'domains';
        else if (this.name.includes('Type')) dataParam = 'types';
        else if (this.name.includes('Routing')) dataParam = 'routings';
        else if (this.name.includes('Monitor')) dataParam = 'monitors';
        else if (this.name.includes('View')) dataParam = 'views';
        else if (this.name.includes('JwtToken')) dataParam = 'jwt_tokens';
        else if (this.name.includes('User')) dataParam = 'users';
        
        fetch(w2uiUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            credentials: 'include',
            body: JSON.stringify({
                cmd: 'delete-records',
                data: dataParam,
                selected: selectedRows.length === 1 ? selectedRows[0] : selectedRows
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                this.reload();
            } else {
                alert(`Failed to delete records: ${data.message}`);
            }
        })
        .catch(err => {
            alert(`Delete failed: ${err.message}`);
        });
    };

    const form = config[`form${base}`];
    
    form.method = 'POST';
    form.url = w2uiUrl;
    
    form.show = {
        footer: true,
        actions: true
    };
    
    form.onChange = function(event) {
        const fieldName = event.target?.name || event.field;
        const fieldValue = event.target?.value !== undefined ? event.target.value : event.value;
        
        if (fieldName && this.record) {
            this.record[fieldName] = fieldValue;
        }
    };

    form.actions = {
        Close() { 
            if (w2ui.formJwtTokens) {
                jwtEditModeActive = false;
            }
            w2popup.close(); 
        },
        Save() {
            this.validate(true); 
            
            if (this.validate().length === 0) {
                const realCheckbox = document.getElementById('jwt-real-enabled');
                if (realCheckbox) {
                    this.record.enabled = realCheckbox.checked ? 1 : 0;
                }

                const record = structuredClone(this.record || {});
                
                if (base === 'JwtTokens' && (!this.recid || this.recid === 0)) {
                    const existingToken = w2ui.gridJwtTokens.records.find(
                        r => r.name === record.name
                    );
                    if (existingToken) {
                        w2alert('Token with name already exists. Please use a different name.');
                        return;
                    }
                }
                
                if (base === 'JwtTokens') {
                    if (!record.user_id && record.userID) {
                        record.user_id = record.userID;
                    }
                    if (this.recid && parseInt(this.recid) > 0) {
                        delete record.token;
                    }
                }

                for (let k in record) {
                    if (typeof record[k] === 'object' && record[k] !== null && record[k].id !== undefined) {
                        record[k] = record[k].id;
                    }
                }
                
                const params = new URLSearchParams();
                
                params.append('cmd', 'save-record');
                const dataName = base === 'JwtTokens' ? 'jwt_tokens' : base.toLowerCase();
                params.append('data', dataName);
                params.append('recid', this.recid || record.recid || 0);
                
                for (let key in record) {
                    if (record[key] !== undefined && record[key] !== null) {
                        params.append(`record[${key}]`, record[key]);
                    }
                }
                
                fetch('/admin/w2ui', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded'
                    },
                    credentials: 'include',
                    body: params.toString()
                })
                .then(resp => resp.json())
                .then(data => {
                    if (data && data.status === 'error') {
                        w2alert('Error: ' + (data.message || 'Unknown error'));
                    } else {
                        w2popup.close();
                        const gridName = `grid${base}`;
                        if (w2ui[gridName]) {
                            w2ui[gridName].reload();
                        }
                        const runtimeForm = w2ui[`form${base}`];
                        if (runtimeForm && typeof runtimeForm.onSave === 'function') {
                            runtimeForm.onSave({ status: 'success' });
                        }
                    }
                })
                .catch(err => {
                    w2alert('Error: ' + err.message);
                });
            }
        }
};
    
    form.formHTML = `
        <div class="w2ui-page page-0">
            ${form.fields.map(f => {
                let tag = `<input name="${f.field}" type="text"/>`;
                if (f.type === 'textarea') {
                    tag = `<textarea name="${f.field}" style="width: 100%; height: 90px; resize: vertical;"></textarea>`;
                } else if (f.type === 'checkbox' || f.type === 'toggle') {
                    tag = `<input name="${f.field}" type="checkbox"/>`;
                } else if (f.type === 'password') {
                    tag = `<input name="${f.field}" type="password"/>`;
                } else if (f.type === 'list' || f.type === 'combo') {
                    tag = `<input name="${f.field}" type="list"/>`;
                }
                return `
                    <div class="w2ui-field">
                        <label>${f.html?.label || f.field}:</label>
                        <div>${tag}</div>
                    </div>
                `;
            }).join('')}
        </div>
        <div class="w2ui-buttons">
            <button class="w2ui-btn" name="Close">Close</button>
            <button class="w2ui-btn w2ui-btn-blue" name="Save">Save</button>
        </div>
    `;

    form.style = 'border: 0px; background-color: transparent';
    form.url = w2uiUrl;
    
    const dataName = base === 'JwtTokens' ? 'jwt_tokens' : base.toLowerCase();
    form.postData = {
        cmd: 'save-record',
        data: dataName
    };

    const originalOnSubmit = form.onSubmit;
    form.onSubmit = function (event) {
        if (typeof originalOnSubmit === 'function') {
            originalOnSubmit.call(this, event);
        }
        
        if (!event.postData) event.postData = {};
        event.postData.record = this.record;
        event.postData.recid = this.recid || 0;
    };

    const originalOnSave = form.onSave;
    form.onSave = function (event) {
        if (event?.error) {
            console.error(`❌ ERROR in onSave for ${base}:`, event.error);
        }
        
        if (typeof originalOnSave === 'function') {
            originalOnSave.call(this, event);
        } else {
            w2ui[`grid${base}`].reload();
        }
    };
});

// App initialization
document.addEventListener('DOMContentLoaded', () => {
    if (document.documentElement.getAttribute('data-theme') === 'dark') {
        document.body.classList.add('w2ui-dark');
    }
    w2utils.settings.date_format = 'yyyy-mm-dd';
    w2utils.settings.groupSymbol = '';

    new w2layout({ ...config.layout, box: '#powergslb' });
    w2ui.layout.html('left', new w2sidebar(config.sidebar));
    
    new w2grid(config.gridStatus);
    new w2grid(config.gridAudit);
    new w2grid(config.gridDomains);
    new w2grid(config.gridJwtTokens);
    new w2grid(config.gridMonitors);
    new w2grid(config.gridRecords);
    new w2grid(config.gridRoutings);
    new w2grid(config.gridTypes);
    new w2grid(config.gridUsers);
    new w2grid(config.gridViews);

    new w2form(config.formDomains);
    new w2form(config.formJwtTokens);
    new w2form(config.formMonitors);
    new w2form(config.formRecords);
    new w2form(config.formRoutings);
    new w2form(config.formTypes);
    new w2form(config.formUsers);
    new w2form(config.formViews);

    w2ui.layout.html('main', w2ui.gridStatus);

    // ====================================================
    // Setup column visibility menu (keep open, real-time preview)
    // ====================================================
    
    // Generate column mapping from existing grid configurations
    const columnMap = {};
    Object.keys(config).forEach(key => {
        if (key.startsWith('grid') && config[key].columns) {
            config[key].columns.forEach(col => {
                columnMap[col.text] = col.field;
            });
        }
    });
    
    // Save grid state (column visibility)
    const saveGridState = (gridName) => {
        const grid = w2ui[gridName];
        if (!grid) return;
        
        const state = {
            columns: grid.columns.map(col => ({
                field: col.field,
                hidden: col.hidden
            }))
        };
        
        localStorage.setItem(`powergslb_grid_state_${gridName}`, JSON.stringify(state));
    };
    
    // Restore grid state (column visibility)
    const restoreGridState = (gridName) => {
        const grid = w2ui[gridName];
        if (!grid) return;
        
        const saved = localStorage.getItem(`powergslb_grid_state_${gridName}`);
        if (!saved) return;
        
        try {
            const state = JSON.parse(saved);
            state.columns.forEach(savedCol => {
                const column = grid.columns.find(c => c.field === savedCol.field);
                if (column) {
                    column.hidden = savedCol.hidden;
                }
            });
            grid.refresh();
        } catch (err) {
            console.error('Failed to restore grid state:', err);
        }
    };
    
    // Restore grid states on load
    ['gridStatus', 'gridAudit', 'gridDomains', 'gridJwtTokens', 'gridMonitors', 
     'gridRecords', 'gridRoutings', 'gridTypes', 'gridUsers', 'gridViews'].forEach(gridName => {
        setTimeout(() => restoreGridState(gridName), 100);
    });
    
    document.addEventListener('click', (e) => {
        const menuItem = e.target.closest('.w2ui-menu-item');
        if (!menuItem) return;
        
        const overlay = menuItem.closest('[name*="toolbar-drop"]');
        if (!overlay) return;
        
        const text = menuItem.querySelector('.menu-text')?.textContent?.trim() || '';
        
        // Handle Save Grid State
        if (text.includes('Save Grid State')) {
            const overlayName = overlay.getAttribute('name') || '';
            const gridName = overlayName.split('_')[0];
            saveGridState(gridName);
            // Don't prevent default - let menu close
            return;
        }
        
        // Handle Restore Default State
        if (text.includes('Restore Default State')) {
            const overlayName = overlay.getAttribute('name') || '';
            const gridName = overlayName.split('_')[0];
            const grid = w2ui[gridName];
            if (grid) {
                // Show all columns by default
                grid.columns.forEach(col => { col.hidden = false; });
                grid.refresh();
                // Clear saved state
                localStorage.removeItem(`powergslb_grid_state_${gridName}`);
            }
            // Don't prevent default - let menu close
            return;
        }
        
        // Skip input field
        if (text.includes('Skip') || text.includes('records')) {
            return;
        }
        
        // Prevent menu from closing for regular column items
        e.preventDefault();
        e.stopPropagation();
        
        const columnText = text.split(/[\r\n]/)[0].trim();
        const fieldName = columnMap[columnText];
        if (!fieldName) return;
        
        const overlayName = overlay.getAttribute('name') || '';
        const gridName = overlayName.split('_')[0];
        const grid = w2ui[gridName];
        if (!grid) return;
        
        const column = grid.columns.find(c => c.field === fieldName);
        if (!column) return;
        
        // Toggle visibility
        column.hidden = !column.hidden;
        grid.refresh();
    }, true);
});
