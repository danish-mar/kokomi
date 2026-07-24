function atlasApp() {
  return {
    // State
    workflows: {},
    lastWorkflowStates: {},
    workflowList: [],
    activeRunId: null,
    activeWorkflow: null,
    currentView: 'home',
    activeTab: 'overview',
    zoomLevel: 1.0,
    planningStep: 0,
    planningInterval: null,
    wsList: null,
    wsDetail: null,
    // Reconnect backoff (ms): grows on each failed WS attempt, resets on a
    // successful open. Without this, a WS that can't connect (e.g. proxy not
    // upgrading, or the server event loop momentarily busy) retried on a fixed
    // 3s timer forever, producing the console reconnect storm.
    _wsListBackoff: 1000,
    _wsDetailBackoff: 1000,
    _wsBackoffMax: 30000,
    selectedTask: null,
    debugMode: false,
    inputText: '',
    submitting: false,
    selectedModel: 'gpt-4o',
    sidebarCollapsed: false,
    models: [],
    workflowFiles: [],
    filesLoading: false,
    fileViewMode: 'grid',
    currentDirPath: '',
    templates: {},
    templatesLoading: false,
    showTemplateForm: false,
    tplIsEdit: false,
    tplForm: { id: '', name: '', purpose: '', system_prompt_template: '', allowed_tools: [], timeout: 300, retry_limit: 3 },
    tplGenDesc: '',
    tplGenerating: false,
    pollingTimer: null,
    detailPollingTimer: null,
    availableTools: [],
    isFullscreen: false,
    showSystemEvents: true,
    isStreamingChat: false,
    prefs: {},
    
    // Scheduling & Sidebar filtering
    schedules: [],
    showScheduleModal: false,
    schedRepeatMode: 're_execute',
    schedInterval: 'daily',
    schedCron: '',
    alarmHour: '08',
    alarmMinute: '00',
    alarmAmpm: 'AM',
    schedSaving: false,
    schedError: '',
    sidebarSearch: '',
    historyCollapsed: false,
    showRestartDropdown: false,
    
    // Drag/Drop Graph Nodes
    nodeOffsets: {},
    draggingNode: null,
    dragStartX: 0,
    dragStartY: 0,
    dragInitialOffsetX: 0,
    dragInitialOffsetY: 0,
    currentTime: Date.now(),
    showTourPrompt: false,

    suggestions: [
      { title: 'Research & PDF', desc: 'Research 8086 Microcomputer, compile to PDF, email me', text: 'Research 8086 Microcomputer architecture, compile findings to a detailed PDF, and email me the report', icon: 'fa-microchip' },
      { title: 'Style Guide', desc: 'Analyze Python best practices and write a style guide', text: 'Analyze Python best practices and community standards, then write a comprehensive style guide document', icon: 'fa-book-open' },
      { title: 'Quantum Summary', desc: 'Research quantum computing breakthroughs 2025', text: 'Research quantum computing breakthroughs in 2025 and create a detailed summary report', icon: 'fa-atom' },
      { title: 'FastAPI Cheatsheet', desc: 'Crawl FastAPI docs and create a cheatsheet PDF', text: 'Crawl the FastAPI documentation, extract key concepts, and create a concise cheatsheet PDF', icon: 'fa-bolt' },
    ],

    async init() {
      await this.loadPrefs();
      await this.loadModels();
      await this.loadWorkflows();
      await this.loadSchedules();

      // Deep linking parser for direct deep workflow URLs on load
      const params = new URLSearchParams(window.location.search);
      const wfId = params.get('workflow');
      if (wfId && this.workflows[wfId]) {
        this.selectWorkflow(wfId);
      }

      this.startPolling();
      // Load templates & dynamic available tools in background
      this.loadTemplates();
      this.loadAvailableTools();
      // Live stopwatch clock
      setInterval(() => { this.currentTime = Date.now(); }, 1000);

      // Deep link synchronization for back/forward navigation
      window.addEventListener('popstate', () => {
        const p = new URLSearchParams(window.location.search);
        const wId = p.get('workflow');
        if (wId && this.workflows[wId]) {
          this.selectWorkflow(wId);
        } else {
          this.clearActive();
        }
      });
      
      // Request browser notification permission
      if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission();
      }

      if (params.get('tour') === 'true') {
        // Clean URL to prevent repeated tours on refresh
        const cleanUrl = window.location.protocol + "//" + window.location.host + window.location.pathname + (wfId ? `?workflow=${wfId}` : '');
        window.history.replaceState({ path: cleanUrl }, '', cleanUrl);
        
        setTimeout(() => {
          this.startTour(true);
        }, 800);
      } else {
        // Trigger Guided Tour Prompt if not completed
        setTimeout(() => {
          if (this.prefs && this.prefs.tour_completed === false) {
            localStorage.removeItem('atlas_tour_completed');
          }
          if ((!this.prefs || this.prefs.tour_completed !== true) && localStorage.getItem('atlas_tour_completed') !== 'true') {
            this.showTourPrompt = true;
          }
        }, 1200);
      }
    },

    async loadModels() {
      try {
        this.models = await this.api('GET', '/api/models');
      } catch(e) { console.warn('Failed to load models', e); }
    },

    // ── API helpers ──
    async api(method, url, body) {
      const opts = { method, headers: { 'Content-Type': 'application/json' } };
      if (body) opts.body = JSON.stringify(body);
      const r = await fetch(url, opts);
      if (!r.ok) throw new Error(`API ${r.status}: ${url}`);
      return r.json();
    },

    async loadPrefs() {
      try {
        const p = await this.api('GET', '/api/prefs');
        this.prefs = p;
      } catch(e) { console.warn('Prefs load failed', e); }
    },
    async startTour(force = false) {
      this.showTourPrompt = false;
      localStorage.setItem('atlas_tour_completed', 'true');
      
      if (!window.driver || !window.driver.js) {
        console.error('Driver.js is not loaded.');
        return;
      }

      // Save original state so we can restore it when the tour ends
      const originalRunId = this.activeRunId;
      const originalWorkflow = this.activeWorkflow;
      const originalTab = this.activeTab;
      const originalWorkflows = { ...this.workflows };

      const mockRunId = 'mock-tour-run';
      const mockWorkflow = {
        run_id: mockRunId,
        run_title: 'Research & PDF: Microcomputer 8086',
        user_request: 'Research 8086 Microcomputer architecture, compile findings to a detailed PDF, and email me the report',
        status: 'completed',
        created_at: new Date(Date.now() - 3600000).toISOString(),
        tasks: [
          {
            task_id: 'task-1',
            title: 'Specifications Crawler',
            description: 'Fetch architecture sheets, register mappings, and timing diagram specs for 8086 microprocessors from official datasheets.',
            worker_type: 'researcher',
            status: 'completed',
            depends_on: [],
            attempt_count: 0,
            timestamps: { start: '10:00:15 AM', end: '10:01:05 AM' },
            step_log: [
              { type: 'info', message: 'Initializing tavily-web-researcher with 8086 specs...' },
              { type: 'info', message: 'Extracting data on 16-bit registers (AX, BX, CX, DX) and segments (CS, DS, SS, ES)...' },
              { type: 'info', message: 'Caching register specifications successfully.' }
            ],
            output: { specs_found: ['16-bit register files', 'Segment-Offset addressing model', '40-pin DIP layout'] }
          },
          {
            task_id: 'task-2',
            title: 'Python Compiler Script',
            description: 'Generate python compiler model simulation showing addressing offset computations and timing simulations.',
            worker_type: 'coder',
            status: 'completed',
            depends_on: ['task-1'],
            attempt_count: 0,
            timestamps: { start: '10:01:08 AM', end: '10:01:58 AM' },
            step_log: [
              { type: 'info', message: 'Starting python execution sandbox environment...' },
              { type: 'info', message: 'Running offset computation validation suite...' },
              { type: 'info', message: 'Generated structural diagram simulation script.' }
            ],
            output: { exit_code: 0, stdout: 'Offset computations match. Segmentation fault checks passed!' }
          },
          {
            task_id: 'task-3',
            title: 'Document Compilation',
            description: 'Synthesize research text findings, register tables, and addressing specs into a sleek Outfit PDF guide.',
            worker_type: 'writer',
            status: 'completed',
            depends_on: ['task-2'],
            attempt_count: 0,
            timestamps: { start: '10:02:00 AM', end: '10:02:45 AM' },
            step_log: [
              { type: 'info', message: 'Compiling structural markdown into styled document layout...' },
              { type: 'info', message: 'Rendering custom tables for CS/DS segments...' },
              { type: 'info', message: 'Wrote final output to data/outputs/microprocessor_guide_8086.pdf.' }
            ],
            output: { pdf_path: 'data/outputs/microprocessor_guide_8086.pdf', pages: 8 }
          },
          {
            task_id: 'task-4',
            title: 'Email Notification Dispatcher',
            description: 'Send the compiled microprocessor guide PDF directly to user mailbox using standard mail protocols.',
            worker_type: 'mailer',
            status: 'completed',
            depends_on: ['task-3'],
            attempt_count: 0,
            timestamps: { start: '10:02:48 AM', end: '10:03:10 AM' },
            step_log: [
              { type: 'info', message: 'Connecting to SMTP server...' },
              { type: 'info', message: 'Attaching data/outputs/microprocessor_guide_8086.pdf...' },
              { type: 'info', message: 'Dispatching mail to user inbox...' },
              { type: 'info', message: 'Email sent successfully!' }
            ],
            output: { status: 'dispatched', tracking_id: 'mail-8086-guide-9912' }
          }
        ],
        collaborative_chat: [
          { role: 'user', sender: 'User', message: 'Can you summarize the register timing diagrams of the 8086 microcomputer?', timestamp: '10:00:05 AM' },
          { role: 'assistant', sender: 'Supervisor', message: 'Absolutely! I am orchestrating the Researchers to query timing diagrams and register layouts. The coder will build an addressing simulator, and the writer will compile a structured PDF guidebook.', timestamp: '10:00:12 AM' },
          { role: 'system', sender: 'System', message: 'Specs Crawler (Researcher) has started operation.', timestamp: '10:00:15 AM' },
          { role: 'system', sender: 'System', message: 'Document Compilation (Writer) successfully compiled 8-page Microcomputer Guide PDF.', timestamp: '10:02:45 AM' },
          { role: 'assistant', sender: 'Supervisor', message: 'The timing guide and microprocessor guide have been successfully compiled and sent to your email inbox! You can also download the PDF directly in the Files tab.', timestamp: '10:03:15 AM' }
        ]
      };

      const tourSteps = [
        {
          popover: {
            title: 'Welcome to Atlas Terminal!',
            description: 'Atlas is a multi-agent orchestration console designed to decompose complex objectives into nested parallel operations. We have loaded a live mock completed pipeline to show you exactly how Atlas manages Specialized Agents (Researchers, Coders, Writers) in real time!',
            side: 'bottom',
            align: 'center'
          },
          onHighlightStarted: () => {
            this.workflows[mockRunId] = mockWorkflow;
            this.activeRunId = mockRunId;
            this.activeWorkflow = mockWorkflow;
            this.activeTab = 'overview';
          }
        },
        {
          element: '#atlas-sidebar',
          popover: {
            title: 'Agent Control Sidebar',
            description: 'Use the sidebar to explore past run histories, trigger new outcomes, check active recurring execution schedules, or configure reusable team templates.',
            side: 'right',
            align: 'start'
          },
          onHighlightStarted: () => {
            this.activeTab = 'overview';
          }
        },
        {
          element: '#tour-topbar-tabs',
          popover: {
            title: 'Interactive Tab Swapper',
            description: 'These tabs allow you to swap views dynamically to inspect the active pipeline from different dimensions: List, Graph, Files, and Chat. Let\'s explore them together!',
            side: 'bottom',
            align: 'center'
          },
          onHighlightStarted: () => {
            this.activeTab = 'overview';
          }
        },
        {
          element: '#atlas-content',
          popover: {
            title: 'Tab 1: Granular Task List',
            description: 'The List tab displays active worker cards executing parallel tasks, their live duration clocks, status badges, and overall process execution progress. Click any card to see real-time stdout streams!',
            side: 'top',
            align: 'center'
          },
          onHighlightStarted: () => {
            this.activeTab = 'overview';
          }
        },
        {
          element: '#atlas-content',
          popover: {
            title: 'Tab 2: Visual Execution Graph',
            description: 'The Graph tab renders a fully dynamic, draggable topological DAG execution tree. Instantly trace sequential task dependencies, parallel execution routes, and parent-child workflows.',
            side: 'top',
            align: 'center'
          },
          onHighlightStarted: () => {
            this.activeTab = 'graph';
          }
        },
        {
          element: '#atlas-content',
          popover: {
            title: 'Tab 3: Delivered Project Files',
            description: 'In the Files tab, browse and download assets generated within the sandboxed environment (like microprocessor_guide_8086.pdf, python codes, or timing JSON sheets). You can also download a full zip bundle!',
            side: 'top',
            align: 'center'
          },
          onHighlightStarted: () => {
            this.activeTab = 'files';
            this.loadFiles();
          }
        },
        {
          element: '#atlas-content',
          popover: {
            title: 'Tab 4: Supervisor Collaborative Chat',
            description: 'Finally, the Chat tab lets you communicate directly with the Supervisor. Refine requirements, review system notifications, and interact with the AI coordinator while worker nodes operate.',
            side: 'top',
            align: 'center'
          },
          onHighlightStarted: () => {
            this.activeTab = 'chat';
          }
        },
        {
          element: '#tour-schedule-btn',
          popover: {
            title: 'Schedule Recurring Execution',
            description: 'Automate your workflows! Use the Schedule button to set recurring cron triggers (hourly, daily, weekly, or custom cron patterns) so the supervisor runs the multi-agent task automatically in the background.',
            side: 'bottom',
            align: 'center'
          },
          onHighlightStarted: () => {
            this.activeTab = 'overview';
          }
        },
        {
          popover: {
            title: "That's All, Folks!",
            description: "You're now fully equipped to orchestrate specialized AI agent pipelines with Atlas Terminal. Click Done to return to your workspace and start building!",
            side: 'bottom',
            align: 'center',
            onNextClick: () => {
              window.location.href = '/';
            }
          },
          onHighlightStarted: () => {
            this.activeTab = 'overview';
          }
        }
      ];

      const driverObj = window.driver.js.driver({
        showProgress: true,
        allowClose: true,
        popoverClass: 'driverjs-theme',
        steps: tourSteps,
        onDestroyed: () => {
          // Restore original user state
          this.activeRunId = originalRunId;
          this.activeWorkflow = originalWorkflow;
          this.activeTab = originalTab;
          this.workflows = originalWorkflows;
          this.$nextTick(() => {
            if (originalRunId) {
              this.loadFiles();
            }
          });
        }
      });

      driverObj.drive();
    },

    dismissTour() {
      this.showTourPrompt = false;
      localStorage.setItem('atlas_tour_completed', 'true');
    },
    resolvedAtlasModel() {
      if (!this.prefs) return 'Loading...';
      const provider = this.prefs.atlas_llm_provider || 'groq';
      if (provider === 'custom') return this.prefs.atlas_custom_model || 'Custom Model';
      if (provider === 'nvidia') return this.prefs.atlas_nvidia_model || 'NVIDIA NIM';
      return this.prefs.atlas_model_name || 'qwen-2.5-32b';
    },
    getAtlasModelName() {
      if (!this.prefs) return 'Loading...';
      const provider = this.prefs.atlas_llm_provider || 'groq';
      let rawName = 'qwen-2.5-32b';
      if (provider === 'custom') rawName = this.prefs.atlas_custom_model || 'Custom Model';
      else if (provider === 'nvidia') rawName = this.prefs.atlas_nvidia_model || 'NVIDIA NIM';
      else rawName = this.prefs.atlas_model_name || 'qwen-2.5-32b';
      
      if (rawName.includes('/')) {
        return rawName.split('/').slice(1).join('/');
      }
      return rawName;
    },
    getAtlasModelIcon() {
      if (!this.prefs) return 'fa-solid fa-circle-notch fa-spin';
      const provider = this.prefs.atlas_llm_provider || 'groq';
      let rawName = '';
      if (provider === 'custom') rawName = this.prefs.atlas_custom_model || 'custom';
      else if (provider === 'nvidia') rawName = this.prefs.atlas_nvidia_model || 'nvidia';
      else rawName = this.prefs.atlas_model_name || 'groq';
      
      rawName = rawName.toLowerCase();
      
      if (rawName.includes('openai') || rawName.includes('gpt-')) {
        return 'fa-brands fa-openai';
      }
      if (rawName.includes('google') || rawName.includes('gemini')) {
        return 'fa-brands fa-google';
      }
      if (rawName.includes('meta') || rawName.includes('llama')) {
        return 'fa-solid fa-circle-dot';
      }
      if (rawName.includes('deepseek')) {
        return 'fa-solid fa-circle-dot';
      }
      
      if (provider === 'groq') return 'fa-solid fa-bolt';
      if (provider === 'google') return 'fa-brands fa-google';
      if (provider === 'custom') return 'fa-solid fa-plug';
      return 'fa-solid fa-brain';
    },

    // ── Scheduling & Sidebar decluttering ──
    async loadSchedules() {
      try {
        this.schedules = await this.api('GET', '/api/workflows/schedules');
      } catch(e) { console.warn('Failed to load schedules', e); }
    },
    async saveSchedule() {
      if (!this.activeRunId) return;
      this.schedSaving = true;
      this.schedError = '';
      try {
        let cronVal = '';
        if (this.schedInterval === 'custom') {
          let hrVal = parseInt(this.alarmHour, 10);
          const minVal = parseInt(this.alarmMinute, 10);
          if (this.alarmAmpm === 'PM' && hrVal !== 12) {
            hrVal += 12;
          } else if (this.alarmAmpm === 'AM' && hrVal === 12) {
            hrVal = 0;
          }
          cronVal = `${minVal} ${hrVal} * * *`;
        }
        const payload = {
          repeat_mode: this.schedRepeatMode,
          interval: this.schedInterval,
          cron: cronVal
        };
        // Check if schedule already exists for this workflow
        const existing = this.schedules.find(s => s.source_run_id === this.activeRunId);
        if (existing) {
          await this.api('PUT', `/api/workflows/schedules/${existing.id}`, payload);
        } else {
          await this.api('POST', `/api/workflows/${this.activeRunId}/schedule`, payload);
        }
        await this.loadSchedules();
        this.showScheduleModal = false;
      } catch(e) {
        this.schedError = 'Failed to save schedule settings.';
      } finally {
        this.schedSaving = false;
      }
    },
    async toggleScheduleActive(sched) {
      try {
        await this.api('PUT', `/api/workflows/schedules/${sched.id}`, { active: !sched.active });
        await this.loadSchedules();
      } catch(e) { console.warn('Failed to toggle schedule active', e); }
    },
    async deleteSchedule(schedId) {
      try {
        await this.api('DELETE', `/api/workflows/schedules/${schedId}`);
        await this.loadSchedules();
      } catch(e) { console.warn('Failed to delete schedule', e); }
    },
    openScheduleModal() {
      if (!this.activeRunId) return;
      const existing = this.schedules.find(s => s.source_run_id === this.activeRunId);
      if (existing) {
        this.schedRepeatMode = existing.repeat_mode;
        this.schedInterval = existing.interval;
        this.schedCron = existing.cron || '';
        
        // Parse custom alarm settings from cron
        if (existing.interval === 'custom' && existing.cron) {
          const parts = existing.cron.split(' ');
          if (parts.length >= 2) {
            const minVal = parseInt(parts[0], 10);
            let hrVal = parseInt(parts[1], 10);
            
            this.alarmAmpm = hrVal >= 12 ? 'PM' : 'AM';
            let hr12 = hrVal % 12;
            if (hr12 === 0) hr12 = 12;
            
            this.alarmHour = String(hr12).padStart(2, '0');
            this.alarmMinute = String(minVal).padStart(2, '0');
          } else {
            this.alarmHour = '08';
            this.alarmMinute = '00';
            this.alarmAmpm = 'AM';
          }
        } else {
          this.alarmHour = '08';
          this.alarmMinute = '00';
          this.alarmAmpm = 'AM';
        }
      } else {
        this.schedRepeatMode = 're_execute';
        this.schedInterval = 'daily';
        this.schedCron = '';
        this.alarmHour = '08';
        this.alarmMinute = '00';
        this.alarmAmpm = 'AM';
      }
      this.schedError = '';
      this.showScheduleModal = true;
    },
    getScheduleForActive() {
      if (!this.activeRunId) return null;
      return this.schedules.find(s => s.source_run_id === this.activeRunId) || null;
    },
    formatTime(ts) {
      if (!ts) return 'Never';
      const d = new Date(ts * 1000);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) + ' ' + d.toLocaleDateString();
    },
    // Sidebar declutter helpers
    filteredHistory() {
      const q = (this.sidebarSearch || '').trim().toLowerCase();
      const list = this.workflowList.filter(w => w.status === 'completed' || w.status === 'failed');
      if (!q) return list;
      return list.filter(w => 
        (w.run_title || '').toLowerCase().includes(q) || 
        (w.user_request || '').toLowerCase().includes(q) || 
        w.run_id.toLowerCase().includes(q)
      );
    },
    filteredActive() {
      const q = (this.sidebarSearch || '').trim().toLowerCase();
      const list = this.workflowList.filter(w => w.status === 'running' || w.status === 'pending');
      if (!q) return list;
      return list.filter(w => 
        (w.run_title || '').toLowerCase().includes(q) || 
        (w.user_request || '').toLowerCase().includes(q) || 
        w.run_id.toLowerCase().includes(q)
      );
    },

    // ── Workflows ──
    async loadWorkflows() {
      try {
        const data = await this.api('GET', '/api/workflows');
        this.workflows = data;
        this.workflowList = Object.values(data).sort((a, b) =>
          new Date(b.created_at || 0) - new Date(a.created_at || 0)
        );
        if (this.activeRunId && data[this.activeRunId]) {
          this.activeWorkflow = data[this.activeRunId];
          this.$nextTick(() => this.scrollConsoleToBottom());
        }
      } catch(e) { console.warn('Workflow load failed', e); }
    },

    async loadWorkflowDetail() {
      if (!this.activeRunId) return;
      if (this.activeRunId === 'mock-tour-run') return;
      try {
        const wf = await this.api('GET', `/api/workflows/${this.activeRunId}`);
        if (this.isStreamingChat) {
          const chatBackup = this.activeWorkflow ? this.activeWorkflow.collaborative_chat : null;
          this.activeWorkflow = wf;
          if (chatBackup && this.activeWorkflow) {
            this.activeWorkflow.collaborative_chat = chatBackup;
          }
        } else {
          this.activeWorkflow = wf;
        }
        // update in list
        const idx = this.workflowList.findIndex(w => w.run_id === this.activeRunId);
        if (idx >= 0) this.workflowList[idx] = wf;
        this.$nextTick(() => {
          this.scrollConsoleToBottom();
          this.scrollChatToBottom(false);
        });
      } catch(e) {}
    },

    selectWorkflow(runId, tab = null) {
      this.activeRunId = runId;
      this.activeWorkflow = this.workflows[runId] || null;
      this.currentView = 'home';
      if (tab) {
        this.activeTab = tab;
      } else {
        const hash = window.location.hash;
        if (hash === '#chat') this.activeTab = 'chat';
        else if (hash === '#graph') this.activeTab = 'graph';
        else if (hash === '#files') this.activeTab = 'files';
        else this.activeTab = 'overview';
      }
      this.debugMode = false;
      this.restartDetailPolling();

      const currentTab = this.activeTab;
      const hashName = currentTab === 'overview' ? '#list' : '#' + currentTab;
      history.replaceState(null, '', '/atlas?workflow=' + runId + hashName);
    },

    clearActive() {
      this.activeRunId = null;
      this.activeWorkflow = null;
      this.currentView = 'home';
      this.activeTab = 'overview';
      this.inputText = '';
    },

    async submitWorkflow() {
      if (!this.inputText.trim() || this.submitting) return;
      this.submitting = true;
      const msg = this.inputText.trim();
      this.inputText = '';
      this.$nextTick(() => {
        if (this.$refs.mainInput) {
          this.$refs.mainInput.style.height = '24px';
        }
      });

      // Start planning subtitle interval if starting a brand new workflow
      if (!this.activeRunId) {
        this.planningStep = 0;
        this.planningInterval = setInterval(() => {
          this.planningStep = (this.planningStep + 1) % 5;
        }, 1500);
      } else {
        // If collaborative chat, append user message instantly and insert a temporary typing indicator
        if (!this.activeWorkflow.collaborative_chat) {
          this.activeWorkflow.collaborative_chat = [];
        }
        
        // Append user message locally
        this.activeWorkflow.collaborative_chat.push({
          role: 'user',
          sender: 'User',
          message: msg,
          timestamp: new Date().toLocaleTimeString('en-US', { hour12: false, hour:'2-digit', minute:'2-digit', second:'2-digit' })
        });
        
        // Append supervisor placeholder with streaming typing indicator
        this.activeWorkflow.collaborative_chat.push({
          role: 'assistant',
          sender: 'Supervisor',
          message: '',
          streaming: true,
          timestamp: new Date().toLocaleTimeString('en-US', { hour12: false, hour:'2-digit', minute:'2-digit', second:'2-digit' })
        });
        
        this.$nextTick(() => {
          this.scrollChatToBottom(true);
        });
      }

      try {
        if (this.activeRunId) {
          // Send collaborative message to existing supervisor
          const resp = await this.api('POST', `/api/workflows/${this.activeRunId}/chat`, { message: msg });
          
          if (resp && resp.status === 'conversation' && resp.response) {
            this.isStreamingChat = true;
            const targetText = resp.response;
            let currentText = '';
            let charIdx = 0;
            
            // Find our local streaming placeholder
            const chat = this.activeWorkflow ? this.activeWorkflow.collaborative_chat : null;
            const placeholder = chat ? chat.find(m => m.streaming) : null;
            
            if (placeholder) {
              placeholder.streaming = false;
              
              const interval = setInterval(() => {
                if (charIdx < targetText.length) {
                  // Append chunks of 4 characters to print fluidly and fast
                  currentText += targetText.substring(charIdx, charIdx + 4);
                  charIdx += 4;
                  placeholder.message = currentText;
                  this.scrollChatToBottom(false);
                } else {
                  clearInterval(interval);
                  this.isStreamingChat = false;
                  this.loadWorkflowDetail();
                }
              }, 12);
            } else {
              await this.loadWorkflowDetail();
            }
          } else {
            await this.loadWorkflowDetail();
          }
          
          this.restartDetailPolling();
        } else {
          // Start a new workflow
          const resp = await this.api('POST', '/api/workflows', { message: msg });
          await this.loadWorkflows();
          if (resp.run_id) {
            history.pushState(null, '', '/atlas?workflow=' + resp.run_id + '#chat');
            this.selectWorkflow(resp.run_id, 'chat');
          }
        }
      } catch(e) {
        // Remove typing placeholder on error
        if (this.activeRunId && this.activeWorkflow && this.activeWorkflow.collaborative_chat) {
          this.activeWorkflow.collaborative_chat = this.activeWorkflow.collaborative_chat.filter(m => !m.streaming);
        }
        alert('Failed to send instruction: ' + e.message);
      } finally {
        this.submitting = false;
        if (this.planningInterval) {
          clearInterval(this.planningInterval);
          this.planningInterval = null;
        }
      }
    },

    fillAndSubmit(text) {
      this.inputText = text;
      this.currentView = 'home';
      this.$nextTick(() => this.submitWorkflow());
    },

    async restartTaskNode(taskId) {
      if (!this.activeRunId) return;
      try {
        const resp = await this.api('POST', `/api/workflows/${this.activeRunId}/restart-node`, { task_id: taskId });
        if (resp.ok) {
          this.selectedTask = null;
          await this.loadWorkflowDetail();
          await this.loadWorkflows();
          this.restartDetailPolling();
        }
      } catch(e) {
        alert('Failed to restart task node: ' + e.message);
      }
    },

    // ── Dry-run DAG: approve a draft plan and begin execution ────────────────
    async startWorkflow() {
      if (!this.activeRunId) return;
      try {
        await this.api('POST', `/api/workflows/${this.activeRunId}/start`, {});
        await this.loadWorkflowDetail();
        await this.loadWorkflows();
        this.restartDetailPolling();
      } catch(e) {
        alert('Failed to start workflow: ' + e.message);
      }
    },

    // Persist edits to the draft/paused plan (task add/remove/edit/checkpoint).
    async savePlan() {
      if (!this.activeWorkflow) return;
      const tasks = (this.activeWorkflow.tasks || []).map(t => ({
        task_id: t.task_id, title: t.title, description: t.description,
        worker_type: t.worker_type, depends_on: t.depends_on || [],
        allowed_tools: t.allowed_tools || [], checkpoint: !!t.checkpoint,
      }));
      try {
        const resp = await this.api('PUT', `/api/workflows/${this.activeRunId}/plan`, { tasks });
        if (resp && resp.tasks) this.activeWorkflow.tasks = resp.tasks;
        await this.loadWorkflowDetail();
      } catch(e) {
        alert('Failed to update plan: ' + e.message);
      }
    },

    // Flip the human-approval checkpoint flag on a node (draft/paused only).
    async toggleCheckpoint(taskId) {
      const t = (this.activeWorkflow.tasks || []).find(x => x.task_id === taskId);
      if (!t) return;
      t.checkpoint = !t.checkpoint;
      if (this.selectedTask && this.selectedTask.task_id === taskId) {
        this.selectedTask.checkpoint = t.checkpoint;
      }
      await this.savePlan();
    },

    async deleteTaskNode(taskId) {
      if (!confirm('Remove this task from the plan?')) return;
      this.activeWorkflow.tasks = (this.activeWorkflow.tasks || []).filter(x => x.task_id !== taskId);
      if (this.selectedTask && this.selectedTask.task_id === taskId) this.selectedTask = null;
      await this.savePlan();
    },

    addTaskNode() {
      const tasks = this.activeWorkflow.tasks = this.activeWorkflow.tasks || [];
      const n = tasks.length + 1;
      tasks.push({
        task_id: 't_new_' + Date.now().toString(36),
        title: 'New task ' + n,
        description: '',
        worker_type: 'researcher',
        depends_on: [],
        allowed_tools: ['web_search'],
        checkpoint: false,
        status: 'pending',
      });
      this.savePlan();
    },

    // Approve or reject the checkpoint the run is paused at.
    async resolveCheckpoint(approve) {
      const taskId = this.activeWorkflow && this.activeWorkflow.paused_at_task;
      if (!this.activeRunId || !taskId) return;
      try {
        await this.api('POST', `/api/workflows/${this.activeRunId}/checkpoint/${taskId}`, { approve });
        await this.loadWorkflowDetail();
        await this.loadWorkflows();
        this.restartDetailPolling();
      } catch(e) {
        alert('Failed to resolve checkpoint: ' + e.message);
      }
    },

    zoomIn() {
      this.zoomLevel = Math.min(this.zoomLevel + 0.1, 2.0);
    },
    zoomOut() {
      this.zoomLevel = Math.max(this.zoomLevel - 0.1, 0.5);
    },
    resetZoom() {
      this.zoomLevel = 1.0;
    },
    arrangeLayout() {
      this.nodeOffsets = {};
    },
    toggleFullscreen() {
      this.isFullscreen = !this.isFullscreen;
      // Trigger a browser resize event after layout changes to let layout components adapt
      setTimeout(() => {
        window.dispatchEvent(new Event('resize'));
      }, 100);
    },

    planningSubtitle() {
      const steps = [
        "Initializing Top-Level Workflow Supervisor...",
        "Analyzing project constraints & dependency requirements...",
        "Spawning custom Multi-Agent LangGraph layout...",
        "Structuring recursive research & compiler nodes...",
        "Saving workflow blueprint to disk..."
      ];
      return steps[this.planningStep || 0];
    },

    getTaskDisplayStatus(task) {
      if (!task) return 'pending';
      if (task.status === 'completed') return 'completed';
      if (task.status === 'failed') return 'failed';
      if (task.status === 'running') return 'working';
      
      if (task.depends_on && task.depends_on.length) {
        const tasks = this.activeWorkflow?.tasks || [];
        const unmet = task.depends_on.some(depId => {
          const depTask = tasks.find(tk => tk.task_id === depId);
          return !depTask || depTask.status !== 'completed';
        });
        if (unmet) return 'waiting';
      }
      return 'pending';
    },

    getFilename(path) {
      if (!path) return '';
      return path.split('/').pop().split('\\').pop();
    },
    truncateFilename(name, len = 16) {
      if (!name) return '';
      if (name.length <= len) return name;
      const ext = name.split('.').pop();
      const base = name.substring(0, name.lastIndexOf('.'));
      if (base.length > len - 4) {
        return base.substring(0, len - 5 - ext.length) + '...' + ext;
      }
      return name;
    },
    getArtifactIcon(path) {
      if (!path) return 'fa-file';
      const ext = path.split('.').pop().toLowerCase();
      if (ext === 'pdf') return 'fa-file-pdf text-red-500';
      if (ext === 'pptx' || ext === 'ppt') return 'fa-file-powerpoint text-orange-500';
      if (ext === 'xlsx' || ext === 'xls' || ext === 'csv') return 'fa-file-excel text-green-500';
      if (ext === 'docx' || ext === 'doc') return 'fa-file-word text-blue-500';
      if (ext === 'md') return 'fa-file-code text-indigo-400';
      return 'fa-file';
    },
    getArtifactDownloadUrl(filepath) {
      if (!this.activeRunId) return '#';
      return `/api/workflows/${this.activeRunId}/download?filepath=${encodeURIComponent(filepath)}`;
    },

    async confirmDeleteWorkflow(runId) {
      const wf = this.workflows[runId];
      const title = wf?.run_title || runId;
      if (!confirm(`Delete workflow "${title}"? This cannot be undone.`)) return;
      try {
        await this.api('DELETE', `/api/workflows/${runId}`);
        if (this.activeRunId === runId) this.clearActive();
        await this.loadWorkflows();
      } catch(e) { alert('Delete failed: ' + e.message); }
    },

    getWorkflowDurationText(wf, tick) {
      if (!wf) return '';
      
      const startStr = wf.started_at || wf.created_at;
      if (!startStr) return '';
      
      const start = new Date(startStr).getTime();
      let end = tick || Date.now();
      
      if (wf.status === 'completed' || wf.status === 'failed') {
        if (wf.completed_at) {
          end = new Date(wf.completed_at).getTime();
        }
      }
      
      const diffMs = Math.max(0, end - start);
      const totalSec = Math.floor(diffMs / 1000);
      
      const mins = Math.floor(totalSec / 60);
      const secs = totalSec % 60;
      
      if (mins > 0) {
        return `${mins}m ${secs}s`;
      }
      return `${secs}s`;
    },

    playNotificationSound(isSuccess = true) {
      try {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (!AudioContext) return;
        const ctx = new AudioContext();
        const now = ctx.currentTime;
        
        if (isSuccess) {
          // Success chime: gentle high notes (E6 -> G6 -> C7)
          const notes = [1318.51, 1567.98, 2093.00];
          notes.forEach((freq, idx) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.setValueAtTime(freq, now + idx * 0.12);
            gain.gain.setValueAtTime(0.08, now + idx * 0.12);
            gain.gain.exponentialRampToValueAtTime(0.0001, now + idx * 0.12 + 0.5);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start(now + idx * 0.12);
            osc.stop(now + idx * 0.12 + 0.55);
          });
        } else {
          // Failure chime: flat gentle warning notes (A4 -> Ab4)
          const notes = [440.00, 415.30];
          notes.forEach((freq, idx) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'triangle';
            osc.frequency.setValueAtTime(freq, now + idx * 0.18);
            gain.gain.setValueAtTime(0.12, now + idx * 0.18);
            gain.gain.exponentialRampToValueAtTime(0.0001, now + idx * 0.18 + 0.7);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start(now + idx * 0.18);
            osc.stop(now + idx * 0.18 + 0.75);
          });
        }
      } catch(e) {
        console.warn('Failed to play audio chime:', e);
      }
    },

    triggerBrowserNotification(w) {
      this.playNotificationSound(w.status === 'completed');
      
      if ('Notification' in window) {
        if (Notification.permission === 'granted') {
          const title = w.status === 'completed' ? 'Workflow Completed ✓' : 'Workflow Failed ✗';
          const body = `"${w.run_title}" is now ${w.status}.`;
          try {
            new Notification(title, {
              body: body,
              tag: w.run_id,
              requireInteraction: false
            });
          } catch(err) {
            console.error('Notification creation failed:', err);
          }
        }
      }
    },

    // ── WebSockets / Real-time ──
    startPolling() {
      const loc = window.location;
      const wsProto = loc.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${wsProto}//${loc.host}/api/ws/workflows`;
      
      if (this.wsList) {
        try { this.wsList.close(); } catch(e) {}
      }
      
      this.wsList = new WebSocket(wsUrl);
      this.wsList.onopen = () => { this._wsListBackoff = 1000; };
      this.wsList.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'workflows_list') {
          this.workflowList = data.workflows;
          data.workflows.forEach(w => {
            const oldStatus = this.lastWorkflowStates[w.run_id] || w.status;
            
            if (!this.workflows[w.run_id]) {
              this.workflows[w.run_id] = w;
            } else {
              this.workflows[w.run_id].status = w.status;
              this.workflows[w.run_id].run_title = w.run_title;
            }
            
            if (oldStatus === 'running' && (w.status === 'completed' || w.status === 'failed')) {
              this.triggerBrowserNotification(w);
            }
            this.lastWorkflowStates[w.run_id] = w.status;
          });
        }
      };
      
      this.wsList.onclose = () => {
        const delay = this._wsListBackoff;
        this._wsListBackoff = Math.min(this._wsListBackoff * 2, this._wsBackoffMax);
        setTimeout(() => this.startPolling(), delay);
      };
    },

    restartDetailPolling() {
      if (!this.activeRunId || this.activeRunId === 'mock-tour-run') {
        if (this.wsDetail) {
          try { this.wsDetail.close(); } catch(e) {}
          this.wsDetail = null;
        }
        return;
      }
      
      const loc = window.location;
      const wsProto = loc.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${wsProto}//${loc.host}/api/ws/workflows/${this.activeRunId}`;
      
      if (this.wsDetail) {
        try { this.wsDetail.close(); } catch(e) {}
      }
      
      this.wsDetail = new WebSocket(wsUrl);
      this.wsDetail.onopen = () => { this._wsDetailBackoff = 1000; };
      this.wsDetail.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'workflow_detail' && data.run_id === this.activeRunId) {
          const oldStatus = this.lastWorkflowStates[this.activeRunId] || data.workflow.status;
          
          if (this.isStreamingChat) {
            const chatBackup = this.activeWorkflow ? this.activeWorkflow.collaborative_chat : null;
            this.activeWorkflow = data.workflow;
            if (chatBackup && this.activeWorkflow) {
              this.activeWorkflow.collaborative_chat = chatBackup;
            }
          } else {
            this.activeWorkflow = data.workflow;
          }
          this.workflows[this.activeRunId] = data.workflow;
          
          if (oldStatus === 'running' && (data.workflow.status === 'completed' || data.workflow.status === 'failed')) {
            this.triggerBrowserNotification(data.workflow);
          }
          this.lastWorkflowStates[this.activeRunId] = data.workflow.status;
          
          this.$nextTick(() => {
            if (this.$refs.consoleEl) {
              this.$refs.consoleEl.scrollTop = this.$refs.consoleEl.scrollHeight;
            }
            if (this.$refs.debugConsole) {
              this.$refs.debugConsole.scrollTop = this.$refs.debugConsole.scrollHeight;
            }
          });
        }
      };
      
      this.wsDetail.onclose = () => {
        if (this.activeRunId) {
          const delay = this._wsDetailBackoff;
          this._wsDetailBackoff = Math.min(this._wsDetailBackoff * 2, this._wsBackoffMax);
          setTimeout(() => this.restartDetailPolling(), delay);
        }
      };
    },

    startPan(e) {
      if (e.target.closest('.graph-node') || e.target.closest('.zoom-controls') || e.target.closest('.node-artifact-pill')) return;
      
      this.isPanning = true;
      const container = e.currentTarget;
      this.startX = e.pageX - container.offsetLeft;
      this.startY = e.pageY - container.offsetTop;
      this.scrollLeft = container.scrollLeft;
      this.scrollTop = container.scrollTop;
      
      container.style.cursor = 'grabbing';
      container.style.userSelect = 'none';
      
      const onMouseMove = (moveEvt) => {
        if (!this.isPanning) return;
        const x = moveEvt.pageX - container.offsetLeft;
        const y = moveEvt.pageY - container.offsetTop;
        const walkX = (x - this.startX) * 1.3;
        const walkY = (y - this.startY) * 1.3;
        container.scrollLeft = this.scrollLeft - walkX;
        container.scrollTop = this.scrollTop - walkY;
      };
      
      const onMouseUp = () => {
        this.isPanning = false;
        container.style.cursor = 'grab';
        container.style.userSelect = 'auto';
        window.removeEventListener('mousemove', onMouseMove);
        window.removeEventListener('mouseup', onMouseUp);
      };
      
      window.addEventListener('mousemove', onMouseMove);
      window.addEventListener('mouseup', onMouseUp);
    },

    // ── Files ──
    async loadFiles() {
      if (!this.activeRunId) return;
      if (this.activeRunId === 'mock-tour-run') {
        this.filesLoading = true;
        this.workflowFiles = [
          { name: 'microprocessor_guide_8086.pdf', is_dir: false, size: 845210, path: 'microprocessor_guide_8086.pdf' },
          { name: 'timing_simulations.json', is_dir: false, size: 12450, path: 'timing_simulations.json' },
          { name: 'addressing_offset_tests.py', is_dir: false, size: 4120, path: 'addressing_offset_tests.py' }
        ];
        this.filesLoading = false;
        return;
      }
      this.filesLoading = true;
      try {
        const pathParam = encodeURIComponent(this.currentDirPath);
        this.workflowFiles = await this.api('GET', `/api/workflows/${this.activeRunId}/files?path=${pathParam}`);
      } catch(e) { 
        this.workflowFiles = []; 
      }
      this.filesLoading = false;
    },

    downloadZip() {
      if (!this.activeRunId) return;
      window.open(`/api/workflows/${this.activeRunId}/download_zip`, '_blank');
    },

    async stopWorkflow() {
      if (!this.activeRunId) return;
      if (!confirm("Are you sure you want to stop this workflow run?")) return;
      try {
        await this.api('POST', `/api/workflows/${this.activeRunId}/stop`);
        // Refresh details
        await this.loadWorkflowDetail();
      } catch (e) {
        alert("Failed to stop workflow: " + e.message);
      }
    },

    async restartWorkflow() {
      if (!this.activeRunId) return;
      if (!confirm("Are you sure you want to restart failed/incomplete tasks in this workflow?")) return;
      try {
        await this.api('POST', `/api/workflows/${this.activeRunId}/restart`);
        // Refresh details
        await this.loadWorkflowDetail();
      } catch (e) {
        alert("Failed to restart workflow: " + e.message);
      }
    },
    async recreateWorkflow() {
      if (!this.activeRunId) return;
      if (!confirm("Are you sure you want to recreate the workflow from the original prompt? This will generate a fresh new plan.")) return;
      try {
        const res = await this.api('POST', `/api/workflows/${this.activeRunId}/recreate`);
        if (res.run_id) {
          // Switch to new run
          this.activeRunId = res.run_id;
          await this.loadWorkflows();
          await this.loadWorkflowDetail();
        }
      } catch (e) {
        alert("Failed to recreate workflow: " + e.message);
      }
    },

    async uploadFile(event) {
      const file = event.target.files?.[0];
      if (!file || !this.activeRunId) return;
      const fd = new FormData();
      fd.append('file', file);
      try {
        const pathParam = encodeURIComponent(this.currentDirPath);
        const r = await fetch(`/api/workflows/${this.activeRunId}/upload?path=${pathParam}`, { method: 'POST', body: fd });
        if (r.ok) await this.loadFiles();
      } catch(e) { 
        alert('Upload failed'); 
      }
      event.target.value = '';
    },

    async makeFolder() {
      const name = prompt("Enter new folder name:");
      if (!name) return;
      try {
        const res = await this.api('POST', `/api/workflows/${this.activeRunId}/mkdir`, {
          path: this.currentDirPath,
          name: name
        });
        if (res.status === "success") {
          await this.loadFiles();
        }
      } catch(e) {
        alert("Failed to create folder: " + e.message);
      }
    },

    async makeFile() {
      const name = prompt("Enter new filename:");
      if (!name) return;
      try {
        const res = await this.api('POST', `/api/workflows/${this.activeRunId}/touch`, {
          path: this.currentDirPath,
          name: name
        });
        if (res.status === "success") {
          await this.loadFiles();
        }
      } catch(e) {
        alert("Failed to create file: " + e.message);
      }
    },

    navigateToFolder(folderName) {
      if (this.currentDirPath === "") {
        this.currentDirPath = folderName;
      } else {
        this.currentDirPath = this.currentDirPath + "/" + folderName;
      }
      this.loadFiles();
    },

    navigateToPath(fullPath) {
      this.currentDirPath = fullPath;
      this.loadFiles();
    },

    getBreadcrumbs() {
      if (this.currentDirPath === "") return [{ name: "Home", path: "" }];
      const parts = this.currentDirPath.split("/");
      const crumbs = [{ name: "Home", path: "" }];
      let current = "";
      parts.forEach(p => {
        if (p) {
          current = current ? current + "/" + p : p;
          crumbs.push({ name: p, path: current });
        }
      });
      return crumbs;
    },

    getFileUrl(filename) {
      const filepath = this.currentDirPath ? this.currentDirPath + "/" + filename : filename;
      return `/api/workflows/${this.activeRunId}/download?filepath=${encodeURIComponent(filepath)}`;
    },

    handleAttach(event) {
      // For now just show file name in input
      const f = event.target.files?.[0];
      if (f) this.inputText = (this.inputText ? this.inputText + ' ' : '') + `[Attached: ${f.name}]`;
      event.target.value = '';
    },

    getParsedChatMessages(notifications) {
      if (!notifications) return [];
      const messages = [];
      notifications.forEach(n => {
        if (n.startsWith("💬 User: ")) {
          messages.push({
            role: "user",
            sender: "User",
            message: n.substring("💬 User: ".length)
          });
        } else if (n.startsWith("🤖 Supervisor: ")) {
          messages.push({
            role: "assistant",
            sender: "Supervisor",
            message: n.substring("🤖 Supervisor: ".length)
          });
        }
      });
      return messages;
    },

    renderMarkdown(text) {
      if (!text) return "";
      try {
        if (window.marked && typeof window.marked.parse === "function") {
          return window.marked.parse(text, { gfm: true, breaks: true });
        }
      } catch (e) {
        console.error("Marked parsing error:", e);
      }
      
      // Fallback escape and render
      let html = text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
      
      // Basic markdown replacement
      // Bold
      html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
      // Italic
      html = html.replace(/\*(.*?)\*/g, "<em>$1</em>");
      // Code blocks
      html = html.replace(/```([\s\S]*?)```/g, "<pre style='background:rgba(15,14,71,0.5); padding:8px; border-radius:6px; font-family:monospace; font-size:11px; margin:8px 0; overflow-x:auto; border:1px solid rgba(134,134,172,0.2);'>$1</pre>");
      // Inline code
      html = html.replace(/`(.*?)`/g, "<code style='background:rgba(80,80,129,0.25); padding:2px 6px; border-radius:4px; font-family:monospace; font-size:11px; color:#ffb86c;'>$1</code>");
      // Bullet points
      html = html.replace(/^\*\s(.*)$/gm, "<li style='margin-left:14px; list-style-type:disc;'>$1</li>");
      // Newlines to br
      html = html.replace(/\n/g, "<br>");
      return html;
    },

    async loadAvailableTools() {
      try {
        const tools = await this.api('GET', '/api/workflow/tools');
        if (Array.isArray(tools)) {
          this.availableTools = tools;
        }
      } catch(e) { console.warn('Failed to load available tools dynamically', e); }
    },
    taskDuration(task) {
      if (!task || !task.timestamps || !task.timestamps.start) return '';
      const start = new Date(task.timestamps.start).getTime();
      let end;
      if (task.status === 'running') {
        end = this.currentTime;
      } else if (task.timestamps.end) {
        end = new Date(task.timestamps.end).getTime();
      } else {
        return '';
      }
      
      const diffMs = end - start;
      if (diffMs < 0) return '0s';
      
      const diffSec = Math.floor(diffMs / 1000);
      if (task.status === 'running') {
        const m = Math.floor(diffSec / 60).toString().padStart(2, '0');
        const s = (diffSec % 60).toString().padStart(2, '0');
        return `${m}:${s}`;
      } else {
        if (diffSec < 60) return `${diffSec}s`;
        const m = Math.floor(diffSec / 60);
        const s = diffSec % 60;
        return s > 0 ? `${m}m ${s}s` : `${m}m`;
      }
    },
    
    startDragNode(event, nodeId) {
      if (event.button !== 0) return; // Only left click
      event.preventDefault(); // Block native text selection and drag disruptions
      event.stopPropagation();
      this.dragStartX = event.clientX;
      this.dragStartY = event.clientY;
      let hasMoved = false;
      
      if (!this.nodeOffsets[nodeId]) {
        this.nodeOffsets[nodeId] = { x: 0, y: 0 };
      }
      this.dragInitialOffsetX = this.nodeOffsets[nodeId].x;
      this.dragInitialOffsetY = this.nodeOffsets[nodeId].y;
      
      const moveHandler = (e) => {
        if (!hasMoved) {
          // Only start dragging if moved > 5 pixels
          if (Math.abs(e.clientX - this.dragStartX) > 5 || Math.abs(e.clientY - this.dragStartY) > 5) {
            hasMoved = true;
            this.draggingNode = nodeId;
          } else {
            return;
          }
        }
        const dx = (e.clientX - this.dragStartX) / this.zoomLevel;
        const dy = (e.clientY - this.dragStartY) / this.zoomLevel;
        
        // Deep reactive update
        this.nodeOffsets[this.draggingNode] = {
          x: this.dragInitialOffsetX + dx,
          y: this.dragInitialOffsetY + dy
        };
        this.nodeOffsets = { ...this.nodeOffsets };
      };
      
      const upHandler = () => {
        window.removeEventListener('mousemove', moveHandler);
        window.removeEventListener('mouseup', upHandler);
        if (hasMoved) {
          setTimeout(() => { this.draggingNode = null; }, 100);
        }
      };
      
      window.addEventListener('mousemove', moveHandler);
      window.addEventListener('mouseup', upHandler);
    },
    
    getGraphLayout() {
      if (!this.activeWorkflow || !this.activeWorkflow.tasks) return { nodes: [], edges: [] };
      const tasks = this.activeWorkflow.tasks;
      const nodes = [];
      const edges = [];
      
      const taskMap = {};
      tasks.forEach(t => { taskMap[t.task_id] = t; });
      
      // Initialize topological layer representation
      const layers = {};
      tasks.forEach(t => {
        layers[t.task_id] = 0;
      });
      
      // Converge layered topological sort cleanly
      let changed = true;
      const maxPasses = tasks.length * 2 + 5;
      for (let pass = 0; pass < maxPasses && changed; pass++) {
        changed = false;
        tasks.forEach(t => {
          const deps = t.depends_on || [];
          if (deps.length > 0) {
            let maxDepLayer = -1;
            let allDepsResolved = true;
            deps.forEach(d => {
              if (layers[d] !== undefined) {
                maxDepLayer = Math.max(maxDepLayer, layers[d]);
              } else {
                allDepsResolved = false;
              }
            });
            const targetLayer = allDepsResolved ? maxDepLayer + 1 : 0;
            if (layers[t.task_id] !== targetLayer) {
              layers[t.task_id] = targetLayer;
              changed = true;
            }
          }
        });
      }
      
      const layerGroups = {};
      tasks.forEach(t => {
        const l = layers[t.task_id] || 0;
        if (!layerGroups[l]) layerGroups[l] = [];
        layerGroups[l].push(t);
      });
      
      const H_SPACING = 240;
      const V_SPACING = 90;
      const X_OFFSET = 60;
      
      const nodeCoords = {};
      
      Object.keys(layerGroups).forEach(lStr => {
        const l = parseInt(lStr);
        const group = layerGroups[l];
        group.forEach((t, idx) => {
          const baseX = X_OFFSET + l * H_SPACING;
          
          // Center group vertically around common center line (220px) preventing negative offsets
          const groupHeight = (group.length - 1) * V_SPACING;
          const baseY = Math.max(40, 220 - groupHeight / 2) + idx * V_SPACING;
          
          const ox = this.nodeOffsets[t.task_id] ? this.nodeOffsets[t.task_id].x : 0;
          const oy = this.nodeOffsets[t.task_id] ? this.nodeOffsets[t.task_id].y : 0;
          
          const x = baseX + ox;
          const y = baseY + oy;
          nodeCoords[t.task_id] = { x, y };
          
          nodes.push({
            id: t.task_id,
            task: t,
            x,
            y
          });
        });
      });
      
      tasks.forEach(t => {
        const deps = t.depends_on || [];
        deps.forEach(d => {
          if (nodeCoords[d] && nodeCoords[t.task_id]) {
            const from = nodeCoords[d];
            const to = nodeCoords[t.task_id];
            edges.push({
              fromId: d,
              toId: t.task_id,
              fromX: from.x + 160,
              fromY: from.y + 25,
              toX: to.x,
              toY: to.y + 25,
              status: t.status
            });
          }
        });
      });
      
      return { nodes, edges };
    },

    renderSvgEdges() {
      const layout = this.getGraphLayout();
      let html = '';
      layout.edges.forEach(e => {
        const stroke = e.status === 'running' ? '#ef4444' : (e.status === 'completed' ? '#22c55e' : 'rgba(134,134,172,0.35)');
        const dash = e.status === 'pending' ? 'stroke-dasharray="4"' : '';
        const width = e.status === 'running' ? '2.5' : '1.5';
        const marker = `url(#arrow-${e.status || 'pending'})`;
        
        html += `<path d="M ${e.fromX} ${e.fromY} C ${e.fromX + 40} ${e.fromY}, ${e.toX - 40} ${e.toY}, ${e.toX} ${e.toY}" 
                       stroke="${stroke}" 
                       ${dash} 
                       stroke-width="${width}" 
                       fill="none" 
                       marker-end="${marker}"></path>`;
      });
      return html;
    },

    // ── Templates ──
    async loadTemplates() {
      this.templatesLoading = true;
      try {
        this.templates = await this.api('GET', '/api/workflow/templates');
      } catch(e) { this.templates = {}; }
      this.templatesLoading = false;
    },

    async saveTemplate() {
      const t = this.tplForm;
      if (!t.id || !t.name) { alert('ID and Name are required.'); return; }
      try {
        await this.api('POST', '/api/workflow/templates', {
          id: t.id, name: t.name, purpose: t.purpose,
          system_prompt_template: t.system_prompt_template,
          allowed_tools: t.allowed_tools,
          timeout: +t.timeout || 300,
          retry_limit: +t.retry_limit || 3,
        });
        await this.loadTemplates();
        this.showTemplateForm = false;
        this.tplForm = { id:'', name:'', purpose:'', system_prompt_template:'', allowed_tools:[], timeout:300, retry_limit:3 };
        this.tplIsEdit = false;
      } catch(e) { alert('Save failed: ' + e.message); }
    },

    toggleTemplateForm() {
      if (this.showTemplateForm) {
        this.showTemplateForm = false;
        this.tplForm = { id:'', name:'', purpose:'', system_prompt_template:'', allowed_tools:[], timeout:300, retry_limit:3 };
        this.tplIsEdit = false;
      } else {
        this.tplForm = { id:'', name:'', purpose:'', system_prompt_template:'', allowed_tools:[], timeout:300, retry_limit:3 };
        this.tplIsEdit = false;
        this.showTemplateForm = true;
      }
    },

    editTemplate(id, tpl) {
      this.tplIsEdit = true;
      this.tplForm = {
        id: id,
        name: tpl.name || '',
        purpose: tpl.purpose || '',
        system_prompt_template: tpl.system_prompt_template || '',
        allowed_tools: [...(tpl.allowed_tools || [])],
        timeout: tpl.timeout || 300,
        retry_limit: tpl.retry_limit || 3
      };
      this.showTemplateForm = true;
    },

    async deleteTemplate(id) {
      if (!confirm(`Delete template "${id}"?`)) return;
      try {
        await this.api('DELETE', `/api/workflow/templates/${id}`);
        await this.loadTemplates();
      } catch(e) { alert('Delete failed: ' + e.message); }
    },

    async generateTemplate() {
      if (!this.tplGenDesc.trim()) return;
      this.tplGenerating = true;
      try {
        const result = await this.api('POST', '/api/workflow/templates/generate', { description: this.tplGenDesc });
        // auto-fill form
        this.tplForm = {
          id: result.id || '',
          name: result.name || '',
          purpose: result.purpose || '',
          system_prompt_template: result.system_prompt_template || '',
          allowed_tools: result.allowed_tools || [],
          timeout: result.timeout || 300,
          retry_limit: result.retry_limit || 3,
        };
        this.showTemplateForm = true;
      } catch(e) { alert('Generation failed: ' + e.message); }
      this.tplGenerating = false;
    },

    toggleTool(tool) {
      const idx = this.tplForm.allowed_tools.indexOf(tool);
      if (idx >= 0) this.tplForm.allowed_tools.splice(idx, 1);
      else this.tplForm.allowed_tools.push(tool);
    },

    // ── Task modal ──
    openTaskModal(task) { this.selectedTask = task; },

    // ── UI helpers ──
    topbarTitle() {
      if (this.currentView === 'templates') return 'Agent Templates';
      if (this.activeWorkflow) return this.activeWorkflow.run_title || this.activeRunId;
      return 'Atlas Terminal';
    },

    wfIconStyle(status) {
      const map = {
        running:   { background: 'rgba(42,127,200,0.15)', color: '#2a7fc8' },
        completed: { background: 'rgba(40,168,96,0.13)', color: '#28a860' },
        failed:    { background: 'rgba(208,58,58,0.12)', color: '#d03a3a' },
        pending:   { background: 'rgba(180,140,40,0.12)', color: '#b8922a' },
      };
      const s = map[status] || map.pending;
      return `background:${s.background}; color:${s.color};`;
    },

    taskCardClass(status) {
      if (status === 'running') return 'status-running-card';
      if (status === 'completed') return 'status-completed-card';
      if (status === 'failed') return 'status-failed-card';
      return '';
    },

    workerIcon(type) {
      const map = { researcher: 'fa-magnifying-glass', writer: 'fa-pen-nib', pdf_worker: 'fa-file-pdf', email_worker: 'fa-envelope', browser: 'fa-globe', code_worker: 'fa-code' };
      return map[type] || 'fa-robot';
    },

    workerIconStyle(type) {
      const map = {
        researcher: { bg: 'rgba(80,120,200,0.15)', color: '#5080c8' },
        writer:     { bg: 'rgba(160,80,200,0.15)', color: '#a050c8' },
        pdf_worker: { bg: 'rgba(220,80,80,0.15)',  color: '#dc5050' },
        email_worker:{ bg: 'rgba(80,180,120,0.15)', color: '#50b478' },
        browser:    { bg: 'rgba(40,160,200,0.15)', color: '#28a0c8' },
        code_worker:{ bg: 'rgba(200,160,40,0.15)', color: '#c8a028' },
      };
      const s = map[type] || { bg: 'rgba(134,134,172,0.12)', color: 'var(--accent)' };
      return `background:${s.bg}; color:${s.color};`;
    },

    consoleClass(text) {
      const t = (typeof text === 'string' ? text : '').toLowerCase();
      if (t.includes('fail') || t.includes('error') || t.includes('✗')) return 'console-msg-error';
      if (t.includes('completed') || t.includes('success') || t.includes('✓') || t.includes('finished')) return 'console-msg-success';
      if (t.includes('started') || t.includes('execution')) return 'console-msg-info';
      return 'console-msg-default';
    },

    fileIcon(name) {
      const ext = (name || '').split('.').pop().toLowerCase();
      const map = { pdf: 'fa-file-pdf', py: 'fa-file-code', js: 'fa-file-code', ts: 'fa-file-code', json: 'fa-file-code', md: 'fa-file-lines', txt: 'fa-file-lines', png: 'fa-file-image', jpg: 'fa-file-image', jpeg: 'fa-file-image', zip: 'fa-file-zipper', csv: 'fa-file-csv', doc: 'fa-file-word', docx: 'fa-file-word', html: 'fa-file-code' };
      return map[ext] || 'fa-file';
    },

    artFilename(path) {
      return (path || '').split('/').pop().split('\\').pop() || path;
    },

    formatBytes(bytes) {
      if (!bytes || bytes < 1024) return (bytes || 0) + ' B';
      if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
      return (bytes / 1048576).toFixed(1) + ' MB';
    },

    formatRelTime(ts) {
      if (!ts) return '';
      const d = new Date(ts);
      const now = Date.now();
      const diff = now - d.getTime();
      if (isNaN(diff)) return '';
      if (diff < 60000) return 'just now';
      if (diff < 3600000) return Math.floor(diff/60000) + 'm ago';
      if (diff < 86400000) return Math.floor(diff/3600000) + 'h ago';
      return d.toLocaleDateString();
    },

    formatLogTs(ts) {
      if (!ts) return '';
      const d = new Date(ts);
      if (isNaN(d)) return ts;
      return d.toLocaleTimeString('en-US', { hour12: false, hour:'2-digit', minute:'2-digit', second:'2-digit' });
    },

    renderMd(text) {
      if (!text) return '';
      try { return marked.parse(text); } catch(e) { return text; }
    },

    scrollConsoleToBottom() {
      const el = this.$refs.consoleEl;
      if (el) el.scrollTop = el.scrollHeight;
      const dl = this.$refs.debugConsole;
      if (dl) dl.scrollTop = dl.scrollHeight;
    },

    autoResize(el) {
      if (!el) return;
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 120) + 'px';
    },

    scrollChatToBottom(force = false) {
      const el = document.getElementById("collaborative-chat-messages");
      if (!el) return;
      const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 200;
      if (force || isNearBottom) {
        this.$nextTick(() => {
          el.scrollTop = el.scrollHeight;
        });
      }
    },

    getCombinedChatMessages() {
      if (!this.activeWorkflow) return [];
      const notifications = this.activeWorkflow.notifications || [];
      const collaborative = this.activeWorkflow.collaborative_chat || [];
      
      const rounds = [];
      let currentRound = null;
      
      // Chronologically pair users, supervisor messages, and group specific in-between system logs
      notifications.forEach((n, idx) => {
        if (n.startsWith("💬 User: ")) {
          if (currentRound) {
            rounds.push(currentRound);
          }
          const text = n.substring(9);
          currentRound = {
            id: 'round_' + idx,
            userMsg: {
              role: 'user',
              sender: 'User',
              message: text,
              timestamp: ''
            },
            systemLogs: [],
            assistantMsg: null
          };
        } else if (n.startsWith("🤖 Supervisor: ")) {
          if (!currentRound) {
            currentRound = {
              id: 'round_auto_' + idx,
              userMsg: null,
              systemLogs: [],
              assistantMsg: null
            };
          }
          const text = n.substring(15);
          currentRound.assistantMsg = {
            role: 'assistant',
            sender: 'Supervisor',
            message: text,
            timestamp: ''
          };
        } else {
          // System event status log specific to this execution turn!
          if (!currentRound) {
            currentRound = {
              id: 'round_sys_' + idx,
              userMsg: null,
              systemLogs: [],
              assistantMsg: null
            };
          }
          const cleanMsg = n.startsWith("• ") ? n.substring(2) : n;
          
          // Filter out user/supervisor messages so they are never rendered as status dots
          const isUserOrSupervisorChat = collaborative.some(m => 
            m.message === cleanMsg || 
            (m.message && cleanMsg.includes(m.message)) ||
            cleanMsg.startsWith("💬 User: ") || 
            cleanMsg.startsWith("🤖 Supervisor: ")
          );
          
          if (!isUserOrSupervisorChat) {
            const isDuplicate = currentRound.systemLogs.some(item => item.message === cleanMsg);
            if (!isDuplicate) {
              currentRound.systemLogs.push({
                role: 'system',
                message: cleanMsg
              });
            }
          }
        }
      });
      
      if (currentRound) {
        rounds.push(currentRound);
      }
      
      // Fallback for first round: if the first round has no user message, but we have a user message in collaborative chat, pair them!
      const firstUserMsg = collaborative.find(m => m.role === 'user');
      if (firstUserMsg && rounds.length > 0 && !rounds[0].userMsg) {
        rounds[0].userMsg = {
          role: 'user',
          sender: 'User',
          message: firstUserMsg.message,
          timestamp: firstUserMsg.timestamp || ''
        };
      }
      
      // If we have local temporary/streaming placeholders, push them to the final active round
      const streamingMsgs = collaborative.filter(m => {
        if (m.streaming) return true;
        if (m.role === 'user') {
          // Check if this message is already the userMsg of any round
          const alreadyPaired = rounds.some(r => r.userMsg && r.userMsg.message === m.message);
          if (alreadyPaired) return false;
          // Check if in notifications
          return !notifications.some(n => n.includes(m.message));
        }
        return false;
      });
      streamingMsgs.forEach((sm, smIdx) => {
        if (sm.role === 'user') {
          rounds.push({
            id: 'stream_user_' + smIdx,
            userMsg: {
              role: 'user',
              sender: 'User',
              message: sm.message,
              timestamp: sm.timestamp || ''
            },
            systemLogs: [],
            assistantMsg: null
          });
        } else {
          if (rounds.length > 0 && !rounds[rounds.length - 1].assistantMsg) {
            rounds[rounds.length - 1].assistantMsg = {
              role: 'assistant',
              sender: 'Supervisor',
              message: sm.message,
              streaming: sm.streaming,
              timestamp: sm.timestamp || ''
            };
          } else {
            rounds.push({
              id: 'stream_asst_' + smIdx,
              userMsg: null,
              systemLogs: [],
              assistantMsg: {
                role: 'assistant',
                sender: 'Supervisor',
                message: sm.message,
                streaming: sm.streaming,
                timestamp: sm.timestamp || ''
              }
            });
          }
        }
      });
      
      return rounds;
    },
  };
}

// Attach to window so Alpine can find it under SES/strict environments
window.atlasApp = atlasApp;
