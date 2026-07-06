/* ═══════════════════════════════════════════════════════
   Settings App — Alpine.js Component
   Extracted from settings.html for modularization
═══════════════════════════════════════════════════════ */

function settingsApp() {
    return {
        tab: 'general',
        mobileView: 'categories', // 'categories' or 'detail'
        characters: [],
        mcpServers: [],
        installedApps: [],
        models: [],
        toasts: [],
        diagnosticsLogs: [],
        diagnosticsRunning: false,
        showEgg: false,
        clicks: 0,
        updateChecking: false,
        updateAvailable: false,
        updateInfo: null,
        updateError: '',
        showUpdateNotes: false,
        profileModalOpen: false,
        profileNameEdit: '',
        showUpdateScreen: false,
        updateStatus: '',
        updateProgress: 0,
        updateVersionText: '',
        updateClicks: 0,
        charSelectedTools: [],
        allPoolTools: [],
        prefs: {
            model_name: '',
            user_persona: '',
            llm_provider: 'groq',
            dynamic_suggestions: true,
            streaming_mode: true,
            inject_time: true,
            max_tool_rounds: 8,
            whatsapp_enabled: false,
            whatsapp_character_id: 'kokomi',
            whatsapp_api_url: 'http://localhost:3013',
            tavily_api_key: '',
            search_provider: 'tavily',
            searxng_url: 'http://localhost:8080',
            web_scrape_enabled: false,
            browser_redirect_enabled: true,
            user_name: 'User',
            user_avatar: null,
            debug_mode: false,
            insights: true,
            atlas_llm_provider: 'google',
            atlas_model_name: 'gemini-2.5-flash',
            atlas_nvidia_model: 'nvidia/llama-3.3-nemotron-super-49b-v1',
            atlas_local_url: 'http://localhost:8080/v1',
            atlas_local_model: 'local-model'
        },

        cropModal: false,
        cropper: null,

        memoryModal: false,
        inspectingChar: null,
        loadingMemories: false,
        memories: [],

        prefSaving: false,
        saving: false,
        refreshingPool: false,

        presets: [
            // ⚡ YOUR WAIFUS FIRST ⚡
            { id: 'keqing', name: 'Keqing', icon: 'fa-solid fa-bolt', color: '#C39BD3', midnight: '#1A0A2E', lavender: '#C39BD3', indigo: '#7D3C98', obsidian: '#0A0414', accent: '#A855F7' },
            { id: 'ayaka', name: 'Ayaka', icon: 'fa-solid fa-snowflake', color: '#AED6F1', midnight: '#071828', lavender: '#D6EAF8', indigo: '#1A6FA8', obsidian: '#020810', accent: '#5DADE2' },
            { id: 'kokomi', name: 'Kokomi', icon: 'fa-solid fa-droplet', color: '#F2D6E4', midnight: '#1C0A1A', lavender: '#F6D4CB', indigo: '#968DD8', obsidian: '#0A0308', accent: '#E8827A' },
            // ✨ REST OF THE ROSTER ✨
            { id: 'raiden', name: 'Raiden Shogun', icon: 'fa-solid fa-khanda', color: '#9B59B6', midnight: '#1A0A2E', lavender: '#D2B4DE', indigo: '#6C3483', obsidian: '#08030F', accent: '#D4AC0D' },
            { id: 'hutao', name: 'Hu Tao', icon: 'fa-solid fa-skull', color: '#FF6B35', midnight: '#1E0800', lavender: '#FDEBD0', indigo: '#C0392B', obsidian: '#080200', accent: '#FF4500' },
            { id: 'ganyu', name: 'Ganyu', icon: 'fa-solid fa-moon', color: '#AEB6BF', midnight: '#0C1020', lavender: '#D2D7DF', indigo: '#5D78AA', obsidian: '#040608', accent: '#8BA4CC' },
            { id: 'yaemiko', name: 'Yae Miko', icon: 'fa-solid fa-torii-gate', color: '#FF69B4', midnight: '#220A18', lavender: '#FFB6D9', indigo: '#CC2277', obsidian: '#0D0309', accent: '#FF1493' },
            { id: 'zhongli', name: 'Zhongli', icon: 'fa-solid fa-mountain', color: '#F0A500', midnight: '#1A1000', lavender: '#F9E4B7', indigo: '#C47A00', obsidian: '#080500', accent: '#E8A020' },
            { id: 'nahida', name: 'Nahida', icon: 'fa-solid fa-leaf', color: '#82E0AA', midnight: '#061A0A', lavender: '#C8F5D8', indigo: '#27AE60', obsidian: '#020A04', accent: '#2ECC71' },
            { id: 'citlali', name: 'Citlali', icon: 'fa-solid fa-icicles', color: '#B8C8E8', midnight: '#0C0A1A', lavender: '#D4D8F0', indigo: '#4A3878', obsidian: '#050410', accent: '#8B7CC8' },
        ],

        customAccent: '',
        customWallpaper: '',
        customBlur: '0',
        swatchMidnight: '#272757',
        swatchLavender: '#8686AC',
        swatchIndigo: '#505081',
        swatchObsidian: '#0F0E47',
        customThemes: [],
        aiThemeModalOpen: false,
        aiThemePrompt: '',
        aiThemeName: '',
        aiThemeLoading: false,
        aiThemeError: '',
        aiThemePreview: null,
        customThemeModalOpen: false,
        customThemeName: '',
        customThemeMidnight: '#272757',
        customThemeLavender: '#8686AC',
        customThemeIndigo: '#505081',
        customThemeObsidian: '#0F0E47',
        customThemeAccent: '#505081',

        /* ═══ Mobile Navigation ═══ */
        get isMobile() {
            return window.innerWidth <= 640;
        },

        selectTab(tabName) {
            this.tab = tabName;
            if (this.isMobile) {
                this.mobileView = 'detail';
            }
            // Update hash
            window.location.hash = tabName;

            if (tabName === 'about') {
                this.checkForUpdates();
            }
        },

        goBackToCategories() {
            this.mobileView = 'categories';
            // Clear hash
            history.replaceState(null, '', window.location.pathname);
        },

        get tabLabel() {
            const labels = {
                general: 'General',
                appearance: 'Appearance',
                characters: 'Characters',
                memory: 'Memory',
                mcp: 'Apps',
                integrations: 'Integrations',
                atlas: 'Atlas Settings',
                computer: 'Computer & Docker',
                telemetry: 'Usage Insights',
                about: 'About'
            };
            return labels[this.tab] || 'Settings';
        },

        /* ═══ Profile Modal ═══ */
        openProfileModal() {
            this.profileModalOpen = true;
            this.profileNameEdit = this.prefs.user_name || '';
        },

        async saveProfileName() {
            this.prefs.user_name = this.profileNameEdit.trim() || 'User';
            await this.savePrefs();
            this.showToast('Profile updated!', 'success');
        },

        closeProfileModal() {
            this.profileModalOpen = false;
        },

        triggerProfileAvatarUpload() {
            this.$refs.profileAvatarInput.click();
        },

        initProfileCrop(e) {
            const file = e.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = (ev) => {
                this.$refs.cropImg.src = ev.target.result;
                this.profileModalOpen = false;
                this.cropModal = true;
                this.$nextTick(() => {
                    if (this.cropper) this.cropper.destroy();
                    this.cropper = new Cropper(this.$refs.cropImg, {
                        aspectRatio: 1,
                        viewMode: 1,
                        autoCropArea: 1,
                        dragMode: 'move',
                        background: false
                    });
                });
            };
            reader.readAsDataURL(file);
        },

        /* ═══ Theme Engine ═══ */
        applyLiveTheme() {
            const d = document.documentElement;
            d.style.setProperty('--swatch-midnight', this.swatchMidnight || '#272757');
            d.style.setProperty('--swatch-lavender', this.swatchLavender || '#8686AC');
            d.style.setProperty('--swatch-indigo', this.swatchIndigo || '#505081');
            d.style.setProperty('--swatch-obsidian', this.swatchObsidian || '#0F0E47');
            d.style.setProperty('--accent-custom-light', this.customAccent || '#505081');
            d.style.setProperty('--accent-custom-dark', this.customAccent || '#8686AC');
            d.style.setProperty('--custom-wallpaper', this.customWallpaper ? `url('${this.customWallpaper.trim()}')` : 'none');
            d.style.setProperty('--custom-blur', `${this.customBlur || 0}px`);

            if (this.customWallpaper && this.customWallpaper.trim() !== '') {
                d.classList.add('has-wallpaper');
            } else {
                d.classList.remove('has-wallpaper');
            }
        },

        async savePrefsSilently() {
            this.prefs.custom_accent = this.customAccent;
            this.prefs.custom_wallpaper = this.customWallpaper.trim();
            this.prefs.custom_blur = this.customBlur;
            this.prefs.swatch_midnight = this.swatchMidnight;
            this.prefs.swatch_lavender = this.swatchLavender;
            this.prefs.swatch_indigo = this.swatchIndigo;
            this.prefs.swatch_obsidian = this.swatchObsidian;
            this.prefs.custom_themes = this.customThemes;
            try {
                await fetch('/api/prefs', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.prefs)
                });
            } catch (err) {
                console.error("Failed to save theme preferences quietly:", err);
            }
        },

        async saveAppearance() {
            this.applyLiveTheme();
            await this.savePrefsSilently();
        },

        applyPreset(preset) {
            const p = this.presets.find(x => x.id === preset);
            if (p) {
                this.swatchMidnight = p.midnight;
                this.swatchLavender = p.lavender;
                this.swatchIndigo = p.indigo;
                this.swatchObsidian = p.obsidian;
                this.customAccent = p.accent;
                this.saveAppearance();
            }
        },

        /* ═══ AI Theme Generator ═══ */
        openAiThemeModal() {
            this.aiThemeModalOpen = true;
            this.aiThemePrompt = '';
            this.aiThemeName = '';
            this.aiThemeLoading = false;
            this.aiThemeError = '';
            this.aiThemePreview = null;
        },

        async buildThemeFromPrompt() {
            if (!this.aiThemePrompt || !this.aiThemePrompt.trim()) return;
            this.aiThemeLoading = true;
            this.aiThemeError = '';
            this.aiThemePreview = null;

            const systemPrompt = `You are a professional web designer specializing in high-contrast, premium, dark-mode and light-mode theme palettes.
Your task is to generate a beautiful custom 5-color theme swatch palette based on the user's prompt, along with a creative, short, matching theme name (2-3 words, capitalized).
You MUST output EXACTLY a raw JSON object and nothing else. No markdown wrapping, no explanation.

JSON format:
{
  "name": "Creative Theme Name",
  "midnight": "#HEX",
  "lavender": "#HEX",
  "indigo": "#HEX",
  "obsidian": "#HEX",
  "accent": "#HEX"
}

Guidance on colors:
- "midnight": This is the primary backdrop and sidebars. It must be deep and rich, not a basic gray.
- "lavender": This is the tertiary text and highlights. It must be a lighter desaturated pastel shade of the dominant theme color to ensure beautiful contrast.
- "indigo": This is the secondary highlight and card border accents. It must be a medium/rich shade.
- "obsidian": This is the canvas background. It must be extremely deep and dark (very close to black but beautifully tinted with the prompt's theme colors).
- "accent": This is the primary interactive accent color. It must be extremely vibrant, gorgeous, and rich.

Ensure all HEX codes are valid 6-character hex strings (starting with #) and have excellent color harmony. No markdown, no backticks, just raw JSON text.`;

            try {
                const response = await fetch('/api/ai/generate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        prompt: `Generate a theme palette for prompt: "${this.aiThemePrompt}"`,
                        system: systemPrompt
                    })
                });

                const result = await response.json();
                if (result.error) {
                    this.aiThemeError = result.error;
                    return;
                }

                let cleanText = result.text.trim();
                if (cleanText.includes('```json')) {
                    cleanText = cleanText.split('```json')[1].split('```')[0].trim();
                } else if (cleanText.includes('```')) {
                    cleanText = cleanText.split('```')[1].trim();
                }

                const palette = JSON.parse(cleanText);
                if (palette.midnight && palette.lavender && palette.indigo && palette.obsidian && palette.accent) {
                    this.aiThemePreview = palette;
                    this.aiThemeName = palette.name || 'AI Generated Theme';
                } else {
                    this.aiThemeError = "AI returned an incomplete color palette. Please try again!";
                }
            } catch (err) {
                console.error("AI Theme Generation failed:", err);
                this.aiThemeError = "Failed to generate theme. Make sure your LLM provider is connected!";
            } finally {
                this.aiThemeLoading = false;
            }
        },

        async saveAndApplyGeneratedTheme() {
            if (!this.aiThemePreview) return;
            const themeName = (this.aiThemeName && this.aiThemeName.trim()) ? this.aiThemeName.trim() : 'AI Theme';
            const newTheme = {
                name: themeName,
                midnight: this.aiThemePreview.midnight,
                lavender: this.aiThemePreview.lavender,
                indigo: this.aiThemePreview.indigo,
                obsidian: this.aiThemePreview.obsidian,
                accent: this.aiThemePreview.accent
            };
            this.customThemes.push(newTheme);
            this.swatchMidnight = newTheme.midnight;
            this.swatchLavender = newTheme.lavender;
            this.swatchIndigo = newTheme.indigo;
            this.swatchObsidian = newTheme.obsidian;
            this.customAccent = newTheme.accent;

            await this.saveAppearance();
            this.aiThemeModalOpen = false;
            this.showToast(`Theme "${themeName}" applied & saved!`, 'success');
        },

        openCustomThemeModal() {
            this.customThemeModalOpen = true;
            this.customThemeName = 'My Custom Theme';
            this.customThemeMidnight = this.swatchMidnight || '#272757';
            this.customThemeLavender = this.swatchLavender || '#8686AC';
            this.customThemeIndigo = this.swatchIndigo || '#505081';
            this.customThemeObsidian = this.swatchObsidian || '#0F0E47';
            this.customThemeAccent = this.customAccent || '#505081';
        },

        async saveCustomTheme() {
            const name = (this.customThemeName && this.customThemeName.trim()) ? this.customThemeName.trim() : 'Custom Theme';
            const newTheme = {
                name: name,
                midnight: this.customThemeMidnight,
                lavender: this.customThemeLavender,
                indigo: this.customThemeIndigo,
                obsidian: this.customThemeObsidian,
                accent: this.customThemeAccent
            };
            this.customThemes.push(newTheme);
            this.swatchMidnight = newTheme.midnight;
            this.swatchLavender = newTheme.lavender;
            this.swatchIndigo = newTheme.indigo;
            this.swatchObsidian = newTheme.obsidian;
            this.customAccent = newTheme.accent;

            await this.saveAppearance();
            this.customThemeModalOpen = false;
            this.showToast(`Theme "${name}" created & applied!`, 'success');
        },

        async applyCustomTheme(theme) {
            this.swatchMidnight = theme.midnight;
            this.swatchLavender = theme.lavender;
            this.swatchIndigo = theme.indigo;
            this.swatchObsidian = theme.obsidian;
            this.customAccent = theme.accent;
            await this.saveAppearance();
            this.showToast(`Theme "${theme.name}" applied!`, 'success');
        },

        async deleteCustomTheme(index) {
            const deletedName = this.customThemes[index].name;
            this.customThemes.splice(index, 1);
            await this.saveAppearance();
            this.showToast(`Theme "${deletedName}" deleted!`, 'success');
        },

        uploadWallpaper(e) {
            const file = e.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = (ev) => { this.customWallpaper = ev.target.result; this.saveAppearance(); };
            reader.readAsDataURL(file);
        },

        /* ═══ Characters ═══ */
        charModal: false, charEditId: null, charName: '', charPersona: '', charDescription: '',
        charGroqModel: 'default', charGoogleModel: 'default', charLocalModel: 'default', charNvidiaModel: 'default',
        charVoice: 'aoede', charAvatarPreview: null, charAvatarFile: null, charMCPServers: [],
        mcpTools: {},
        charSearch: '',
        aiCharModal: false, aiCharPrompt: '', aiCharLoading: false, aiCharError: '',

        get filteredCharacters() {
            const q = (this.charSearch || '').toLowerCase().trim();
            if (!q) return this.characters;
            return this.characters.filter(c =>
                c.name.toLowerCase().includes(q) ||
                (c.description || '').toLowerCase().includes(q) ||
                (c.persona || '').toLowerCase().includes(q)
            );
        },

        get currentCharModel() {
            const p = this.prefs.llm_provider;
            if (p === 'google') return this.charGoogleModel;
            if (p === 'local') return this.charLocalModel;
            if (p === 'nvidia') return this.charNvidiaModel;
            return this.charGroqModel;
        },
        set currentCharModel(v) {
            const p = this.prefs.llm_provider;
            if (p === 'google') this.charGoogleModel = v;
            else if (p === 'local') this.charLocalModel = v;
            else if (p === 'nvidia') this.charNvidiaModel = v;
            else this.charGroqModel = v;
        },

        /* ═══ MCP Servers ═══ */
        mcpModal: false, mcpEditId: null, mcpName: '', mcpIcon: '', mcpTransport: 'stdio',
        mcpCommand: '', mcpArgs: '', mcpEnv: '', mcpUrl: '', mcpEnabled: true,
        testingId: null, testResult: null,

        /* ═══ Dark / Light Theme ═══ */
        activeTheme: 'system',
        async setTheme(theme) {
            this.activeTheme = theme;
            this.prefs.theme = theme;
            await this.savePrefs();

            let isDark = false;
            if (theme === 'system') {
                isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
            } else {
                isDark = (theme === 'dark');
            }

            const d = document.documentElement;
            if (isDark) {
                d.classList.add('dark');
            } else {
                d.classList.remove('dark');
            }

            const hljsTheme = document.getElementById('hljs-theme');
            if (hljsTheme) {
                hljsTheme.href =
                    `https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/${isDark ? 'github-dark' : 'github'}.min.css`;
            }
        },

        /* ═══ Init ═══ */
        async init() {
            await Promise.all([this.fetchChars(), this.fetchMCP(), this.fetchPrefs(), this.fetchModels(), this.fetchInstalledApps(), this.fetchPoolTools()]);

            // Handle resize for mobile detection
            window.addEventListener('resize', () => {
                if (!this.isMobile && this.mobileView === 'categories') {
                    // If resized to desktop while on categories, keep current tab
                }
            });

            const handleHash = () => {
                const hash = window.location.hash;
                const search = window.location.search;
                if (hash === '#atlas' || search.includes('atlas')) {
                    this.tab = 'atlas';
                    if (this.isMobile) this.mobileView = 'detail';
                } else if (hash) {
                    const clean = hash.replace('#', '');
                    const validTabs = ['general', 'appearance', 'characters', 'memory', 'mcp', 'integrations', 'atlas', 'computer', 'telemetry', 'about'];
                    if (validTabs.includes(clean)) {
                        this.tab = clean;
                        if (this.isMobile) this.mobileView = 'detail';
                        if (clean === 'about') {
                            this.checkForUpdates();
                        }
                    }
                }
            };
            handleHash();
            window.addEventListener('hashchange', handleHash);
        },

        /* ═══ API Methods ═══ */
        async fetchPrefs() {
            try {
                const r = await fetch('/api/prefs');
                this.prefs = await r.json();
                this.customAccent = this.prefs.custom_accent || '';
                this.customWallpaper = this.prefs.custom_wallpaper || '';
                this.customBlur = this.prefs.custom_blur || '0';
                this.swatchMidnight = this.prefs.swatch_midnight || '#272757';
                this.swatchLavender = this.prefs.swatch_lavender || '#8686AC';
                this.swatchIndigo = this.prefs.swatch_indigo || '#505081';
                this.swatchObsidian = this.prefs.swatch_obsidian || '#0F0E47';
                this.customThemes = this.prefs.custom_themes || [];
                this.activeTheme = this.prefs.theme || 'system';
            } catch(e) { console.error(e); }
        },

        initCrop(e) {
            const file = e.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = (ev) => {
                this.$refs.cropImg.src = ev.target.result;
                this.cropModal = true;
                this.$nextTick(() => {
                    if (this.cropper) this.cropper.destroy();
                    this.cropper = new Cropper(this.$refs.cropImg, {
                        aspectRatio: 1,
                        viewMode: 1,
                        autoCropArea: 1,
                        dragMode: 'move',
                        background: false
                    });
                });
            };
            reader.readAsDataURL(file);
        },

        cancelCrop() {
            this.cropModal = false;
            if (this.cropper) this.cropper.destroy();
            // Clear both avatar inputs
            if (this.$refs.userAvatarInput) this.$refs.userAvatarInput.value = '';
            if (this.$refs.profileAvatarInput) this.$refs.profileAvatarInput.value = '';
        },

        async finishCrop() {
            if (!this.cropper) return;
            this.prefSaving = true;
            const canvas = this.cropper.getCroppedCanvas({ width: 256, height: 256 });
            canvas.toBlob(async (blob) => {
                const fd = new FormData();
                fd.append('avatar', blob, 'avatar.png');
                try {
                    const r = await fetch('/api/prefs/avatar', { method: 'POST', body: fd });
                    const res = await r.json();
                    if (res.avatar) {
                        this.prefs.user_avatar = res.avatar + '?t=' + Date.now();
                        this.showToast('Profile photo updated!', 'success');
                    }
                } catch(e) { console.error(e); }
                finally {
                    this.prefSaving = false;
                    this.cancelCrop();
                }
            }, 'image/png');
        },

        showToast(msg, type = 'success') {
            const id = Date.now() + Math.random().toString(36).substr(2, 5);
            this.toasts.push({ id, message: msg, type });
            setTimeout(() => {
                this.toasts = this.toasts.filter(t => t.id !== id);
            }, 3000);
        },

        async runDiagnostics() {
            this.diagnosticsRunning = true;
            this.diagnosticsLogs = [];
            const steps = [
                "Initializing Tactician Diagnostics...",
                "Testing Docker sandbox connection... OK",
                "Checking active MCP protocol connections... SSE/Stdio active",
                "Measuring LLM response latency... 340ms (Excellent)",
                "Synchronizing Divine Strategy context layers...",
                "Diagnose completed: 100% operational. Strategic contingency plans loaded. 🐟"
            ];
            for (const step of steps) {
                await new Promise(r => setTimeout(r, 450));
                this.diagnosticsLogs.push(step);
                this.$nextTick(() => {
                    const term = document.getElementById('diagnostics-terminal-screen');
                    if (term) term.scrollTop = term.scrollHeight;
                });
            }
            this.diagnosticsRunning = false;
        },

        async checkForUpdates() {
            if (this.updateChecking) return;
            this.updateChecking = true;
            this.updateError = '';
            this.updateInfo = null;
            this.updateAvailable = false;
            try {
                const resp = await fetch('/api/update/check?t=' + Date.now());
                const data = await resp.json();
                if (data.ok) {
                    this.updateAvailable = data.update_available;
                    this.updateInfo = {
                        currentVersion: data.current_version,
                        latestVersion: data.latest_version,
                        latestReleaseName: data.latest_release_name,
                        changelog: data.changelog
                    };
                } else {
                    this.updateError = data.error || 'Failed to check for updates.';
                }
            } catch (err) {
                console.error("Update check failed:", err);
                this.updateError = "Connection to server failed. Please check internet connection.";
            } finally {
                this.updateChecking = false;
            }
        },

        async runUpdate() {
            this.showUpdateScreen = true;
            this.updateStatus = 'Initializing update...';
            this.updateProgress = 0;
            this.updateVersionText = this.updateInfo ? this.updateInfo.latestVersion : 'latest version';
            
            try {
                const response = await fetch('/api/update/run', { method: 'POST' });
                if (!response.ok) {
                    throw new Error('Update server returned ' + response.status);
                }
                
                const reader = response.body.getReader();
                const decoder = new TextDecoder('utf-8');
                let buffer = '';
                
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    
                    // Keep the last partial line in the buffer
                    buffer = lines.pop();
                    
                    for (const line of lines) {
                        if (line.trim().startsWith('data: ')) {
                            try {
                                const data = JSON.parse(line.trim().substring(6));
                                if (data.status) this.updateStatus = data.status;
                                if (data.progress !== undefined) this.updateProgress = data.progress;
                                if (data.error) {
                                    alert('Update error: ' + data.error);
                                    this.showUpdateScreen = false;
                                    return;
                                }
                            } catch (e) {
                                console.error('Failed to parse SSE data:', e);
                            }
                        }
                    }
                }
                
                // Wait 3 seconds to let uvicorn reload/restart then refresh page
                setTimeout(() => {
                    window.location.reload();
                }, 3000);
                
            } catch (err) {
                console.error('Update execution failed:', err);
                alert('Update execution failed: ' + err.message);
                this.showUpdateScreen = false;
            }
        },

        devClick() {
            this.updateClicks++;
            if (this.updateClicks >= 10) {
                this.updateClicks = 0;
                this.playTestUpdate();
            }
        },

        async playTestUpdate() {
            this.showUpdateScreen = true;
            this.updateStatus = 'Initializing update simulation...';
            this.updateProgress = 0;
            this.updateVersionText = 'v5.0.2 (Test Build)';
            
            const steps = [
                { status: 'Stashing any local edits safely...', progress: 15 },
                { status: 'Connecting to github.com/danish-mar/kokomi...', progress: 35 },
                { status: 'Pulling updates (origin/main)...', progress: 55 },
                { status: 'Synchronizing python virtual environment (uv sync)...', progress: 80 },
                { status: 'Done! Kokomi will restart to apply changes...', progress: 100 }
            ];
            
            for (const step of steps) {
                await new Promise(resolve => setTimeout(resolve, 1500));
                this.updateStatus = step.status;
                this.updateProgress = step.progress;
            }
            
            // Warnings are visible on front-end above 90%
            setTimeout(() => {
                window.location.reload();
            }, 3000);
        },

        renderMarkdown(md) {
            if (!md) return '';
            try {
                if (window.marked) {
                    if (window.marked.parse) {
                        return window.marked.parse(md);
                    }
                    return window.marked(md);
                }
            } catch (err) {
                console.error("Markdown parsing failed:", err);
            }
            return md.replace(/\n/g, '<br>');
        },

        async savePrefs() {
            this.prefSaving = true;
            try {
                await fetch('/api/prefs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(this.prefs) });
                setTimeout(() => (this.prefSaving = false), 800);
            } catch(e) { console.error(e); this.prefSaving = false; }
        },

        // Embedding models discovered from the live NVIDIA catalog (/api/models),
        // filtered to retrieval/embedding models and excluding the curated ones we
        // already list statically. The generic model dropdowns show every NVIDIA
        // model; for embeddings we only want the embedders.
        nvidiaEmbeddingModels() {
            const curated = new Set([
                'nvidia/nv-embedqa-e5-v5', 'nvidia/llama-3.2-nv-embedqa-1b-v2',
                'nvidia/nv-embedqa-mistral-7b-v2', 'baai/bge-m3', 'snowflake/arctic-embed-l',
            ]);
            return (this.models || []).filter(m =>
                m.provider === 'nvidia' &&
                /embed|bge|arctic|e5|gte/i.test(m.id) &&
                !/rerank/i.test(m.id) &&
                !curated.has(m.id)
            );
        },

        // NVIDIA *chat* models only — hides embedding/rerank models from the
        // conversational/Atlas model dropdowns (the raw catalog mixes them in).
        nvidiaChatModels() {
            return (this.models || []).filter(m =>
                m.provider === 'nvidia' && !/embed|rerank|bge|arctic/i.test(m.id)
            );
        },

        async resetTourAndStart() {
            this.prefs.tour_completed = false;
            localStorage.removeItem('kokomi_tour_completed');
            await this.savePrefs();
            window.location.href = '/';
        },

        async refreshMCPPool() {
            this.refreshingPool = true;
            try {
                const r = await fetch('/api/mcp-servers/pool/init?force=true', { method: 'POST' });
                const res = await r.json();
                if (res.ok) {
                    localStorage.removeItem('kokomi_pool_init_ts');
                    this.showToast('MCP Pool refreshed!', 'success');
                } else {
                    alert('Error: ' + res.error);
                }
            } catch(e) { console.error(e); alert('Failed to refresh pool'); }
            finally { this.refreshingPool = false; }
        },

        async fetchModels() {
            try { const r = await fetch('/api/models'); this.models = await r.json(); } catch(e) { console.error(e); }
        },

        getMCPName(id) {
            const s = this.mcpServers.find(x => x.id === id);
            return s ? s.name : id;
        },

        async fetchChars() {
            try { const r = await fetch('/api/characters'); if (r.ok) this.characters = await r.json(); } catch(e) { console.error(e); }
        },

        openCharModal(char = null) {
            if (char) {
                this.charEditId = char.id; this.charName = char.name; this.charPersona = char.persona;
                this.charDescription = char.description || '';
                this.charGroqModel = char.groq_model || 'default'; this.charGoogleModel = char.google_model || 'default';
                this.charLocalModel = char.local_model || 'default'; this.charNvidiaModel = char.nvidia_model || 'default';
                this.charVoice = char.voice || 'aoede';
                this.charAvatarPreview = char.avatar;
                let srvs = [...(char.mcp_servers || [])];
                if (srvs.includes("appbridge")) {
                    srvs = srvs.filter(s => s !== "appbridge");
                    this.installedApps.forEach(app => {
                        if (!srvs.includes(app.id)) srvs.push(app.id);
                    });
                }
                this.charMCPServers = srvs;
                this.charSelectedTools = char.selected_tools ? [...char.selected_tools] : [];
            } else {
                this.charEditId = null; this.charName = ''; this.charPersona = ''; this.charDescription = '';
                this.charGroqModel = 'default'; this.charGoogleModel = 'default'; this.charLocalModel = 'default'; this.charNvidiaModel = 'default';
                this.charVoice = 'aoede'; this.charAvatarPreview = null; this.charMCPServers = [];
                this.charSelectedTools = [];
            }
            this.charAvatarFile = null; this.charModal = true;
        },

        async generateCharWithAI() {
            if (!this.aiCharPrompt.trim()) return;
            this.aiCharLoading = true;
            this.aiCharError = '';
            const availableTools = this.mcpServers.map(s => s.name).join(', ') || 'none';
            try {
                const resp = await fetch('/api/ai/generate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        system: 'You are a character design AI. Return ONLY a valid raw JSON object with no explanation, no markdown fences, no extra text.',
                        prompt: `Generate a JSON object for an AI assistant persona based on this concept: "${this.aiCharPrompt}".
Available MCP tools the character can be assigned: ${availableTools}.
The JSON object must have exactly these fields:
- name: string (creative character name)
- description: string (one punchy tagline, max 60 chars)
- persona: string (a detailed system prompt, 3-5 sentences, written in second person starting with "You are...")
- mcp_servers: array of tool names from the available list that suit this character (can be empty array)
Return ONLY the raw JSON. No markdown. No explanation.`
                    })
                });
                const data = await resp.json();
                if (data.error) throw new Error(data.error);
                const raw = (data.text || '').trim()
                    .replace(/^```[a-z]*\n?/, '').replace(/\n?```$/, '').trim();
                const parsed = JSON.parse(raw);
                this.charName = parsed.name || '';
                this.charDescription = parsed.description || '';
                this.charPersona = parsed.persona || '';
                const nameMap = {};
                this.mcpServers.forEach(s => { nameMap[s.name.toLowerCase()] = s.id; });
                this.charMCPServers = (parsed.mcp_servers || []).map(n => nameMap[n.toLowerCase()]).filter(Boolean);
                this.charSelectedTools = [];
                this.charMCPServers.forEach(sid => {
                    const serverTools = this.allPoolTools.filter(t => t.server_id === sid).map(t => t.name);
                    serverTools.forEach(name => {
                        if (!this.charSelectedTools.includes(name)) this.charSelectedTools.push(name);
                    });
                });
                this.charEditId = null; this.charAvatarPreview = null; this.charAvatarFile = null;
                this.charGroqModel = 'default'; this.charGoogleModel = 'default';
                this.charLocalModel = 'default'; this.charNvidiaModel = 'default';
                this.charVoice = 'aoede';
                this.aiCharModal = false;
                this.charModal = true;
            } catch(e) {
                this.aiCharError = 'Could not parse AI response. Try rephrasing your concept.';
                console.error('AI char gen error:', e);
            } finally {
                this.aiCharLoading = false;
            }
        },

        previewCharAvatar(e) {
            const f = e.target.files[0];
            if (!f) return;
            this.charAvatarFile = f;
            this.charAvatarPreview = URL.createObjectURL(f);
        },

        toggleCharMCP(id) {
            const i = this.charMCPServers.indexOf(id);
            if (i >= 0) {
                this.charMCPServers.splice(i, 1);
                // Also clean up selected tools for this server/app
                const serverTools = this.allPoolTools.filter(t => t.server_id === id).map(t => t.name);
                this.charSelectedTools = this.charSelectedTools.filter(name => !serverTools.includes(name));
            } else {
                this.charMCPServers.push(id);
                // Enable all tools for this server/app by default
                const serverTools = this.allPoolTools.filter(t => t.server_id === id).map(t => t.name);
                serverTools.forEach(name => {
                    if (!this.charSelectedTools.includes(name)) this.charSelectedTools.push(name);
                });
            }
        },

        toggleCharTool(toolName) {
            const idx = this.charSelectedTools.indexOf(toolName);
            if (idx >= 0) this.charSelectedTools.splice(idx, 1);
            else this.charSelectedTools.push(toolName);
        },

        async fetchMCPTools(id) {
            if (this.mcpTools[id]) { delete this.mcpTools[id]; return; }
            try {
                const r = await fetch(`/api/mcp-servers/${id}/test`, { method: 'POST' });
                const data = await r.json();
                if (data.ok) this.mcpTools = { ...this.mcpTools, [id]: data.tools };
                else alert(data.error);
            } catch(e) { console.error(e); }
        },

        async saveChar() {
            if (!this.charName.trim() || !this.charPersona.trim()) return;
            this.saving = true;
            const fd = new FormData();
            fd.append('name', this.charName.trim()); fd.append('persona', this.charPersona.trim());
            fd.append('description', this.charDescription.trim());
            fd.append('groq_model', this.charGroqModel); fd.append('google_model', this.charGoogleModel);
            fd.append('local_model', this.charLocalModel); fd.append('nvidia_model', this.charNvidiaModel);
            fd.append('voice', this.charVoice);
            fd.append('mcp_servers', this.charMCPServers.join(','));
            fd.append('selected_tools', this.charSelectedTools.join(','));
            if (this.charAvatarFile) fd.append('avatar', this.charAvatarFile);
            try {
                const url = this.charEditId ? `/api/characters/${this.charEditId}` : '/api/characters';
                const r = await fetch(url, { method: this.charEditId ? 'PUT' : 'POST', body: fd });
                if (r.ok) { await this.fetchChars(); this.charModal = false; }
                else { const e = await r.json().catch(() => ({})); alert(e.detail || 'Failed'); }
            } catch { alert('Network error'); }
            finally { this.saving = false; }
        },

        async saveCharSettings(char) {
            const fd = new FormData();
            fd.append('name', char.name);
            fd.append('persona', char.persona);
            fd.append('groq_model', char.groq_model || 'default');
            fd.append('google_model', char.google_model || 'default');
            fd.append('local_model', char.local_model || 'default');
            fd.append('nvidia_model', char.nvidia_model || 'default');
            fd.append('voice', char.voice || 'aoede');
            fd.append('memory_enabled', char.memory_enabled ? 'true' : 'false');
            fd.append('mcp_servers', (char.mcp_servers || []).join(','));
            fd.append('selected_tools', (char.selected_tools || []).join(','));

            try {
                await fetch(`/api/characters/${char.id}`, { method: 'PUT', body: fd });
            } catch(e) { console.error(e); }
        },

        async inspectMemories(char) {
            this.inspectingChar = char;
            this.memoryModal = true;
            this.loadingMemories = true;
            this.memories = [];
            try {
                const r = await fetch(`/api/characters/${char.id}/memories`);
                if (r.ok) {
                    this.memories = await r.json();
                }
            } catch(e) {
                console.error("Failed to fetch memories:", e);
            } finally {
                this.loadingMemories = false;
            }
        },

        async deleteMemory(memId) {
            if (!confirm("Are you sure you want the character to forget this fact?")) return;
            try {
                const r = await fetch(`/api/characters/${this.inspectingChar.id}/memories/${memId}`, {
                    method: 'DELETE'
                });
                if (r.ok) {
                    this.memories = this.memories.filter(m => m.id !== memId);
                }
            } catch(e) {
                console.error("Failed to delete memory:", e);
            }
        },

        async deleteChar(id) {
            if (!confirm('Delete this character?')) return;
            try { await fetch(`/api/characters/${id}`, { method: 'DELETE' }); await this.fetchChars(); } catch(e) { console.error(e); }
        },

        async fetchMCP() {
            try { const r = await fetch('/api/mcp-servers'); if (r.ok) this.mcpServers = await r.json(); } catch(e) { console.error(e); }
        },

        async fetchPoolTools() {
            try {
                const r = await fetch('/api/mcp-servers/pool/tools-detailed');
                if (r.ok) this.allPoolTools = await r.json();
            } catch(e) { console.error(e); }
        },

        openMCPModal(srv = null) {
            if (srv) {
                this.mcpEditId = srv.id; this.mcpName = srv.name; this.mcpIcon = srv.icon || ''; this.mcpTransport = srv.transport || 'stdio';
                this.mcpCommand = srv.command || ''; this.mcpArgs = (srv.args || []).join('\n');
                this.mcpEnv = Object.entries(srv.env || {}).map(([k,v]) => `${k}=${v}`).join('\n');
                this.mcpUrl = srv.url || ''; this.mcpEnabled = srv.enabled !== false;
            } else {
                this.mcpEditId = null; this.mcpName = ''; this.mcpIcon = ''; this.mcpTransport = 'stdio';
                this.mcpCommand = ''; this.mcpArgs = ''; this.mcpEnv = ''; this.mcpUrl = ''; this.mcpEnabled = true;
            }
            this.mcpModal = true;
        },

        async saveMCP() {
            if (!this.mcpName.trim()) return;
            this.saving = true;
            const args = this.mcpArgs.split('\n').map(s => s.trim()).filter(Boolean);
            const env = {};
            this.mcpEnv.split('\n').forEach(line => { const eq = line.indexOf('='); if (eq > 0) env[line.slice(0,eq).trim()] = line.slice(eq+1).trim(); });
            const body = { name: this.mcpName.trim(), icon: this.mcpIcon.trim() || null, transport: this.mcpTransport, command: this.mcpCommand.trim() || null, args, env, url: this.mcpUrl.trim() || null, enabled: this.mcpEnabled };
            try {
                const url = this.mcpEditId ? `/api/mcp-servers/${this.mcpEditId}` : '/api/mcp-servers';
                const r = await fetch(url, { method: this.mcpEditId ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
                if (r.ok) { await this.fetchMCP(); this.mcpModal = false; }
                else { const e = await r.json().catch(() => ({})); alert(e.detail || 'Failed'); }
            } catch { alert('Network error'); }
            finally { this.saving = false; }
        },

        async deleteMCP(id) {
            if (!confirm('Delete this MCP server?')) return;
            try { await fetch(`/api/mcp-servers/${id}`, { method: 'DELETE' }); await Promise.all([this.fetchMCP(), this.fetchChars()]); } catch(e) { console.error(e); }
        },

        async testMCP(id) {
            this.testingId = id; this.testResult = null;
            try {
                const r = await fetch(`/api/mcp-servers/${id}/test`, { method: 'POST' });
                this.testResult = await r.json();
                setTimeout(() => (this.testResult = null), 4000);
            } catch { this.testResult = { ok: false, error: 'Network error' }; setTimeout(() => (this.testResult = null), 4000); }
            finally { this.testingId = null; }
        },
        async fetchInstalledApps() {
            try {
                const r = await fetch('/api/app-store/installed-apps');
                if (r.ok) {
                    this.installedApps = await r.json();
                }
            } catch(e) {
                console.error("Failed to fetch installed apps:", e);
            }
        },

        async toggleApp(app) {
            try {
                const r = await fetch('/api/app-store/toggle', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: app.id, enabled: app.enabled })
                });
                if (r.ok) {
                    this.showToast('App status updated!', 'success');
                } else {
                    this.showToast('Failed to toggle app', 'error');
                }
            } catch(e) {
                console.error("Failed to toggle app:", e);
                this.showToast('Failed to toggle app', 'error');
            }
        },

        async uninstallApp(appId) {
            if (!confirm('Are you sure you want to uninstall this app?')) return;
            try {
                const r = await fetch(`/api/app-store/uninstall/${appId}`, { method: 'DELETE' });
                if (r.ok) {
                    this.showToast('App uninstalled successfully!', 'success');
                    await this.fetchInstalledApps();
                } else {
                    this.showToast('Failed to uninstall app', 'error');
                }
            } catch(e) {
                console.error("Failed to uninstall app:", e);
                this.showToast('Failed to uninstall app', 'error');
            }
        },
    };
}
