"""HTML gallery generation."""

import html as html_lib
import json
from pathlib import Path

from .models import VideoMetadata, UserEditsFile


def generate_gallery(output_dir: Path, transcribe: bool = True) -> None:
    """Generate HTML gallery from all processed videos in subdirectories."""
    print("Generating gallery...")

    video_groups = []
    all_user_edits = {}  # {source_name: user_edits_data}

    for subdir in sorted(output_dir.iterdir()):
        if not subdir.is_dir():
            continue
        metadata_path = subdir / "metadata.json"
        if not metadata_path.exists():
            continue

        metadata = VideoMetadata.load(metadata_path)
        video_groups.append((subdir.name, metadata.clips, subdir))
        print(f"  Found {len(metadata.clips)} clips in {subdir.name}")

        # Load user edits if exists
        user_edits_path = subdir / "user_edits.json"
        if user_edits_path.exists():
            edits = UserEditsFile.load(user_edits_path)
            all_user_edits[subdir.name] = edits.model_dump()

    if not video_groups:
        print("  No processed videos found")
        return

    total_clips = sum(len(clips) for _, clips, _ in video_groups)
    print(f"  Total: {total_clips} clips from {len(video_groups)} videos")

    html = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Video Gallery</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #1a1a1a;
            color: #fff;
            padding: 20px;
        }
        h1 { margin-bottom: 20px; }
        .controls {
            display: flex;
            gap: 12px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }
        .search-box {
            flex: 1;
            min-width: 200px;
            padding: 10px 14px;
            font-size: 14px;
            border: 1px solid #444;
            border-radius: 6px;
            background: #2a2a2a;
            color: #fff;
        }
        .search-box:focus { outline: none; border-color: #666; }
        .btn {
            padding: 10px 16px;
            font-size: 14px;
            border: 1px solid #444;
            border-radius: 6px;
            background: #2a2a2a;
            color: #fff;
            cursor: pointer;
        }
        .btn:hover { background: #3a3a3a; }
        .source-group { margin-bottom: 24px; }
        .source-header {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px;
            background: #333;
            border-radius: 8px;
            cursor: pointer;
            margin-bottom: 12px;
        }
        .source-header:hover { background: #3a3a3a; }
        .source-header h2 {
            font-size: 16px;
            font-weight: 500;
        }
        .source-header .clip-count {
            font-size: 12px;
            color: #888;
        }
        .source-tags-year {
            display: flex;
            flex-wrap: wrap;
            gap: 4px;
            align-items: center;
            flex: 1;
        }
        .source-header .toggle-icon {
            font-size: 12px;
            color: #888;
            transition: transform 0.2s;
        }
        .source-group.collapsed .toggle-icon { transform: rotate(-90deg); }
        .source-group.collapsed .gallery { display: none; }
        .gallery {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
            gap: 16px;
        }
        .video-card {
            background: #2a2a2a;
            border-radius: 8px;
            overflow: hidden;
        }
        .video-card.hidden { display: none; }
        .thumb-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 2px;
            cursor: pointer;
        }
        .thumb-grid img {
            width: 100%;
            aspect-ratio: 16/9;
            object-fit: cover;
        }
        .video-info { padding: 10px; }
        .video-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .video-name {
            font-size: 14px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .video-duration {
            font-size: 12px;
            color: #888;
        }
        .transcript-toggle {
            font-size: 12px;
            color: #888;
            cursor: pointer;
            margin-top: 8px;
        }
        .transcript-toggle:hover { color: #aaa; }
        .transcript {
            display: none;
            margin-top: 8px;
            padding: 8px;
            background: #222;
            border-radius: 4px;
            font-size: 12px;
            line-height: 1.5;
            max-height: 200px;
            overflow-y: auto;
            white-space: pre-wrap;
        }
        .transcript.expanded { display: block; }
        .transcript mark {
            background: #665500;
            color: #fff;
        }
        .tags-year {
            display: flex;
            flex-wrap: wrap;
            gap: 4px;
            align-items: center;
            flex: 1;
            margin-left: 8px;
        }
        .tag, .year-badge {
            display: inline-flex;
            align-items: center;
            gap: 3px;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 11px;
            background: #555;
        }
        .tag::before, .year-badge::before {
            font-size: 8px;
        }
        .year-badge { border-radius: 4px; }
        /* High confidence: fully visible */
        .confidence-high { opacity: 1; }
        /* Medium confidence: slightly faded with ? */
        .confidence-medium { opacity: 0.75; }
        .confidence-medium::after { content: "?"; margin-left: 2px; opacity: 0.7; }
        /* Low confidence: faded with ?? */
        .confidence-low { opacity: 0.55; }
        .confidence-low::after { content: "??"; margin-left: 2px; opacity: 0.7; }
        /* Inherited: italic */
        .tag.inherited, .year-badge.inherited { font-style: italic; opacity: 0.5; }
        /* Inline editing */
        .tag.editable, .year-badge.editable { cursor: pointer; }
        .tag.editable:hover, .year-badge.editable:hover { filter: brightness(1.2); }
        .add-btn {
            display: none;
            padding: 2px 4px;
            font-size: 11px;
            background: none;
            border: none;
            color: #666;
            cursor: pointer;
        }
        .add-btn:hover { color: #aaa; }
        body.api-available .video-card:hover .add-btn,
        body.api-available .source-header:hover .add-btn { display: inline-block; }
        .inline-popup {
            display: none;
            position: fixed;
            background: #333;
            border: 1px solid #555;
            border-radius: 6px;
            padding: 8px;
            z-index: 1001;
            box-shadow: 0 4px 12px rgba(0,0,0,0.5);
        }
        .inline-popup.active { display: block; }
        .inline-popup input, .inline-popup select {
            padding: 6px 8px;
            font-size: 13px;
            border: 1px solid #444;
            border-radius: 4px;
            background: #2a2a2a;
            color: #fff;
        }
        .inline-popup input { width: 120px; }
        .inline-popup input.year-input { width: 70px; }
        .inline-popup .popup-row {
            display: flex;
            gap: 6px;
            align-items: center;
            margin-bottom: 6px;
        }
        .inline-popup .popup-row:last-child { margin-bottom: 0; }
        .inline-popup .conf-label {
            font-size: 10px;
            color: #888;
            margin-right: 4px;
        }
        .inline-popup .conf-btn {
            padding: 4px 8px;
            font-size: 11px;
            border: 1px solid #555;
            border-radius: 4px;
            background: #444;
            color: #ccc;
            cursor: pointer;
        }
        .inline-popup .conf-btn:hover { background: #555; }
        .inline-popup .conf-btn.active { background: #666; border-color: #888; color: #fff; }
        .inline-popup .delete-btn {
            padding: 4px 8px;
            font-size: 11px;
            border: none;
            border-radius: 4px;
            background: #633;
            color: #faa;
            cursor: pointer;
        }
        .inline-popup .delete-btn:hover { background: #844; }
        .modal {
            display: none;
            position: fixed;
            top: 0; left: 0;
            width: 100%; height: 100%;
            background: rgba(0,0,0,0.9);
            z-index: 1000;
            align-items: center;
            justify-content: center;
        }
        .modal.active { display: flex; }
        .modal video {
            max-width: 90%;
            max-height: 85vh;
        }
        .modal-close {
            position: absolute;
            top: 20px;
            right: 30px;
            font-size: 40px;
            color: #fff;
            cursor: pointer;
        }
    </style>
</head>
<body>
    <h1>Video Gallery</h1>
    <div class="controls">
        <input type="text" class="search-box" id="search" placeholder="Search transcripts and tags...">
        <button class="btn" id="expandAll">Expand All</button>
        <button class="btn" id="collapseGroups">Collapse Sources</button>
    </div>
    <div id="content">
'''

    for source_name, clips, subdir in video_groups:
        html += f'''    <div class="source-group" data-source="{source_name}">
        <div class="source-header">
            <span class="toggle-icon" onclick="toggleGroup(this.parentElement)">▼</span>
            <h2 onclick="toggleGroup(this.parentElement)">{source_name}</h2>
            <div class="source-tags-year" data-source="{source_name}"></div>
            <span class="clip-count" onclick="toggleGroup(this.parentElement)">{len(clips)} clips</span>
        </div>
        <div class="gallery">
'''
        for clip in clips:
            thumbs_html = ''.join(f'<img src="{source_name}/{t}" alt="">' for t in clip.thumbs)
            video_path = f"{source_name}/{clip.file}"
            transcript_escaped = html_lib.escape(clip.transcript)
            html += f'''            <div class="video-card" data-transcript="{transcript_escaped}" data-source="{source_name}" data-clip="{clip.name}">
                <div class="thumb-grid" onclick="playVideo('{video_path}')">{thumbs_html}</div>
                <div class="video-info">
                    <div class="video-header">
                        <div class="video-name">{clip.name}</div>
                        <div class="tags-year" data-source="{source_name}" data-clip="{clip.name}"></div>
                        <div class="video-duration">{clip.duration}</div>
                    </div>
                    <div class="transcript-toggle" onclick="toggleTranscript(this)">Show transcript</div>
                    <div class="transcript">{transcript_escaped}</div>
                </div>
            </div>
'''
        html += '''        </div>
    </div>
'''

    user_edits_json = json.dumps(all_user_edits)

    html += f'''    </div>
    <div class="modal" id="modal" onclick="closeModal(event)">
        <span class="modal-close">&times;</span>
        <video id="player" controls></video>
    </div>
    <div class="inline-popup" id="inlinePopup"></div>
    <script src="https://cdn.jsdelivr.net/npm/fuse.js@7.0.0/dist/fuse.min.js"></script>
    <script>
        const userEdits = {user_edits_json};
        const modal = document.getElementById('modal');
        const player = document.getElementById('player');
        const search = document.getElementById('search');
        const expandBtn = document.getElementById('expandAll');
        const collapseBtn = document.getElementById('collapseGroups');
        const cards = document.querySelectorAll('.video-card');
        const groups = document.querySelectorAll('.source-group');

        // Get resolved tags and year for a clip (with inheritance)
        function getResolvedMeta(source, clipName) {{
            const edits = userEdits[source] || {{}};
            const videoMeta = edits.video || {{}};
            const clipMeta = (edits.clips || {{}})[clipName] || {{}};

            // Merge tags: clip tags override video tags with same name
            const videoTags = (videoMeta.tags || []).map(t => ({{...t, inherited: true}}));
            const clipTags = clipMeta.tags || [];
            const clipTagNames = new Set(clipTags.map(t => t.name));
            const mergedTags = [
                ...clipTags,
                ...videoTags.filter(t => !clipTagNames.has(t.name))
            ];

            // Year: clip overrides video
            let year = clipMeta.year || null;
            let yearInherited = false;
            if (!year && videoMeta.year) {{
                year = videoMeta.year;
                yearInherited = true;
            }}

            return {{ tags: mergedTags, year, yearInherited }};
        }}

        // Render tags and year for a clip
        function renderTagsYear(container) {{
            const source = container.dataset.source;
            const clipName = container.dataset.clip;
            const {{ tags, year, yearInherited }} = getResolvedMeta(source, clipName);

            let html = '';
            for (let i = 0; i < tags.length; i++) {{
                const tag = tags[i];
                const inherited = tag.inherited ? ' inherited' : '';
                const inheritedText = tag.inherited ? ' (inherited from video)' : '';
                const title = `${{tag.confidence}} confidence${{inheritedText}}. Click to edit.`;
                const editable = !tag.inherited ? ' editable' : '';
                html += `<span class="tag confidence-${{tag.confidence}}${{inherited}}${{editable}}" title="${{title}}" data-idx="${{i}}" data-name="${{tag.name}}" data-conf="${{tag.confidence}}" data-inherited="${{tag.inherited || false}}">${{tag.name}}</span>`;
            }}
            if (year) {{
                const inherited = yearInherited ? ' inherited' : '';
                const inheritedText = yearInherited ? ' (inherited from video)' : '';
                const title = `${{year.confidence}} confidence${{inheritedText}}. Click to edit.`;
                const editable = !yearInherited ? ' editable' : '';
                html += `<span class="year-badge confidence-${{year.confidence}}${{inherited}}${{editable}}" title="${{title}}" data-year="${{year.year}}" data-conf="${{year.confidence}}" data-inherited="${{yearInherited}}">${{year.year}}</span>`;
            }}
            // Add buttons (only shown when API available)
            html += `<button class="add-btn" data-action="add-tag">+tag</button>`;
            if (!year || yearInherited) {{
                html += `<button class="add-btn" data-action="add-year">+year</button>`;
            }}
            container.innerHTML = html;
        }}

        // Render video-level tags/year in source headers
        function renderSourceTagsYear(container) {{
            const source = container.dataset.source;
            const edits = userEdits[source] || {{}};
            const videoMeta = edits.video || {{}};
            const tags = videoMeta.tags || [];
            const year = videoMeta.year;

            let html = '';
            for (let i = 0; i < tags.length; i++) {{
                const tag = tags[i];
                const title = `${{tag.confidence}} confidence. Click to edit.`;
                html += `<span class="tag confidence-${{tag.confidence}} editable" title="${{title}}" data-idx="${{i}}" data-name="${{tag.name}}" data-conf="${{tag.confidence}}">${{tag.name}}</span>`;
            }}
            if (year) {{
                const title = `${{year.confidence}} confidence. Click to edit.`;
                html += `<span class="year-badge confidence-${{year.confidence}} editable" title="${{title}}" data-year="${{year.year}}" data-conf="${{year.confidence}}">${{year.year}}</span>`;
            }}
            // Add buttons (only shown when API available)
            html += `<button class="add-btn" data-action="add-tag">+tag</button>`;
            if (!year) {{
                html += `<button class="add-btn" data-action="add-year">+year</button>`;
            }}
            container.innerHTML = html;
        }}

        // Render all tags/year
        document.querySelectorAll('.source-tags-year').forEach(renderSourceTagsYear);
        document.querySelectorAll('.video-card .tags-year').forEach(renderTagsYear);

        // Build search data including tags
        const cardData = Array.from(cards).map((card, i) => {{
            const source = card.dataset.source;
            const clipName = card.dataset.clip;
            const {{ tags }} = getResolvedMeta(source, clipName);
            return {{
                idx: i,
                transcript: card.dataset.transcript,
                tags: tags.map(t => t.name).join(' ')
            }};
        }});
        const fuse = new Fuse(cardData, {{
            keys: ['transcript', 'tags'],
            threshold: 0.4,
            ignoreLocation: true,
            includeMatches: true,
            minMatchCharLength: 2
        }});'''

    html += '''
        function playVideo(src) {
            player.src = src;
            modal.classList.add('active');
            player.play();
        }

        function closeModal(e) {
            if (e.target === modal || e.target.classList.contains('modal-close')) {
                modal.classList.remove('active');
                player.pause();
                player.src = '';
            }
        }

        function toggleTranscript(el) {
            const transcript = el.nextElementSibling;
            const isExpanded = transcript.classList.toggle('expanded');
            el.textContent = isExpanded ? 'Hide transcript' : 'Show transcript';
        }

        function toggleGroup(header) {
            header.parentElement.classList.toggle('collapsed');
        }

        function highlightMatches(text, matches) {
            if (!matches || !matches.length) return text;
            const indices = matches[0].indices.sort((a, b) => b[0] - a[0]);
            let result = text;
            for (const [start, end] of indices) {
                result = result.slice(0, start) + '<mark>' + result.slice(start, end + 1) + '</mark>' + result.slice(end + 1);
            }
            return result;
        }

        search.addEventListener('input', () => {
            const q = search.value.trim();
            if (!q) {
                cards.forEach(card => {
                    card.classList.remove('hidden');
                    card.querySelector('.transcript').textContent = card.dataset.transcript;
                });
                groups.forEach(g => g.classList.remove('collapsed'));
                return;
            }

            const results = fuse.search(q);
            const matchedIndices = new Set(results.map(r => r.item.idx));

            cards.forEach((card, i) => {
                const isMatch = matchedIndices.has(i);
                card.classList.toggle('hidden', !isMatch);

                const transcriptEl = card.querySelector('.transcript');
                if (isMatch) {
                    const result = results.find(r => r.item.idx === i);
                    transcriptEl.innerHTML = highlightMatches(card.dataset.transcript, result.matches);
                } else {
                    transcriptEl.textContent = card.dataset.transcript;
                }
            });

            groups.forEach(g => g.classList.remove('collapsed'));
        });

        let allExpanded = false;
        expandBtn.addEventListener('click', () => {
            allExpanded = !allExpanded;
            expandBtn.textContent = allExpanded ? 'Collapse All' : 'Expand All';
            cards.forEach(card => {
                const transcript = card.querySelector('.transcript');
                const toggle = card.querySelector('.transcript-toggle');
                if (allExpanded) {
                    transcript.classList.add('expanded');
                    toggle.textContent = 'Hide transcript';
                } else {
                    transcript.classList.remove('expanded');
                    toggle.textContent = 'Show transcript';
                }
            });
        });

        let groupsCollapsed = false;
        collapseBtn.addEventListener('click', () => {
            groupsCollapsed = !groupsCollapsed;
            collapseBtn.textContent = groupsCollapsed ? 'Expand Sources' : 'Collapse Sources';
            groups.forEach(g => g.classList.toggle('collapsed', groupsCollapsed));
        });

        document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal({target: modal}); });

        // Inline editing functionality
        const popup = document.getElementById('inlinePopup');
        let popupContext = null; // {source, clipName, type, tagIdx}

        // Check if API is available (skip for file:// to avoid CORS errors)
        async function checkApi() {
            if (location.protocol === 'file:') return;
            try {
                const firstSource = groups[0]?.dataset.source;
                if (!firstSource) return;
                const resp = await fetch(`/api/edits/${firstSource}`);
                if (resp.ok) {
                    document.body.classList.add('api-available');
                }
            } catch (e) {}
        }
        checkApi();

        function closePopup() {
            popup.classList.remove('active');
            popupContext = null;
        }

        function positionPopup(element) {
            const rect = element.getBoundingClientRect();
            popup.style.left = rect.left + 'px';
            popup.style.top = (rect.bottom + 4) + 'px';
        }

        function confButtons(current) {
            const btns = ['high', 'medium', 'low'].map(c =>
                `<button class="conf-btn${c === current ? ' active' : ''}" data-conf="${c}">${c}</button>`
            ).join('');
            return `<span class="conf-label">Confidence:</span>${btns}`;
        }

        function showTagPopup(element, source, clipName, tagIdx, tagName, tagConf) {
            popupContext = { source, clipName, type: 'edit-tag', tagIdx };
            popup.innerHTML = `
                <div class="popup-row">
                    <input type="text" class="tag-name-input" value="${tagName}">
                </div>
                <div class="popup-row">
                    ${confButtons(tagConf)}
                    <button class="delete-btn">Delete</button>
                </div>
            `;
            positionPopup(element);
            popup.classList.add('active');
            popup.querySelector('.tag-name-input').focus();
        }

        function showYearPopup(element, source, clipName, year, conf) {
            popupContext = { source, clipName, type: 'edit-year' };
            popup.innerHTML = `
                <div class="popup-row">
                    <input type="number" class="year-input" value="${year}" placeholder="Year">
                </div>
                <div class="popup-row">
                    ${confButtons(conf)}
                    <button class="delete-btn">Delete</button>
                </div>
            `;
            positionPopup(element);
            popup.classList.add('active');
            popup.querySelector('.year-input').focus();
        }

        function showAddTagPopup(element, source, clipName) {
            popupContext = { source, clipName, type: 'add-tag' };
            popup.innerHTML = `
                <div class="popup-row">
                    <input type="text" class="tag-name-input" placeholder="Tag name">
                </div>
                <div class="popup-row">
                    ${confButtons('high')}
                </div>
            `;
            positionPopup(element);
            popup.classList.add('active');
            popup.querySelector('.tag-name-input').focus();
        }

        function showAddYearPopup(element, source, clipName) {
            popupContext = { source, clipName, type: 'add-year' };
            popup.innerHTML = `
                <div class="popup-row">
                    <input type="number" class="year-input" placeholder="Year">
                </div>
                <div class="popup-row">
                    ${confButtons('low')}
                </div>
            `;
            positionPopup(element);
            popup.classList.add('active');
            popup.querySelector('.year-input').focus();
        }

        async function saveEdits(source, clipName, newMeta) {
            const edits = userEdits[source] || { video: { tags: [], year: null }, clips: {} };
            if (clipName) {
                edits.clips[clipName] = newMeta;
            } else {
                edits.video = newMeta;
            }

            try {
                const resp = await fetch(`/api/edits/${source}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(edits)
                });
                if (resp.ok) {
                    userEdits[source] = edits;
                    // Re-render affected elements
                    if (clipName) {
                        const container = document.querySelector(`.tags-year[data-source="${source}"][data-clip="${clipName}"]`);
                        if (container) renderTagsYear(container);
                    } else {
                        const container = document.querySelector(`.source-tags-year[data-source="${source}"]`);
                        if (container) renderSourceTagsYear(container);
                        // Also update all clips in this group (inheritance may have changed)
                        document.querySelectorAll(`.tags-year[data-source="${source}"]`).forEach(renderTagsYear);
                    }
                }
            } catch (e) {
                console.error('Save failed:', e);
            }
        }

        function getCurrentMeta(source, clipName) {
            const edits = userEdits[source] || { video: { tags: [], year: null }, clips: {} };
            if (clipName) {
                return edits.clips[clipName] || { tags: [], year: null };
            }
            return edits.video || { tags: [], year: null };
        }

        // Handle clicks on tags-year containers (event delegation)
        document.addEventListener('click', async (e) => {
            const tag = e.target.closest('.tag.editable');
            const yearBadge = e.target.closest('.year-badge.editable');
            const addBtn = e.target.closest('.add-btn');
            const confBtn = e.target.closest('.conf-btn');
            const deleteBtn = e.target.closest('.delete-btn');

            // Close popup if clicking outside
            if (!e.target.closest('.inline-popup') && !tag && !yearBadge && !addBtn) {
                if (popup.classList.contains('active')) {
                    // Save any pending input before closing
                    await handlePopupSave();
                    closePopup();
                }
                return;
            }

            // Handle confidence button in popup
            if (confBtn && popupContext) {
                popup.querySelectorAll('.conf-btn').forEach(b => b.classList.remove('active'));
                confBtn.classList.add('active');
                // Auto-save for edit-tag and edit-year
                if (popupContext.type === 'edit-tag' || popupContext.type === 'edit-year') {
                    await handlePopupSave();
                }
                return;
            }

            // Handle delete button in popup
            if (deleteBtn && popupContext) {
                const { source, clipName, type, tagIdx } = popupContext;
                const meta = getCurrentMeta(source, clipName);

                if (type === 'edit-tag') {
                    meta.tags = meta.tags.filter((_, i) => i !== tagIdx);
                } else if (type === 'edit-year') {
                    meta.year = null;
                }

                await saveEdits(source, clipName, meta);
                closePopup();
                return;
            }

            // Handle add buttons
            if (addBtn) {
                const container = addBtn.closest('.tags-year, .source-tags-year');
                const source = container.dataset.source;
                const clipName = container.dataset.clip || null;
                const action = addBtn.dataset.action;

                closePopup();
                if (action === 'add-tag') {
                    showAddTagPopup(addBtn, source, clipName);
                } else if (action === 'add-year') {
                    showAddYearPopup(addBtn, source, clipName);
                }
                return;
            }

            // Handle tag click
            if (tag && tag.dataset.inherited !== 'true') {
                const container = tag.closest('.tags-year, .source-tags-year');
                const source = container.dataset.source;
                const clipName = container.dataset.clip || null;
                closePopup();
                showTagPopup(tag, source, clipName, parseInt(tag.dataset.idx), tag.dataset.name, tag.dataset.conf);
                return;
            }

            // Handle year badge click
            if (yearBadge && yearBadge.dataset.inherited !== 'true') {
                const container = yearBadge.closest('.tags-year, .source-tags-year');
                const source = container.dataset.source;
                const clipName = container.dataset.clip || null;
                closePopup();
                showYearPopup(yearBadge, source, clipName, yearBadge.dataset.year, yearBadge.dataset.conf);
                return;
            }
        });

        async function handlePopupSave() {
            if (!popupContext) return;

            const { source, clipName, type, tagIdx } = popupContext;
            const meta = getCurrentMeta(source, clipName);
            const activeConf = popup.querySelector('.conf-btn.active')?.dataset.conf || 'high';

            if (type === 'edit-tag') {
                const nameInput = popup.querySelector('.tag-name-input');
                const newName = nameInput?.value.trim();
                if (newName && meta.tags[tagIdx]) {
                    meta.tags[tagIdx] = { name: newName, confidence: activeConf };
                    await saveEdits(source, clipName, meta);
                }
            } else if (type === 'edit-year') {
                const yearInput = popup.querySelector('.year-input');
                const newYear = parseInt(yearInput?.value);
                if (newYear) {
                    meta.year = { year: newYear, confidence: activeConf };
                    await saveEdits(source, clipName, meta);
                }
            } else if (type === 'add-tag') {
                const nameInput = popup.querySelector('.tag-name-input');
                const newName = nameInput?.value.trim();
                if (newName) {
                    meta.tags = meta.tags || [];
                    meta.tags.push({ name: newName, confidence: activeConf });
                    await saveEdits(source, clipName, meta);
                }
            } else if (type === 'add-year') {
                const yearInput = popup.querySelector('.year-input');
                const newYear = parseInt(yearInput?.value);
                if (newYear) {
                    meta.year = { year: newYear, confidence: activeConf };
                    await saveEdits(source, clipName, meta);
                }
            }
        }

        // Handle Enter key in popup inputs
        popup.addEventListener('keydown', async (e) => {
            if (e.key === 'Enter') {
                await handlePopupSave();
                closePopup();
            } else if (e.key === 'Escape') {
                closePopup();
            }
        });

        // Also close popup on Escape globally
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && popup.classList.contains('active')) {
                closePopup();
            }
        });
    </script>
</body>
</html>
'''

    gallery_path = output_dir / "gallery.html"
    gallery_path.write_text(html)
    print(f"  Gallery: {gallery_path}")
