(() => {
  const escapeHtml = (value) => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');

  const normalizeFilterText = (value) => String(value ?? '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim();
  const filterTokens = (value) => normalizeFilterText(value).split(' ').filter(Boolean);

  // STREAMFORGE_PANEL_RUNTIME_PREFIX_FIX_V2242:
  // Dynamic panel requests must stay under the configured Panel/API root
  // (for example /admin). Absolute /status.json bypasses that root and the
  // channel page silently loses uptime/HLS readiness updates.
  const panelRoot = String(window.STREAMFORGE_APP_ROOT || '').replace(/\/+$/, '');
  const panelUrl = (path) => `${panelRoot}${String(path || '').startsWith('/') ? '' : '/'}${path || ''}`;

  const present = (value) => value !== null && value !== undefined && value !== '';
  const formatBits = (value) => {
    if (!present(value) || Number(value) <= 0) return '—';
    const bits = Number(value);
    if (bits >= 1_000_000_000) return `${(bits / 1_000_000_000).toFixed(2)} Gb/s`;
    if (bits >= 1_000_000) return `${(bits / 1_000_000).toFixed(2)} Mb/s`;
    if (bits >= 1_000) return `${(bits / 1_000).toFixed(0)} kb/s`;
    return `${bits} b/s`;
  };
  const formatBytes = (value) => {
    if (!present(value) || Number(value) <= 0) return '—';
    const bytes = Number(value);
    if (bytes >= 1_073_741_824) return `${(bytes / 1_073_741_824).toFixed(2)} GiB`;
    if (bytes >= 1_048_576) return `${(bytes / 1_048_576).toFixed(2)} MiB`;
    if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
    return `${bytes} B`;
  };
  const formatDuration = (value) => {
    if (!present(value) || Number(value) <= 0) return 'Live / unknown';
    const seconds = Math.floor(Number(value));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const rest = seconds % 60;
    return [hours, minutes, rest].map((item) => String(item).padStart(2, '0')).join(':');
  };
  // STREAMFORGE_UI_DURATION_DAYS_V63R8:
  // Replica uptime uses day-aware formatting after 24 hours.
  const formatUptime = (value) => {
    let seconds = Math.max(0, Math.floor(Number(value) || 0));
    if (seconds < 60) return `${seconds}s`;
    const days = Math.floor(seconds / 86400);
    seconds %= 86400;
    const hours = Math.floor(seconds / 3600);
    seconds %= 3600;
    const minutes = Math.floor(seconds / 60);
    const rest = seconds % 60;
    if (days > 0) return `${days}d ${hours}h ${minutes}m`;
    if (hours > 0) return `${hours}h ${minutes}m`;
    return `${minutes}m ${rest}s`;
  };

  // STREAMFORGE_TOTAL_OUTPUT_MIBIT_V63R8:
  // Dashboard encoder bitrate is stored as kb/s. Promote totals to Mb/s at
  // 1024 kb/s so large aggregate values remain readable.
  const formatKbpsTotal = (value) => {
    const kbps = Math.max(0, Number(value) || 0);
    if (kbps >= 1024) return `${(kbps / 1024).toFixed(2)} Mb/s`;
    return `${Math.round(kbps)} kb/s`;
  };

  const copyText = async (value, sourceInput) => {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(value);
        return true;
      } catch (_) {}
    }

    const input = sourceInput || document.createElement('textarea');
    const temporary = !sourceInput;
    if (temporary) {
      input.value = value;
      input.setAttribute('readonly', '');
      input.style.position = 'fixed';
      input.style.opacity = '0';
      input.style.pointerEvents = 'none';
      document.body.appendChild(input);
    }

    try {
      input.removeAttribute('readonly');
      input.focus({preventScroll: true});
      input.select();
      if (typeof input.setSelectionRange === 'function') input.setSelectionRange(0, input.value.length);
      const copied = document.execCommand('copy');
      input.setAttribute('readonly', '');
      return copied;
    } catch (_) {
      return false;
    } finally {
      if (temporary) input.remove();
      else {
        input.setAttribute('readonly', '');
        if (typeof input.setSelectionRange === 'function') input.setSelectionRange(0, 0);
        input.blur();
      }
    }
  };


  const initCategoryRenameModal = () => {
    const modal = document.querySelector('[data-category-rename-modal]');
    if (!modal) return;
    const form = modal.querySelector('[data-category-rename-form]');
    const input = modal.querySelector('[data-category-rename-input]');
    const openButtons = document.querySelectorAll('[data-category-rename-open]');
    const closeButtons = modal.querySelectorAll('[data-category-rename-close]');
    let lastTrigger = null;
    const open = (button) => {
      if (!button || !form || !input) return;
      lastTrigger = button;
      const categoryId = button.getAttribute('data-category-id') || '0';
      const categoryName = button.getAttribute('data-category-name') || '';
      form.action = panelUrl(`/categories/${categoryId}/rename`); // STREAMFORGE_MAIN_NATIVE_DYNAMIC_ACTION_V48
      input.value = categoryName;
      modal.hidden = false;
      document.body.classList.add('category-rename-open');
      window.setTimeout(() => {
        input.focus();
        input.select();
      }, 0);
    };
    const close = () => {
      modal.hidden = true;
      document.body.classList.remove('category-rename-open');
      if (lastTrigger && typeof lastTrigger.focus === 'function') lastTrigger.focus();
    };
    openButtons.forEach((button) => button.addEventListener('click', () => open(button)));
    closeButtons.forEach((button) => button.addEventListener('click', close));
    modal.addEventListener('click', (event) => { if (event.target === modal) close(); });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !modal.hidden) close(); });
  };

  const appShell = document.querySelector('.app-shell');
  const sidebarToggle = document.querySelector('[data-sidebar-toggle]');
  if (appShell && sidebarToggle) {
    const sidebarStorageKey = 'streamforge.sidebar.collapsed';
    const mobileSidebar = window.matchMedia('(max-width: 760px)');
    let mobileOpen = false;
    let storedSidebarCollapsed = false;
    try { storedSidebarCollapsed = localStorage.getItem(sidebarStorageKey) === '1'; } catch (_) {}
    const setSidebarCollapsed = (collapsed) => {
      if (mobileSidebar.matches) {
        appShell.classList.remove('sidebar-collapsed');
        appShell.classList.toggle('mobile-nav-open', mobileOpen);
        sidebarToggle.textContent = mobileOpen ? '×' : '☰';
        sidebarToggle.title = mobileOpen ? 'Close menu' : 'Open menu';
        sidebarToggle.setAttribute('aria-label', mobileOpen ? 'Close menu' : 'Open menu');
        sidebarToggle.setAttribute('aria-expanded', mobileOpen ? 'true' : 'false');
        return;
      }
      appShell.classList.remove('mobile-nav-open');
      appShell.classList.toggle('sidebar-collapsed', collapsed);
      sidebarToggle.textContent = collapsed ? '❯' : '❮';
      sidebarToggle.title = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
      sidebarToggle.setAttribute('aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
      sidebarToggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    };
    setSidebarCollapsed(storedSidebarCollapsed);
    sidebarToggle.addEventListener('click', () => {
      if (mobileSidebar.matches) {
        mobileOpen = !mobileOpen;
        setSidebarCollapsed(storedSidebarCollapsed);
        return;
      }
      const collapsed = !appShell.classList.contains('sidebar-collapsed');
      setSidebarCollapsed(collapsed);
      try { localStorage.setItem(sidebarStorageKey, collapsed ? '1' : '0'); } catch (_) {}
    });
    appShell.querySelectorAll('.sidebar nav a').forEach((link) => link.addEventListener('click', () => {
      if (!mobileSidebar.matches) return;
      mobileOpen = false;
      setSidebarCollapsed(storedSidebarCollapsed);
    }));
    mobileSidebar.addEventListener?.('change', () => {
      mobileOpen = false;
      setSidebarCollapsed(storedSidebarCollapsed);
    });
    document.addEventListener('click', (event) => {
      if (!mobileSidebar.matches || !mobileOpen || event.target.closest('.sidebar')) return;
      mobileOpen = false;
      setSidebarCollapsed(storedSidebarCollapsed);
    });
  }

  document.querySelectorAll('[data-copy]').forEach((button) => {
    button.addEventListener('click', async () => {
      const input = button.parentElement?.querySelector('input');
      if (!input) return;
      const original = button.textContent;
      const copied = await copyText(input.value, input);
      button.textContent = copied ? 'Copied' : 'Copy failed';
      button.classList.toggle('copy-failed', !copied);
      setTimeout(() => {
        button.textContent = original;
        button.classList.remove('copy-failed');
      }, 1400);
    });
  });

  initCategoryRenameModal();

  const mainPlaylistContentMode = document.querySelector('[data-main-playlist-content-mode]');
  const mainPlaylistCustomChannels = document.querySelector('[data-main-playlist-custom-channels]');
  if (mainPlaylistContentMode && mainPlaylistCustomChannels) {
    const syncMainPlaylistContentMode = () => {
      const customSelected = mainPlaylistContentMode.value !== 'all';
      mainPlaylistCustomChannels.hidden = !customSelected;
      mainPlaylistCustomChannels.setAttribute('aria-hidden', customSelected ? 'false' : 'true');
      mainPlaylistCustomChannels.querySelectorAll('input,button,select,textarea').forEach((control) => {
        control.disabled = !customSelected;
      });
      if (customSelected) {
        const search = mainPlaylistCustomChannels.querySelector('[data-user-channel-search]');
        if (search) {
          search.disabled = false;
          search.dispatchEvent(new Event('input', { bubbles: true }));
        }
        mainPlaylistCustomChannels.dispatchEvent(new CustomEvent('streamforge:custom-channels-shown', { bubbles: true }));
      }
    };
    mainPlaylistContentMode.addEventListener('change', syncMainPlaylistContentMode);
    mainPlaylistContentMode.addEventListener('input', syncMainPlaylistContentMode);
    window.addEventListener('pageshow', syncMainPlaylistContentMode);
    syncMainPlaylistContentMode();
  }

  const userChannelList = document.querySelector('[data-user-channel-list]');
  if (userChannelList) {
    const channelSection = userChannelList.closest('[data-playlist-channel-section], [data-custom-channel-options], .form-section') || document;
    const channelChecks = Array.from(userChannelList.querySelectorAll('input[name="channel_ids"]'));
    const channelLabels = channelChecks.map((checkbox) => checkbox.closest('label')).filter(Boolean);
    const selectEnabledButton = channelSection.querySelector('[data-user-select-enabled]');
    const selectAllButton = channelSection.querySelector('[data-user-select-all]');
    const clearAllButton = channelSection.querySelector('[data-user-clear-all]');
    const selectedCount = channelSection.querySelector('[data-user-channel-count]');
    const channelSearch = channelSection.querySelector('[data-user-channel-search]');
    const searchCount = channelSection.querySelector('[data-user-channel-search-count]');
    const searchEmpty = channelSection.querySelector('[data-user-channel-search-empty]');

    const isEnabledChannel = (checkbox) => String(checkbox.dataset.channelEnabled || checkbox.closest('label')?.dataset.channelEnabled || '1') === '1';
    const updateUserChannelSelection = () => {
      const count = channelChecks.filter((checkbox) => checkbox.checked).length;
      const enabledChecks = channelChecks.filter(isEnabledChannel);
      const enabledSelectionExact = enabledChecks.every((checkbox) => checkbox.checked)
        && channelChecks.filter((checkbox) => !isEnabledChannel(checkbox)).every((checkbox) => !checkbox.checked);
      if (selectedCount) selectedCount.textContent = `${count} selected`;
      if (selectEnabledButton) selectEnabledButton.disabled = enabledChecks.length === 0 || enabledSelectionExact;
      if (selectAllButton) selectAllButton.disabled = channelChecks.length === 0 || count === channelChecks.length;
      if (clearAllButton) clearAllButton.disabled = count === 0;
      channelChecks.forEach((checkbox) => {
        const label = checkbox.closest('label');
        if (label) label.classList.toggle('channel-entitlement-selected', checkbox.checked);
      });
    };

    selectEnabledButton?.addEventListener('click', () => {
      channelChecks.forEach((checkbox) => { checkbox.checked = isEnabledChannel(checkbox); });
      updateUserChannelSelection();
    });
    selectAllButton?.addEventListener('click', () => {
      channelChecks.forEach((checkbox) => { checkbox.checked = true; });
      updateUserChannelSelection();
    });
    clearAllButton?.addEventListener('click', () => {
      channelChecks.forEach((checkbox) => { checkbox.checked = false; });
      updateUserChannelSelection();
    });
    channelChecks.forEach((checkbox) => checkbox.addEventListener('change', updateUserChannelSelection));

    const filterUserChannels = () => {
      const tokens = filterTokens(channelSearch?.value || '');
      let shown = 0;
      channelLabels.forEach((label) => {
        const haystack = normalizeFilterText(label.dataset.channelSearch || `${label.dataset.channelName || ''} ${label.textContent || ''}`);
        const visible = !tokens.length || tokens.every((token) => haystack.includes(token));
        label.hidden = !visible;
        label.classList.toggle('channel-search-hidden', !visible);
        if (visible) shown += 1;
      });
      if (searchCount) searchCount.textContent = tokens.length ? `${shown} of ${channelLabels.length} shown` : `${channelLabels.length} channels`;
      if (searchEmpty) searchEmpty.hidden = shown !== 0 || channelLabels.length === 0;
    };
    channelSearch?.addEventListener('input', filterUserChannels);
    channelSearch?.addEventListener('search', filterUserChannels);
    channelSearch?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') event.preventDefault();
      if (event.key === 'Escape') {
        event.preventDefault();
        channelSearch.value = '';
        filterUserChannels();
      }
    });
    filterUserChannels();
    updateUserChannelSelection();
  }

  const playlistOrderForm = document.querySelector('[data-playlist-order-form]');
  if (playlistOrderForm) {
    const list = playlistOrderForm.querySelector('[data-playlist-order-list]');
    const hidden = playlistOrderForm.querySelector('[data-playlist-order-input]');
    const checks = Array.from(playlistOrderForm.querySelectorAll('input[name="channel_ids"]'));
    let order = [];
    try {
      order = JSON.parse(playlistOrderForm.dataset.currentPlaylistOrder || '[]').map(Number).filter(Number.isFinite);
    } catch (_) { order = []; }

    const selected = () => checks.filter((item) => item.checked).map((item) => Number(item.value));
    const nameFor = (id) => {
      const checkbox = checks.find((item) => Number(item.value) === Number(id));
      return checkbox?.closest('label')?.dataset.channelName || checkbox?.closest('label')?.querySelector('b')?.textContent || `Channel ${id}`;
    };
    const normalize = () => {
      const chosen = selected();
      order = order.filter((id) => chosen.includes(id));
      chosen.forEach((id) => { if (!order.includes(id)) order.push(id); });
      if (hidden) hidden.value = JSON.stringify(order);
    };
    const render = () => {
      normalize();
      if (!list) return;
      list.innerHTML = '';
      order.forEach((id, index) => {
        const item = document.createElement('li');
        item.dataset.channelId = String(id);
        const label = document.createElement('span');
        label.innerHTML = `<b>${index + 1}</b><span>${escapeHtml(nameFor(id))}</span>`;
        const actions = document.createElement('span');
        actions.className = 'playlist-order-actions';
        const up = document.createElement('button');
        up.type = 'button'; up.className = 'button small'; up.textContent = '↑'; up.disabled = index === 0;
        up.addEventListener('click', () => { [order[index - 1], order[index]] = [order[index], order[index - 1]]; render(); });
        const down = document.createElement('button');
        down.type = 'button'; down.className = 'button small'; down.textContent = '↓'; down.disabled = index === order.length - 1;
        down.addEventListener('click', () => { [order[index + 1], order[index]] = [order[index], order[index + 1]]; render(); });
        actions.append(up, down);
        item.append(label, actions);
        list.appendChild(item);
      });
      if (!order.length) list.innerHTML = '<li class="playlist-order-empty">Select channels to set their playlist order.</li>';
    };
    checks.forEach((item) => item.addEventListener('change', render));
    playlistOrderForm.querySelector('[data-user-select-all]')?.addEventListener('click', () => setTimeout(render, 0));
    playlistOrderForm.querySelector('[data-user-clear-all]')?.addEventListener('click', () => setTimeout(render, 0));
    playlistOrderForm.addEventListener('submit', normalize);
    render();
  }


  const mainPlaylistHierarchyForm = document.querySelector('[data-main-playlist-hierarchy-form]');
  if (mainPlaylistHierarchyForm) {
    const categoryList = mainPlaylistHierarchyForm.querySelector('[data-main-playlist-category-list]');
    const groupContainer = mainPlaylistHierarchyForm.querySelector('[data-main-playlist-channel-groups]');
    const categoryInput = mainPlaylistHierarchyForm.querySelector('[data-main-playlist-category-order-input]');
    const channelInput = mainPlaylistHierarchyForm.querySelector('[data-main-playlist-channel-order-input]');

    const directItems = (list, selector) => Array.from(list?.children || []).filter((item) => item.matches(selector));
    const updateListControls = (list, selector) => {
      const items = directItems(list, selector);
      items.forEach((item, index) => {
        const number = item.querySelector('[data-order-number]');
        const up = item.querySelector('[data-order-up]');
        const down = item.querySelector('[data-order-down]');
        if (number) number.textContent = String(index + 1);
        if (up) up.disabled = index === 0;
        if (down) down.disabled = index === items.length - 1;
      });
    };
    const reorderGroups = () => {
      if (!categoryList || !groupContainer) return;
      const groups = new Map(
        Array.from(groupContainer.querySelectorAll('[data-playlist-category-group]')).map((group) => [group.dataset.categoryKey || '', group]),
      );
      directItems(categoryList, '[data-playlist-category-item]').forEach((item) => {
        const group = groups.get(item.dataset.categoryKey || '');
        if (group) groupContainer.appendChild(group);
      });
    };
    const syncHierarchy = () => {
      const categoryItems = directItems(categoryList, '[data-playlist-category-item]');
      if (categoryInput) categoryInput.value = JSON.stringify(categoryItems.map((item) => item.dataset.categoryKey || '').filter(Boolean));
      reorderGroups();
      const channelIds = [];
      Array.from(groupContainer?.querySelectorAll('[data-playlist-category-group]') || []).forEach((group) => {
        const list = group.querySelector('[data-playlist-channel-list]');
        directItems(list, '[data-playlist-channel-item]').forEach((item) => {
          const id = Number(item.dataset.channelId);
          if (Number.isFinite(id)) channelIds.push(id);
        });
        updateListControls(list, '[data-playlist-channel-item]');
      });
      if (channelInput) channelInput.value = JSON.stringify(channelIds);
      updateListControls(categoryList, '[data-playlist-category-item]');
    };
    const moveItem = (item, direction) => {
      const parent = item?.parentElement;
      if (!parent) return;
      if (direction < 0) {
        const previous = item.previousElementSibling;
        if (previous) parent.insertBefore(item, previous);
      } else {
        const next = item.nextElementSibling;
        if (next) parent.insertBefore(next, item);
      }
      syncHierarchy();
    };
    mainPlaylistHierarchyForm.addEventListener('click', (event) => {
      const button = event.target.closest('[data-order-up],[data-order-down]');
      if (!button || !mainPlaylistHierarchyForm.contains(button)) return;
      const item = button.closest('[data-playlist-category-item],[data-playlist-channel-item]');
      if (!item) return;
      event.preventDefault();
      moveItem(item, button.matches('[data-order-up]') ? -1 : 1);
    });
    mainPlaylistHierarchyForm.addEventListener('submit', syncHierarchy);
    syncHierarchy();
  }


  const categoryOrderForm = document.querySelector('[data-category-order-form]');
  if (categoryOrderForm) {
    const list = categoryOrderForm.querySelector('[data-category-order-list]');
    const hidden = categoryOrderForm.querySelector('[data-category-order-input]');
    const names = {};
    document.querySelectorAll('[data-category-id]').forEach((row) => {
      names[Number(row.dataset.categoryId)] = row.dataset.categoryName || `Category ${row.dataset.categoryId}`;
    });
    let order = [];
    try { order = JSON.parse(categoryOrderForm.dataset.currentCategoryOrder || '[]').map(Number).filter(Number.isFinite); } catch (_) { order = []; }
    const renderCategoryOrder = () => {
      if (hidden) hidden.value = JSON.stringify(order);
      if (!list) return;
      list.innerHTML = '';
      order.forEach((id, index) => {
        const item = document.createElement('li');
        const label = document.createElement('span');
        label.innerHTML = `<b>${index + 1}</b><span>${escapeHtml(names[id] || `Category ${id}`)}</span>`;
        const actions = document.createElement('span'); actions.className = 'playlist-order-actions';
        const up = document.createElement('button'); up.type='button'; up.className='button small'; up.textContent='↑'; up.disabled=index===0;
        up.addEventListener('click',()=>{[order[index-1],order[index]]=[order[index],order[index-1]];renderCategoryOrder();});
        const down = document.createElement('button'); down.type='button'; down.className='button small'; down.textContent='↓'; down.disabled=index===order.length-1;
        down.addEventListener('click',()=>{[order[index+1],order[index]]=[order[index],order[index+1]];renderCategoryOrder();});
        actions.append(up,down); item.append(label,actions); list.appendChild(item);
      });
    };
    categoryOrderForm.addEventListener('submit',()=>{if(hidden) hidden.value=JSON.stringify(order);});
    renderCategoryOrder();
  }

  const categoryChannelOrderForm = document.querySelector('[data-category-channel-order-form]');
  if (categoryChannelOrderForm) {
    const list = categoryChannelOrderForm.querySelector('[data-category-channel-order-list]');
    const hidden = categoryChannelOrderForm.querySelector('[data-category-channel-order-input]');
    const names = {};
    categoryChannelOrderForm.querySelectorAll('[data-order-channel-id]').forEach((row) => {
      names[Number(row.dataset.orderChannelId)] = row.dataset.orderChannelName || `Channel ${row.dataset.orderChannelId}`;
    });
    let order = [];
    try { order = JSON.parse(categoryChannelOrderForm.dataset.currentChannelOrder || '[]').map(Number).filter(Number.isFinite); } catch (_) { order = []; }
    const renderChannelOrder = () => {
      if (hidden) hidden.value = JSON.stringify(order);
      if (!list) return;
      list.innerHTML = '';
      order.forEach((id, index) => {
        const item = document.createElement('li');
        item.dataset.categoryChannelRow = '';
        item.dataset.channelSearch = String(names[id] || `Channel ${id}`).toLowerCase();
        const label = document.createElement('span');
        label.innerHTML = `<b>${index + 1}</b><span>${escapeHtml(names[id] || `Channel ${id}`)}</span>`;
        const actions = document.createElement('span');
        actions.className = 'playlist-order-actions';
        const up = document.createElement('button');
        up.type = 'button'; up.className = 'button small'; up.textContent = '↑'; up.disabled = index === 0;
        up.addEventListener('click', () => { [order[index - 1], order[index]] = [order[index], order[index - 1]]; renderChannelOrder(); });
        const down = document.createElement('button');
        down.type = 'button'; down.className = 'button small'; down.textContent = '↓'; down.disabled = index === order.length - 1;
        down.addEventListener('click', () => { [order[index + 1], order[index]] = [order[index], order[index + 1]]; renderChannelOrder(); });
        actions.append(up, down);
        item.append(label, actions);
        list.appendChild(item);
      });
      if (!order.length) list.innerHTML = '<li class="playlist-order-empty">No channels found in this category.</li>';
    };
    categoryChannelOrderForm.addEventListener('submit', () => { if (hidden) hidden.value = JSON.stringify(order); });
    renderChannelOrder();
  }


  const resolution = document.querySelector('#resolution-preset');
  const width = document.querySelector('#video-width');
  const height = document.querySelector('#video-height');
  if (resolution && width && height) {
    const current = width.value && height.value ? `${width.value}x${height.value}` : 'source';
    const matching = Array.from(resolution.options).some((option) => option.value === current);
    resolution.value = matching ? current : 'custom';
    resolution.addEventListener('change', () => {
      if (resolution.value === 'source') {
        width.value = '';
        height.value = '';
      } else if (resolution.value !== 'custom') {
        const [w, h] = resolution.value.split('x');
        width.value = w;
        height.value = h;
      }
    });
    [width, height].forEach((input) => input.addEventListener('input', () => {
      const value = width.value && height.value ? `${width.value}x${height.value}` : 'source';
      const exists = Array.from(resolution.options).some((option) => option.value === value);
      resolution.value = exists ? value : 'custom';
    }));
  }

  const detail = (label, value) => {
    if (!present(value) && value !== 0) return '';
    return `<div><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>`;
  };

  const renderPrograms = (programs, selectedProgram) => {
    if (!programs?.length) return '<div class="probe-empty compact">No MPEG-TS programs were reported for this source.</div>';
    return `<div class="probe-program-grid">${programs.map((program) => {
      const selected = selectedProgram && Number(selectedProgram) === Number(program.program_id);
      const streamSummary = (program.streams || []).reduce((acc, stream) => {
        acc[stream.type] = (acc[stream.type] || 0) + 1;
        return acc;
      }, {});
      const summary = Object.entries(streamSummary).map(([type, count]) => `${count} ${type}`).join(' · ') || 'No streams';
      return `<article class="program-card ${selected ? 'selected' : ''}">
        <header><div><span>Program</span><strong>${escapeHtml(program.program_id ?? program.program_num ?? '—')}</strong></div>${selected ? '<em>Selected</em>' : ''}</header>
        <h4>${escapeHtml(program.service_name || 'Unnamed service')}</h4>
        <p>${escapeHtml(program.service_provider || 'Provider not reported')}</p>
        <div class="program-meta"><span>PMT ${escapeHtml(program.pmt_pid || '—')}</span><span>PCR ${escapeHtml(program.pcr_pid || '—')}</span></div>
        <small>${escapeHtml(summary)}</small>
      </article>`;
    }).join('')}</div>`;
  };

  const renderStream = (stream) => {
    const type = stream.type || 'unknown';
    const title = `${type.toUpperCase()} #${present(stream.index) ? stream.index : '—'}`;
    let headline = stream.codec_long || stream.codec || 'Unknown codec';
    const fields = [];
    fields.push(['Codec', stream.profile ? `${stream.codec} · ${stream.profile}` : stream.codec]);
    fields.push(['PID', stream.pid]);
    fields.push(['Program', stream.program_id]);
    fields.push(['Bitrate', formatBits(stream.bit_rate)]);
    fields.push(['Language', stream.language]);
    fields.push(['Title', stream.title]);
    if (type === 'video') {
      const resolutionText = stream.width && stream.height ? `${stream.width}×${stream.height}` : null;
      headline = [resolutionText, stream.fps ? `${stream.fps} fps` : null, stream.codec?.toUpperCase()].filter(Boolean).join(' · ') || headline;
      fields.push(['Resolution', resolutionText]);
      fields.push(['Frame rate', stream.fps ? `${stream.fps} fps` : null]);
      fields.push(['Pixel format', stream.pixel_format]);
      fields.push(['Aspect ratio', stream.aspect_ratio]);
      fields.push(['Field order', stream.field_order]);
      fields.push(['Color space', stream.color_space]);
    } else if (type === 'audio') {
      headline = [stream.codec?.toUpperCase(), stream.sample_rate ? `${stream.sample_rate} Hz` : null, stream.channels ? `${stream.channels} ch` : null].filter(Boolean).join(' · ') || headline;
      fields.push(['Sample rate', stream.sample_rate ? `${stream.sample_rate} Hz` : null]);
      fields.push(['Channels', stream.channels]);
      fields.push(['Layout', stream.channel_layout]);
      fields.push(['Sample format', stream.sample_format]);
    }
    if (stream.disposition?.length) fields.push(['Disposition', stream.disposition.join(', ')]);
    return `<article class="stream-card stream-${escapeHtml(type)}">
      <header><span class="stream-type">${escapeHtml(title)}</span><b>${escapeHtml(headline)}</b></header>
      <div class="stream-detail-grid">${fields.map(([label, value]) => detail(label, value)).join('')}</div>
    </article>`;
  };

  const renderProbe = (container, data) => {
    if (!data.ok) {
      container.innerHTML = `<div class="probe-error"><b>Stream information could not be read</b><p>${escapeHtml(data.error || 'Unknown ffprobe error')}</p><small>${escapeHtml(data.captured_at || '')}</small></div>`;
      return;
    }
    const format = data.format || {};
    const counts = data.counts || {};
    const countText = Object.entries(counts).map(([type, count]) => `${count} ${type}`).join(' · ') || 'No elementary streams';
    const captured = data.captured_at ? new Date(data.captured_at).toLocaleString() : '—';
    container.innerHTML = `
      <div class="probe-summary-grid">
        ${detail('Container', format.long_name || format.name || 'Unknown')}
        ${detail('Source bitrate', formatBits(format.bit_rate))}
        ${detail('Duration', formatDuration(format.duration))}
        ${detail('Streams', countText)}
        ${detail('Service', format.service_name)}
        ${detail('Provider', format.service_provider)}
        ${detail('Probe score', format.probe_score)}
        ${detail('Read at', captured)}
      </div>
      <div class="probe-subsection"><h3>Programs / services</h3>${renderPrograms(data.programs || [], data.configured_program_id)}</div>
      <div class="probe-subsection"><h3>Elementary streams</h3><div class="stream-card-grid">${(data.streams || []).map(renderStream).join('') || '<div class="probe-empty compact">No streams found.</div>'}</div></div>
      ${data.stderr ? `<details class="probe-details"><summary>ffprobe notes</summary><pre>${escapeHtml(data.stderr)}</pre></details>` : ''}
    `;
  };

  const loadProbe = async (target) => {
    const container = document.querySelector(`[data-probe-result="${target}"]`);
    if (!container) return;
    const endpoint = container.dataset.endpoint;
    container.innerHTML = `<div class="probe-loading"><span class="spinner"></span> Reading ${escapeHtml(target)} stream…</div>`;
    document.querySelectorAll(`[data-probe-refresh="${target}"]`).forEach((button) => { button.disabled = true; });
    try {
      const response = await fetch(`${panelUrl(endpoint)}&_=${Date.now()}`, {cache: 'no-store', credentials: 'same-origin'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      renderProbe(container, data);
    } catch (error) {
      renderProbe(container, {ok: false, error: error.message});
    } finally {
      document.querySelectorAll(`[data-probe-refresh="${target}"]`).forEach((button) => { button.disabled = false; });
    }
  };

  // STREAMFORGE_STREAM_INFO_ON_DEMAND_REPLICAS_V63R6:
  // Stream Info performs one live Node/channel check on page open. There is no
  // interval/timer, so Remote Nodes are not continuously queried in background.
  const replicaRoot = document.querySelector('[data-replica-status]');
  const replicaStatusClass = (value) => {
    const state = String(value || 'unknown').toLowerCase();
    if (['online','running','up'].includes(state)) return 'running';
    if (['starting','restarting','waiting','checking'].includes(state)) return 'starting';
    if (['offline','error'].includes(state)) return 'error';
    return state === 'degraded' ? 'starting' : 'stopped';
  };
  const replicaLabel = (value) => String(value || 'unknown').replaceAll('_', ' ').replace(/\b\w/g, (m) => m.toUpperCase());
  const renderReplicas = (payload) => {
    if (!replicaRoot) return;
    const replicas = Array.isArray(payload?.replicas) ? payload.replicas : [];
    replicas.forEach((item) => {
      const card = replicaRoot.querySelector(`[data-replica-node-id="${CSS.escape(String(item.node_id ?? ''))}"]`);
      if (!card) return;
      const nodeState = card.querySelector('[data-replica-node-state]');
      const channelState = card.querySelector('[data-replica-channel-state]');
      const metrics = card.querySelector('[data-replica-metrics]');
      const nodeUptime = card.querySelector('[data-replica-node-uptime]');
      const channelUptime = card.querySelector('[data-replica-channel-uptime]');
      const error = card.querySelector('[data-replica-error]');
      if (nodeState) {
        nodeState.textContent = `Node ${replicaLabel(item.node_state)}`;
        nodeState.className = `status ${replicaStatusClass(item.node_state)}`;
      }
      if (channelState) {
        channelState.textContent = `Channel ${replicaLabel(item.channel_state)}`;
        channelState.className = `status ${replicaStatusClass(item.channel_state)}`;
      }
      // STREAMFORGE_REPLICA_NODE_CHANNEL_UPTIME_V63R7:
      // Node host uptime and channel process uptime are separate clocks.
      if (nodeUptime) {
        const seconds = Number(item.node_uptime_seconds || 0);
        nodeUptime.textContent = seconds > 0 ? formatUptime(seconds) : (String(item.node_state || '').toLowerCase() === 'online' ? '0s' : '—');
      }
      if (channelUptime) {
        const seconds = Number(item.channel_uptime_seconds ?? item.uptime_seconds ?? 0);
        channelUptime.textContent = seconds > 0 ? formatUptime(seconds) : (String(item.channel_state || '').toLowerCase() === 'running' ? '0s' : '—');
      }
      if (metrics) {
        const speed = Number(item.speed_x || 0) > 0 ? `${Number(item.speed_x).toFixed(2)}x` : '—';
        const hls = item.hls_ready ? 'HLS ready' : 'HLS not ready';
        metrics.textContent = `${Number(item.bitrate_kbps || 0)} kb/s · ${speed} · ${hls}`;
      }
      if (error) {
        const text = String(item.last_error || '').trim();
        error.textContent = text;
        error.hidden = !text;
      }
    });
    const captured = replicaRoot.querySelector('[data-replica-captured]');
    if (captured) captured.textContent = payload?.captured_at ? `Checked ${new Date(payload.captured_at).toLocaleString()}` : '';
  };
  const loadReplicas = async () => {
    if (!replicaRoot) return;
    const endpoint = replicaRoot.dataset.endpoint || '';
    const button = replicaRoot.querySelector('[data-replica-refresh]');
    if (button) button.disabled = true;
    replicaRoot.querySelectorAll('[data-replica-node-state]').forEach((node) => { node.textContent = 'Checking node'; node.className = 'status starting'; });
    replicaRoot.querySelectorAll('[data-replica-channel-state]').forEach((node) => { node.textContent = 'Checking channel'; node.className = 'status starting'; });
    replicaRoot.querySelectorAll('[data-replica-node-uptime],[data-replica-channel-uptime]').forEach((node) => { node.textContent = '—'; });
    try {
      const separator = endpoint.includes('?') ? '&' : '?';
      const response = await fetch(`${panelUrl(endpoint)}${separator}_=${Date.now()}`, {cache: 'no-store', credentials: 'same-origin'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      renderReplicas(data);
    } catch (error) {
      replicaRoot.querySelectorAll('[data-replica-node-state]').forEach((node) => { node.textContent = 'Node Offline'; node.className = 'status error'; });
      replicaRoot.querySelectorAll('[data-replica-channel-state]').forEach((node) => { node.textContent = 'Channel Offline'; node.className = 'status error'; });
      replicaRoot.querySelectorAll('[data-replica-error]').forEach((node) => { node.textContent = error.message || 'Live status check failed'; node.hidden = false; });
    } finally {
      if (button) button.disabled = false;
    }
  };
  replicaRoot?.querySelector('[data-replica-refresh]')?.addEventListener('click', loadReplicas);

  document.querySelectorAll('[data-probe-refresh]').forEach((button) => {
    button.addEventListener('click', () => loadProbe(button.dataset.probeRefresh));
  });
  if (document.querySelector('[data-stream-info]')) {
    loadReplicas();
    loadProbe('input');
    const output = document.querySelector('[data-probe-result="output"]');
    if (output?.dataset.autoLoad === '1') loadProbe('output');
  }

  document.querySelectorAll('[data-category-picker]').forEach((picker) => {
    const summary = picker.querySelector('[data-category-picker-summary]');
    const boxes = Array.from(picker.querySelectorAll('input[name="category_ids"]'));
    const syncCategorySummary = () => {
      const names = boxes.filter((box) => box.checked).map((box) => box.closest('label')?.querySelector('span')?.textContent?.trim()).filter(Boolean);
      if (summary) summary.textContent = names.length ? names.join(', ') : 'Uncategorized';
    };
    boxes.forEach((box) => box.addEventListener('change', syncCategorySummary));
    syncCategorySummary();
  });

  document.querySelectorAll('[data-stream-source-editor]').forEach((editor) => {
    const list = editor.querySelector('[data-source-list]');
    const addButton = editor.querySelector('[data-source-add]');
    const scanAllButton = editor.querySelector('[data-source-scan-all]');
    const primaryHidden = editor.querySelector('[data-primary-source-value]');
    const backupHidden = editor.querySelector('[data-backup-source-value]');
    /* STREAMFORGE_MAIN_SOURCE_SCAN_PUBLIC_PREFIX_V52: AJAX endpoints must use the configured public Panel/API prefix; internal /channels paths are hidden and may be silently dropped by strict-host routing. */
    const scanRoot = String(window.STREAMFORGE_APP_ROOT || '').replace(/\/+$/, '');
    const rawEndpoint = editor.dataset.scanEndpoint || '/channels/source-scan.json';
    const endpoint = rawEndpoint.startsWith('/') && scanRoot && !rawEndpoint.startsWith(scanRoot + '/') ? scanRoot + rawEndpoint : rawEndpoint;

    const rows = () => Array.from(list?.querySelectorAll('[data-source-row]') || []);
    const inputFor = (row) => row.querySelector('input[name="source_urls"]');
    const programSelectFor = (row) => row.querySelector('[data-source-program-select]');
    const programFieldFor = (row) => row.querySelector('[data-source-program-field]');
    const programLabel = (program) => {
      const id = program?.program_id ?? program?.program_num;
      const service = String(program?.service_name || '').trim();
      const provider = String(program?.service_provider || '').trim();
      const name = service || `Program ${id}`;
      return `${name} — ID ${id}${provider ? ` · ${provider}` : ''}`;
    };
    const resetRowProgram = (row, hide = true) => {
      const select = programSelectFor(row);
      const field = programFieldFor(row);
      if (!select) return;
      select.innerHTML = '<option value="">Auto / no program</option>';
      select.value = '';
      select.dataset.programSource = '';
      if (field) field.hidden = hide;
    };
    const populateRowPrograms = (row, data, sourceValue) => {
      const select = programSelectFor(row);
      const field = programFieldFor(row);
      if (!select || !field) return;
      const formatName = String(data?.format?.name || data?.format?.long_name || '').toLowerCase();
      const isHls = formatName.includes('hls') || formatName.includes('applehttp');
      const normalized = isHls ? [] : (data?.programs || []).filter((program) => program?.program_id != null || program?.program_num != null);
      const previous = String(select.value || '');
      select.innerHTML = '';
      const blank = document.createElement('option');
      blank.value = '';
      blank.textContent = normalized.length ? (normalized.length === 1 ? 'Auto / detected program' : 'Select program / service') : 'Auto / no program';
      select.appendChild(blank);
      const ids = [];
      normalized.forEach((program) => {
        const id = String(program.program_id ?? program.program_num);
        ids.push(id);
        const option = document.createElement('option');
        option.value = id;
        option.textContent = programLabel(program);
        select.appendChild(option);
      });
      if (normalized.length === 1) select.value = ids[0];
      else if (ids.includes(previous)) select.value = previous;
      else select.value = '';
      select.dataset.programSource = sourceValue;
      field.hidden = normalized.length === 0;
    };
    const compactInfo = (data, row) => {
      const target = row.querySelector('[data-source-compact-info]');
      if (!target) return;
      if (!data?.ok) {
        target.innerHTML = `<span class="source-info-error">× ${escapeHtml(data?.error || data?.detail || 'Stream probe failed')}</span>`;
        resetRowProgram(row, true);
        return;
      }
      const streams = data.streams || [];
      const video = streams.find((item) => item.type === 'video');
      const audio = streams.find((item) => item.type === 'audio');
      const tags = [];
      if (video?.width && video?.height) tags.push(`<span>▣ ${escapeHtml(video.width)} × ${escapeHtml(video.height)}</span>`);
      if (video?.codec) tags.push(`<span>▸ ${escapeHtml(String(video.codec).toLowerCase())}</span>`);
      if (audio?.codec) tags.push(`<span>♪ ${escapeHtml(String(audio.codec).toLowerCase())}</span>`);
      if (video?.fps) tags.push(`<span>◆ ${escapeHtml(Math.round(Number(video.fps) * 100) / 100)} FPS</span>`);
      const formatName = data.format?.name || data.format?.long_name;
      if (formatName) tags.push(`<span>◇ ${escapeHtml(String(formatName).split(',')[0])}</span>`);
      target.innerHTML = tags.join('') || '<span class="source-info-muted">No stream details</span>';
    };

    const sync = () => {
      const current = rows();
      current.forEach((row, index) => {
        const input = inputFor(row);
        const rank = row.querySelector('[data-source-rank]');
        const up = row.querySelector('[data-source-up]');
        const down = row.querySelector('[data-source-down]');
        const remove = row.querySelector('[data-source-remove]');
        if (rank) rank.textContent = String(index + 1);
        if (input) input.required = index === 0;
        if (up) up.disabled = index === 0;
        if (down) down.disabled = index === current.length - 1;
        if (remove) remove.disabled = current.length === 1;
        const select = programSelectFor(row);
        if (select && select.dataset.programSource && select.dataset.programSource !== (input?.value.trim() || '')) resetRowProgram(row, true);
      });
      const values = current.map((row) => inputFor(row)?.value.trim() || '').filter(Boolean);
      if (primaryHidden) primaryHidden.value = values[0] || '';
      if (backupHidden) backupHidden.value = values.slice(1).join('\n');
    };

    const scanRow = async (row) => {
      const input = inputFor(row);
      const target = row.querySelector('[data-source-compact-info]');
      const button = row.querySelector('[data-source-scan]');
      const value = input?.value.trim() || '';
      if (!value) {
        if (target) target.innerHTML = '<span class="source-info-error">Enter a stream URL</span>';
        input?.focus();
        return;
      }
      if (button) button.disabled = true;
      if (target) target.innerHTML = '<span class="source-info-muted"><span class="spinner"></span> Scanning…</span>';
      try {
        const response = await fetch(endpoint, {
          method: 'POST', cache: 'no-store', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({input_url: value}),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        compactInfo(data, row);
        populateRowPrograms(row, data, value);
      } catch (error) {
        compactInfo({ok: false, error: error.message}, row);
      } finally {
        if (button) button.disabled = false;
      }
    };

    const bindRow = (row) => {
      row.querySelector('[data-source-up]')?.addEventListener('click', () => {
        const previous = row.previousElementSibling;
        if (previous) list.insertBefore(row, previous);
        sync();
      });
      row.querySelector('[data-source-down]')?.addEventListener('click', () => {
        const next = row.nextElementSibling;
        if (next) list.insertBefore(next, row);
        sync();
      });
      row.querySelector('[data-source-remove]')?.addEventListener('click', () => {
        if (rows().length <= 1) return;
        row.remove();
        sync();
      });
      row.querySelector('[data-source-scan]')?.addEventListener('click', () => scanRow(row));
      inputFor(row)?.addEventListener('input', () => { resetRowProgram(row, true); sync(); });
    };

    const createRow = () => {
      const row = document.createElement('article');
      row.className = 'stream-source-row';
      row.dataset.sourceRow = '';
      row.innerHTML = `
        <div class="stream-source-priority"><button class="button small" type="button" data-source-up>↑</button><button class="button small" type="button" data-source-down>↓</button></div>
        <div class="stream-source-url"><span class="source-rank" data-source-rank></span><input name="source_urls" placeholder="http://source/live/index.m3u8 or YouTube Live URL"></div>
        <div class="stream-source-info"><div class="stream-source-scan-summary" data-source-compact-info></div><label class="source-program-field" data-source-program-field hidden><select name="source_program_ids" data-source-program-select><option value="">Auto / no program</option></select></label></div>
        <div class="stream-source-actions"><button class="button small" type="button" data-source-scan>Scan</button><button class="button ghost-danger small" type="button" data-source-remove>×</button></div>`;
      list.appendChild(row);
      bindRow(row);
      sync();
      inputFor(row)?.focus();
    };

    rows().forEach(bindRow);
    addButton?.addEventListener('click', createRow);
    scanAllButton?.addEventListener('click', async () => {
      scanAllButton.disabled = true;
      for (const row of rows()) await scanRow(row);
      scanAllButton.disabled = false;
    });
    editor.closest('form')?.addEventListener('submit', (event) => {
      sync();
      if (!rows().some((row) => inputFor(row)?.value.trim())) {
        event.preventDefault();
        inputFor(rows()[0])?.focus();
      }
    });
    sync();
  });

  const sourceScanButton = document.querySelector('[data-source-scan-button]');
  const sourceScanResult = document.querySelector('[data-source-scan-result]');
  if (sourceScanButton && sourceScanResult) {
    const input = document.querySelector('input[name="input_url"]');
    const applyDetected = (data, programId = '') => {
      const selectedProgram = (data.programs || []).find((item) => String(item.program_id ?? item.program_num ?? '') === String(programId));
      const pool = selectedProgram?.streams?.length ? selectedProgram.streams : (data.streams || []);
      const video = pool.find((stream) => stream.type === 'video');
      if (programInput && programId) programInput.value = programId;
      if (video) {
        if (widthInput && video.width) widthInput.value = video.width;
        if (heightInput && video.height) heightInput.value = video.height;
        if (fpsInput && video.fps) fpsInput.value = Math.round(Number(video.fps) * 1000) / 1000;
        const preset = document.querySelector('#resolution-preset');
        if (preset && widthInput?.value && heightInput?.value) {
          const resolutionValue = `${widthInput.value}x${heightInput.value}`;
          preset.value = Array.from(preset.options).some((option) => option.value === resolutionValue) ? resolutionValue : 'custom';
        }
      }
    };
    sourceScanButton.addEventListener('click', async () => {
      const target = input?.value?.trim() || '';
      if (!target) {
        renderProbe(sourceScanResult, {ok: false, error: 'Enter the input URL before scanning.'});
        input?.focus();
        return;
      }
      sourceScanButton.disabled = true;
      sourceScanResult.innerHTML = '<div class="probe-loading"><span class="spinner"></span> Scanning source…</div>';
      try {
        /* STREAMFORGE_MAIN_SOURCE_SCAN_PUBLIC_PREFIX_V52 */
        const scanRoot = String(window.STREAMFORGE_APP_ROOT || '').replace(/\/+$/, '');
        const response = await fetch((scanRoot || '') + '/channels/source-scan.json', {
          method: 'POST', cache: 'no-store', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({input_url: target}),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        renderProbe(sourceScanResult, data);
        const programs = data.programs || [];
        const controls = document.createElement('div');
        controls.className = 'source-scan-controls';
        const options = programs.map((item) => {
          const id = item.program_id ?? item.program_num ?? '';
          const label = [id ? `Program ${id}` : 'Program', item.service_name || 'Unnamed service'].join(' · ');
          return `<option value="${escapeHtml(id)}">${escapeHtml(label)}</option>`;
        }).join('');
        controls.innerHTML = programs.length
          ? `<label>Detected service<select data-source-program>${options}</select></label><button type="button" class="button primary small" data-source-apply>Use selected program & video values</button>`
          : '<button type="button" class="button primary small" data-source-apply>Use detected video values</button>';
        sourceScanResult.prepend(controls);
        controls.querySelector('[data-source-apply]')?.addEventListener('click', () => {
          const selected = controls.querySelector('[data-source-program]')?.value || '';
          applyDetected(data, selected);
        });
      } catch (error) {
        renderProbe(sourceScanResult, {ok: false, error: error.message});
      } finally {
        sourceScanButton.disabled = false;
      }
    });
  }

  const bulkForm = document.querySelector('#channel-bulk-form');
  if (bulkForm) {
    const selectAll = document.querySelector('[data-select-all]');
    const channelChecks = Array.from(document.querySelectorAll('.channel-select'));
    const selectedButtons = Array.from(document.querySelectorAll('[data-selected-action]'));
    const selectedCount = document.querySelector('[data-selected-count]');
    const globalActions = document.querySelector('[data-global-actions]');
    const selectedActions = document.querySelector('[data-selected-actions]');

    const visibleChannelChecks = () => channelChecks.filter((checkbox) => !checkbox.closest('tr')?.hidden);
    const updateChannelSelection = () => {
      const checked = channelChecks.filter((checkbox) => checkbox.checked);
      const visible = visibleChannelChecks();
      const visibleChecked = visible.filter((checkbox) => checkbox.checked);
      const hasSelection = checked.length > 0;
      if (selectedCount) selectedCount.textContent = `${checked.length} selected`;
      if (globalActions) globalActions.hidden = hasSelection;
      if (selectedActions) selectedActions.hidden = !hasSelection;
      selectedButtons.forEach((button) => { button.disabled = !hasSelection; });
      channelChecks.forEach((checkbox) => {
        const row = checkbox.closest('tr');
        if (row) row.classList.toggle('channel-row-selected', checkbox.checked);
      });
      if (selectAll) {
        selectAll.checked = visible.length > 0 && visibleChecked.length === visible.length;
        selectAll.indeterminate = visibleChecked.length > 0 && visibleChecked.length < visible.length;
      }
    };

    if (selectAll) {
      selectAll.addEventListener('change', () => {
        visibleChannelChecks().forEach((checkbox) => { checkbox.checked = selectAll.checked; });
        updateChannelSelection();
      });
    }
    channelChecks.forEach((checkbox) => checkbox.addEventListener('change', updateChannelSelection));
    document.addEventListener('streamforge:channel-filtered', updateChannelSelection);

    // STREAMFORGE_BULK_NODE_ASSIGNMENT_AJAX_UI_V1125:
    // Add/Remove/Set Node should feel like a local table edit.  Submit only the
    // selected rows with fetch(), keep the current catalogue/search/page in
    // place, and update server badges from the compact JSON response.  Remote
    // Node reconciliation continues in the backend worker.
    const bulkNodeActions = new Set(['nodes_add', 'nodes_remove', 'nodes_set']);
    const bulkResult = document.querySelector('[data-channel-bulk-result]');
    const showBulkResult = (message, warning = false) => {
      if (!bulkResult) return;
      bulkResult.hidden = !message;
      bulkResult.textContent = message || '';
      bulkResult.className = `alert ${warning ? 'warning' : 'success'} channel-page-alert`;
      bulkResult.scrollIntoView?.({block: 'nearest'});
    };
    const renderNodeAssignment = (item) => {
      const row = document.querySelector(`[data-channel-id="${Number(item.channel_id)}"]`);
      if (!row) return;
      const nodes = Array.isArray(item.nodes) ? item.nodes : [];
      const ids = Array.isArray(item.node_ids) ? item.node_ids.map((value) => Number(value)).filter(Number.isFinite) : [];
      row.dataset.channelNodeIds = ids.join(',');
      const cell = row.querySelector('.server-cluster-cell');
      if (!cell || !nodes.length) return;
      const activeSource = row.querySelector('[data-active-source]')?.textContent?.trim() || 'Local source';
      const nodeLabel = (node) => String(node.node_type || '') === 'local' ? 'Main Server' : String(node.name || `Node ${node.id || ''}`);
      const nodeDetail = (node) => String(node.node_type || '') === 'local' ? activeSource : String(node.detail || 'Remote server');
      const primary = nodes[0];
      row.dataset.sortServer = nodeLabel(primary).toLowerCase();
      const renderEntry = (node) => {
        const label = escapeHtml(nodeLabel(node));
        const detail = escapeHtml(nodeDetail(node));
        const active = String(node.node_type || '') === 'local' ? ' data-active-source' : '';
        return `<a href="/channels?node=${encodeURIComponent(String(node.id || ''))}"><b>${label}</b><small${active}>${detail}</small></a>`;
      };
      if (nodes.length > 1) {
        const label = escapeHtml(nodeLabel(primary));
        const detail = escapeHtml(nodeDetail(primary));
        const active = String(primary.node_type || '') === 'local' ? ' data-active-source' : '';
        cell.innerHTML = `<details class="server-cluster main-server-cluster"><summary><span class="server-primary"><b>${label}</b><small${active}>${detail}</small></span><span class="server-extra">+${nodes.length - 1}</span></summary><div class="server-cluster-menu">${nodes.map(renderEntry).join('')}</div></details>`;
      } else {
        const label = escapeHtml(nodeLabel(primary));
        const detail = escapeHtml(nodeDetail(primary));
        const active = String(primary.node_type || '') === 'local' ? ' data-active-source' : '';
        cell.innerHTML = `<a class="server-primary single" href="/channels?node=${encodeURIComponent(String(primary.id || ''))}"><b>${label}</b><small${active}>${detail}</small></a>`;
      }
      if (row.dataset.channelSearch) {
        row.dataset.channelSearch = `${row.dataset.channelSearch} ${nodes.map((node) => nodeLabel(node)).join(' ')}`.toLowerCase();
      }
    };

    bulkForm.addEventListener('submit', async (event) => {
      const submitter = event.submitter;
      if (!submitter) return;
      const checkedChannels = channelChecks.filter((checkbox) => checkbox.checked);
      const checkedCount = checkedChannels.length;
      if (submitter.hasAttribute('data-selected-action') && checkedCount === 0) {
        event.preventDefault();
        return;
      }
      let message = submitter.dataset.confirm || '';
      if (submitter.dataset.confirmSelected) {
        message = `${submitter.dataset.confirmSelected} (${checkedCount})`;
      }
      if (message && !window.confirm(message)) {
        event.preventDefault();
        return;
      }
      // The row checkboxes live outside the toolbar form. Although their
      // HTML form= association is standards-compliant, some embedded clients
      // omit those controls during submission. Mirror the exact selection.
      bulkForm.querySelectorAll('input[data-bulk-channel-mirror]').forEach((input) => input.remove());
      checkedChannels.forEach((checkbox) => {
        const mirror = document.createElement('input');
        mirror.type = 'hidden';
        mirror.name = 'channel_ids';
        mirror.value = checkbox.value;
        mirror.dataset.bulkChannelMirror = '1';
        bulkForm.appendChild(mirror);
        checkbox.disabled = true;
      });

      const action = String(submitter.value || '');
      if (!bulkNodeActions.has(action)) return;
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation?.();

      const chosenNodeCount = Array.from(bulkForm.querySelectorAll('select[name="bulk_node_ids"] option:checked')).length;
      if (!chosenNodeCount) {
        checkedChannels.forEach((checkbox) => { checkbox.disabled = false; });
        bulkForm.querySelectorAll('input[data-bulk-channel-mirror]').forEach((input) => input.remove());
        showBulkResult('Select at least one node', true);
        updateChannelSelection();
        return;
      }

      const originalText = submitter.textContent;
      submitter.disabled = true;
      submitter.textContent = 'Saving…';
      showBulkResult('');
      try {
        const payload = new FormData(bulkForm);
        payload.set('action', action);
        const response = await fetch(panelUrl('/channels/bulk'), {
          method: 'POST',
          body: payload,
          credentials: 'same-origin',
          cache: 'no-store',
          headers: {'Accept': 'application/json', 'X-StreamForge-Ajax': '1'},
        });
        let data = {};
        try { data = await response.json(); } catch (_) { data = {}; }
        if (!response.ok || data.ok === false) throw new Error(String(data.error || `HTTP ${response.status}`));
        (Array.isArray(data.channels) ? data.channels : []).forEach(renderNodeAssignment);
        showBulkResult(String(data.warning || data.message || 'Node assignment saved'), Boolean(data.warning));
        if (typeof window.streamforgeApplyChannelFilter === 'function') window.streamforgeApplyChannelFilter();
        if (typeof window.streamforgeApplyChannelSort === 'function') window.streamforgeApplyChannelSort();
      } catch (error) {
        showBulkResult(`Node assignment failed: ${error.message || error}`, true);
      } finally {
        checkedChannels.forEach((checkbox) => { checkbox.disabled = false; });
        bulkForm.querySelectorAll('input[data-bulk-channel-mirror]').forEach((input) => input.remove());
        submitter.textContent = originalText;
        updateChannelSelection();
      }
    });

    updateChannelSelection();
  }

  const systemMetricsPanel = document.querySelector('[data-system-metrics]');
  if (systemMetricsPanel) {
    const endpoint = systemMetricsPanel.dataset.endpoint || '/system-metrics.json';
    const setText = (selector, value) => {
      const node = systemMetricsPanel.querySelector(selector);
      if (node) node.textContent = value;
    };
    const setMeter = (selector, value) => {
      const node = systemMetricsPanel.querySelector(selector);
      if (node) node.style.width = `${Math.max(0, Math.min(100, Number(value) || 0))}%`;
    };
    const formatHostUptime = (value) => {
      let seconds = Math.max(0, Math.floor(Number(value) || 0));
      const days = Math.floor(seconds / 86400);
      seconds %= 86400;
      const hours = Math.floor(seconds / 3600);
      seconds %= 3600;
      const minutes = Math.floor(seconds / 60);
      seconds %= 60;
      return [days ? `${days}d` : '', `${hours}h`, `${minutes}m`, `${seconds}s`].filter(Boolean).join(' ');
    };
    const renderSystemMetrics = (data) => {
      const cpu = data.cpu || {};
      const memory = data.memory || {};
      const disk = data.disk || {};
      const network = data.network || {};
      const cpuPercent = Math.max(0, Number(cpu.percent) || 0);
      const memoryPercent = Math.max(0, Number(memory.percent) || 0);
      const diskPercent = Math.max(0, Number(disk.percent) || 0);
      const rx = Math.max(0, Number(network.rx_bits_per_second) || 0);
      const tx = Math.max(0, Number(network.tx_bits_per_second) || 0);
      setText('[data-cpu-percent]', `${cpuPercent.toFixed(1)}%`);
      setText('[data-cpu-cores]', String(cpu.cores || 1));
      setText('[data-load-average]', Number(cpu.load_1 || 0).toFixed(2));
      setMeter('[data-cpu-meter]', cpuPercent);
      setText('[data-memory-percent]', `${memoryPercent.toFixed(1)}%`);
      setText('[data-memory-used]', formatBytes(memory.used_bytes));
      setText('[data-memory-total]', formatBytes(memory.total_bytes));
      setMeter('[data-memory-meter]', memoryPercent);
      // STREAMFORGE_MAIN_DASHBOARD_DISK_USAGE_V96
      setText('[data-disk-percent]', `${diskPercent.toFixed(1)}%`);
      setText('[data-disk-used]', formatBytes(disk.used_bytes));
      setText('[data-disk-free]', formatBytes(disk.free_bytes));
      setText('[data-disk-total]', formatBytes(disk.total_bytes));
      setMeter('[data-disk-meter]', diskPercent);
      setText('[data-network-rx]', formatBits(rx));
      setText('[data-network-tx]', formatBits(tx));
      setText('[data-network-total]', formatBits(rx + tx));
      setText('[data-network-interfaces]', (network.interfaces || []).join(', ') || 'No active interface');
      // STREAMFORGE_MAIN_GPU_DASHBOARD_LIVE_V1069
      const gpu = data.gpu || {};
      const gpuCard = systemMetricsPanel.querySelector('[data-gpu-card]');
      document.querySelectorAll('[data-gpu-history]').forEach((node) => { node.hidden = !gpu.available; });
      if (gpuCard) {
        gpuCard.hidden = !gpu.available;
        if (gpu.available) {
          const gpuPercent = gpu.usage_percent == null ? null : Math.max(0, Number(gpu.usage_percent) || 0);
          setText('[data-gpu-percent]', gpuPercent == null ? '—' : `${gpuPercent.toFixed(1)}%`);
          setMeter('[data-gpu-meter]', gpuPercent == null ? 0 : gpuPercent);
          setText('[data-gpu-name]', String(gpu.name || 'GPU'));
          const gpuMemoryTotal = Math.max(0, Number(gpu.memory_total_bytes) || 0);
          const gpuMemoryUsed = Math.max(0, Number(gpu.memory_used_bytes) || 0);
          setText('[data-gpu-vram]', gpuMemoryTotal ? `${formatBytes(gpuMemoryUsed)} / ${formatBytes(gpuMemoryTotal)}` : 'shared / unavailable');
          setText('[data-gpu-temp]', gpu.temperature_c == null ? 'Temp —' : `${Number(gpu.temperature_c).toFixed(0)}°C`);
          setText('[data-gpu-encoder]', String(gpu.encoder || 'GPU encoder'));
          setText('[data-gpu-streams]', String(Math.max(0, Number(gpu.active_streams) || 0)));
        }
      }
      setText('[data-host-uptime]', formatHostUptime(data.uptime_seconds));
      const uptimeStat = document.querySelector('[data-system-uptime]');
      if (uptimeStat) uptimeStat.textContent = formatHostUptime(data.uptime_seconds);
    };
    // STREAMFORGE_MAIN_POLL_BACKPRESSURE_V34:
    // Never stack metric polls if Main is briefly busy. One bounded request is
    // enough; a later interval retries after timeout/recovery.
    let systemMetricsRefreshInFlight = false;
    const refreshSystemMetrics = async () => {
      if (systemMetricsRefreshInFlight) return;
      systemMetricsRefreshInFlight = true;
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 4000);
      try {
        const response = await fetch(`${endpoint}?_=${Date.now()}`, {cache: 'no-store', signal: controller.signal});
        if (!response.ok) return;
        renderSystemMetrics(await response.json());
      } catch (_) {
      } finally {
        window.clearTimeout(timeout);
        systemMetricsRefreshInFlight = false;
      }
    };
    // STREAMFORGE_MAIN_DASHBOARD_FIRST_PAINT_POLL_DELAY_V1113:
    // Initial cards are server-rendered. Let navigation paint before background
    // metric traffic competes for a Main worker.
    // STREAMFORGE_MAIN_DASHBOARD_METRICS_RELAXED_POLL_V1114:
    // Resource cards are server-rendered and system_metrics is background-cached.
    // Five-second non-overlapping refresh is responsive without creating a
    // constant two-second request stream on the single Main control worker.
    // STREAMFORGE_MAIN_DASHBOARD_METRICS_AFTER_5S_V1116:
    // The first resource snapshot is already server-rendered. Avoid a redundant
    // XHR during first paint; start the live cadence only when the first 5s tick
    // is due.
    setTimeout(() => {
      refreshSystemMetrics();
      setInterval(refreshSystemMetrics, 5000);
    }, 5000);
  }

  const dashboardChannelSearch = document.querySelector('[data-dashboard-channel-search]');
  if (dashboardChannelSearch) {
    const panel = dashboardChannelSearch.closest('.panel');
    const rows = Array.from(panel?.querySelectorAll('tbody tr[data-channel-search]') || []);
    const result = panel?.querySelector('[data-dashboard-channel-result]');
    const empty = panel?.querySelector('[data-dashboard-filter-empty]');
    const normalize = (value) => {
      const source = String(value ?? '');
      try { return source.normalize('NFKD').replace(/\p{Diacritic}/gu, '').toLocaleLowerCase(); }
      catch (_) { return source.toLocaleLowerCase(); }
    };
    const applyDashboardSearch = () => {
      const tokens = normalize(dashboardChannelSearch.value).trim().split(/\s+/).filter(Boolean);
      let visible = 0;
      rows.forEach((row) => {
        const haystack = normalize(row.dataset.channelSearch || '');
        const isLive = row.dataset.runtimeLive !== '0';
        const matched = isLive && (tokens.length === 0 || tokens.every((token) => haystack.includes(token)));
        row.hidden = !matched;
        if (matched) visible += 1;
      });
      if (result) result.textContent = `${visible} of ${rows.length} channels`;
      if (empty) empty.hidden = visible !== 0;
      document.dispatchEvent(new CustomEvent('streamforge:channel-filtered'));
    };
    dashboardChannelSearch.addEventListener('input', applyDashboardSearch);
    dashboardChannelSearch.addEventListener('search', applyDashboardSearch);
    dashboardChannelSearch.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') event.preventDefault();
      if (event.key === 'Escape') { dashboardChannelSearch.value = ''; applyDashboardSearch(); }
    });
    window.streamforgeApplyDashboardSearch = applyDashboardSearch;
    applyDashboardSearch();
  }

  // STREAMFORGE_MAIN_LOG_AUTO_FILTER_EXTENDED_LIMIT_V85:
  // Log filters apply immediately on selection; the old Apply button is gone.
  const mainLogAutoFilter = document.querySelector('[data-log-auto-filter]');
  if (mainLogAutoFilter) {
    Array.from(mainLogAutoFilter.querySelectorAll('select')).forEach((select) => {
      select.addEventListener('change', () => mainLogAutoFilter.requestSubmit());
    });
  }

  // STREAMFORGE_MAIN_CHANNEL_LIVE_SEARCH_NO_RELOAD_V1122:
  // Channels search is handled by the full-catalogue in-page filter below.
  // No GET form submit, document navigation or focus-restoration workaround is
  // required: every keystroke filters the already-rendered catalogue directly.

  // STREAMFORGE_MAIN_CHANNEL_PAGINATION_V1078:
  // Keep the full catalogue in the DOM for instant search/sort/status updates,
  // but page the matching rows instead of permanently truncating them to the
  // first Show N entries.
  const liveChannelFilterForm = document.querySelector('[data-live-channel-filter-form]');
  if (liveChannelFilterForm) {
    const input = liveChannelFilterForm.querySelector('[data-live-channel-search]');
    const categorySelect = liveChannelFilterForm.querySelector('select[name="category"]');
    const nodeSelect = liveChannelFilterForm.querySelector('select[name="node"]');
    const statusSelect = liveChannelFilterForm.querySelector('select[name="status"]');
    const limitSelect = liveChannelFilterForm.querySelector('[data-channel-limit-filter]');
    const clearButton = liveChannelFilterForm.querySelector('[data-live-filter-clear]');
    const result = liveChannelFilterForm.querySelector('[data-live-filter-result]');
    const empty = document.querySelector('[data-live-filter-empty]');
    const pagination = document.querySelector('[data-channel-pagination]');
    const prevButton = pagination?.querySelector('[data-channel-page-prev]');
    const nextButton = pagination?.querySelector('[data-channel-page-next]');
    const pageNumbers = pagination?.querySelector('[data-channel-page-numbers]');
    const pageSummary = pagination?.querySelector('[data-channel-page-summary]');
    const storageKey = 'streamforge.channels.filters.v5';
    let currentPage = 1;

    const normalizeSearch = (value) => {
      const source = String(value ?? '');
      try {
        return source.normalize('NFKD').replace(/\p{Diacritic}/gu, '').toLocaleLowerCase();
      } catch (_) {
        return source.toLocaleLowerCase();
      }
    };
    const searchTokens = (value) => normalizeSearch(value).trim().split(/\s+/).filter(Boolean);
    const readStoredState = () => {
      try {
        const parsed = JSON.parse(sessionStorage.getItem(storageKey) || '{}');
        return parsed && typeof parsed === 'object' ? parsed : {};
      } catch (_) { return {}; }
    };
    const writeStoredState = (state) => {
      try { sessionStorage.setItem(storageKey, JSON.stringify(state)); } catch (_) {}
    };
    const currentState = () => ({
      q: String(input?.value || '').trim(),
      category: String(categorySelect?.value || ''),
      node: String(nodeSelect?.value || ''),
      status: String(statusSelect?.value || ''),
      limit: String(limitSelect?.value || '10'),
      page: currentPage,
    });
    const restoreState = () => {
      const params = new URL(window.STREAMFORGE_INTERNAL_PANEL_URL || window.location.href, window.location.origin).searchParams;
      const urlHasFilter = ['q', 'category', 'node', 'status', 'limit', 'page'].some((key) => params.has(key));
      const stored = readStoredState();
      if (!urlHasFilter) {
        if (input && typeof stored.q === 'string') input.value = stored.q;
        if (categorySelect && typeof stored.category === 'string' && Array.from(categorySelect.options).some((item) => item.value === stored.category)) categorySelect.value = stored.category;
        if (nodeSelect && typeof stored.node === 'string' && Array.from(nodeSelect.options).some((item) => item.value === stored.node)) nodeSelect.value = stored.node;
        if (statusSelect && typeof stored.status === 'string' && Array.from(statusSelect.options).some((item) => item.value === stored.status)) statusSelect.value = stored.status;
        if (limitSelect && typeof stored.limit === 'string' && Array.from(limitSelect.options).some((item) => item.value === stored.limit)) limitSelect.value = stored.limit;
        currentPage = Math.max(1, Number(stored.page) || 1);
      } else {
        currentPage = Math.max(1, Number(params.get('page')) || 1);
      }
    };
    const updateAddressBar = (state) => {
      try {
        const url = new URL(window.STREAMFORGE_INTERNAL_PANEL_URL || window.location.href, window.location.origin);
        ['q', 'category', 'node', 'status', 'limit'].forEach((key) => {
          if (state[key]) url.searchParams.set(key, state[key]);
          else url.searchParams.delete(key);
        });
        if (Number(state.page) > 1) url.searchParams.set('page', String(state.page));
        else url.searchParams.delete('page');
        url.searchParams.delete('message');
        url.searchParams.delete('error');
        window.STREAMFORGE_INTERNAL_PANEL_URL = `${url.pathname}${url.search}${url.hash}`;
        if (!window.STREAMFORGE_FIXED_PANEL_ADDRESS_BAR) history.replaceState(history.state, '', window.STREAMFORGE_INTERNAL_PANEL_URL);
      } catch (_) {}
    };
    const renderPagination = (pageCount, selectedPage, matched, pageSize) => {
      if (!pagination || !pageNumbers || !prevButton || !nextButton || !pageSummary) return;
      const active = pageSize > 0 && matched > pageSize;
      pagination.hidden = !active;
      pageNumbers.replaceChildren();
      if (!active) return;
      prevButton.disabled = selectedPage <= 1;
      nextButton.disabled = selectedPage >= pageCount;
      const start = Math.max(1, Math.min(selectedPage - 2, Math.max(1, pageCount - 4)));
      const finish = Math.min(pageCount, start + 4);
      for (let number = start; number <= finish; number += 1) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `button compact${number === selectedPage ? ' primary' : ''}`;
        button.textContent = String(number);
        button.dataset.channelPage = String(number);
        pageNumbers.appendChild(button);
      }
      pageSummary.textContent = `Page ${selectedPage} of ${pageCount}`;
    };
    const applyLiveFilter = ({persist = true, resetPage = false, scroll = false} = {}) => {
      if (resetPage) currentPage = 1;
      const state = currentState();
      const tokens = searchTokens(state.q);
      const orderedRows = Array.from(document.querySelectorAll('[data-live-channel-rows] tr[data-channel-search]'));
      const matchedRows = [];
      orderedRows.forEach((row) => {
        const haystack = normalizeSearch(row.dataset.channelSearch || '');
        const categoryIds = String(row.dataset.channelCategoryIds || row.dataset.channelCategoryId || '').split(',').map((item) => item.trim()).filter(Boolean);
        const nodeIds = String(row.dataset.channelNodeIds || '').split(',').map((item) => item.trim()).filter(Boolean);
        const textMatched = tokens.length === 0 || tokens.every((token) => haystack.includes(token));
        const categoryMatched = !state.category || (state.category === 'uncategorized' ? categoryIds.length === 0 : categoryIds.includes(state.category));
        const nodeMatched = !state.node || nodeIds.includes(state.node);
        // STREAMFORGE_CHANNEL_STRICT_HLS_UP_WAITING_V3055:
        // Preserve exact delivery-state filtering inside the paginated catalogue.
        const runtimeStatus = String(row.dataset.runtimeStatus || 'unknown').toLowerCase();
        const statusMatched = !state.status || runtimeStatus === state.status;
        const matched = textMatched && categoryMatched && nodeMatched && statusMatched;
        if (matched) matchedRows.push(row);
        row.hidden = true;
      });
      const pageSize = state.limit === 'all' ? 0 : Math.max(1, Number(state.limit) || 10);
      const pageCount = pageSize > 0 ? Math.max(1, Math.ceil(matchedRows.length / pageSize)) : 1;
      currentPage = Math.min(Math.max(1, currentPage), pageCount);
      const fromIndex = pageSize > 0 ? (currentPage - 1) * pageSize : 0;
      const toIndex = pageSize > 0 ? Math.min(matchedRows.length, fromIndex + pageSize) : matchedRows.length;
      matchedRows.slice(fromIndex, toIndex).forEach((row) => { row.hidden = false; });
      const visible = Math.max(0, toIndex - fromIndex);
      const matched = matchedRows.length;
      if (result) {
        if (!matched) result.textContent = `0 of ${orderedRows.length} channels`;
        else if (matched === orderedRows.length) result.textContent = `${fromIndex + 1}–${toIndex} of ${orderedRows.length} channels`;
        else result.textContent = `${fromIndex + 1}–${toIndex} of ${matched} matched · ${orderedRows.length} total`;
      }
      if (empty) empty.hidden = matched !== 0;
      if (clearButton) clearButton.hidden = !(state.q || state.category || state.node || state.status);
      renderPagination(pageCount, currentPage, matched, pageSize);
      if (persist) {
        const nextState = currentState();
        writeStoredState(nextState);
        updateAddressBar(nextState);
      }
      if (scroll) document.querySelector('.main-channel-control-table')?.scrollIntoView({block: 'start', behavior: 'smooth'});
      document.dispatchEvent(new CustomEvent('streamforge:channel-filtered'));
    };

    restoreState();
    liveChannelFilterForm.addEventListener('submit', (event) => event.preventDefault());
    input?.addEventListener('input', () => applyLiveFilter({resetPage: true}));
    input?.addEventListener('search', () => applyLiveFilter({resetPage: true}));
    input?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') event.preventDefault();
      if (event.key === 'Escape') {
        input.value = '';
        applyLiveFilter({resetPage: true});
      }
    });
    [categorySelect, nodeSelect, statusSelect, limitSelect].forEach((select) => select?.addEventListener('change', () => applyLiveFilter({resetPage: true})));
    clearButton?.addEventListener('click', () => {
      if (input) input.value = '';
      if (categorySelect) categorySelect.value = '';
      if (nodeSelect) nodeSelect.value = '';
      if (statusSelect) statusSelect.value = '';
      currentPage = 1;
      try { sessionStorage.removeItem(storageKey); } catch (_) {}
      applyLiveFilter();
      input?.focus();
    });
    prevButton?.addEventListener('click', () => { if (currentPage > 1) { currentPage -= 1; applyLiveFilter({scroll: true}); } });
    nextButton?.addEventListener('click', () => { currentPage += 1; applyLiveFilter({scroll: true}); });
    pageNumbers?.addEventListener('click', (event) => {
      const button = event.target.closest('[data-channel-page]');
      if (!button) return;
      currentPage = Math.max(1, Number(button.dataset.channelPage) || 1);
      applyLiveFilter({scroll: true});
    });
    window.streamforgeApplyChannelFilter = () => applyLiveFilter({persist: false});
    applyLiveFilter();
  }

  // STREAMFORGE_CHANNEL_TABLE_SORT_V65R10:
  // Main Channels table headers sort the already-loaded catalogue in-browser.
  // Repeated clicks toggle ascending/descending; live numeric fields keep their
  // active order when status polling refreshes row data.
  const channelSortTable = document.querySelector('.main-channel-control-table');
  if (channelSortTable) {
    const tbody = channelSortTable.querySelector('tbody[data-live-channel-rows]');
    const sortableRows = Array.from(tbody?.querySelectorAll('tr[data-channel-id]') || []);
    const emptyRow = tbody?.querySelector('[data-live-filter-empty]');
    const buttons = Array.from(channelSortTable.querySelectorAll('[data-channel-sort-key]'));
    const sortStorageKey = 'streamforge.channels.sort.v1';
    let sortState = {key: '', type: 'text', direction: 'asc'};

    const numberValue = (value) => {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : -1;
    };
    const textValue = (value) => String(value ?? '').trim().toLocaleLowerCase();
    const statusRank = (value) => ({up: 0, waiting: 1, down: 2, error: 3, unknown: 4}[textValue(value)] ?? 5);
    const rowValue = (row, key, type) => {
      const value = row.dataset[`sort${key.charAt(0).toUpperCase()}${key.slice(1)}`];
      if (type === 'number') return numberValue(value);
      if (type === 'status') return [statusRank(value), -numberValue(row.dataset.sortUptime)];
      return textValue(value);
    };
    const compareValues = (left, right, type) => {
      if (type === 'status') {
        const primary = left[0] - right[0];
        return primary || (left[1] - right[1]);
      }
      if (type === 'number') return left - right;
      return left.localeCompare(right, undefined, {numeric: true, sensitivity: 'base'});
    };
    const updateSortHeaders = () => {
      buttons.forEach((button) => {
        const active = button.dataset.channelSortKey === sortState.key;
        const th = button.closest('th');
        const indicator = button.querySelector('.channel-sort-indicator');
        if (indicator) indicator.textContent = active ? (sortState.direction === 'asc' ? '▲' : '▼') : '↕';
        button.classList.toggle('active', active);
        if (th) th.setAttribute('aria-sort', active ? (sortState.direction === 'asc' ? 'ascending' : 'descending') : 'none');
      });
    };
    const applyChannelSort = ({persist = true} = {}) => {
      if (!tbody || !sortState.key) { updateSortHeaders(); return; }
      const direction = sortState.direction === 'desc' ? -1 : 1;
      sortableRows.sort((a, b) => {
        const compared = compareValues(rowValue(a, sortState.key, sortState.type), rowValue(b, sortState.key, sortState.type), sortState.type);
        return (compared || (numberValue(a.dataset.sortDbId) - numberValue(b.dataset.sortDbId))) * direction;
      });
      sortableRows.forEach((row) => tbody.appendChild(row));
      if (emptyRow) tbody.appendChild(emptyRow);
      updateSortHeaders();
      if (persist) {
        try { sessionStorage.setItem(sortStorageKey, JSON.stringify(sortState)); } catch (_) {}
      }
      if (typeof window.streamforgeApplyChannelFilter === 'function') window.streamforgeApplyChannelFilter();
    };
    try {
      const stored = JSON.parse(sessionStorage.getItem(sortStorageKey) || '{}');
      if (stored && buttons.some((button) => button.dataset.channelSortKey === stored.key)) {
        const button = buttons.find((item) => item.dataset.channelSortKey === stored.key);
        sortState = {
          key: String(stored.key || ''),
          type: String(button?.dataset.channelSortType || stored.type || 'text'),
          direction: stored.direction === 'desc' ? 'desc' : 'asc',
        };
      }
    } catch (_) {}
    buttons.forEach((button) => {
      button.addEventListener('click', () => {
        const key = String(button.dataset.channelSortKey || '');
        const type = String(button.dataset.channelSortType || 'text');
        if (sortState.key === key) sortState.direction = sortState.direction === 'asc' ? 'desc' : 'asc';
        else sortState = {key, type, direction: 'asc'};
        sortState.type = type;
        applyChannelSort();
      });
    });
    window.streamforgeApplyChannelSort = () => applyChannelSort({persist: false});
    applyChannelSort({persist: false});
  }

  const nodeCardSearch = document.querySelector('[data-node-card-search]');
  if (nodeCardSearch) {
    const cards = Array.from(document.querySelectorAll('[data-node-card]'));
    const filterNodeCards = () => {
      const tokens = filterTokens(nodeCardSearch.value);
      cards.forEach((card) => {
        const haystack = normalizeFilterText(card.dataset.nodeCardSearch || '');
        card.hidden = Boolean(tokens.length && !tokens.every((token) => haystack.includes(token)));
      });
    };
    nodeCardSearch.addEventListener('input', filterNodeCards);
    nodeCardSearch.addEventListener('search', filterNodeCards);
    filterNodeCards();
  }

  const channelLogModal = (() => {
    let modal = null;
    let state = {endpoint: '', clearEndpoint: '', channelName: '', page: 1, pageSize: 10, loading: false};
    const ensure = () => {
      if (modal) return modal;
      modal = document.createElement('div');
      modal.className = 'channel-log-overlay';
      modal.hidden = true;
      modal.innerHTML = `
        <section class="channel-log-dialog" role="dialog" aria-modal="true" aria-labelledby="channel-log-title">
          <header class="channel-log-header">
            <div><h2 id="channel-log-title" data-channel-log-title>Channel stream logs</h2><p data-channel-log-subtitle>Started, stopped, restart and failure events</p></div>
            <button type="button" class="channel-log-close" data-channel-log-close aria-label="Close">×</button>
          </header>
          <div class="channel-log-toolbar">
            <button type="button" class="button danger small" data-channel-log-clear hidden>Clear Stream Logs</button>
            <label class="channel-log-page-size">Show <select data-channel-log-size><option>10</option><option>25</option><option>50</option><option>100</option></select> entries</label>
            <span class="channel-log-count" data-channel-log-count></span>
          </div>
          <div class="channel-log-current" data-channel-log-current hidden></div>
          <div class="channel-log-table-wrap">
            <table class="channel-log-table">
              <thead><tr><th>Server name</th><th>Source</th><th>Action</th><th>Date</th></tr></thead>
              <tbody data-channel-log-body></tbody>
            </table>
          </div>
          <footer class="channel-log-footer"><span data-channel-log-range></span><nav class="channel-log-pagination" data-channel-log-pages aria-label="Channel log pages"></nav></footer>
        </section>`;
      document.body.appendChild(modal);
      modal.querySelector('[data-channel-log-close]')?.addEventListener('click', close);
      modal.addEventListener('click', (event) => { if (event.target === modal) close(); });
      modal.querySelector('[data-channel-log-size]')?.addEventListener('change', (event) => {
        state.pageSize = Math.max(5, Number(event.target.value) || 10);
        state.page = 1;
        load();
      });
      modal.querySelector('[data-channel-log-clear]')?.addEventListener('click', async () => {
        if (!state.clearEndpoint || !window.confirm(`Clear stream logs for ${state.channelName || 'this channel'}?`)) return;
        const button = modal.querySelector('[data-channel-log-clear]');
        button.disabled = true;
        try {
          const response = await fetch(state.clearEndpoint, {method: 'POST', cache: 'no-store'});
          const data = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
          state.page = 1;
          document.querySelectorAll(`[data-error-endpoint="${CSS.escape(state.rawEndpoint || state.endpoint)}"]`).forEach((trigger) => {
            trigger.dataset.hasError = '0';
            trigger.className = trigger.className.replace(/\bhas-error\b/g, '').replace(/\s+/g, ' ').trim() + ' healthy';
            trigger.removeAttribute('data-tooltip');
          });
          await load();
        } catch (error) {
          window.alert(error.message || 'Could not clear logs');
        } finally {
          button.disabled = false;
        }
      });
      return modal;
    };
    const close = () => {
      if (!modal) return;
      modal.hidden = true;
      document.body.classList.remove('channel-log-open');
    };
    const actionClass = (value) => String(value || 'info').replace(/[^a-z0-9-]/gi, '').toLowerCase();
    const pageItems = (page, pages) => {
      if (pages <= 7) return Array.from({length: pages}, (_, index) => index + 1);
      const values = [1];
      const start = Math.max(2, page - 2);
      const end = Math.min(pages - 1, page + 2);
      if (start > 2) values.push('…');
      for (let value = start; value <= end; value += 1) values.push(value);
      if (end < pages - 1) values.push('…');
      values.push(pages);
      return values;
    };
    const render = (data) => {
      const root = ensure();
      const body = root.querySelector('[data-channel-log-body]');
      const entries = Array.isArray(data.entries) ? data.entries : [];
      root.querySelector('[data-channel-log-title]').textContent = data.channel_name || state.channelName || 'Channel stream logs';
      root.querySelector('[data-channel-log-subtitle]').textContent = 'Stream event and error history';
      const current = root.querySelector('[data-channel-log-current]');
      const currentText = String(data.current_error || '').trim();
      current.hidden = !currentText;
      current.innerHTML = currentText ? `<b>Current error</b><span>${escapeHtml(currentText)}</span>` : '';
      const clearButton = root.querySelector('[data-channel-log-clear]');
      // STREAMFORGE_CHANNEL_STREAM_LOG_CLEAR_PREFIX_V66:
      // The JSON endpoint returns canonical /channels/... URLs. Never let that
      // overwrite the already panel-prefixed clear URL (for example /admin).
      const responseClearEndpoint = String(data.clear_endpoint || state.rawClearEndpoint || state.clearEndpoint || '');
      state.rawClearEndpoint = responseClearEndpoint;
      state.clearEndpoint = panelUrl(responseClearEndpoint);
      clearButton.hidden = !data.can_clear;
      body.innerHTML = entries.map((entry) => {
        let detailText = String(entry.details || '').trim();
        if (detailText) {
          try { detailText = JSON.stringify(JSON.parse(detailText), null, 2); } catch (_) {}
        }
        return `<tr class="channel-log-row" tabindex="0" data-channel-log-row>
          <td>${escapeHtml(entry.server_name || 'Main Server')}</td>
          <td class="mono">${escapeHtml(entry.source || '—')}</td>
          <td><span class="channel-log-action ${actionClass(entry.action_class)}">${escapeHtml(entry.action || 'INFO')}</span></td>
          <td>${escapeHtml(entry.created_at || '')}</td>
        </tr><tr class="channel-log-details" hidden><td colspan="4"><b>${escapeHtml(entry.message || 'Event details')}</b>${detailText ? `<pre>${escapeHtml(detailText)}</pre>` : ''}</td></tr>`;
      }).join('') || '<tr><td colspan="4" class="empty">No stream logs found.</td></tr>';
      body.querySelectorAll('[data-channel-log-row]').forEach((row) => {
        const toggle = () => { const details = row.nextElementSibling; if (details) details.hidden = !details.hidden; };
        row.addEventListener('click', toggle);
        row.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggle(); } });
      });
      const total = Math.max(0, Number(data.total) || 0);
      const page = Math.max(1, Number(data.page) || 1);
      const pages = Math.max(1, Number(data.pages) || 1);
      const size = Math.max(1, Number(data.page_size) || state.pageSize);
      state.page = page;
      state.pageSize = size;
      root.querySelector('[data-channel-log-size]').value = String(size);
      root.querySelector('[data-channel-log-count]').textContent = `${total} event${total === 1 ? '' : 's'}`;
      const from = total ? ((page - 1) * size) + 1 : 0;
      const to = Math.min(total, page * size);
      root.querySelector('[data-channel-log-range]').textContent = total ? `Showing ${from} to ${to} of ${total}` : 'Showing 0 entries';
      const pagination = root.querySelector('[data-channel-log-pages]');
      const makeButton = (label, target, disabled = false, active = false) => `<button type="button" data-page="${target}" ${disabled ? 'disabled' : ''} class="${active ? 'active' : ''}">${label}</button>`;
      pagination.innerHTML = makeButton('Previous', page - 1, page <= 1)
        + pageItems(page, pages).map((item) => item === '…' ? '<span>…</span>' : makeButton(item, item, false, Number(item) === page)).join('')
        + makeButton('Next', page + 1, page >= pages);
      pagination.querySelectorAll('button[data-page]').forEach((button) => button.addEventListener('click', () => {
        if (button.disabled) return;
        state.page = Number(button.dataset.page) || 1;
        load();
      }));
    };
    const load = async () => {
      if (!state.endpoint || state.loading) return;
      state.loading = true;
      const root = ensure();
      root.querySelector('[data-channel-log-body]').innerHTML = '<tr><td colspan="4" class="channel-log-loading">Loading stream logs…</td></tr>';
      try {
        const url = new URL(state.endpoint, window.location.origin);
        url.searchParams.set('page', String(state.page));
        url.searchParams.set('page_size', String(state.pageSize));
        url.searchParams.set('_', String(Date.now()));
        const response = await fetch(url, {cache: 'no-store'});
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        render(data);
      } catch (error) {
        root.querySelector('[data-channel-log-body]').innerHTML = `<tr><td colspan="4" class="empty">${escapeHtml(error.message || 'Could not load stream logs')}</td></tr>`;
      } finally {
        state.loading = false;
      }
    };
    const open = (trigger, requireError = true) => {
      if (requireError && trigger.dataset.hasError !== '1') return;
      // STREAMFORGE_CHANNEL_STREAM_LOG_PREFIX_FIX_V2244:
      // The template stores canonical /channels/... endpoints, but the live
      // browser request must stay inside the configured Panel/API root such as
      // /admin. Otherwise Stream logs opens but fetches the public/root URL.
      const rawEndpoint = String(trigger.dataset.errorEndpoint || '');
      const rawClearEndpoint = String(trigger.dataset.clearEndpoint || '');
      state = {
        rawEndpoint,
        rawClearEndpoint,
        endpoint: panelUrl(rawEndpoint),
        clearEndpoint: panelUrl(rawClearEndpoint),
        channelName: trigger.dataset.channelName || '',
        page: 1,
        pageSize: 10,
        loading: false,
      };
      const root = ensure();
      root.hidden = false;
      document.body.classList.add('channel-log-open');
      load();
      requestAnimationFrame(() => root.querySelector('[data-channel-log-close]')?.focus());
    };
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && modal && !modal.hidden) close(); });
    return {open, close};
  })();

  document.querySelectorAll('[data-runtime-alert-menu]').forEach((details) => {
    const trigger = details.querySelector('[data-runtime-alert]');
    trigger?.addEventListener('click', (event) => {
      event.preventDefault();
      details.open = false;
      channelLogModal.open(trigger, true);
    });
  });
  document.querySelectorAll('[data-channel-log-open]').forEach((trigger) => {
    trigger.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      const details = trigger.closest('details');
      if (details) details.open = false;
      channelLogModal.open(trigger, false);
    });
  });

  const positionFloatingDetails = (details) => {
    const menu = details.querySelector('.server-cluster-menu, .channel-more-popover');
    const summary = details.querySelector('summary');
    if (!menu || !summary || !details.open) return;
    const rect = summary.getBoundingClientRect();
    menu.style.position = 'fixed';
    menu.style.top = `${Math.min(window.innerHeight - menu.offsetHeight - 10, rect.bottom + 6)}px`;
    if (details.classList.contains('channel-more-menu')) {
      menu.style.right = `${Math.max(8, window.innerWidth - rect.right)}px`;
      menu.style.left = 'auto';
    } else {
      menu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - menu.offsetWidth - 8))}px`;
      menu.style.right = 'auto';
    }
  };
  document.querySelectorAll('.server-cluster, .channel-more-menu').forEach((details) => {
    details.addEventListener('toggle', () => {
      if (details.open) {
        document.querySelectorAll('.server-cluster[open], .channel-more-menu[open]').forEach((other) => { if (other !== details) other.open = false; });
        requestAnimationFrame(() => positionFloatingDetails(details));
      }
    });
  });
  window.addEventListener('resize', () => document.querySelectorAll('.server-cluster[open], .channel-more-menu[open]').forEach(positionFloatingDetails));
  window.addEventListener('scroll', () => document.querySelectorAll('.server-cluster[open], .channel-more-menu[open]').forEach(positionFloatingDetails), true);
  document.addEventListener('click', (event) => {
    document.querySelectorAll('.server-cluster[open], .channel-more-menu[open]').forEach((details) => {
      if (!details.contains(event.target)) details.open = false;
    });
  });

  const nodeCards = Array.from(document.querySelectorAll('[data-node-card]'));
  if (nodeCards.length) {
    const updateNodeCard = async (card) => {
      const id = card.dataset.nodeId;
      const statusNode = card.querySelector('[data-node-status]');
      const cpuNode = card.querySelector('[data-node-cpu]');
      const memoryNode = card.querySelector('[data-node-memory]');
      const downloadNode = card.querySelector('[data-node-download]');
      const uploadNode = card.querySelector('[data-node-upload]');
      const uptimeNode = card.querySelector('[data-node-uptime]');
      const onlineUsersNode = card.querySelector('[data-node-online-users]');
      const upNode = card.querySelector('[data-node-up]');
      const downNode = card.querySelector('[data-node-down]');
      const channelsNode = card.querySelector('[data-node-channels]');
      const errorNode = card.querySelector('[data-node-error]');
      const versionNode = card.querySelector('[data-node-version]');
      const capacityNode = card.querySelector('[data-node-capacity]');
      const controller = new AbortController();
      const requestTimeout = window.setTimeout(() => controller.abort(), 5000);
      try {
        const endpoint = card.dataset.nodeStatusEndpoint || panelUrl(`/nodes/${id}/status.json`);
        const separator = endpoint.includes('?') ? '&' : '?';
        const response = await fetch(`${endpoint}${separator}_=${Date.now()}`, {cache: 'no-store', signal: controller.signal, credentials: 'same-origin'});
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
        const metrics = data.metrics || {};
        const cpu = metrics.cpu || {};
        const memory = metrics.memory || {};
        const network = metrics.network || {};
        const hasCpu = present(metrics.cpu_percent) || present(cpu.percent);
        const hasMemory = present(metrics.memory_percent) || present(memory.percent);
        const hasDown = present(metrics.download_mbps) || present(metrics.network_download_mbps) || present(network.rx_bits_per_second);
        const hasUp = present(metrics.upload_mbps) || present(metrics.network_upload_mbps) || present(network.tx_bits_per_second);
        const cpuPercent = Number(metrics.cpu_percent ?? cpu.percent ?? 0);
        const memoryPercent = Number(metrics.memory_percent ?? memory.percent ?? 0);
        const down = present(metrics.download_mbps)
          ? Number(metrics.download_mbps)
          : present(metrics.network_download_mbps)
            ? Number(metrics.network_download_mbps)
            : Number(network.rx_bits_per_second ?? 0) / 1_000_000;
        const up = present(metrics.upload_mbps)
          ? Number(metrics.upload_mbps)
          : present(metrics.network_upload_mbps)
            ? Number(metrics.network_upload_mbps)
            : Number(network.tx_bits_per_second ?? 0) / 1_000_000;
        const formatNodeUptime = (value) => {
          let seconds = Math.max(0, Math.floor(Number(value) || 0));
          const days = Math.floor(seconds / 86400); seconds %= 86400;
          const hours = Math.floor(seconds / 3600); seconds %= 3600;
          const minutes = Math.floor(seconds / 60); seconds %= 60;
          return [days ? `${days}d` : '', `${hours}h`, `${minutes}m`, `${seconds}s`].filter(Boolean).join(' ');
        };
        if (statusNode) {
          statusNode.textContent = 'online';
          statusNode.className = 'status running';
        }
        if (cpuNode && hasCpu) cpuNode.textContent = `${Math.max(0, cpuPercent).toFixed(1)}%`;
        if (memoryNode && hasMemory) memoryNode.textContent = `${Math.max(0, memoryPercent).toFixed(1)}%`;
        if (downloadNode && hasDown) downloadNode.textContent = `${Math.max(0, down).toFixed(2)} Mb/s`;
        if (uploadNode && hasUp) uploadNode.textContent = `${Math.max(0, up).toFixed(2)} Mb/s`;
        if (uptimeNode && present(metrics.uptime_seconds)) uptimeNode.textContent = formatNodeUptime(metrics.uptime_seconds);
        if (onlineUsersNode) onlineUsersNode.textContent = String(Number(data.online_users) || 0);
        if (versionNode) versionNode.textContent = String(data.version || 'unknown');
        if (capacityNode) {
          const limit = Number(data.total_max_connections || 0);
          const active = Number(data.active_connections ?? data.online_users ?? 0);
          capacityNode.textContent = limit > 0 ? `${active} / ${limit}` : `${active} / Unlimited`;
        }
        // STREAMFORGE_NODE_CARD_WAITING_AS_DOWN_V46: backend already folds Waiting into Down.
        const counts = data.channel_counts || {};
        if (upNode) upNode.textContent = String(Number(counts.up) || 0);
        if (downNode) downNode.textContent = String(Number(counts.down) || 0);
        if (channelsNode) channelsNode.textContent = String(Number(counts.total) || 0);
        if (errorNode) {
          errorNode.hidden = true;
          errorNode.textContent = '';
        }
      } catch (error) {
        // STREAMFORGE_NODE_CARD_TIMEOUT_PRESERVE_HEARTBEAT_V60R2:
        // A browser-side timeout means this Main request was delayed; it does
        // not prove the Remote Node is offline. Keep the last heartbeat-backed
        // card state and let the next poll retry. A real stale/offline result is
        // returned explicitly by /status.json and handled in the success path.
        const timedOut = error && error.name === 'AbortError';
        if (!timedOut && statusNode) {
          statusNode.textContent = 'offline';
          statusNode.className = 'status error';
        }
        if (!timedOut && versionNode && ['checking…', 'checking...'].includes(versionNode.textContent.trim().toLowerCase())) {
          versionNode.textContent = 'unknown';
        }
        if (errorNode) {
          if (timedOut) {
            errorNode.hidden = true;
            errorNode.textContent = '';
          } else {
            errorNode.hidden = false;
            errorNode.textContent = error.message;
          }
        }
      } finally {
        window.clearTimeout(requestTimeout);
      }
    };
    let nodeRefreshRunning = false;
    const refreshNodes = async () => {
      if (nodeRefreshRunning) return;
      nodeRefreshRunning = true;
      try {
        for (let index = 0; index < nodeCards.length; index += 3) {
          await Promise.allSettled(nodeCards.slice(index, index + 3).map(updateNodeCard));
        }
      } finally {
        nodeRefreshRunning = false;
      }
    };
    refreshNodes();
    setInterval(refreshNodes, 10000);
  }

  const roleForm = document.querySelector('[data-role-form]');
  if (roleForm) {
    const checks = Array.from(roleForm.querySelectorAll('input[name="permissions"]'));
    const selectAllButton = roleForm.querySelector('[data-role-select-all]');
    const clearAllButton = roleForm.querySelector('[data-role-clear-all]');
    const countNode = roleForm.querySelector('[data-role-selected-count]');
    const dependencies = {
      'system_metrics.view': ['dashboard.view'],
      'main_access.view': ['nodes.view'],
      'main_access.edit': ['main_access.view', 'nodes.view'],
      'settings.edit': ['settings.view'],
      'backups.add': ['backups.view'],
      'backups.edit': ['backups.view'],
      'backups.run': ['backups.view'],
      'backups.download': ['backups.view'],
      'backups.restore': ['backups.view'],
      'backups.delete': ['backups.view'],
      'main_system.service_restart': ['nodes.view'],
      'main_system.reboot': ['nodes.view'],
      'nodes.create': ['nodes.view'],
      'nodes.edit': ['nodes.view'],
      'nodes.delete': ['nodes.view'],
      'nodes.service_restart': ['nodes.view'],
      'nodes.reboot': ['nodes.view'],
      'nodes.test': ['nodes.view'],
      'channels.assign_node': ['channels.view', 'nodes.view'],
      'channels.info': ['channels.view'],
      'channels.playback': ['channels.view'],
      'channels.create': ['channels.view'],
      'channels.edit': ['channels.view'],
      'channels.start': ['channels.view'],
      'channels.stop': ['channels.view'],
      'channels.restart': ['channels.view'],
      'channels.delete': ['channels.view'],
      'categories.create': ['categories.view'],
      'categories.edit': ['categories.view'],
      'categories.reorder': ['categories.view'],
      'categories.delete': ['categories.view'],
      'imports.execute': ['imports.view'],
      'stream_users.playlist': ['stream_users.view'],
      'stream_users.create': ['stream_users.view'],
      'stream_users.edit': ['stream_users.view'],
      'stream_users.token': ['stream_users.view'],
      'stream_users.delete': ['stream_users.view'],
      'logs.clear': ['logs.view'],
      'panel_users.create': ['panel_users.view'],
      'panel_users.edit': ['panel_users.view'],
      'panel_users.delete': ['panel_users.view'],
      'roles.create': ['roles.view'],
      'roles.edit': ['roles.view'],
      'roles.delete': ['roles.view'],
    };
    const byValue = new Map(checks.map((checkbox) => [checkbox.value, checkbox]));

    const enforceDependencies = () => {
      checks.filter((checkbox) => checkbox.checked).forEach((checkbox) => {
        (dependencies[checkbox.value] || []).forEach((required) => {
          const requiredCheck = byValue.get(required);
          if (requiredCheck) requiredCheck.checked = true;
        });
      });
    };

    const updateRoleSelection = () => {
      enforceDependencies();
      const selected = checks.filter((checkbox) => checkbox.checked).length;
      if (countNode) countNode.textContent = `${selected} selected`;
      if (selectAllButton) selectAllButton.disabled = selected === checks.length;
      if (clearAllButton) clearAllButton.disabled = selected === 0;
      checks.forEach((checkbox) => {
        const option = checkbox.closest('.permission-option');
        if (option) option.classList.toggle('selected', checkbox.checked);
      });
      roleForm.querySelectorAll('.permission-group').forEach((group) => {
        const groupChecks = Array.from(group.querySelectorAll('input[name="permissions"]'));
        const toggle = group.querySelector('[data-permission-group-toggle]');
        if (!toggle) return;
        const allSelected = groupChecks.length > 0 && groupChecks.every((checkbox) => checkbox.checked);
        toggle.textContent = allSelected ? 'Clear group' : 'Select group';
        toggle.dataset.groupSelected = allSelected ? '1' : '0';
      });
    };

    selectAllButton?.addEventListener('click', () => {
      checks.forEach((checkbox) => { checkbox.checked = true; });
      updateRoleSelection();
    });
    clearAllButton?.addEventListener('click', () => {
      checks.forEach((checkbox) => { checkbox.checked = false; });
      updateRoleSelection();
    });
    roleForm.querySelectorAll('[data-permission-group-toggle]').forEach((button) => {
      button.addEventListener('click', () => {
        const group = button.closest('.permission-group');
        const groupChecks = Array.from(group?.querySelectorAll('input[name="permissions"]') || []);
        const shouldSelect = button.dataset.groupSelected !== '1';
        groupChecks.forEach((checkbox) => { checkbox.checked = shouldSelect; });
        updateRoleSelection();
      });
    });
    checks.forEach((checkbox) => checkbox.addEventListener('change', updateRoleSelection));
    updateRoleSelection();
  }

  const userNodeList = document.querySelector('[data-user-node-list]');
  if (userNodeList) {
    const nodeChecks = Array.from(userNodeList.querySelectorAll('input[name="node_ids"]'));
    const loadBalanceToggle = document.querySelector('[data-user-load-balance-toggle]');
    const selectAllNodes = document.querySelector('[data-user-node-select-all]');
    const clearAllNodes = document.querySelector('[data-user-node-clear-all]');
    const enforceNodeLimit = (preferred = null) => {
      if (loadBalanceToggle?.checked) return;
      const selected = nodeChecks.filter((checkbox) => checkbox.checked);
      const keep = preferred?.checked ? preferred : selected[0];
      selected.forEach((checkbox) => { checkbox.checked = checkbox === keep; });
    };
    const updateNodeSelection = () => {
      enforceNodeLimit();
      const selected = nodeChecks.filter((checkbox) => checkbox.checked).length;
      if (selectAllNodes) selectAllNodes.disabled = !loadBalanceToggle?.checked || nodeChecks.length === 0 || selected === nodeChecks.length;
      if (clearAllNodes) clearAllNodes.disabled = selected === 0;
      nodeChecks.forEach((checkbox) => {
        const label = checkbox.closest('label');
        if (label) label.classList.toggle('channel-entitlement-selected', checkbox.checked);
      });
    };
    selectAllNodes?.addEventListener('click', () => {
      if (!loadBalanceToggle?.checked) return;
      nodeChecks.forEach((checkbox) => { checkbox.checked = true; });
      updateNodeSelection();
    });
    clearAllNodes?.addEventListener('click', () => {
      nodeChecks.forEach((checkbox) => { checkbox.checked = false; });
      updateNodeSelection();
    });
    nodeChecks.forEach((checkbox) => checkbox.addEventListener('change', () => {
      enforceNodeLimit(checkbox);
      updateNodeSelection();
    }));
    loadBalanceToggle?.addEventListener('change', updateNodeSelection);
    updateNodeSelection();
  }

  const rows = document.querySelectorAll('[data-channel-id]');
  if (rows.length) {
    // STREAMFORGE_MAIN_STATUS_ROW_CACHE_V1116:
    // Status polling used to repeat many querySelector()/closest() traversals for
    // every channel on every refresh. Cache stable row nodes once; only the two
    // optional dynamically-created controls remain mutable.
    const rowStateById = new Map();
    Array.from(rows).forEach((row) => {
      const id = Number(row.dataset.channelId);
      if (!Number.isFinite(id)) return;
      rowStateById.set(id, {
        row,
        status: row.querySelector('[data-status]'),
        bitrate: row.querySelector('[data-bitrate]'),
        resolution: row.querySelector('[data-resolution]'),
        speed: row.querySelector('[data-speed]'),
        fps: row.querySelector('[data-fps]'),
        onlineUsers: row.querySelector('[data-online-users]'),
        pid: row.querySelector('[data-pid]'),
        uptime: row.querySelector('[data-uptime]'),
        httpAction: row.querySelector('[data-http-action]'),
        httpSlot: row.querySelector('[data-http-slot]'),
        runtimeAlert: row.querySelector('[data-runtime-alert]'),
        runtimeAlertDot: row.querySelector('[data-runtime-alert] span'),
        activeSources: Array.from(row.querySelectorAll('[data-active-source]')),
        runtimeForms: Array.from(row.querySelectorAll('form[data-channel-runtime-action]')),
        restartFlash: row.querySelector('[data-restart-flash]'),
        dashboard: Boolean(row.closest('.dashboard-control-table')),
      });
    });
    let refreshInFlight = false;
    const refresh = async () => {
      if (refreshInFlight) return;
      const statusFilterActive = Boolean(document.querySelector('[data-channel-status-filter]')?.value);
      const visibleChannelIds = Array.from(rows)
        .filter((row) => statusFilterActive || !row.hidden)
        .map((row) => Number(row.dataset.channelId))
        .filter(Number.isFinite);
      if (!visibleChannelIds.length) return;
      const statusUrl = panelUrl(`/status.json?channel_ids=${encodeURIComponent(visibleChannelIds.join(','))}`);
      refreshInFlight = true;
      const controller = new AbortController();
      const requestTimeout = window.setTimeout(() => controller.abort(), 6000);
      try {
        // STREAMFORGE_MAIN_POLL_BACKPRESSURE_V34: a slow status request must
        // not pin this page forever. Abort and let the next interval retry.
        const response = await fetch(statusUrl, {cache: 'no-store', signal: controller.signal});
        if (!response.ok) return;
        const data = await response.json();
        let total = 0;
        data.channels.forEach((channel) => {
          const state = rowStateById.get(Number(channel.id));
          if (!state) return;
          const {row, status, bitrate, resolution, speed, fps, onlineUsers, pid, uptime, httpSlot, runtimeAlert} = state;
          let httpAction = state.httpAction;
          const httpUrl = String(channel.http_url || row.dataset.httpUrl || httpAction?.dataset.httpUrl || '').trim();
          if (!httpAction && httpSlot && httpUrl) {
            httpAction = document.createElement('a');
            httpAction.className = 'channel-icon-button http http-action-hidden';
            httpAction.dataset.httpAction = '';
            httpAction.dataset.httpUrl = httpUrl;
            httpAction.href = httpUrl;
            httpAction.target = '_blank';
            httpAction.rel = 'noopener';
            httpAction.title = 'Open HTTP output';
            httpAction.setAttribute('aria-label', 'Open HTTP output');
            httpAction.innerHTML = '<span class="button-glyph">H</span>';
            httpSlot.appendChild(httpAction);
            state.httpAction = httpAction;
          }
          const deliveryStatus = String(channel.status || 'down').toLowerCase();
          const processStatus = String(channel.runtime_status || channel.status || 'unknown').toLowerCase();
          row.dataset.runtimeStatus = deliveryStatus;
          row.dataset.processStatus = processStatus;
          row.dataset.sortStatus = deliveryStatus;
          row.dataset.sortUptime = String(Math.max(0, Number(channel.uptime_seconds) || 0));
          row.dataset.sortBitrate = String(Math.max(0, Number(channel.bitrate) || 0));
          row.dataset.sortOnline = String(Math.max(0, Number(channel.online_users) || 0));

          // STREAMFORGE_MAIN_CHANNEL_ACTION_BUTTON_STATE_V2251:
          // STREAMFORGE_MAIN_CHANNEL_ACTION_DISPLAY_SYNC_V2252:
          const actionUp = Boolean(channel.desired_running)
            || ['running', 'starting', 'restarting', 'degraded'].includes(processStatus);
          state.runtimeForms.forEach((form) => {
            const action = String(form.dataset.channelRuntimeAction || '');
            const shouldShow = action === 'start'
              ? !actionUp
              : ((action === 'stop' || action === 'restart') ? actionUp : true);
            form.hidden = !shouldShow;
            form.style.display = shouldShow ? '' : 'none';
            form.setAttribute('aria-hidden', shouldShow ? 'false' : 'true');
          });

          if (state.dashboard) {
            row.dataset.runtimeLive = deliveryStatus === 'up' ? '1' : '0';
          }
          if (status) {
            status.textContent = channel.status;
            status.className = `status ${channel.status}`;
          }
          if (runtimeAlert) {
            const errorText = String(channel.last_error || '').trim();
            const errorCount = Math.max(0, Number(channel.error_count) || 0);
            const restartCount = Math.max(0, Number(channel.restart_count) || 0);
            const hasError = Boolean(errorText || errorCount);
            const indicatorState = hasError ? 'has-error' : (deliveryStatus === 'up' ? 'healthy' : 'idle');
            const dot = state.runtimeAlertDot;
            if (dot) dot.textContent = hasError ? '!' : '';
            runtimeAlert.className = `runtime-alert-dot ${indicatorState}`;
            runtimeAlert.dataset.hasError = hasError ? '1' : '0';
            if (hasError) {
              runtimeAlert.dataset.tooltip = restartCount > 0
                ? `${restartCount} restart${restartCount === 1 ? '' : 's'} · click for details`
                : `${errorCount || 1} recent error${(errorCount || 1) === 1 ? '' : 's'} · click for details`;
            } else {
              runtimeAlert.removeAttribute('data-tooltip');
            }
            runtimeAlert.setAttribute('aria-label', errorText || (hasError ? 'Channel error details' : (deliveryStatus === 'up' ? 'Up' : (deliveryStatus === 'waiting' ? 'Waiting for HLS output' : 'Down'))));
          }
          if (bitrate) bitrate.textContent = `${Math.max(0, Number(channel.bitrate) || 0)} kb/s`;
          if (resolution) resolution.textContent = String(channel.resolution || 'Source');
          if (onlineUsers) onlineUsers.textContent = String(Math.max(0, Number(channel.online_users) || 0));
          // STREAMFORGE_MAIN_ACTIVE_INPUT_SOURCE_V87
          const activeSource = String(channel.active_source || '').trim();
          if (activeSource) state.activeSources.forEach((node) => { node.textContent = activeSource; });
          if (speed) {
            const speedValue = Math.max(0, Number(channel.speed) || 0);
            row.dataset.sortSpeed = String(speedValue);
            speed.textContent = speedValue > 0 ? `${speedValue.toFixed(2)}x` : '—';
            speed.title = speedValue > 0 ? 'FFmpeg processing speed' : 'No live progress yet';
          }
          if (fps) {
            const fpsValue = Math.max(0, Number(channel.fps) || 0);
            row.dataset.sortFps = String(fpsValue);
            fps.textContent = fpsValue > 0 ? `${Math.round(fpsValue * 100) / 100}` : '—';
            fps.title = fpsValue > 0 ? 'Live output frame rate' : 'FPS not detected yet';
          }
          if (pid) pid.textContent = channel.pid || '—';
          if (uptime) uptime.textContent = formatUptime(channel.uptime_seconds);
          // STREAMFORGE_MAIN_AUTO_RESTART_VISIBLE_V54: keep fast automatic
          // recoveries visible even when the polling interval misses restarting.
          let restartFlash = state.restartFlash;
          if (!restartFlash && uptime && uptime.parentElement) {
            restartFlash = document.createElement('span');
            restartFlash.className = 'restart-flash';
            restartFlash.dataset.restartFlash = '';
            restartFlash.hidden = true;
            uptime.insertAdjacentElement('afterend', restartFlash);
            state.restartFlash = restartFlash;
          }
          if (restartFlash) {
            const recently = Boolean(channel.auto_restarted_recently);
            restartFlash.hidden = !recently;
            if (recently) restartFlash.textContent = `↻ Auto restarted ${Math.max(0, Number(channel.auto_restart_age_seconds) || 0)}s ago`;
          }
          if (httpAction) {
            const httpReady = String(channel.output_type || 'hls').toLowerCase() === 'hls'
              && Boolean(channel.http_ready);
            httpAction.hidden = !httpReady;
            httpAction.classList.toggle('http-action-hidden', !httpReady);
            httpAction.setAttribute('aria-hidden', httpReady ? 'false' : 'true');
          }
          total += channel.bitrate || 0;
        });
        const totalNode = document.querySelector('#total-bitrate');
        if (totalNode) totalNode.textContent = formatKbpsTotal(total);
        const onlineTotal = document.querySelector('[data-online-total]');
        if (onlineTotal) onlineTotal.textContent = String(Math.max(0, Number(data.online_users) || 0));
        if (typeof window.streamforgeApplyDashboardSearch === 'function') window.streamforgeApplyDashboardSearch();
        if (typeof window.streamforgeApplyChannelFilter === 'function') window.streamforgeApplyChannelFilter();
        if (typeof window.streamforgeApplyChannelSort === 'function') window.streamforgeApplyChannelSort();
      } catch (_) {
      } finally {
        window.clearTimeout(requestTimeout);
        refreshInFlight = false;
      }
    };
    // STREAMFORGE_MAIN_CHANNEL_ACTION_NO_RELOAD_V2250:
    document.querySelectorAll('form[data-channel-runtime-action]').forEach((form) => {
      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        if (form.dataset.busy === '1') return;
        form.dataset.busy = '1';
        const button = form.querySelector('button');
        if (button) button.disabled = true;
        try {
          const response = await fetch(panelUrl(form.getAttribute('action') || ''), {
            method: 'POST',
            body: new FormData(form),
            credentials: 'same-origin',
            cache: 'no-store',
            headers: {'Accept': 'application/json', 'X-StreamForge-Ajax': '1'},
          });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const action = String(form.dataset.channelRuntimeAction || '');
          const row = form.closest('[data-channel-id]');
          if (row && (action === 'start' || action === 'stop')) {
            const optimisticUp = action === 'start';
            row.querySelectorAll('form[data-channel-runtime-action]').forEach((candidate) => {
              const candidateAction = String(candidate.dataset.channelRuntimeAction || '');
              const shouldShow = candidateAction === 'start'
                ? !optimisticUp
                : ((candidateAction === 'stop' || candidateAction === 'restart') ? optimisticUp : true);
              candidate.hidden = !shouldShow;
              candidate.style.display = shouldShow ? '' : 'none';
              candidate.setAttribute('aria-hidden', shouldShow ? 'false' : 'true');
            });
          }
          await refresh();
          setTimeout(refresh, 700);
          setTimeout(refresh, 1800);
        } catch (error) {
          console.error('StreamForge channel action failed', error);
        } finally {
          form.dataset.busy = '0';
          if (button) button.disabled = false;
        }
      });
    });

    // STREAMFORGE_MAIN_DASHBOARD_STATUS_FIRST_FRAME_V1116:
    // v11.15 isolated 100+ FFmpeg processes from the Main Gunicorn worker, so
    // the old fixed 800ms hold is now counterproductive. Start the authoritative
    // status batch on the first animation frame: the shell paints immediately,
    // then live values settle ~one request later instead of ~1 second later.
    if (document.querySelector('.dashboard-control-table')) requestAnimationFrame(() => refresh());
    else refresh();
    document.addEventListener('streamforge:channel-filtered', refresh);
    setInterval(refresh, rows.length > 100 ? 5000 : 3000);
  }


  const deliveryForm = document.querySelector('[data-user-delivery-form]');
  if (deliveryForm) {
    const mode = deliveryForm.querySelector('[data-delivery-mode]');
    const directSection = deliveryForm.querySelector('[data-direct-user-options]');
    const centralSection = deliveryForm.querySelector('[data-central-user-options]');
    const directNode = deliveryForm.querySelector('[data-direct-node-select]');
    const channelLabels = Array.from(deliveryForm.querySelectorAll('[data-channel-node-ids]'));

    const updateDeliveryMode = () => {
      const direct = mode?.value === 'direct_node';
      if (directSection) directSection.hidden = !direct;
      if (centralSection) centralSection.hidden = direct;
      if (directNode) directNode.required = direct;
      const selectedNode = directNode?.value || '';
      channelLabels.forEach((label) => {
        const checkbox = label.querySelector('input[name="channel_ids"]');
        const allowedNodes = (label.dataset.channelNodeIds || '').split(',').filter(Boolean);
        const available = !direct || !selectedNode || allowedNodes.includes(selectedNode);
        label.hidden = !available;
        if (checkbox) {
          checkbox.disabled = !available;
          if (!available) checkbox.checked = false;
          checkbox.dispatchEvent(new Event('change', {bubbles: true}));
        }
      });
    };
    mode?.addEventListener('change', updateDeliveryMode);
    directNode?.addEventListener('change', updateDeliveryMode);
    updateDeliveryMode();
  }

  const panelNodeList = document.querySelector('[data-panel-node-list]');
  if (panelNodeList) {
    const boxes = Array.from(panelNodeList.querySelectorAll('input[name="node_ids"]'));
    const count = document.querySelector('[data-panel-node-count]');
    const refresh = () => {
      const selected = boxes.filter((box) => box.checked).length;
      if (count) count.textContent = `${selected} node${selected === 1 ? '' : 's'} selected`;
      boxes.forEach((box) => box.closest('label')?.classList.toggle('channel-entitlement-selected', box.checked));
    };
    document.querySelector('[data-panel-node-select-all]')?.addEventListener('click', () => {
      boxes.forEach((box) => { box.checked = true; });
      refresh();
    });
    document.querySelector('[data-panel-node-clear-all]')?.addEventListener('click', () => {
      boxes.forEach((box) => { box.checked = false; });
      refresh();
    });
    boxes.forEach((box) => box.addEventListener('change', refresh));
    refresh();
  }

})();

(() => {
  const form = document.querySelector('[data-user-delivery-form]');
  if (!form) return;
  const playlistSelect = form.querySelector('[data-playlist-profile-select]');
  const customSection = form.querySelector('[data-custom-channel-options]');
  if (!playlistSelect || !customSection) return;

  const isCustomProfile = () => {
    const value = String(playlistSelect.value ?? '').trim().toLowerCase();
    return value === '' || value === '0' || value === 'custom' || value === '__custom__';
  };

  const updatePlaylistProfileChannels = () => {
    const customSelected = isCustomProfile();
    customSection.hidden = !customSelected;
    customSection.setAttribute('aria-hidden', customSelected ? 'false' : 'true');
    customSection.querySelectorAll('input,button,select,textarea').forEach((item) => {
      if (item.name === 'playlist_order') return;
      item.disabled = !customSelected;
    });
    if (customSelected) {
      const search = customSection.querySelector('[data-user-channel-search]');
      if (search) {
        search.disabled = false;
        search.value = '';
        search.dispatchEvent(new Event('input', {bubbles: true}));
      }
      customSection.dispatchEvent(new CustomEvent('streamforge:custom-channels-shown', {bubbles: true}));
    }
  };

  playlistSelect.addEventListener('change', updatePlaylistProfileChannels);
  playlistSelect.addEventListener('input', updatePlaylistProfileChannels);
  window.addEventListener('pageshow', updatePlaylistProfileChannels);
  updatePlaylistProfileChannels();
})();


// v2.1.61: Users & Playlists allowed-node filter without page reload.
(() => {
  const select = document.querySelector('[data-user-node-filter]');
  if (!select) return;

  const clear = document.querySelector('[data-user-node-filter-clear]');
  const records = Array.from(document.querySelectorAll('[data-stream-user]'));
  const emptyStates = Array.from(document.querySelectorAll('[data-user-filter-empty]'));

  const applyFilter = () => {
    const selected = String(select.value || '');
    let visible = 0;

    records.forEach((record) => {
      const ids = String(record.dataset.nodeIds || '')
        .split(',')
        .map((value) => value.trim())
        .filter(Boolean);

      const show = !selected || ids.includes(selected);
      record.hidden = !show;
      if (show) visible += 1;
    });

    emptyStates.forEach((item) => {
      item.hidden = !selected || visible > 0;
    });

    if (clear) clear.hidden = !selected;

    // Keep the selected filter in the address bar without navigating.
    const url = new URL(window.STREAMFORGE_INTERNAL_PANEL_URL || window.location.href, window.location.origin);
    if (selected) url.searchParams.set('node_id', selected);
    else url.searchParams.delete('node_id');
    window.STREAMFORGE_INTERNAL_PANEL_URL = `${url.pathname}${url.search}${url.hash}`;
    if (!window.STREAMFORGE_FIXED_PANEL_ADDRESS_BAR) {
      window.history.replaceState({}, '', window.STREAMFORGE_INTERNAL_PANEL_URL);
    }
  };

  select.addEventListener('change', applyFilter);

  if (clear) {
    clear.addEventListener('click', () => {
      select.value = '';
      applyFilter();
      select.focus();
    });
  }

  applyFilter();
})();
