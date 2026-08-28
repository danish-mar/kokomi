/**
 * Application Initial State
 */

export function getInitialState() {
    return {
        // Persists across refreshes; a first-ever visit falls back to
        // open-on-desktop/closed-on-mobile since there's no stored value yet.
        sidebarOpen: localStorage.getItem('sidebarOpen') !== null
            ? localStorage.getItem('sidebarOpen') === 'true'
            : window.innerWidth > 768,
        input: '',
        attachments: [],
        loading: false,
        loadingStatus: 'Thinking...',
        messages: [],
        conversations: [],
        folders: [],
        openFolders: JSON.parse(localStorage.getItem('openFolders') || '[]'),
        currentConvId: null,
        projectModal: { show: false, id: null, name: '', icon: 'fa-solid fa-folder' },
        iconChoices: [
            'fa-solid fa-folder', 'fa-solid fa-briefcase', 'fa-solid fa-code', 
            'fa-solid fa-book', 'fa-solid fa-graduation-cap', 'fa-solid fa-user-group',
            'fa-solid fa-star', 'fa-solid fa-heart', 'fa-solid fa-bolt', 
            'fa-solid fa-lightbulb', 'fa-solid fa-compass', 'fa-solid fa-flask',
            'fa-solid fa-music', 'fa-solid fa-palette', 'fa-solid fa-microchip'
        ],
        prefs: { dynamic_suggestions: true, artifacts: true, debug_mode: false },
        artifactModal: {
            show: false, id: null, title: '', type: '', icon: '',
            content: '', renderedContent: '', output: '', executing: false,
            tab: 'code'
        },
        // "Forward to a Triton machine" dropdown on the artifact modal
        artifactForward: {
            open: false, loading: false, devices: [], sendingId: null, error: ''
        },
        // Canvas — an editable artifact opened beside the chat (chat 40% / canvas 60%).
        // mode 'code' mounts Monaco, mode 'document' mounts a Word-style Quill page.
        // The editor instances themselves live in KokomiCanvas (canvas.js), not here:
        // Alpine's reactive proxy must never wrap them or the editors break.
        canvas: {
            open: false, id: null, mode: 'code', language: 'plaintext',
            title: '', content: '',
            dirty: false, saving: false, savedAt: null,
            // True while the model is writing into the canvas (live typing)
            streaming: false,
            // Download-format dropdown
            exportOpen: false,
            // True while a targeted AI edit is being generated
            editing: false,
            // % of the row given to the chat column; the canvas takes the rest
            splitPct: (parseFloat(localStorage.getItem('canvasSplitPct')) || 40),
            wordCount: 0
        },
        // Right-click AI menu inside the document canvas
        canvasMenu: { open: false, x: 0, y: 0, selection: '' },
        // Ctrl+Space inline instruction box
        canvasPrompt: { open: false, x: 0, y: 0, text: '', selection: '' },
        // The AI's live, unanswered clarifying question — rendered as a floating
        // card docked above the composer (not an inline artifact card).
        pendingQuestion: null,
        pendingQuestionOther: '',
        // True for the brief moment between tabs while the body fades/slides out
        // and back in — see _switchQuestionTab in ui.js.
        pendingQuestionAnimating: false,
        // Fullscreen image viewer for AI-generated galleries
        lightbox: { show: false, images: [], index: 0, src: '' },
        // Resizable sidebar width (px), persisted client-side
        sidebarWidth: (parseInt(localStorage.getItem('sidebarWidth'), 10) || 300),
        abortController: null,
        spaces: [],
        activeSpaceId: null,
        // ChatGPT-style message editing: index of the message bubble currently
        // showing its inline edit textarea (null = none), and its draft text.
        editingIndex: null,
        editDraft: '',
        // Set while a branch switch/edit/regenerate network call is in flight,
        // per message index, to disable the nav arrows against double-clicks.
        branchBusyIndex: null,

        // Characters
        characters: [],
        activeCharId: localStorage.getItem('lastCharacterId') || 'kokomi',
        charPickerOpen: false,
        welcomePickerOpen: false,
        groupParticipants: [],
        roomPickerOpen: false,
        roomCharQ: '',
        currentStreamingCharId: null,
        toasts: [],
        isAnonymous: false,
        exitTempModal: false,
        useWebSearch: localStorage.getItem('useWebSearch') === 'true',
        // Composer brain-icon slider: 'fast' | 'normal' | 'smart'
        modelTier: localStorage.getItem('modelTier') || 'normal',
        modelTierHover: false,
        modelTierPinned: false,
        isRecording: false,
        liveStats: { tps: null, ttft: null, context: null },
        showTourPrompt: false,
        messagesLoaded: true,

        // Quick prompts 
        quickPrompts: [
            { icon: 'fa-solid fa-code',       label: 'Write Code',  text: 'Write a Python function to parse JSON and handle errors.' },
            { icon: 'fa-solid fa-brain',      label: 'Explain',     text: 'Explain how transformer attention mechanisms work.' },
            { icon: 'fa-solid fa-pen-nib',    label: 'Draft',       text: 'Write a professional follow-up email for a job application.' },
            { icon: 'fa-solid fa-chart-line', label: 'Analyze',     text: 'What are the best practices for data visualization?' },
        ],

        // Spotlight Search
        spotlightOpen: false,
        spotlightQuery: '',
        spotlightResults: [],
        spotlightSuggestions: [],
        spotlightSelectedId: null,
        spotlightPreview: null,
        spotlightLoading: false,
        spotlightGlowing: false,
        _spotlightDebounceTimer: null,
    };
}
