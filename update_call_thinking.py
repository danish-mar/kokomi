import re

with open("templates/call.html", "r") as f:
    content = f.read()

# 1. Update Auras HTML
old_auras = """                <!-- Auras -->
                <div class="think-aura" :style="(callStatus === 'connecting' || callStatus.startsWith('Using tool')) ? 'opacity:1;' : 'opacity:0;'"></div>
                <div class="speak-aura" :style="(callStatus === 'active' && aiVolume > 0.05) ? `opacity:${0.4 + aiVolume * 0.6}; transform:scale(${1 + aiVolume * 0.3});` : 'opacity:0;'"></div>"""

new_auras = """                <!-- Auras -->
                <div class="think-aura" :style="(callStatus === 'connecting' || callStatus.startsWith('Using tool') || isThinking) ? 'opacity:1;' : 'opacity:0;'"></div>
                <div class="speak-aura" :style="(callStatus === 'active' && aiVolume > 0.05) ? `opacity:${0.4 + aiVolume * 0.6}; transform:scale(${1 + aiVolume * 0.3});` : 'opacity:0;'"></div>"""
content = content.replace(old_auras, new_auras)

# 2. Add isThinking to JS state
content = content.replace("        isSpeaking: false,", "        isSpeaking: false,\n        isThinking: false,")

# 3. Update websocket handling
old_ws = """                        } else if (msg.type === 'audio') {
                            if (this.callStatus !== 'active') this.callStatus = 'active';
                            this._playAudio(msg.data);
                            this.isSpeaking = true;
                            clearTimeout(this._speakTimeout);
                            this._speakTimeout = setTimeout(() => { this.isSpeaking = false; }, 1000);
                        } else if (msg.type === 'transcript') {
                            if (this.callStatus !== 'active') this.callStatus = 'active';
                            this.transcript = msg.text;
                        } else if (msg.type === 'turn_complete') {"""

new_ws = """                        } else if (msg.type === 'audio') {
                            if (this.callStatus !== 'active') this.callStatus = 'active';
                            this._playAudio(msg.data);
                            this.isSpeaking = true;
                            this.isThinking = false;
                            clearTimeout(this._speakTimeout);
                            this._speakTimeout = setTimeout(() => { this.isSpeaking = false; }, 1000);
                        } else if (msg.type === 'transcript') {
                            if (this.callStatus !== 'active') this.callStatus = 'active';
                            this.transcript = msg.text;
                            if (!this.isSpeaking) {
                                this.isThinking = true;
                                clearTimeout(this._thinkTimeout);
                                this._thinkTimeout = setTimeout(() => { this.isThinking = false; }, 3000);
                            }
                        } else if (msg.type === 'turn_complete') {"""
content = content.replace(old_ws, new_ws)

with open("templates/call.html", "w") as f:
    f.write(content)
