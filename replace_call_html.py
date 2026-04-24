import re

with open("templates/call.html", "r") as f:
    content = f.read()

css_old = """    .horizontal-contacts-container { margin-bottom:28px; }
    .horizontal-contacts { display:flex; gap:16px; overflow-x:auto; padding:0 20px 8px; scrollbar-width:none; -ms-overflow-style:none; }
    .horizontal-contacts::-webkit-scrollbar { display:none; }
    .h-contact-item { display:flex; flex-direction:column; align-items:center; gap:8px; cursor:pointer; width:68px; flex-shrink:0; }
    .h-contact-avatar { width:68px; height:68px; border-radius:34px; background:var(--bg-surface); display:flex; align-items:center; justify-content:center; overflow:hidden; font-weight:700; color:var(--text-primary); font-size:24px; box-shadow:0 4px 12px rgba(0,0,0,0.1); outline:2px solid var(--accent-subtle); outline-offset:2px; transition:all 0.2s; }
    .h-contact-item:active .h-contact-avatar { transform:scale(0.95); }
    .h-contact-avatar img { width:100%; height:100%; object-fit:cover; }
    .h-contact-name { font-size:13px; font-weight:600; color:var(--text-secondary); text-align:center; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; width:100%; }"""

css_new = """    .horizontal-contacts-container { margin-bottom:32px; }
    .horizontal-contacts { display:flex; gap:16px; overflow-x:auto; padding:0 20px 12px; scrollbar-width:none; -ms-overflow-style:none; }
    .horizontal-contacts::-webkit-scrollbar { display:none; }
    
    .contact-card { position:relative; width:120px; aspect-ratio:3/4; border-radius:20px; overflow:hidden; cursor:pointer; background:var(--bg-surface); transition:all 0.3s cubic-bezier(0.2, 0.8, 0.2, 1); box-shadow:0 8px 24px rgba(0,0,0,0.08); flex-shrink:0; }
    .contact-card:active { transform:scale(0.96); box-shadow:0 4px 12px rgba(0,0,0,0.1); }
    .contact-card img { width:100%; height:100%; object-fit:cover; transition:transform 0.5s ease; }
    .contact-card .placeholder { width:100%; height:100%; display:flex; align-items:center; justify-content:center; font-size:40px; font-weight:700; color:#fff; background:linear-gradient(135deg, #505081, #8686AC); }
    .card-overlay { position:absolute; inset:0; background:linear-gradient(to bottom, transparent 40%, rgba(15,14,71,0.9) 100%); pointer-events:none; }
    .card-info { position:absolute; bottom:0; left:0; right:0; padding:12px; z-index:2; }
    .card-name { font-size:14px; font-weight:700; color:#fff; letter-spacing:-0.02em; margin-bottom:2px; text-shadow:0 2px 8px rgba(0,0,0,0.5); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .card-call-icon { position:absolute; right:8px; bottom:8px; width:28px; height:28px; border-radius:14px; background:rgba(52,199,89,0.9); color:#fff; display:flex; align-items:center; justify-content:center; font-size:12px; backdrop-filter:blur(20px); opacity:0; transform:translateY(10px); transition:all 0.3s ease; }
    .contact-card:hover .card-call-icon { opacity:1; transform:translateY(0); }"""

content = content.replace(css_old, css_new)

html_old = """        <!-- Horizontal Contacts List -->
        <div class="horizontal-contacts-container">
            <h2 class="section-title">Contacts</h2>
            <div class="horizontal-contacts">
                <template x-for="char in filteredChars" :key="char.id">
                    <div class="h-contact-item" @click="startCall(char)">
                        <div class="h-contact-avatar">
                            <template x-if="char.avatar"><img :src="char.avatar" /></template>
                            <template x-if="!char.avatar"><span x-text="char.name.charAt(0)"></span></template>
                        </div>
                        <div class="h-contact-name" x-text="char.name.split(' ')[0]"></div>
                    </div>
                </template>
                <template x-if="filteredChars.length === 0">
                    <div style="padding:0 20px; color:var(--text-tertiary); font-size:14px;">No contacts found.</div>
                </template>
            </div>
        </div>"""

html_new = """        <!-- Horizontal Contacts List -->
        <div class="horizontal-contacts-container">
            <h2 class="section-title">Contacts</h2>
            <div class="horizontal-contacts">
                <template x-for="char in filteredChars" :key="char.id">
                    <div class="contact-card" @click="startCall(char)">
                        <template x-if="char.avatar">
                            <img :src="char.avatar" />
                        </template>
                        <template x-if="!char.avatar">
                            <div class="placeholder" x-text="char.name.charAt(0)"></div>
                        </template>
                        <div class="card-overlay"></div>
                        <div class="card-info">
                            <div class="card-name" x-text="char.name"></div>
                        </div>
                        <div class="card-call-icon">
                            <i class="fa-solid fa-phone"></i>
                        </div>
                    </div>
                </template>
                <template x-if="filteredChars.length === 0">
                    <div style="padding:0 20px; color:var(--text-tertiary); font-size:14px;">No contacts found.</div>
                </template>
            </div>
        </div>"""

content = content.replace(html_old, html_new)

with open("templates/call.html", "w") as f:
    f.write(content)
