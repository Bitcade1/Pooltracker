(() => {
    const form = document.getElementById('email-form');
    if (!form) return;
    const subject = form.elements.subject;
    const body = form.elements.body;
    const improveButton = document.getElementById('improve-email');
    const sendButton = document.getElementById('send-invoice');
    const aiStatus = document.getElementById('ai-status');
    const panel = document.getElementById('ai-suggestion');
    const applyButton = document.getElementById('apply-ai');
    const undoButton = document.getElementById('undo-ai');
    let dirty = false;
    let aiBusy = false;
    let sending = false;
    let candidate = null;
    let undo = null;
    const draft = () => ({subject: subject.value, body: body.value});
    const same = (a, b) => a.subject === b.subject && a.body === b.body;
    function clearSuggestion() { candidate = null; panel.hidden = true; }
    form.addEventListener('input', event => {
        dirty = true;
        if (event.target === subject || event.target === body) {
            if (candidate) aiStatus.textContent = 'Your draft changed. Improve it again for a new suggestion.';
            clearSuggestion();
            undo = null;
            undoButton.hidden = true;
        }
    });
    improveButton.addEventListener('click', async () => {
        if (aiBusy || sending) return;
        if (!body.value.trim()) {
            aiStatus.textContent = 'Write your message first, then improve it with AI.';
            body.focus();
            return;
        }
        if (!subject.checkValidity() || !body.checkValidity()) {
            aiStatus.textContent = 'Check the subject and message lengths before improving them.';
            return;
        }
        const original = draft();
        const sendWasDisabled = sendButton.disabled;
        clearSuggestion();
        aiBusy = true;
        improveButton.disabled = true;
        improveButton.setAttribute('aria-busy', 'true');
        improveButton.textContent = 'Improving...';
        sendButton.disabled = true;
        aiStatus.textContent = 'Working on a clearer version of your email...';
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 35000);
        try {
            const response = await fetch(form.dataset.improveUrl, {
                method: 'POST', credentials: 'same-origin', signal: controller.signal,
                headers: {'Content-Type': 'application/json', 'X-CSRF-Token': form.elements.csrf_token.value},
                body: JSON.stringify({...original, invoice_version: form.elements.invoice_version.value}),
            });
            let result;
            try { result = await response.json(); }
            catch { throw new Error('The AI request could not be completed. Your original message is unchanged; reload if your session has expired.'); }
            if (!response.ok) throw new Error(result.error || 'AI is unavailable. Your original message is unchanged.');
            if (!same(original, draft())) {
                aiStatus.textContent = 'Your draft changed while AI was working. Nothing was replaced; improve it again when ready.';
                return;
            }
            const suggestion = result.suggestion;
            if (!suggestion || typeof suggestion.subject !== 'string' || typeof suggestion.body !== 'string'
                || !suggestion.subject.trim() || !suggestion.body.trim() || suggestion.subject.length > 180 || suggestion.body.length > 10000) {
                throw new Error('AI did not return usable wording. Your original message is unchanged.');
            }
            candidate = {original, suggestion};
            document.getElementById('ai-suggested-subject').textContent = suggestion.subject;
            document.getElementById('ai-suggested-body').textContent = suggestion.body;
            panel.hidden = false;
            aiStatus.textContent = 'Suggestion ready. Your original draft is unchanged.';
            document.getElementById('ai-suggestion-title').focus({preventScroll: true});
        } catch (error) {
            aiStatus.textContent = error.name === 'AbortError'
                ? 'AI took too long. Your original message is unchanged; try again later.'
                : error.message || 'AI is unavailable. Your original message is unchanged.';
        } finally {
            clearTimeout(timeout);
            aiBusy = false;
            improveButton.disabled = sending;
            improveButton.removeAttribute('aria-busy');
            improveButton.textContent = 'Improve with AI';
            if (!sending) sendButton.disabled = sendWasDisabled;
        }
    });
    applyButton.addEventListener('click', () => {
        if (!candidate || sending) return;
        if (!same(candidate.original, draft())) {
            clearSuggestion();
            aiStatus.textContent = 'Your draft changed. Improve it again for a new suggestion.';
            return;
        }
        undo = {original: candidate.original, applied: candidate.suggestion};
        subject.value = candidate.suggestion.subject;
        body.value = candidate.suggestion.body;
        dirty = true;
        clearSuggestion();
        undoButton.hidden = false;
        aiStatus.textContent = 'Suggested wording applied. You can edit it or undo the change before sending.';
    });
    document.getElementById('discard-ai').addEventListener('click', () => {
        clearSuggestion();
        aiStatus.textContent = 'Kept your original draft.';
    });
    undoButton.addEventListener('click', () => {
        if (!undo || sending) return;
        if (same(undo.applied, draft())) {
            subject.value = undo.original.subject;
            body.value = undo.original.body;
            dirty = true;
            aiStatus.textContent = 'Your original wording has been restored.';
        }
        undo = null;
        undoButton.hidden = true;
        clearSuggestion();
    });
    form.addEventListener('submit', event => {
        if (aiBusy || sending) { event.preventDefault(); return; }
        dirty = false;
        sending = true;
        improveButton.disabled = applyButton.disabled = undoButton.disabled = true;
        sendButton.disabled = true;
        sendButton.textContent = 'Sending...';
        document.getElementById('send-status').textContent = 'Creating the PDF and submitting your email to Microsoft 365...';
    });
    window.addEventListener('beforeunload', event => {
        if (dirty) { event.preventDefault(); event.returnValue = ''; }
    });
})();
