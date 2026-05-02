/**
 * Application Initial State
 */

export function getInitialState() {
    return {
        sidebarOpen: window.innerWidth > 768,
        input: '',
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
        prefs: { dynamic_suggestions: true },
        abortController: null,
        spaces: [],
        activeSpaceId: null,

        // Characters
        characters: [],
        activeCharId: localStorage.getItem('lastCharacterId') || 'kokomi',
        charPickerOpen: false,
        welcomePickerOpen: false,
        groupParticipants: [],
        roomPickerOpen: false,
        currentStreamingCharId: null,
        toasts: [],
        isAnonymous: false,
        exitTempModal: false,
        useWebSearch: false,
        isRecording: false,

        // Quick prompts 
        quickPrompts: [
            { icon: 'fa-solid fa-code',       label: 'Write Code',  text: 'Write a Python function to parse JSON and handle errors.' },
            { icon: 'fa-solid fa-brain',      label: 'Explain',     text: 'Explain how transformer attention mechanisms work.' },
            { icon: 'fa-solid fa-pen-nib',    label: 'Draft',       text: 'Write a professional follow-up email for a job application.' },
            { icon: 'fa-solid fa-chart-line', label: 'Analyze',     text: 'What are the best practices for data visualization?' },
        ],
    };
}
