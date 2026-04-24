import re

with open("templates/index.html", "r") as f:
    content = f.read()

# 1. Add "Spaces" to sidebar
old_sidebar_projects = """                    <h2 class="sidebar-title">Projects</h2>"""
new_sidebar_spaces = """                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <h2 class="sidebar-title">Projects</h2>
                        <h2 class="sidebar-title" style="cursor:pointer;" @click="activeView = 'spaces'; isMobileMenuOpen = false">Spaces</h2>
                    </div>"""
if "Spaces</h2>" not in content:
    content = content.replace(old_sidebar_projects, new_sidebar_spaces)

# 2. Add spaces fetching and state to alpine store
# Find init()
init_old = """                async init() {
                    this.loadChatState();
                    await this.fetchConversations();
                    await this.fetchCharacters();"""
init_new = """                async init() {
                    this.loadChatState();
                    await this.fetchConversations();
                    await this.fetchCharacters();
                    await this.fetchSpaces();"""
content = content.replace(init_old, init_new)

# Find fetchConversations to insert fetchSpaces
fetch_old = """                async fetchConversations() {"""
fetch_new = """                spaces: [],
                activeSpaceId: null,
                spaceModal: false,
                spaceFiles: [],
                newSpaceName: '',
                newSpaceIcon: 'fa-solid fa-book',
                
                async fetchSpaces() {
                    try {
                        const r = await fetch('/api/spaces');
                        if (r.ok) this.spaces = await r.json();
                    } catch(e) { console.error(e); }
                },
                
                async createSpace() {
                    if (!this.newSpaceName) return;
                    try {
                        const r = await fetch('/api/spaces', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ name: this.newSpaceName, icon: this.newSpaceIcon })
                        });
                        if (r.ok) {
                            await this.fetchSpaces();
                            this.newSpaceName = '';
                            this.spaceModal = false;
                        }
                    } catch(e) { console.error(e); }
                },
                
                async deleteSpace(id) {
                    if (!confirm("Delete this knowledge space?")) return;
                    try {
                        await fetch(`/api/spaces/${id}`, { method: 'DELETE' });
                        await this.fetchSpaces();
                        if (this.activeSpaceId === id) this.activeSpaceId = null;
                    } catch(e) { console.error(e); }
                },
                
                async uploadSpaceFile(spaceId, file) {
                    const fd = new FormData();
                    fd.append("file", file);
                    try {
                        const r = await fetch(`/api/spaces/${spaceId}/files`, {
                            method: 'POST',
                            body: fd
                        });
                        if (r.ok) await this.fetchSpaces();
                    } catch(e) { console.error(e); }
                },
                
                async deleteSpaceFile(spaceId, fileId) {
                    try {
                        await fetch(`/api/spaces/${spaceId}/files/${fileId}`, { method: 'DELETE' });
                        await this.fetchSpaces();
                    } catch(e) { console.error(e); }
                },
                
                async fetchConversations() {"""
if "fetchSpaces()" not in content:
    content = content.replace(fetch_old, fetch_new)

# 3. Add Spaces dropdown to the chatbox (near the MCP badge or inside the input area)
# Let's put it on top of the chat input or as an icon in the input area
chat_input_old = """                            <!-- Text Area -->
                            <textarea id="chat-input-textarea" x-model="inputMsg" """
chat_input_new = """                            <!-- Space Selector -->
                            <div class="space-selector" x-show="spaces.length > 0" style="position:absolute; top:-36px; left:20px; background:var(--bg-elevated); padding:4px 12px; border-radius:12px; border:1px solid var(--border); box-shadow:var(--shadow-sm); display:flex; align-items:center; gap:8px; font-size:12px; z-index:10;">
                                <i class="fa-solid fa-database" style="color:var(--text-tertiary);"></i>
                                <select x-model="activeSpaceId" style="background:transparent; border:none; color:var(--text-secondary); outline:none; cursor:pointer;">
                                    <option :value="null">No Space Linked</option>
                                    <template x-for="s in spaces" :key="s.id">
                                        <option :value="s.id" x-text="s.name"></option>
                                    </template>
                                </select>
                            </div>

                            <!-- Text Area -->
                            <textarea id="chat-input-textarea" x-model="inputMsg" """
content = content.replace(chat_input_old, chat_input_new)

# 4. Modify chat POST to include space_id
post_chat_old = """                            body: JSON.stringify({
                                message: this.inputMsg,
                                conversation_id: this.activeConvoId,
                                character_id: charId
                            })"""
post_chat_new = """                            body: JSON.stringify({
                                message: this.inputMsg,
                                conversation_id: this.activeConvoId,
                                character_id: charId,
                                space_id: this.activeSpaceId
                            })"""
content = content.replace(post_chat_old, post_chat_new)

# 5. Add Space Dashboard View
dashboard_old = """            <div class="settings-view" x-show="activeView === 'settings'" x-transition style="display:none; height:100%; overflow-y:auto; overflow-x:hidden;">"""
dashboard_new = """            <!-- Spaces Dashboard -->
            <div class="settings-view" x-show="activeView === 'spaces'" x-transition style="display:none; height:100%; overflow-y:auto; padding:32px;">
                <div style="max-width:900px; margin:0 auto;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:32px;">
                        <div>
                            <h1 style="font-size:32px; font-weight:800; color:var(--text-primary); margin-bottom:8px;">Knowledge Spaces</h1>
                            <p style="color:var(--text-secondary);">Manage RAG databases to extend your AI's knowledge.</p>
                        </div>
                        <button @click="spaceModal = true" class="apple-btn apple-btn-primary">
                            <i class="fa-solid fa-plus"></i> New Space
                        </button>
                    </div>

                    <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); gap:24px;">
                        <template x-for="s in spaces" :key="s.id">
                            <div class="char-card" style="flex-direction:column; align-items:flex-start; position:relative;">
                                <button @click="deleteSpace(s.id)" style="position:absolute; top:16px; right:16px; background:transparent; border:none; color:var(--danger); cursor:pointer;">
                                    <i class="fa-solid fa-trash"></i>
                                </button>
                                <div style="display:flex; align-items:center; gap:12px; margin-bottom:16px;">
                                    <div style="width:40px; height:40px; border-radius:10px; background:rgba(10,132,255,0.1); color:#0A84FF; display:flex; align-items:center; justify-content:center; font-size:18px;">
                                        <i :class="s.icon"></i>
                                    </div>
                                    <div style="font-weight:600; font-size:16px; color:var(--text-primary);" x-text="s.name"></div>
                                </div>
                                
                                <div style="width:100%; background:var(--bg-canvas); border-radius:8px; padding:12px; margin-bottom:16px;">
                                    <div style="font-size:11px; font-weight:600; color:var(--text-tertiary); text-transform:uppercase; margin-bottom:8px; letter-spacing:0.05em;">Files (Stars)</div>
                                    <template x-for="f in s.files" :key="f.id">
                                        <div style="display:flex; justify-content:space-between; align-items:center; font-size:13px; margin-bottom:6px;">
                                            <div style="display:flex; align-items:center; gap:6px; overflow:hidden;">
                                                <i class="fa-solid fa-file-pdf" style="color:#FF3B30;" x-show="f.filename.endsWith('.pdf')"></i>
                                                <i class="fa-solid fa-file-word" style="color:#0A84FF;" x-show="f.filename.endsWith('.docx')"></i>
                                                <i class="fa-solid fa-file-lines" style="color:var(--text-tertiary);" x-show="!f.filename.endsWith('.pdf') && !f.filename.endsWith('.docx')"></i>
                                                <span style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" x-text="f.filename"></span>
                                            </div>
                                            <i class="fa-solid fa-xmark" style="cursor:pointer; color:var(--text-tertiary);" @click="deleteSpaceFile(s.id, f.id)"></i>
                                        </div>
                                    </template>
                                    <div x-show="s.files.length === 0" style="font-size:12px; color:var(--text-tertiary);">No files uploaded.</div>
                                </div>

                                <div style="width:100%;">
                                    <input type="file" :id="'file_' + s.id" style="display:none;" @change="uploadSpaceFile(s.id, $event.target.files[0])" accept=".pdf,.docx,.txt,.csv,.md">
                                    <button @click="document.getElementById('file_' + s.id).click()" class="apple-btn" style="width:100%; justify-content:center;">
                                        <i class="fa-solid fa-upload"></i> Upload File
                                    </button>
                                </div>
                            </div>
                        </template>
                    </div>
                </div>
            </div>

            <!-- New Space Modal -->
            <div class="modal-overlay" x-show="spaceModal" style="display:none;">
                <div class="modal-content" @click.away="spaceModal = false">
                    <h2 class="section-title">Create Knowledge Space</h2>
                    <p class="section-subtitle" style="margin-bottom:24px;">This space will process documents into a vector database for RAG.</p>
                    
                    <label class="apple-label">Space Name</label>
                    <input type="text" x-model="newSpaceName" class="apple-field" placeholder="e.g. Computer Science 101" style="margin-bottom:16px;">
                    
                    <label class="apple-label">Icon (FontAwesome class)</label>
                    <input type="text" x-model="newSpaceIcon" class="apple-field" placeholder="fa-solid fa-book" style="margin-bottom:24px;">

                    <div style="display:flex; justify-content:flex-end; gap:12px;">
                        <button class="apple-btn" @click="spaceModal = false">Cancel</button>
                        <button class="apple-btn apple-btn-primary" @click="createSpace()">Create Space</button>
                    </div>
                </div>
            </div>

            <div class="settings-view" x-show="activeView === 'settings'" x-transition style="display:none; height:100%; overflow-y:auto; overflow-x:hidden;">"""
if "Knowledge Spaces</h1>" not in content:
    content = content.replace(dashboard_old, dashboard_new)

with open("templates/index.html", "w") as f:
    f.write(content)

