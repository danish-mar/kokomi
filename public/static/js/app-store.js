/* ═══════════════════════════════════════════════════════
   App Store — Alpine.js Application Component
   Integrates directly with GitHub raw store & dynamic bridge
   ═══════════════════════════════════════════════════════ */

window.appStoreApp = function() {
    return {
        apps: [],
        featuredApp: null,
        installedIds: [],
        selectedApp: null,
        modalOpen: false,
        uninstallConfirmOpen: false,
        uninstallAppTarget: null,
        modalImgFailed: false,
        catalogBaseUrl: "https://raw.githubusercontent.com/danish-mar/kokomi-appstore/main",
        loadingId: null,
        toast: { show: false, message: "", type: "success" },
        sidebarTab: "discover",
        searchQ: "",
        updates: [
            {
                id: "update-1",
                name: "Weather App",
                version: "v1.0.0",
                date: "Today",
                description: "Initial release of Weather app, pulling actual JSON reports from free weather service.",
                icon: "fa-cloud-sun"
            },
            {
                id: "update-2",
                name: "Spotify Connect",
                version: "v1.0.0",
                date: "Yesterday",
                description: "Simulate playing/pausing music playback natively in LLM context.",
                icon: "fa-music"
            }
        ],
        prefs: {
            user_name: 'User',
            user_avatar: null
        },

        async init() {
            await this.fetchCatalog();
            await this.fetchPrefs();
        },

        async fetchCatalog() {
            try {
                const res = await fetch("/api/app-store/catalog");
                const data = await res.json();
                this.apps = [...data.apps, ...data.personas];
                this.featuredApp = data.featuredApp;
                this.installedIds = data.installedIds || [];
                this.catalogBaseUrl = data.catalogBaseUrl || "https://raw.githubusercontent.com/danish-mar/kokomi-appstore/main";
            } catch (err) {
                console.error("Failed to fetch App Store catalog:", err);
            }
        },

        async fetchPrefs() {
            try {
                const r = await fetch('/api/prefs');
                this.prefs = await r.json();
            } catch (err) {
                console.error("Failed to fetch user preferences:", err);
            }
        },

        getFilteredApps(type) {
            const q = this.searchQ.toLowerCase().trim();
            return this.apps.filter(app => {
                if (type && app.type !== type) return false;
                if (!q) return true;
                return app.name.toLowerCase().includes(q) ||
                       app.tagline.toLowerCase().includes(q) ||
                       app.category.toLowerCase().includes(q) ||
                       app.description.toLowerCase().includes(q);
            });
        },

        getFilteredUpdates() {
            const q = this.searchQ.toLowerCase().trim();
            if (!q) return this.updates;
            return this.updates.filter(u =>
                u.name.toLowerCase().includes(q) ||
                u.description.toLowerCase().includes(q) ||
                u.version.toLowerCase().includes(q)
            );
        },

        getIosDate() {
            const options = { weekday: 'long', month: 'long', day: 'numeric' };
            return new Date().toLocaleDateString('en-US', options).toUpperCase();
        },

        async refreshInstalledStatus() {
            await this.fetchCatalog();
        },

        openAppDetails(app) {
            this.selectedApp = app;
            this.modalOpen = true;
            this.modalImgFailed = false;
        },

        closeModal() {
            this.modalOpen = false;
            this.selectedApp = null;
        },

        isInstalled(appId) {
            return this.installedIds.includes(appId);
        },

        async installApp(app, event) {
            if (event) event.stopPropagation();
            if (this.isInstalled(app.id)) return;

            this.loadingId = app.id;

            try {
                const res = await fetch("/api/app-store/install", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        id: app.id,
                        type: app.type, // 'mcp' (app) or 'character' (persona)
                        path: app.path
                    })
                });
                if (!res.ok) {
                    const errData = await res.json().catch(() => ({}));
                    throw new Error(errData.detail || "Failed to install app");
                }

                this.showToast(`Successfully installed "${app.name}"`, "success");
                await this.fetchCatalog();
            } catch (err) {
                console.error("Installation failed:", err);
                this.showToast(`Failed to install "${app.name}": ${err.message}`, "error");
            } finally {
                this.loadingId = null;
            }
        },

        confirmUninstall(app, event) {
            if (event) event.stopPropagation();
            this.uninstallAppTarget = app;
            this.uninstallConfirmOpen = true;
        },

        closeUninstallModal() {
            this.uninstallConfirmOpen = false;
            this.uninstallAppTarget = null;
        },

        async executeUninstall() {
            if (!this.uninstallAppTarget) return;
            const app = this.uninstallAppTarget;
            this.loadingId = app.id;
            this.closeUninstallModal();
            this.closeModal();
            
            try {
                const res = await fetch(`/api/app-store/uninstall/${app.id}`, {
                    method: "DELETE"
                });
                if (!res.ok) {
                    const errData = await res.json().catch(() => ({}));
                    throw new Error(errData.detail || "Failed to uninstall");
                }
                
                const actionWord = app.type === 'character' ? 'removed' : 'uninstalled';
                const nounWord = app.type === 'character' ? 'Persona' : 'App';
                this.showToast(`Successfully ${actionWord} "${app.name}"`, "success");
                await this.fetchCatalog();
            } catch (err) {
                console.error("Uninstallation failed:", err);
                this.showToast(`Failed to uninstall "${app.name}": ${err.message}`, "error");
            } finally {
                this.loadingId = null;
            }
        },

        async executeUninstallDirect(app) {
            if (!app) return;
            this.loadingId = app.id;
            this.closeModal();
            
            try {
                const res = await fetch(`/api/app-store/uninstall/${app.id}`, {
                    method: "DELETE"
                });
                if (!res.ok) {
                    const errData = await res.json().catch(() => ({}));
                    throw new Error(errData.detail || "Failed to uninstall");
                }
                
                const actionWord = app.type === 'character' ? 'removed' : 'uninstalled';
                this.showToast(`Successfully ${actionWord} "${app.name}"`, "success");
                await this.fetchCatalog();
            } catch (err) {
                console.error("Uninstallation failed:", err);
                this.showToast(`Failed to uninstall "${app.name}": ${err.message}`, "error");
            } finally {
                this.loadingId = null;
            }
        },

        showToast(message, type = "success") {
            this.toast.message = message;
            this.toast.type = type;
            this.toast.show = true;
            setTimeout(() => {
                this.toast.show = false;
            }, 3000);
        }
    };
};
