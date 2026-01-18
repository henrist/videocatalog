// userEdits is defined in the HTML template before this script

const modal = document.getElementById('modal');
const player1 = document.getElementById('player1');
const player2 = document.getElementById('player2');
let activePlayer = player1;
let preloadPlayer = player2;
const search = document.getElementById('search');
const expandBtn = document.getElementById('expandAll');
const collapseBtn = document.getElementById('collapseGroups');
const cards = document.querySelectorAll('.video-card');
const groups = document.querySelectorAll('.source-group');
const tagFiltersEl = document.getElementById('tagFilters');
const showHiddenBtn = document.getElementById('showHiddenBtn');
let activeFilters = new Set();
let showHidden = false;

// Check if clip has any Skjul:* tag
function isClipHidden(source, clipName) {
  const { tags } = getResolvedMeta(source, clipName);
  return tags.some(t => t.name.startsWith('Skjul:'));
}

// Count clips with Skjul:* tags
function getHiddenClipCount() {
  let count = 0;
  cards.forEach(card => {
    if (isClipHidden(card.dataset.source, card.dataset.clip)) count++;
  });
  return count;
}

// Check if a clip is within a group's range (uses string comparison since names are sortable)
function clipInGroup(clipName, group) {
  return clipName >= group.start_clip && clipName <= group.end_clip;
}

// Find the group containing a clip
function findGroupForClip(source, clipName) {
  const edits = userEdits[source] || {};
  const groups = edits.groups || [];
  return groups.find(g => clipInGroup(clipName, g));
}

// Get resolved tags and year for a clip (with inheritance: video -> group -> clip)
function getResolvedMeta(source, clipName) {
  const edits = userEdits[source] || {};
  const videoMeta = edits.video || {};
  const clipMeta = (edits.clips || {})[clipName] || {};
  const group = findGroupForClip(source, clipName);
  const groupMeta = group || {};

  // Start with video tags (marked inherited)
  let baseTags = (videoMeta.tags || []).map(t => ({...t, inherited: true, inheritedFrom: 'video'}));

  // Override with group tags
  if (group) {
    const groupTags = (groupMeta.tags || []).map(t => ({...t, inherited: true, inheritedFrom: 'group'}));
    const groupTagNames = new Set(groupTags.map(t => t.name));
    baseTags = [
      ...groupTags,
      ...baseTags.filter(t => !groupTagNames.has(t.name))
    ];
  }

  // Override with clip tags
  const clipTags = clipMeta.tags || [];
  const clipTagNames = new Set(clipTags.map(t => t.name));
  const mergedTags = [
    ...clipTags,
    ...baseTags.filter(t => !clipTagNames.has(t.name))
  ];

  // Year: clip overrides group overrides video
  let year = clipMeta.year || null;
  let yearInherited = false;
  let yearInheritedFrom = null;
  if (!year && groupMeta.year) {
    year = groupMeta.year;
    yearInherited = true;
    yearInheritedFrom = 'group';
  }
  if (!year && videoMeta.year) {
    year = videoMeta.year;
    yearInherited = true;
    yearInheritedFrom = 'video';
  }

  return { tags: mergedTags, year, yearInherited, yearInheritedFrom, group };
}

// Render tags and year for a clip
function renderTagsYear(container) {
  const source = container.dataset.source;
  const clipName = container.dataset.clip;
  const { tags, year, yearInherited } = getResolvedMeta(source, clipName);

  let html = '';
  for (let i = 0; i < tags.length; i++) {
    const tag = tags[i];
    const inherited = tag.inherited ? ' inherited' : '';
    const inheritedText = tag.inherited ? ' (inherited from video)' : '';
    const title = `${tag.confidence} confidence${inheritedText}. Click to edit.`;
    const editable = !tag.inherited ? ' editable' : '';
    html += `<span class="tag confidence-${tag.confidence}${inherited}${editable}" title="${title}" data-idx="${i}" data-name="${tag.name}" data-conf="${tag.confidence}" data-inherited="${tag.inherited || false}">${tag.name}</span>`;
  }
  if (year) {
    const inherited = yearInherited ? ' inherited' : '';
    const inheritedText = yearInherited ? ' (inherited from video)' : '';
    const title = `${year.confidence} confidence${inheritedText}. Click to edit.`;
    const editable = !yearInherited ? ' editable' : '';
    html += `<span class="year-badge confidence-${year.confidence}${inherited}${editable}" title="${title}" data-year="${year.year}" data-conf="${year.confidence}" data-inherited="${yearInherited}">${year.year}</span>`;
  }
  // Add buttons (only shown when API available)
  html += `<button class="add-btn" data-action="add-tag">+tag</button>`;
  if (!year || yearInherited) {
    html += `<button class="add-btn" data-action="add-year">+year</button>`;
  }
  container.innerHTML = html;
}

// Render video-level tags/year in source headers
function renderSourceTagsYear(container) {
  const source = container.dataset.source;
  const edits = userEdits[source] || {};
  const videoMeta = edits.video || {};
  const tags = videoMeta.tags || [];
  const year = videoMeta.year;

  let html = '';
  for (let i = 0; i < tags.length; i++) {
    const tag = tags[i];
    const title = `${tag.confidence} confidence. Click to edit.`;
    html += `<span class="tag confidence-${tag.confidence} editable" title="${title}" data-idx="${i}" data-name="${tag.name}" data-conf="${tag.confidence}">${tag.name}</span>`;
  }
  if (year) {
    const title = `${year.confidence} confidence. Click to edit.`;
    html += `<span class="year-badge confidence-${year.confidence} editable" title="${title}" data-year="${year.year}" data-conf="${year.confidence}">${year.year}</span>`;
  }
  // Add buttons (only shown when API available)
  html += `<button class="add-btn" data-action="add-tag">+tag</button>`;
  if (!year) {
    html += `<button class="add-btn" data-action="add-year">+year</button>`;
  }
  container.innerHTML = html;
}

// Render source descriptions
function renderSourceDescription(container) {
  const source = container.dataset.source;
  const edits = userEdits[source] || {};
  const desc = edits.video?.description || '';
  container.textContent = desc;
  // Hide +description button if description exists
  const addBtn = document.querySelector(`.add-desc-btn[data-source="${source}"]`);
  if (addBtn) addBtn.style.display = desc ? 'none' : '';
}

function showDescriptionEditor(container) {
  const source = container.dataset.source;
  const groupId = container.dataset.groupId || null;
  const isGroup = !!groupId;

  const edits = userEdits[source] || {};
  let desc;
  if (isGroup) {
    const group = (edits.groups || []).find(g => g.id === groupId);
    desc = group?.description || '';
  } else {
    desc = edits.video?.description || '';
  }

  container.classList.add('editing');
  container.innerHTML = `<textarea placeholder="Add a description...">${desc}</textarea>`;
  const textarea = container.querySelector('textarea');
  textarea.focus();

  const renderFn = isGroup ? renderGroupDescription : renderSourceDescription;

  textarea.addEventListener('blur', async () => {
    const newDesc = textarea.value.trim();
    const meta = getCurrentMeta(source, null, groupId);
    meta.description = newDesc || null;
    await saveEdits(source, null, meta, groupId);
    container.classList.remove('editing');
    renderFn(container);
  });
  textarea.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      container.classList.remove('editing');
      renderFn(container);
    }
  });
}

// Group creation/edit state
// mode: 'create' | 'edit-range'
let groupMode = null; // null | {source, startClip, startCard, mode, groupId}

// Tag paint mode state
let tagMode = null; // null | {name, confidence, action: 'add'|'remove'}

const tagModeToolbar = document.getElementById('tagModeToolbar');
const tagModeName = document.getElementById('tagModeName');
const tagModeDoneBtn = document.getElementById('tagModeDone');

function startTagModeFromTag(name, confidence) {
  tagMode = { name, confidence, action: 'add' };
  tagModeName.textContent = name;
  // Reset action buttons to 'add'
  tagModeToolbar.querySelectorAll('.tag-mode-action .action-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.action === 'add');
  });
  tagModeToolbar.classList.remove('collapsed');
  document.body.classList.add('tag-mode', 'tag-action-add');
  closePopup();
}

function cancelTagMode() {
  document.body.classList.remove('tag-mode', 'tag-action-add', 'tag-action-remove');
  tagModeToolbar.classList.add('collapsed');
  tagMode = null;
}

async function handleTagModeClick(card) {
  const source = card.dataset.source;
  const clipName = card.dataset.clip;
  const meta = getCurrentMeta(source, clipName);
  const existingIdx = (meta.tags || []).findIndex(t => t.name === tagMode.name);

  if (tagMode.action === 'add') {
    if (existingIdx === -1) {
      meta.tags = meta.tags || [];
      meta.tags.push({ name: tagMode.name, confidence: tagMode.confidence });
      await saveEdits(source, clipName, meta);
    }
  } else {
    if (existingIdx !== -1) {
      meta.tags.splice(existingIdx, 1);
      await saveEdits(source, clipName, meta);
    }
  }
}

// Tag mode toolbar events
tagModeDoneBtn.addEventListener('click', cancelTagMode);

tagModeToolbar.querySelectorAll('.tag-mode-action .action-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    tagModeToolbar.querySelectorAll('.tag-mode-action .action-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    if (tagMode) {
      document.body.classList.remove('tag-action-add', 'tag-action-remove');
      tagMode.action = btn.dataset.action;
      document.body.classList.add(`tag-action-${tagMode.action}`);
    }
  });
});

function startGroupMode(source) {
  groupMode = { source, startClip: null, startCard: null, mode: 'create' };
  document.body.classList.add('group-mode');
  // Update button text
  const btn = document.querySelector(`.start-group-btn[data-source="${source}"]`);
  if (btn) btn.textContent = 'Select first clip...';
}

function startEditRangeMode(source, groupId) {
  groupMode = { source, startClip: null, startCard: null, mode: 'edit-range', groupId };
  document.body.classList.add('group-mode');
  // Update edit range button text
  const btn = document.querySelector(`.edit-range-btn[data-source="${source}"][data-group-id="${groupId}"]`);
  if (btn) btn.textContent = 'Select first clip...';
}

function cancelGroupMode() {
  // Reset visual state on cards
  groupMode?.startCard?.classList.remove('group-start');
  document.querySelectorAll('.video-card.group-pending').forEach(c => c.classList.remove('group-pending'));

  // Reset button texts (must happen before clearing groupMode)
  if (groupMode?.mode === 'edit-range' && groupMode?.groupId) {
    const btn = document.querySelector(`.edit-range-btn[data-group-id="${groupMode.groupId}"]`);
    if (btn) btn.textContent = 'edit range';
  }
  document.querySelectorAll('.start-group-btn').forEach(btn => btn.textContent = '+group');

  // Clear state
  groupMode = null;
  document.body.classList.remove('group-mode');
}

function handleGroupModeClick(card) {
  const source = card.dataset.source;
  const clipName = card.dataset.clip;

  // Must be same source
  if (source !== groupMode.source) {
    alert('Please select clips from the same video source');
    return;
  }

  if (!groupMode.startClip) {
    // First clip selected
    groupMode.startClip = clipName;
    groupMode.startCard = card;
    card.classList.add('group-start');
    // Update appropriate button text
    if (groupMode.mode === 'edit-range') {
      const btn = document.querySelector(`.edit-range-btn[data-group-id="${groupMode.groupId}"]`);
      if (btn) btn.textContent = 'Select last clip...';
    } else {
      const btn = document.querySelector(`.start-group-btn[data-source="${source}"]`);
      if (btn) btn.textContent = 'Select last clip...';
    }
  } else {
    // Second clip selected - validate range
    const actualStart = groupMode.startClip <= clipName ? groupMode.startClip : clipName;
    const actualEnd = groupMode.startClip <= clipName ? clipName : groupMode.startClip;

    // Check for overlapping groups (skip the group being edited)
    const edits = userEdits[source] || {};
    const existingGroups = edits.groups || [];
    for (const g of existingGroups) {
      if (groupMode.mode === 'edit-range' && g.id === groupMode.groupId) continue;
      if (!(actualEnd < g.start_clip || actualStart > g.end_clip)) {
        alert('This range overlaps with an existing group. Delete the existing group first.');
        return;
      }
    }

    if (groupMode.mode === 'edit-range') {
      // Update existing group range
      updateGroupRange(source, groupMode.groupId, groupMode.startClip, clipName);
    } else {
      // Create new group
      createGroup(source, groupMode.startClip, clipName);
    }
    cancelGroupMode();
  }
}

async function updateGroupRange(source, groupId, startClip, endClip) {
  const actualStartClip = startClip <= endClip ? startClip : endClip;
  const actualEndClip = startClip <= endClip ? endClip : startClip;

  const edits = userEdits[source] || {};
  const group = (edits.groups || []).find(g => g.id === groupId);
  if (group) {
    group.start_clip = actualStartClip;
    group.end_clip = actualEndClip;
    await saveEditsRaw(source, edits);
    renderGroupsForSource(source);
  }
}

async function createGroup(source, startClip, endClip) {
  // Ensure start <= end (string comparison works with sortable names)
  const actualStartClip = startClip <= endClip ? startClip : endClip;
  const actualEndClip = startClip <= endClip ? endClip : startClip;

  const edits = userEdits[source] || { video: { tags: [], year: null, description: null }, groups: [], clips: {} };
  if (!edits.groups) edits.groups = [];

  const groupId = 'group_' + edits.groups.length;
  edits.groups.push({
    id: groupId,
    start_clip: actualStartClip,
    end_clip: actualEndClip,
    tags: [],
    year: null,
    description: null
  });

  await saveEditsRaw(source, edits);
  renderGroupsForSource(source);
}

// Render group tags/year
function renderGroupTagsYear(container) {
  const source = container.dataset.source;
  const groupId = container.dataset.groupId;
  const edits = userEdits[source] || {};
  const group = (edits.groups || []).find(g => g.id === groupId);
  if (!group) return;

  const tags = group.tags || [];
  const year = group.year;

  let html = '';
  for (let i = 0; i < tags.length; i++) {
    const tag = tags[i];
    const title = `${tag.confidence} confidence. Click to edit.`;
    html += `<span class="tag confidence-${tag.confidence} editable" title="${title}" data-idx="${i}" data-name="${tag.name}" data-conf="${tag.confidence}">${tag.name}</span>`;
  }
  if (year) {
    const title = `${year.confidence} confidence. Click to edit.`;
    html += `<span class="year-badge confidence-${year.confidence} editable" title="${title}" data-year="${year.year}" data-conf="${year.confidence}">${year.year}</span>`;
  }
  // Add buttons
  html += `<button class="add-btn" data-action="add-tag">+tag</button>`;
  if (!year) {
    html += `<button class="add-btn" data-action="add-year">+year</button>`;
  }
  container.innerHTML = html;
}

// Render group description
function renderGroupDescription(container) {
  const source = container.dataset.source;
  const groupId = container.dataset.groupId;
  const edits = userEdits[source] || {};
  const group = (edits.groups || []).find(g => g.id === groupId);
  const desc = group?.description || '';
  container.textContent = desc;
  // Hide +description button if description exists
  const addBtn = container.closest('.clip-group')?.querySelector('.add-group-desc-btn');
  if (addBtn) addBtn.style.display = desc ? 'none' : '';
}

// Render all groups for a source (interleaved with ungrouped clips in natural order)
function renderGroupsForSource(source) {
  const sourceGroup = document.querySelector(`.source-group[data-source="${source}"]`);
  if (!sourceGroup) return;

  // Save scroll position before DOM manipulation
  const savedScrollY = window.scrollY;

  // Collect all cards from both main gallery and existing clip-groups
  const allCards = Array.from(sourceGroup.querySelectorAll('.video-card'));

  // Remove existing clip-group divs
  sourceGroup.querySelectorAll('.clip-group').forEach(el => el.remove());

  const edits = userEdits[source] || {};
  const groups = edits.groups || [];
  const mainGallery = sourceGroup.querySelector(':scope > .gallery');

  // Sort cards by clip name (names are sortable timestamps)
  allCards.sort((a, b) => a.dataset.clip.localeCompare(b.dataset.clip));

  // Sort groups by start clip name
  const sortedGroups = [...groups].sort((a, b) => a.start_clip.localeCompare(b.start_clip));

  // Clear main gallery
  mainGallery.innerHTML = '';

  // Container to hold all content (groups and ungrouped galleries)
  const contentContainer = document.createDocumentFragment();

  // Track which cards have been placed
  const placedCards = new Set();

  // Build sections in order
  let currentUngrouped = [];

  for (const card of allCards) {
    const clipName = card.dataset.clip;

    // Check if this card belongs to a group (string comparison)
    const group = sortedGroups.find(g => clipName >= g.start_clip && clipName <= g.end_clip);

    if (group) {
      // Flush any pending ungrouped cards first
      if (currentUngrouped.length > 0) {
        const ungroupedGallery = document.createElement('div');
        ungroupedGallery.className = 'gallery';
        currentUngrouped.forEach(c => ungroupedGallery.appendChild(c));
        contentContainer.appendChild(ungroupedGallery);
        currentUngrouped = [];
      }

      // Check if we already created this group
      if (!placedCards.has(group.id)) {
        placedCards.add(group.id);

        // Get all cards for this group (string comparison)
        const groupCards = allCards.filter(c =>
          c.dataset.clip >= group.start_clip && c.dataset.clip <= group.end_clip
        );

        // Create group container
        const groupDiv = document.createElement('div');
        groupDiv.className = 'clip-group';
        groupDiv.dataset.source = source;
        groupDiv.dataset.groupId = group.id;

        groupDiv.innerHTML = `
          <div class="clip-group-header">
            <span class="toggle-icon" onclick="this.closest('.clip-group').classList.toggle('collapsed')">▼</span>
            <span class="group-name">${group.start_clip} - ${group.end_clip}</span>
            <div class="group-tags-year" data-source="${source}" data-group-id="${group.id}"></div>
            <span class="add-group-desc-btn add-desc-btn" data-source="${source}" data-group-id="${group.id}">+description</span>
            <div class="group-actions">
              <span class="edit-range-btn" data-source="${source}" data-group-id="${group.id}">edit range</span>
              <span class="delete-group-btn" data-source="${source}" data-group-id="${group.id}">×</span>
            </div>
          </div>
          <div class="group-description" data-source="${source}" data-group-id="${group.id}"></div>
          <div class="gallery"></div>
        `;

        // Move cards into group
        const groupGallery = groupDiv.querySelector('.gallery');
        groupCards.forEach(c => {
          groupGallery.appendChild(c);
          placedCards.add(c.dataset.clip);
        });

        contentContainer.appendChild(groupDiv);

        // Render group tags/year/description
        renderGroupTagsYear(groupDiv.querySelector('.group-tags-year'));
        renderGroupDescription(groupDiv.querySelector('.group-description'));
      }
    } else {
      // Ungrouped card
      if (!placedCards.has(card.dataset.clip)) {
        currentUngrouped.push(card);
        placedCards.add(card.dataset.clip);
      }
    }
  }

  // Flush any remaining ungrouped cards
  if (currentUngrouped.length > 0) {
    const ungroupedGallery = document.createElement('div');
    ungroupedGallery.className = 'gallery';
    currentUngrouped.forEach(c => ungroupedGallery.appendChild(c));
    contentContainer.appendChild(ungroupedGallery);
  }

  // Replace main gallery with new content
  mainGallery.replaceWith(contentContainer);

  // Restore scroll position after DOM manipulation
  window.scrollTo(0, savedScrollY);

  // Re-render all clip tags (inheritance may have changed)
  sourceGroup.querySelectorAll('.video-card .tags-year').forEach(renderTagsYear);
}

async function deleteGroup(source, groupId) {
  const edits = userEdits[source] || {};
  edits.groups = (edits.groups || []).filter(g => g.id !== groupId);
  await saveEditsRaw(source, edits);
  renderGroupsForSource(source);
}

// Raw save that doesn't separate video/clip
async function saveEditsRaw(source, edits) {
  try {
    const resp = await fetch(`/api/edits/${source}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(edits)
    });
    if (resp.ok) {
      userEdits[source] = edits;
      renderTagFilters();
      if (typeof updateNavGroupCounts === 'function') updateNavGroupCounts();
    }
  } catch (e) {
    console.error('Save failed:', e);
  }
}

// Render all tags/year
document.querySelectorAll('.source-tags-year').forEach(renderSourceTagsYear);
document.querySelectorAll('.video-card .tags-year').forEach(renderTagsYear);
document.querySelectorAll('.source-description').forEach(renderSourceDescription);

// Render all groups
groups.forEach(g => renderGroupsForSource(g.dataset.source));

// Build search data including tags
const cardData = Array.from(cards).map((card, i) => {
  const source = card.dataset.source;
  const clipName = card.dataset.clip;
  const { tags } = getResolvedMeta(source, clipName);
  return {
    idx: i,
    transcript: card.dataset.transcript,
    tags: tags.map(t => t.name).join(' ')
  };
});
const fuse = new Fuse(cardData, {
  keys: ['transcript', 'tags'],
  threshold: 0.4,
  ignoreLocation: true,
  includeMatches: true,
  minMatchCharLength: 2
});

let currentCard = null;
let nextCard = null;
const modalInfo = document.getElementById('modalInfo');

function updateModalInfo(card) {
  const name = card.querySelector('.video-name').textContent;
  const tagsEl = card.querySelector('.tags-year');
  const tagSpans = tagsEl ? Array.from(tagsEl.querySelectorAll('.tag, .year-badge')).map(el => el.textContent).join(' ') : '';
  modalInfo.innerHTML = `<div class="clip-name">${name}</div>` +
    (tagSpans ? `<div class="clip-tags">${tagSpans}</div>` : '');
}

function playVideo(card) {
  // In group mode, select clip instead of playing
  if (groupMode) {
    handleGroupModeClick(card);
    return;
  }

  currentCard = card;
  nextCard = getNextCardInGroup(card);

  // Check if preloadPlayer already has this video ready
  if (preloadPlayer.src.endsWith(card.dataset.video) && preloadPlayer.readyState >= 3) {
    // Swap players
    activePlayer.pause();
    activePlayer.style.display = 'none';
    preloadPlayer.style.display = '';
    [activePlayer, preloadPlayer] = [preloadPlayer, activePlayer];
    activePlayer.play();
  } else {
    activePlayer.src = card.dataset.video;
    activePlayer.play();
  }

  updateModalInfo(card);
  modal.classList.add('active');

  // Start preloading next
  if (nextCard) {
    preloadPlayer.src = nextCard.dataset.video;
    preloadPlayer.load();
  }
}

function getNextCardInGroup(card) {
  // Check if card is in a clip-group (then only play within that group)
  const clipGroup = card.closest('.clip-group');
  if (clipGroup) {
    const groupCards = Array.from(clipGroup.querySelectorAll('.video-card:not(.hidden)'));
    const idx = groupCards.indexOf(card);
    return groupCards[idx + 1] || null;  // Stop at group boundary
  }

  // Card is ungrouped - play through ungrouped clips in its gallery section
  const mainGallery = card.closest('.gallery');
  if (mainGallery) {
    const ungroupedCards = Array.from(mainGallery.querySelectorAll('.video-card:not(.hidden)'));
    const idx = ungroupedCards.indexOf(card);
    return ungroupedCards[idx + 1] || null;
  }

  return null;
}

function handleEnded() {
  if (nextCard) {
    // Swap to preloaded player immediately
    activePlayer.style.display = 'none';
    preloadPlayer.style.display = '';
    [activePlayer, preloadPlayer] = [preloadPlayer, activePlayer];

    currentCard = nextCard;
    nextCard = getNextCardInGroup(currentCard);
    updateModalInfo(currentCard);
    activePlayer.play();

    // Preload the next one
    if (nextCard) {
      preloadPlayer.src = nextCard.dataset.video;
      preloadPlayer.load();
    }
  } else {
    closeModal({target: modal});
  }
}

player1.onended = handleEnded;
player2.onended = handleEnded;

function closeModal(e) {
  if (e.target === modal || e.target.classList.contains('modal-close')) {
    modal.classList.remove('active');
    player1.pause();
    player2.pause();
    player1.src = '';
    player2.src = '';
    player1.style.display = '';
    player2.style.display = 'none';
    activePlayer = player1;
    preloadPlayer = player2;
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

// Combined filter: text search AND tag filters AND hidden toggle
function applyAllFilters() {
  const q = search.value.trim();
  const searchMatches = q ? new Set(fuse.search(q).map(r => r.item.idx)) : null;
  const filteringByHiddenTag = [...activeFilters].some(f => f.startsWith('Skjul:'));

  cards.forEach((card, i) => {
    // Check text search
    let matchesSearch = true;
    if (searchMatches !== null) {
      matchesSearch = searchMatches.has(i);
    }

    // Check tag filters
    let matchesTags = true;
    if (activeFilters.size > 0) {
      const { tags } = getResolvedMeta(card.dataset.source, card.dataset.clip);
      const cardTagNames = new Set(tags.map(t => t.name));
      matchesTags = [...activeFilters].every(f => cardTagNames.has(f));
    }

    // Check hidden clips (show if toggle on OR filtering by a Skjul: tag)
    let matchesHidden = true;
    if (!showHidden && !filteringByHiddenTag && isClipHidden(card.dataset.source, card.dataset.clip)) {
      matchesHidden = false;
    }

    card.classList.toggle('hidden', !(matchesSearch && matchesTags && matchesHidden));

    // Update transcript highlighting
    const transcriptEl = card.querySelector('.transcript');
    if (q && matchesSearch) {
      const result = fuse.search(q).find(r => r.item.idx === i);
      transcriptEl.innerHTML = highlightMatches(card.dataset.transcript, result?.matches);
    } else {
      transcriptEl.textContent = card.dataset.transcript;
    }
  });

  // Hide empty clip-groups
  document.querySelectorAll('.clip-group').forEach(group => {
    const visibleCards = group.querySelectorAll('.video-card:not(.hidden)').length;
    group.classList.toggle('hidden', visibleCards === 0);
  });

  // Update source-group counts and visibility
  document.querySelectorAll('.source-group').forEach(group => {
    const visibleCards = group.querySelectorAll('.video-card:not(.hidden)').length;
    const totalCards = group.querySelectorAll('.video-card').length;
    const countEl = group.querySelector('.clip-count');
    if (visibleCards === totalCards) {
      countEl.textContent = `${totalCards} clips`;
    } else {
      countEl.textContent = `${visibleCards}/${totalCards} clips`;
    }
    group.classList.toggle('hidden', visibleCards === 0);
  });

  groups.forEach(g => g.classList.remove('collapsed'));
}

search.addEventListener('input', applyAllFilters);

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

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if (groupMode) cancelGroupMode();
    else if (tagMode) cancelTagMode();
    else closeModal({target: modal});
  }
});

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

function showTagPopup(element, source, clipName, tagIdx, tagName, tagConf, groupId = null) {
  popupContext = { source, clipName, groupId, type: 'edit-tag', tagIdx, tagName, tagConf };
  popup.innerHTML = `
    <div class="popup-row">
      <input type="text" class="tag-name-input" value="${tagName}">
    </div>
    <div class="popup-row">
      ${confButtons(tagConf)}
      <button class="delete-btn">Delete</button>
      <button class="batch-btn">Batch</button>
    </div>
  `;
  positionPopup(element);
  popup.classList.add('active');
  popup.querySelector('.tag-name-input').focus();
}

function showYearPopup(element, source, clipName, year, conf, groupId = null) {
  popupContext = { source, clipName, groupId, type: 'edit-year' };
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

function showAddTagPopup(element, source, clipName, groupId = null) {
  popupContext = { source, clipName, groupId, type: 'add-tag' };
  popup.innerHTML = `
    <div class="popup-row">
      <input type="text" class="tag-name-input" placeholder="Tag name">
    </div>
    <div class="popup-row">
      ${confButtons('high')}
      <button class="batch-btn">Batch</button>
    </div>
  `;
  positionPopup(element);
  popup.classList.add('active');
  popup.querySelector('.tag-name-input').focus();
}

function showAddYearPopup(element, source, clipName, groupId = null) {
  popupContext = { source, clipName, groupId, type: 'add-year' };
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

async function saveEdits(source, clipName, newMeta, groupId = null) {
  const edits = userEdits[source] || { video: { tags: [], year: null, description: null }, groups: [], clips: {} };
  if (groupId) {
    // Update group metadata
    const group = (edits.groups || []).find(g => g.id === groupId);
    if (group) {
      group.tags = newMeta.tags || [];
      group.year = newMeta.year || null;
      if (newMeta.description !== undefined) group.description = newMeta.description;
    }
  } else if (clipName) {
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
      if (groupId) {
        const container = document.querySelector(`.group-tags-year[data-source="${source}"][data-group-id="${groupId}"]`);
        if (container) renderGroupTagsYear(container);
        // Update all clips in this source (inheritance may have changed)
        document.querySelectorAll(`.tags-year[data-source="${source}"]`).forEach(renderTagsYear);
      } else if (clipName) {
        const container = document.querySelector(`.tags-year[data-source="${source}"][data-clip="${clipName}"]`);
        if (container) renderTagsYear(container);
      } else {
        const container = document.querySelector(`.source-tags-year[data-source="${source}"]`);
        if (container) renderSourceTagsYear(container);
        // Also update all clips in this group (inheritance may have changed)
        document.querySelectorAll(`.tags-year[data-source="${source}"]`).forEach(renderTagsYear);
      }
      // Update tag filters
      renderTagFilters();
    }
  } catch (e) {
    console.error('Save failed:', e);
  }
}

function getCurrentMeta(source, clipName, groupId = null) {
  const edits = userEdits[source] || { video: { tags: [], year: null, description: null }, groups: [], clips: {} };
  if (groupId) {
    const group = (edits.groups || []).find(g => g.id === groupId);
    return group ? { tags: group.tags || [], year: group.year, description: group.description } : { tags: [], year: null };
  }
  if (clipName) {
    return edits.clips[clipName] || { tags: [], year: null };
  }
  return edits.video || { tags: [], year: null, description: null };
}

// Handle clicks on tags-year containers (event delegation)
document.addEventListener('click', async (e) => {
  const tag = e.target.closest('.tag.editable');
  const yearBadge = e.target.closest('.year-badge.editable');
  const addBtn = e.target.closest('.add-btn');
  const addDescBtn = e.target.closest('.add-desc-btn:not(.add-group-desc-btn)');
  const addGroupDescBtn = e.target.closest('.add-group-desc-btn');
  const sourceDesc = e.target.closest('.source-description:not(.editing)');
  const groupDesc = e.target.closest('.group-description:not(.editing)');
  const confBtn = e.target.closest('.conf-btn');
  const deleteBtn = e.target.closest('.delete-btn');
  const startGroupBtn = e.target.closest('.start-group-btn');
  const deleteGroupBtn = e.target.closest('.delete-group-btn');
  const editRangeBtn = e.target.closest('.edit-range-btn');
  const videoCard = e.target.closest('.video-card');

  // Handle group mode clicks (intercept all clicks on video cards)
  if (groupMode && videoCard) {
    e.preventDefault();
    e.stopPropagation();
    handleGroupModeClick(videoCard);
    return;
  }

  // Handle tag mode clicks (intercept all clicks on video cards)
  if (tagMode && videoCard) {
    e.preventDefault();
    e.stopPropagation();
    await handleTagModeClick(videoCard);
    return;
  }

  // Handle source header click for expand/collapse (anywhere except action elements)
  const sourceHeader = e.target.closest('.source-header');
  if (sourceHeader && !e.target.closest('.tag, .year-badge, .add-btn, .add-desc-btn, .start-group-btn')) {
    toggleGroup(sourceHeader);
    return;
  }

  // Handle group header click for expand/collapse
  const groupHeader = e.target.closest('.clip-group-header');
  if (groupHeader && !e.target.closest('.tag, .year-badge, .add-btn, .add-desc-btn, .group-actions')) {
    groupHeader.closest('.clip-group').classList.toggle('collapsed');
    return;
  }

  // Handle thumb-grid clicks to play video
  const thumbGrid = e.target.closest('.thumb-grid');
  if (thumbGrid && videoCard) {
    playVideo(videoCard);
    return;
  }

  // Handle start group button
  if (startGroupBtn) {
    const source = startGroupBtn.dataset.source;
    if (groupMode) {
      cancelGroupMode();
    } else {
      startGroupMode(source);
    }
    return;
  }

  // Handle delete group button
  if (deleteGroupBtn) {
    const source = deleteGroupBtn.dataset.source;
    const groupId = deleteGroupBtn.dataset.groupId;
    if (confirm('Delete this group? Clips will remain but group metadata will be lost.')) {
      await deleteGroup(source, groupId);
    }
    return;
  }

  // Handle edit range button
  if (editRangeBtn) {
    const source = editRangeBtn.dataset.source;
    const groupId = editRangeBtn.dataset.groupId;
    if (groupMode) {
      cancelGroupMode();
    } else {
      startEditRangeMode(source, groupId);
    }
    return;
  }

  // Handle +description button for groups
  if (addGroupDescBtn) {
    const source = addGroupDescBtn.dataset.source;
    const groupId = addGroupDescBtn.dataset.groupId;
    const descContainer = document.querySelector(`.group-description[data-source="${source}"][data-group-id="${groupId}"]`);
    if (descContainer) showDescriptionEditor(descContainer);
    return;
  }

  // Handle click on group description to edit
  if (groupDesc && document.body.classList.contains('api-available')) {
    showDescriptionEditor(groupDesc);
    return;
  }

  // Handle +description button
  if (addDescBtn) {
    const source = addDescBtn.dataset.source;
    const descContainer = document.querySelector(`.source-description[data-source="${source}"]`);
    if (descContainer) showDescriptionEditor(descContainer);
    return;
  }

  // Handle click on existing description to edit
  if (sourceDesc && document.body.classList.contains('api-available')) {
    showDescriptionEditor(sourceDesc);
    return;
  }

  // Close popup if clicking outside
  if (!e.target.closest('.inline-popup') && !tag && !yearBadge && !addBtn) {
    if (popup.classList.contains('active')) {
      // Save any pending input before closing
      await handlePopupSave();
      closePopup();
    }
    // Don't return here - allow other handlers to process
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
    const { source, clipName, groupId, type, tagIdx } = popupContext;
    const meta = getCurrentMeta(source, clipName, groupId);

    if (type === 'edit-tag') {
      meta.tags = meta.tags.filter((_, i) => i !== tagIdx);
    } else if (type === 'edit-year') {
      meta.year = null;
    }

    await saveEdits(source, clipName, meta, groupId);
    closePopup();
    return;
  }

  // Handle batch button in popup
  const batchBtn = e.target.closest('.batch-btn');
  if (batchBtn && popupContext) {
    if (popupContext.type === 'edit-tag') {
      startTagModeFromTag(popupContext.tagName, popupContext.tagConf);
    } else if (popupContext.type === 'add-tag') {
      const nameInput = popup.querySelector('.tag-name-input');
      const tagName = nameInput?.value.trim();
      if (!tagName) { nameInput?.focus(); return; }
      const tagConf = popup.querySelector('.conf-btn.active')?.dataset.conf || 'high';
      // Save tag to current clip first
      const { source, clipName, groupId } = popupContext;
      const meta = getCurrentMeta(source, clipName, groupId);
      meta.tags = meta.tags || [];
      meta.tags.push({ name: tagName, confidence: tagConf });
      saveEdits(source, clipName, meta, groupId);
      startTagModeFromTag(tagName, tagConf);
    }
    return;
  }

  // Handle add buttons
  if (addBtn) {
    const container = addBtn.closest('.tags-year, .source-tags-year, .group-tags-year');
    const source = container.dataset.source;
    const clipName = container.dataset.clip || null;
    const groupId = container.dataset.groupId || null;
    const action = addBtn.dataset.action;

    closePopup();
    if (action === 'add-tag') {
      showAddTagPopup(addBtn, source, clipName, groupId);
    } else if (action === 'add-year') {
      showAddYearPopup(addBtn, source, clipName, groupId);
    }
    return;
  }

  // Handle tag click
  if (tag && tag.dataset.inherited !== 'true') {
    const container = tag.closest('.tags-year, .source-tags-year, .group-tags-year');
    const source = container.dataset.source;
    const clipName = container.dataset.clip || null;
    const groupId = container.dataset.groupId || null;
    closePopup();
    showTagPopup(tag, source, clipName, parseInt(tag.dataset.idx), tag.dataset.name, tag.dataset.conf, groupId);
    return;
  }

  // Handle year badge click
  if (yearBadge && yearBadge.dataset.inherited !== 'true') {
    const container = yearBadge.closest('.tags-year, .source-tags-year, .group-tags-year');
    const source = container.dataset.source;
    const clipName = container.dataset.clip || null;
    const groupId = container.dataset.groupId || null;
    closePopup();
    showYearPopup(yearBadge, source, clipName, yearBadge.dataset.year, yearBadge.dataset.conf, groupId);
    return;
  }
});

async function handlePopupSave() {
  if (!popupContext) return;

  const { source, clipName, groupId, type, tagIdx } = popupContext;
  const meta = getCurrentMeta(source, clipName, groupId);
  const activeConf = popup.querySelector('.conf-btn.active')?.dataset.conf || 'high';

  if (type === 'edit-tag') {
    const nameInput = popup.querySelector('.tag-name-input');
    const newName = nameInput?.value.trim();
    if (newName && meta.tags[tagIdx]) {
      meta.tags[tagIdx] = { name: newName, confidence: activeConf };
      await saveEdits(source, clipName, meta, groupId);
    }
  } else if (type === 'edit-year') {
    const yearInput = popup.querySelector('.year-input');
    const newYear = parseInt(yearInput?.value);
    if (newYear) {
      meta.year = { year: newYear, confidence: activeConf };
      await saveEdits(source, clipName, meta, groupId);
    }
  } else if (type === 'add-tag') {
    const nameInput = popup.querySelector('.tag-name-input');
    const newName = nameInput?.value.trim();
    if (newName) {
      meta.tags = meta.tags || [];
      meta.tags.push({ name: newName, confidence: activeConf });
      await saveEdits(source, clipName, meta, groupId);
    }
  } else if (type === 'add-year') {
    const yearInput = popup.querySelector('.year-input');
    const newYear = parseInt(yearInput?.value);
    if (newYear) {
      meta.year = { year: newYear, confidence: activeConf };
      await saveEdits(source, clipName, meta, groupId);
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

// Tag filtering
function getAllTags() {
  const regularTags = new Map(); // tag name -> count
  const hiddenTags = new Map();
  for (const source in userEdits) {
    const edits = userEdits[source];
    const videoTags = edits.video?.tags || [];
    videoTags.forEach(t => {
      const map = t.name.startsWith('Skjul:') ? hiddenTags : regularTags;
      map.set(t.name, (map.get(t.name) || 0) + 1);
    });
    // Include group tags
    const groups = edits.groups || [];
    groups.forEach(g => {
      (g.tags || []).forEach(t => {
        const map = t.name.startsWith('Skjul:') ? hiddenTags : regularTags;
        map.set(t.name, (map.get(t.name) || 0) + 1);
      });
    });
    for (const clipName in edits.clips || {}) {
      const clipTags = edits.clips[clipName]?.tags || [];
      clipTags.forEach(t => {
        const map = t.name.startsWith('Skjul:') ? hiddenTags : regularTags;
        map.set(t.name, (map.get(t.name) || 0) + 1);
      });
    }
  }
  return {
    regular: [...regularTags.entries()].sort((a, b) => b[1] - a[1]),
    hidden: [...hiddenTags.entries()].sort((a, b) => b[1] - a[1])
  };
}

function renderTagFilters() {
  const { regular, hidden } = getAllTags();
  const allTags = [...regular, ...hidden];
  tagFiltersEl.innerHTML = allTags.map(([name, count]) => {
    const isHidden = name.startsWith('Skjul:');
    return `<span class="filter-tag${activeFilters.has(name) ? ' active' : ''}${isHidden ? ' hidden-tag' : ''}" data-tag="${name}">${name} (${count})</span>`;
  }).join('');

  // Also update nav tags when filters are re-rendered (after edits)
  if (typeof renderNavTags === 'function') renderNavTags();
}

function updateHiddenToggle() {
  const count = getHiddenClipCount();
  showHiddenBtn.textContent = showHidden ? `Skjul (${count})` : `Vis skjulte (${count})`;
  showHiddenBtn.classList.toggle('active', showHidden);
  showHiddenBtn.style.display = count > 0 ? '' : 'none';
}

tagFiltersEl.addEventListener('click', (e) => {
  const filterTag = e.target.closest('.filter-tag');
  if (!filterTag) return;
  const tagName = filterTag.dataset.tag;
  if (activeFilters.has(tagName)) {
    activeFilters.delete(tagName);
  } else {
    activeFilters.add(tagName);
  }
  renderTagFilters();
  applyAllFilters();
});

// Show hidden toggle
showHiddenBtn.addEventListener('click', () => {
  showHidden = !showHidden;
  updateHiddenToggle();
  applyAllFilters();
});

renderTagFilters();
updateHiddenToggle();
applyAllFilters(); // Hide hidden clips on page load

// Lazy loading with two-tier debounce: fast for viewport, slow for preload zone
const viewportImages = new Set();
const preloadImages = new Set();
let viewportTimeout = null;
let preloadTimeout = null;

function loadImage(img) {
  if (img.dataset.src && img.classList.contains('lazy')) {
    img.onload = () => img.classList.remove('lazy');
    img.src = img.dataset.src;
    viewportObserver.unobserve(img);
    preloadObserver.unobserve(img);
  }
}

// Fast loading for images in actual viewport
const viewportObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      viewportImages.add(entry.target);
    } else {
      viewportImages.delete(entry.target);
    }
  });

  if (!viewportTimeout && viewportImages.size > 0) {
    viewportTimeout = setTimeout(() => {
      viewportImages.forEach(loadImage);
      viewportImages.clear();
      viewportTimeout = null;
    }, 100);
  }
});

// Slower preloading for images in margin
const preloadObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      preloadImages.add(entry.target);
    } else {
      preloadImages.delete(entry.target);
    }
  });

  if (!preloadTimeout && preloadImages.size > 0) {
    preloadTimeout = setTimeout(() => {
      preloadImages.forEach(loadImage);
      preloadImages.clear();
      preloadTimeout = null;
    }, 300);
  }
}, { rootMargin: `${Math.round(window.innerHeight * 1.5)}px 0px` });

// Observe all lazy images with both observers
document.querySelectorAll('.thumb-grid img.lazy').forEach(img => {
  viewportObserver.observe(img);
  preloadObserver.observe(img);
});

// Navigation panel functionality
function formatDuration(secs) {
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

// Initialize nav durations
document.querySelectorAll('.nav-duration').forEach(el => {
  const secs = parseInt(el.dataset.totalSecs) || 0;
  el.textContent = formatDuration(secs);
});

// Render nav tags and years (collecting from video, groups, clips)
function renderNavTags() {
  document.querySelectorAll('.nav-tags').forEach(container => {
    const source = container.dataset.source;
    const edits = userEdits[source] || {};
    const tags = edits.video?.tags || [];

    // Collect all years from video, groups, and clips
    const years = new Set();
    if (edits.video?.year) years.add(edits.video.year.year);
    (edits.groups || []).forEach(g => { if (g.year) years.add(g.year.year); });
    Object.values(edits.clips || {}).forEach(c => { if (c.year) years.add(c.year.year); });

    let html = '';
    if (years.size > 0) {
      const sorted = [...years].sort();
      const yearText = sorted.length === 1 ? sorted[0] :
        (sorted[sorted.length - 1] - sorted[0] === sorted.length - 1 ?
          `${sorted[0]}-${sorted[sorted.length - 1]}` : sorted.join(', '));
      html += `<span class="year-badge">${yearText}</span>`;
    }
    html += tags.map(t =>
      `<span class="tag confidence-${t.confidence}">${t.name}</span>`
    ).join('');
    container.innerHTML = html;
  });
}
renderNavTags();

// Update nav group counts
function updateNavGroupCounts() {
  document.querySelectorAll('.nav-item').forEach(item => {
    const source = item.dataset.source;
    const edits = userEdits[source] || {};
    const groupCount = (edits.groups || []).length;
    const groupEl = item.querySelector('.nav-group-count');
    if (groupEl) {
      groupEl.textContent = groupCount > 0 ? ` · ${groupCount} groups` : '';
    }
  });
}
updateNavGroupCounts();

// Click-to-scroll
document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', () => {
    const source = item.dataset.source;
    const sourceGroup = document.querySelector(`.source-group[data-source="${source}"]`);
    if (sourceGroup) {
      sourceGroup.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  });
});

// Active source highlighting via scroll position
const navItems = document.querySelectorAll('.nav-item');
const sourceGroups = document.querySelectorAll('.source-group');

function updateActiveNav() {
  const offset = 100; // pixels from viewport top to trigger
  let activeSource = null;

  // Find the last source group whose top has scrolled past the trigger point
  for (const group of sourceGroups) {
    if (group.classList.contains('hidden')) continue;
    if (group.getBoundingClientRect().top <= offset) {
      activeSource = group.dataset.source;
    }
  }

  // If nothing is past the trigger, use the first visible source
  if (!activeSource) {
    for (const group of sourceGroups) {
      if (!group.classList.contains('hidden')) {
        activeSource = group.dataset.source;
        break;
      }
    }
  }

  navItems.forEach(item => item.classList.toggle('active', item.dataset.source === activeSource));
}

window.addEventListener('scroll', updateActiveNav, { passive: true });
updateActiveNav();

// Update nav on filter changes
function updateNavCounts() {
  document.querySelectorAll('.nav-item').forEach(item => {
    const source = item.dataset.source;
    const sourceGroup = document.querySelector(`.source-group[data-source="${source}"]`);
    if (!sourceGroup) return;

    const visibleCards = sourceGroup.querySelectorAll('.video-card:not(.hidden)');
    const totalCards = sourceGroup.querySelectorAll('.video-card');
    const clipCountEl = item.querySelector('.nav-clip-count');

    // Update clip count
    if (visibleCards.length === totalCards.length) {
      clipCountEl.textContent = totalCards.length;
    } else {
      clipCountEl.textContent = `${visibleCards.length}/${totalCards.length}`;
    }

    // Calculate visible duration
    let visibleSecs = 0;
    visibleCards.forEach(card => {
      const durationEl = card.querySelector('.video-duration');
      if (durationEl) {
        const parts = durationEl.textContent.split(':');
        if (parts.length === 2) {
          visibleSecs += parseInt(parts[0]) * 60 + parseInt(parts[1]);
        } else if (parts.length === 3) {
          visibleSecs += parseInt(parts[0]) * 3600 + parseInt(parts[1]) * 60 + parseInt(parts[2]);
        }
      }
    });
    item.querySelector('.nav-duration').textContent = formatDuration(visibleSecs);

    // Dim if no visible clips
    item.classList.toggle('dimmed', visibleCards.length === 0);
  });
}

// Hook into existing filter function
const originalApplyAllFilters = applyAllFilters;
applyAllFilters = function() {
  originalApplyAllFilters();
  updateNavCounts();
  updateHiddenToggle();
};

// Update sticky offsets based on header height
function updateStickyOffsets() {
  const header = document.getElementById('stickyHeader');
  if (!header) return;
  const h = header.offsetHeight;
  document.documentElement.style.setProperty('--sticky-header-height', h + 'px');
  // Measure actual source header height
  const sourceHeader = document.querySelector('.source-header');
  const sh = sourceHeader ? sourceHeader.offsetHeight : 48;
  document.documentElement.style.setProperty('--sticky-header-plus-source', (h + sh) + 'px');
}
window.addEventListener('load', updateStickyOffsets);
window.addEventListener('resize', updateStickyOffsets);
updateStickyOffsets();
