import re

with open("templates/call.html", "r") as f:
    content = f.read()

css_old = """    .call-avatar-ring { width:130px; height:130px; border-radius:65px; padding:6px; margin-bottom:20px; transition:box-shadow 0.08s ease-out; background:var(--bg-elevated); box-shadow:var(--shadow-lg); }
    .call-avatar-ring img, .call-avatar-ring .placeholder { border-radius:60px !important; }"""

css_new = """    .call-avatar-ring-container { position:relative; width:140px; height:140px; margin-bottom:20px; cursor:pointer; }
    .call-avatar-ring { position:absolute; inset:5px; border-radius:50%; padding:4px; background:var(--bg-elevated); box-shadow:var(--shadow-lg); z-index:2; transition:transform 0.2s; }
    .call-avatar-ring:active { transform:scale(0.95); }
    .call-avatar-ring img, .call-avatar-ring .placeholder { border-radius:50% !important; width:100%; height:100%; object-fit:cover; }
    
    /* Reactive speaking aura */
    .speak-aura { position:absolute; inset:0; border-radius:50%; background:var(--accent); opacity:0; transition:opacity 0.1s, transform 0.1s; z-index:1; filter:blur(12px); }
    
    /* Revolving thinking/tool aura */
    .think-aura { position:absolute; inset:-4px; border-radius:50%; background:conic-gradient(from 0deg, transparent 70%, #0A84FF 100%); z-index:1; animation:spin 1s linear infinite; opacity:0; transition:opacity 0.3s; }
    @keyframes spin { 100% { transform:rotate(360deg); } }
"""
content = content.replace(css_old, css_new)

html_old = """            <!-- Avatar -->
            <div class="call-avatar-ring" 
                 :class="callStatus === 'connecting' ? 'connecting' : ((callStatus === 'active' || callStatus.startsWith('Using tool')) ? 'active' : '')"
                 :style="(callStatus === 'active' || callStatus.startsWith('Using tool')) ? `box-shadow: 0 0 0 ${4 + aiVolume * 16}px var(--accent-subtle), 0 0 ${40 + aiVolume * 80}px var(--accent-subtle-h)` : ''">
                <template x-if="callChar?.avatar">
                    <img :src="callChar.avatar" />
                </template>
                <template x-if="!callChar?.avatar">
                    <div class="placeholder" x-text="callChar?.name?.charAt(0) || '?'"></div>
                </template>
            </div>"""

html_new = """            <!-- Avatar Container -->
            <div class="call-avatar-ring-container" @click="showTranscript = !showTranscript">
                <!-- Auras -->
                <div class="think-aura" :style="(callStatus === 'connecting' || callStatus.startsWith('Using tool')) ? 'opacity:1;' : 'opacity:0;'"></div>
                <div class="speak-aura" :style="(callStatus === 'active' && aiVolume > 0.05) ? `opacity:${0.4 + aiVolume * 0.6}; transform:scale(${1 + aiVolume * 0.3});` : 'opacity:0;'"></div>
                
                <div class="call-avatar-ring">
                    <template x-if="callChar?.avatar">
                        <img :src="callChar.avatar" />
                    </template>
                    <template x-if="!callChar?.avatar">
                        <div class="placeholder" x-text="callChar?.name?.charAt(0) || '?'"></div>
                    </template>
                </div>
            </div>"""
content = content.replace(html_old, html_new)

# Add showTranscript to JS
content = content.replace("        inCall: false,", "        inCall: false,\n        showTranscript: false,")

# Update transcript visibility
transcript_old = """            <!-- Transcript overlay -->
            <div class="transcript-area" x-show="transcript" x-transition>
                <p class="transcript-text" x-text="transcript"></p>
            </div>"""

transcript_new = """            <!-- Transcript overlay -->
            <div class="transcript-area" x-show="showTranscript && transcript" x-transition>
                <p class="transcript-text" x-text="transcript"></p>
            </div>"""
content = content.replace(transcript_old, transcript_new)

with open("templates/call.html", "w") as f:
    f.write(content)

