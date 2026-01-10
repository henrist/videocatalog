"""HTML gallery generation."""

import html as html_lib
import json
from dataclasses import asdict
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
            all_user_edits[subdir.name] = {
                'video': {
                    'tags': [asdict(t) for t in edits.video.tags],
                    'year': asdict(edits.video.year) if edits.video.year else None
                },
                'clips': {
                    name: {
                        'tags': [asdict(t) for t in meta.tags],
                        'year': asdict(meta.year) if meta.year else None
                    }
                    for name, meta in edits.clips.items()
                }
            }

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
            gap: 6px;
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
            gap: 6px;
            margin-top: 8px;
            align-items: center;
        }
        .tag {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 11px;
            background: #444;
        }
        .tag.confidence-high { background: #446644; }
        .tag.confidence-medium {
            background: transparent;
            border: 1px dashed #668866;
            color: #aaa;
        }
        .tag.confidence-low {
            background: transparent;
            color: #777;
            font-style: italic;
        }
        .tag.inherited { font-style: italic; opacity: 0.7; }
        .year-badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            background: #445566;
        }
        .year-badge.confidence-medium {
            background: transparent;
            border: 1px dashed #668888;
        }
        .year-badge.confidence-low {
            background: transparent;
            color: #777;
            font-style: italic;
        }
        .year-badge.inherited { font-style: italic; opacity: 0.7; }
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
        <div class="source-header" onclick="toggleGroup(this)">
            <span class="toggle-icon">▼</span>
            <h2>{source_name}</h2>
            <div class="source-tags-year"></div>
            <span class="clip-count">{len(clips)} clips</span>
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
                        <div class="video-duration">{clip.duration}</div>
                    </div>
                    <div class="tags-year"></div>
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

        // Render tags and year for a card
        function renderTagsYear(card) {{
            const source = card.dataset.source;
            const clipName = card.dataset.clip;
            const container = card.querySelector('.tags-year');
            const {{ tags, year, yearInherited }} = getResolvedMeta(source, clipName);

            let html = '';
            for (const tag of tags) {{
                const inherited = tag.inherited ? ' inherited' : '';
                const inheritedText = tag.inherited ? ' (inherited from video)' : '';
                const title = `${{tag.confidence}} confidence${{inheritedText}}`;
                html += `<span class="tag confidence-${{tag.confidence}}${{inherited}}" title="${{title}}">${{tag.name}}</span>`;
            }}
            if (year) {{
                const inherited = yearInherited ? ' inherited' : '';
                const inheritedText = yearInherited ? ' (inherited from video)' : '';
                const title = `${{year.confidence}} confidence${{inheritedText}}`;
                html += `<span class="year-badge confidence-${{year.confidence}}${{inherited}}" title="${{title}}">${{year.year}}</span>`;
            }}
            container.innerHTML = html;
        }}

        // Render video-level tags/year in source headers
        function renderSourceTagsYear(group) {{
            const source = group.dataset.source;
            const edits = userEdits[source] || {{}};
            const videoMeta = edits.video || {{}};
            const container = group.querySelector('.source-tags-year');

            let html = '';
            for (const tag of (videoMeta.tags || [])) {{
                const title = `${{tag.confidence}} confidence`;
                html += `<span class="tag confidence-${{tag.confidence}}" title="${{title}}">${{tag.name}}</span>`;
            }}
            if (videoMeta.year) {{
                const title = `${{videoMeta.year.confidence}} confidence`;
                html += `<span class="year-badge confidence-${{videoMeta.year.confidence}}" title="${{title}}">${{videoMeta.year.year}}</span>`;
            }}
            container.innerHTML = html;
        }}

        // Render all
        groups.forEach(renderSourceTagsYear);
        cards.forEach(renderTagsYear);

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
    </script>
</body>
</html>
'''

    gallery_path = output_dir / "gallery.html"
    gallery_path.write_text(html)
    print(f"  Gallery: {gallery_path}")
