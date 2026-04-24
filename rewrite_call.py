import re

with open("templates/call.html", "r") as f:
    content = f.read()

# 1. Remove Bottom Nav CSS
content = re.sub(r'/\* ── Bottom Nav Bar ── \*/.*?/\* Adjust contacts-view to account for bottom nav \*/', '/* Adjust contacts-view */', content, flags=re.DOTALL)
content = content.replace('.contacts-view { position:relative; padding-bottom:80px; }', '.contacts-view { flex:1; display:flex; flex-direction:column; }')
content = content.replace('.contact-grid { padding:0 20px 24px; overflow-y:auto; flex:1; align-content:start; }', '.contact-grid { padding:0 20px 24px; overflow-y:auto; flex:1; align-content:start; }')

# 2. Fix Call Screen CSS to use flexbox properly so no overlaps occur
old_call_screen = """    /* ── Active Call Screen ── */
    .call-screen { position:fixed; inset:0; z-index:500; display:flex; flex-direction:column; align-items:center; justify-content:center; }
    .call-bg { position:absolute; inset:0; background:var(--bg-canvas); backdrop-filter:var(--vibrancy); -webkit-backdrop-filter:var(--vibrancy); opacity:0.85; }
    .call-content { position:relative; z-index:1; display:flex; flex-direction:column; align-items:center; text-align:center; width:100%; max-width:360px; padding:0 24px; }
    .call-avatar-ring { width:140px; height:140px; border-radius:40px; padding:6px; margin-bottom:28px; transition:box-shadow 0.08s ease-out; background:var(--bg-elevated); box-shadow:var(--shadow-lg); }"""

new_call_screen = """    /* ── Active Call Screen ── */
    .call-screen { position:fixed; inset:0; z-index:500; display:flex; flex-direction:column; }
    .call-bg { position:absolute; inset:0; background:var(--bg-canvas); backdrop-filter:var(--vibrancy); -webkit-backdrop-filter:var(--vibrancy); opacity:0.85; }
    .call-header { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; position:relative; z-index:1; padding:20px; }
    .call-bottom { position:relative; z-index:1; display:flex; flex-direction:column; align-items:center; padding-bottom:50px; width:100%; }
    .call-avatar-ring { width:130px; height:130px; border-radius:65px; padding:6px; margin-bottom:20px; transition:box-shadow 0.08s ease-out; background:var(--bg-elevated); box-shadow:var(--shadow-lg); }
    .call-avatar-ring img, .call-avatar-ring .placeholder { border-radius:60px !important; }"""
content = content.replace(old_call_screen, new_call_screen)

content = content.replace('.call-status { font-size:14px; color:var(--text-tertiary); font-weight:600; margin-bottom:48px; }', '.call-status { font-size:15px; color:var(--text-tertiary); font-weight:500; margin-bottom:20px; }')
content = content.replace('.waveform { display:flex; align-items:center; justify-content:center; gap:3px; height:40px; margin-bottom:48px; }', '.waveform { display:flex; align-items:center; justify-content:center; gap:3px; height:40px; margin-bottom:20px; }')

# Fix transcript css
old_transcript = """    /* ── Transcript ── */
    .transcript-area { position:absolute; bottom:180px; left:0; right:0; text-align:center; padding:0 32px; }
    .transcript-text { font-size:15px; color:var(--text-secondary); font-weight:500; line-height:1.5; max-height:80px; overflow-y:auto; }"""
new_transcript = """    /* ── Transcript ── */
    .transcript-area { text-align:center; padding:0 32px; width:100%; max-width:400px; margin-top:20px; }
    .transcript-text { font-size:16px; color:var(--text-secondary); font-weight:500; line-height:1.5; max-height:120px; overflow-y:auto; }
    
    .mcp-status-bubble { background:rgba(10,132,255,0.15); color:#0A84FF; padding:8px 16px; border-radius:20px; display:inline-flex; align-items:center; gap:8px; font-weight:600; font-size:14px; margin-bottom:12px; }"""
content = content.replace(old_transcript, new_transcript)

# Call Controls CSS
old_call_controls = """    /* ── iOS Call Controls ── */
    .call-controls { display:grid; grid-template-columns:repeat(3, 1fr); gap:16px 28px; margin-bottom:40px; }
    .call-btn-wrapper { display:flex; flex-direction:column; align-items:center; gap:8px; }
    .call-btn { width:68px; height:68px; border-radius:34px; border:none; cursor:pointer; display:flex; align-items:center; justify-content:center; font-size:26px; transition:all 0.2s ease; backdrop-filter:blur(12px); background:rgba(255,255,255,0.15); color:#fff; }
    .call-btn:active { transform:scale(0.92); }
    .call-btn.on { background:#fff; color:#000; }
    .call-btn-label { font-size:14px; font-weight:500; color:#fff; text-shadow:0 1px 4px rgba(0,0,0,0.3); }"""
new_call_controls = """    /* ── iOS Call Controls ── */
    .call-controls { display:grid; grid-template-columns:repeat(3, 1fr); gap:16px 28px; margin-bottom:40px; max-width:320px; width:100%; }
    .call-btn-wrapper { display:flex; flex-direction:column; align-items:center; gap:8px; }
    .call-btn { width:72px; height:72px; border-radius:36px; border:none; cursor:pointer; display:flex; align-items:center; justify-content:center; font-size:28px; transition:all 0.2s ease; backdrop-filter:blur(12px); background:rgba(255,255,255,0.15); color:#fff; }
    .call-btn:active { transform:scale(0.92); }
    .call-btn.on { background:#fff; color:#000; }
    .call-btn-label { font-size:14px; font-weight:500; color:#fff; text-shadow:0 1px 4px rgba(0,0,0,0.3); }"""
content = content.replace(old_call_controls, new_call_controls)

# HTML changes: Remove bottom nav, add Recents button to header
old_contacts_html = """        <div class="contacts-header" style="padding-bottom:12px;">
            <h1 x-text="activeTab === 'recents' ? 'Recents' : (activeTab === 'favorites' ? 'Favorites' : 'Contacts')">Calls</h1>
            <p style="font-size:13px; color:var(--text-tertiary); margin-top:4px;" x-show="activeTab === 'contacts'">Voice call your AI characters with Gemini Live.</p>
        </div>"""
new_contacts_html = """        <div class="contacts-header" style="padding-bottom:12px;">
            <div>
                <h1 x-text="activeTab === 'recents' ? 'Recent Calls' : 'Contacts'">Calls</h1>
                <p style="font-size:13px; color:var(--text-tertiary); margin-top:4px;" x-show="activeTab === 'contacts'">Voice call your AI characters with Gemini Live.</p>
            </div>
            <button @click="activeTab = activeTab === 'contacts' ? 'recents' : 'contacts'" style="background:rgba(10,132,255,0.15); color:#0A84FF; border:none; padding:8px 16px; border-radius:20px; font-weight:600; font-size:14px; cursor:pointer; transition:all 0.2s;">
                <i class="fa-solid fa-clock" style="margin-right:6px;"></i> <span x-text="activeTab === 'contacts' ? 'Recents' : 'Contacts'"></span>
            </button>
        </div>"""
content = content.replace(old_contacts_html, new_contacts_html)

# Remove the bottom nav HTML completely
content = re.sub(r'<!-- Bottom Nav -->.*?</div>\s*</div>\s*<!-- ═══════ ACTIVE CALL SCREEN', '</div>\n\n    <!-- ═══════ ACTIVE CALL SCREEN', content, flags=re.DOTALL)

# Re-structure the Active Call Screen HTML
old_active_call_html = """        <div class="call-bg"></div>

        <div class="call-content">
            <!-- Avatar -->
            <div class="call-avatar-ring" 
                 :class="callStatus === 'connecting' ? 'connecting' : ((callStatus === 'active' || callStatus.startsWith('Using tool')) ? 'active' : '')"
                 :style="(callStatus === 'active' || callStatus.startsWith('Using tool')) ? `box-shadow: 0 0 0 ${4 + aiVolume * 16}px var(--accent-subtle), 0 0 ${40 + aiVolume * 80}px var(--accent-subtle-h)` : ''">
                <template x-if="callChar?.avatar">
                    <img :src="callChar.avatar" />
                </template>
                <template x-if="!callChar?.avatar">
                    <div class="placeholder" x-text="callChar?.name?.charAt(0) || '?'"></div>
                </template>
            </div>

            <!-- Name & Status -->
            <div class="call-char-name" x-text="callChar?.name || 'Unknown'"></div>
            <div class="call-status" :class="(callStatus === 'active' || callStatus.startsWith('Using tool')) && 'active-status'"
                 x-html="callStatus === 'connecting' ? 'Connecting...' : (callStatus === 'active' ? callDurationStr : (callStatus.startsWith('Using tool') ? `<i class='fa-solid fa-gear fa-spin' style='margin-right:4px; font-size:12px;'></i> ${callStatus}` : 'Ended'))">
            </div>

            <!-- Waveform Visualizer -->
            <div class="waveform" :class="isSpeaking && 'speaking'">
                <template x-for="i in 16" :key="i">
                    <div class="bar" :style="'height:' + waveHeights[i-1] + 'px'"></div>
                </template>
            </div>

            <!-- Controls -->
            <div class="call-controls">
                <div class="call-btn-wrapper">
                    <button class="call-btn" :class="isMuted && 'on'" @click="toggleMute()">
                        <i :class="isMuted ? 'fa-solid fa-microphone-slash' : 'fa-solid fa-microphone'"></i>
                    </button>
                    <div class="call-btn-label">mute</div>
                </div>
                <div class="call-btn-wrapper">
                    <button class="call-btn"><i class="fa-solid fa-table-cells"></i></button>
                    <div class="call-btn-label">keypad</div>
                </div>
                <div class="call-btn-wrapper">
                    <button class="call-btn" :class="speakerOn && 'on'" @click="toggleSpeaker()">
                        <i class="fa-solid fa-volume-high"></i>
                    </button>
                    <div class="call-btn-label">audio</div>
                </div>
                <div class="call-btn-wrapper">
                    <button class="call-btn" style="opacity:0.6;"><i class="fa-solid fa-plus"></i></button>
                    <div class="call-btn-label" style="opacity:0.6;">add call</div>
                </div>
                <div class="call-btn-wrapper">
                    <button class="call-btn" style="opacity:0.6;"><i class="fa-solid fa-video"></i></button>
                    <div class="call-btn-label" style="opacity:0.6;">FaceTime</div>
                </div>
                <div class="call-btn-wrapper">
                    <button class="call-btn" style="opacity:0.6;"><i class="fa-solid fa-circle-user"></i></button>
                    <div class="call-btn-label" style="opacity:0.6;">contacts</div>
                </div>
            </div>

            <div class="end-btn-container">
                <button class="call-btn end-btn" @click="endCall()">
                    <i class="fa-solid fa-phone"></i>
                </button>
            </div>
        </div>

        <!-- Transcript overlay -->
        <div class="transcript-area" x-show="transcript" x-transition>
            <p class="transcript-text" x-text="transcript"></p>
        </div>"""

new_active_call_html = """        <div class="call-bg"></div>

        <div class="call-header">
            <!-- Avatar -->
            <div class="call-avatar-ring" 
                 :class="callStatus === 'connecting' ? 'connecting' : ((callStatus === 'active' || callStatus.startsWith('Using tool')) ? 'active' : '')"
                 :style="(callStatus === 'active' || callStatus.startsWith('Using tool')) ? `box-shadow: 0 0 0 ${4 + aiVolume * 16}px var(--accent-subtle), 0 0 ${40 + aiVolume * 80}px var(--accent-subtle-h)` : ''">
                <template x-if="callChar?.avatar">
                    <img :src="callChar.avatar" />
                </template>
                <template x-if="!callChar?.avatar">
                    <div class="placeholder" x-text="callChar?.name?.charAt(0) || '?'"></div>
                </template>
            </div>

            <!-- Name & Status -->
            <div class="call-char-name" x-text="callChar?.name || 'Unknown'"></div>
            
            <template x-if="callStatus.startsWith('Using tool')">
                <div class="mcp-status-bubble" x-transition>
                    <i class="fa-solid fa-server fa-bounce"></i> <span x-text="callStatus"></span>
                </div>
            </template>
            <template x-if="!callStatus.startsWith('Using tool')">
                <div class="call-status" :class="callStatus === 'active' && 'active-status'"
                     x-text="callStatus === 'connecting' ? 'Connecting...' : (callStatus === 'active' ? callDurationStr : 'Ended')">
                </div>
            </template>

            <!-- Waveform Visualizer -->
            <div class="waveform" :class="isSpeaking && 'speaking'" x-show="!callStatus.startsWith('Using tool')">
                <template x-for="i in 16" :key="i">
                    <div class="bar" :style="'height:' + waveHeights[i-1] + 'px'"></div>
                </template>
            </div>

            <!-- Transcript overlay -->
            <div class="transcript-area" x-show="transcript" x-transition>
                <p class="transcript-text" x-text="transcript"></p>
            </div>
        </div>

        <div class="call-bottom">
            <!-- Controls -->
            <div class="call-controls">
                <div class="call-btn-wrapper">
                    <button class="call-btn" :class="isMuted && 'on'" @click="toggleMute()">
                        <i :class="isMuted ? 'fa-solid fa-microphone-slash' : 'fa-solid fa-microphone'"></i>
                    </button>
                    <div class="call-btn-label">mute</div>
                </div>
                <div class="call-btn-wrapper">
                    <button class="call-btn"><i class="fa-solid fa-table-cells"></i></button>
                    <div class="call-btn-label">keypad</div>
                </div>
                <div class="call-btn-wrapper">
                    <button class="call-btn" :class="speakerOn && 'on'" @click="toggleSpeaker()">
                        <i class="fa-solid fa-volume-high"></i>
                    </button>
                    <div class="call-btn-label">speaker</div>
                </div>
                <div class="call-btn-wrapper">
                    <button class="call-btn" style="opacity:0.6;"><i class="fa-solid fa-plus"></i></button>
                    <div class="call-btn-label" style="opacity:0.6;">add call</div>
                </div>
                <div class="call-btn-wrapper">
                    <button class="call-btn" style="opacity:0.6;"><i class="fa-solid fa-video"></i></button>
                    <div class="call-btn-label" style="opacity:0.6;">FaceTime</div>
                </div>
                <div class="call-btn-wrapper">
                    <button class="call-btn" style="opacity:0.6;"><i class="fa-solid fa-circle-user"></i></button>
                    <div class="call-btn-label" style="opacity:0.6;">contacts</div>
                </div>
            </div>

            <div class="end-btn-container">
                <button class="call-btn end-btn" @click="endCall()">
                    <i class="fa-solid fa-phone"></i>
                </button>
            </div>
        </div>"""
content = content.replace(old_active_call_html, new_active_call_html)

with open("templates/call.html", "w") as f:
    f.write(content)

