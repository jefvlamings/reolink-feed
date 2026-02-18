class ReolinkFeedCard extends HTMLElement {
  constructor() {
    super();
    this._config = null;
    this._hass = null;
    this._items = [];
    this._filteredItems = [];
    this._error = null;
    this._loading = false;
    this._refreshTimer = null;
    this._resolvingIds = new Set();
    this._modal = { open: false, title: "", url: "", mime: "" };
    this.attachShadow({ mode: "open" });
  }

  setConfig(config) {
    this._config = {
      title: "Reolink Feed",
      since_hours: 24,
      limit: 200,
      labels: ["person", "animal"],
      cameras: [],
      refresh_seconds: 20,
      ...config,
    };
    this._scheduleRefresh();
    this._render();
    this._loadItems();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._items.length && !this._loading) {
      this._loadItems();
    } else {
      this._render();
    }
  }

  disconnectedCallback() {
    if (this._refreshTimer) {
      clearInterval(this._refreshTimer);
      this._refreshTimer = null;
    }
  }

  getCardSize() {
    return 6;
  }

  _scheduleRefresh() {
    if (this._refreshTimer) {
      clearInterval(this._refreshTimer);
    }
    if (!this._config || !this._config.refresh_seconds) {
      return;
    }
    this._refreshTimer = setInterval(
      () => this._loadItems(),
      Math.max(5, this._config.refresh_seconds) * 1000
    );
  }

  _applyFilters() {
    const cameraFilter = new Set((this._config?.cameras || []).map((v) => String(v).toLowerCase()));
    this._filteredItems = this._items.filter((item) => {
      if (!cameraFilter.size) return true;
      return cameraFilter.has(String(item.camera_name || "").toLowerCase());
    });
  }

  async _loadItems() {
    if (!this._hass || !this._config) {
      return;
    }
    this._loading = true;
    this._error = null;
    this._render();
    try {
      const result = await this._hass.callWS({
        type: "reolink_feed/list",
        since_hours: this._config.since_hours,
        limit: this._config.limit,
        labels: this._config.labels,
      });
      this._items = result.items || [];
      this._applyFilters();
    } catch (err) {
      this._error = err?.message || String(err);
    } finally {
      this._loading = false;
      this._render();
    }
  }

  async _refreshRecording(item, showToast = true) {
    if (!this._hass || !item?.id) return item?.recording || null;
    this._resolvingIds.add(item.id);
    this._render();
    try {
      const recording = await this._hass.callWS({
        type: "reolink_feed/resolve_recording",
        item_id: item.id,
      });
      item.recording = recording;
      if (showToast) {
        if (recording.status === "linked") this._showToast("Recording linked");
        else if (recording.status === "not_found") this._showToast("Recording not found");
        else this._showToast("Recording still pending");
      }
      return recording;
    } catch (err) {
      this._showToast(`Resolve failed: ${err?.message || err}`);
      return null;
    } finally {
      this._resolvingIds.delete(item.id);
      this._render();
    }
  }

  async _openFromThumbnail(item) {
    if (!this._hass) return;
    const recording = await this._refreshRecording(item, false);
    if (!recording || recording.status !== "linked" || !recording.media_content_id) {
      this._showToast("Clip not ready yet");
      return;
    }

    try {
      const resolved = await this._hass.callWS({
        type: "media_source/resolve_media",
        media_content_id: recording.media_content_id,
      });
      if (!resolved?.url) {
        this._showToast("Could not resolve media URL");
        return;
      }
      this._openModal(`${item.camera_name} · ${item.label}`, resolved.url, resolved.mime_type || "video/mp4");
    } catch (err) {
      this._showToast(`Open failed: ${err?.message || err}`);
    }
  }

  _openModal(title, url, mime) {
    this._modal = { open: true, title, url, mime };
    this._render();
  }

  _closeModal() {
    this._modal = { open: false, title: "", url: "", mime: "" };
    this._render();
  }

  _showToast(message) {
    const event = new Event("hass-notification", {
      bubbles: true,
      composed: true,
    });
    event.detail = { message };
    this.dispatchEvent(event);
  }

  _formatTime(ts) {
    if (!ts) return "";
    return new Date(ts).toLocaleString();
  }

  _formatDuration(totalSeconds) {
    if (totalSeconds === null || totalSeconds === undefined) return "-";
    const seconds = Math.max(0, Number(totalSeconds) || 0);
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h > 0) return `${h}h ${m}m ${s}s`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  }

  _render() {
    if (!this.shadowRoot || !this._config) {
      return;
    }

    const title = this._config.title || "Reolink Feed";
    const listHtml = this._filteredItems
      .map((item) => {
        const status = item.recording?.status || "pending";
        const statusText = status === "linked" ? "linked" : status === "not_found" ? "not found" : "pending";
        const image = item.snapshot_url
          ? `<img src="${item.snapshot_url}" alt="${item.camera_name}" loading="lazy" />`
          : `<div class="placeholder">No snapshot</div>`;
        const resolving = this._resolvingIds.has(item.id) ? " resolving" : "";

        return `
          <li class="item" data-id="${item.id}">
            <button class="thumb" aria-label="Open recording preview">${image}</button>
            <div class="meta">
              <div class="line1">
                <span class="camera">${item.camera_name}</span>
                <span class="label ${item.label}">${item.label}</span>
              </div>
              <div class="line2">${this._formatTime(item.start_ts)}</div>
              <div class="line3">
                <span>${this._formatDuration(item.duration_s)}</span>
                <span class="status ${status}">${statusText}</span>
              </div>
            </div>
            <button class="refresh${resolving}" aria-label="Refresh recording link" title="Refresh recording link">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M21 12a9 9 0 1 1-2.64-6.36"></path>
                <polyline points="21 3 21 9 15 9"></polyline>
              </svg>
            </button>
          </li>
        `;
      })
      .join("");

    const modalHtml = this._modal.open
      ? `
      <div class="modal-backdrop" data-close="1">
        <div class="modal" role="dialog" aria-modal="true" aria-label="Recording preview">
          <div class="modal-head">
            <span>${this._modal.title}</span>
            <button class="close" data-close="1" aria-label="Close">✕</button>
          </div>
          <div class="modal-body">
            <video controls autoplay playsinline src="${this._modal.url}"></video>
            <a class="fallback" href="${this._modal.url}" target="_blank" rel="noopener">Open in new tab</a>
          </div>
        </div>
      </div>
      `
      : "";

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 10px; }
        .head { font-weight: 600; margin-bottom: 8px; }
        .state { color: var(--secondary-text-color); font-size: 12px; margin-bottom: 8px; }
        ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
        .item { display: grid; grid-template-columns: 110px 1fr auto; gap: 10px; align-items: center; border: 1px solid var(--divider-color); border-radius: 10px; padding: 8px; }
        .thumb { width: 110px; height: 62px; overflow: hidden; border-radius: 8px; background: #111; border: 1px solid var(--divider-color); padding: 0; cursor: pointer; }
        .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
        .placeholder { color: #ddd; font-size: 11px; padding: 8px; }
        .line1 { display: flex; gap: 8px; align-items: center; font-size: 13px; }
        .camera { font-weight: 600; }
        .label { text-transform: uppercase; font-size: 10px; padding: 2px 6px; border-radius: 999px; letter-spacing: 0.04em; }
        .label.person { background: #1d3557; color: #fff; }
        .label.animal { background: #2a9d8f; color: #fff; }
        .line2, .line3 { color: var(--secondary-text-color); font-size: 12px; margin-top: 2px; }
        .line3 { display: flex; justify-content: space-between; }
        .status { padding: 1px 6px; border-radius: 999px; border: 1px solid var(--divider-color); text-transform: lowercase; }
        .status.linked { color: #0b6b3a; border-color: #0b6b3a55; }
        .status.pending { color: #8a6500; border-color: #8a650055; }
        .status.not_found { color: #8b1e1e; border-color: #8b1e1e55; }
        button.refresh { border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); border-radius: 8px; width: 32px; height: 32px; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; }
        button.refresh:hover { background: var(--secondary-background-color); }
        button.refresh svg { width: 16px; height: 16px; }
        button.refresh.resolving svg { animation: spin 1s linear infinite; }
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        .empty { color: var(--secondary-text-color); font-size: 13px; padding: 8px 2px; }
        .error { color: var(--error-color); font-size: 12px; white-space: pre-wrap; }

        .modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.65); display: grid; place-items: center; z-index: 9999; }
        .modal { width: min(92vw, 980px); background: #111; border: 1px solid #333; border-radius: 12px; overflow: hidden; color: #fff; }
        .modal-head { display: flex; justify-content: space-between; align-items: center; padding: 10px 12px; border-bottom: 1px solid #333; font-weight: 600; }
        .close { border: 1px solid #555; background: transparent; color: #fff; border-radius: 6px; width: 28px; height: 28px; cursor: pointer; }
        .modal-body { padding: 10px; display: grid; gap: 8px; }
        .modal video { width: 100%; max-height: 72vh; background: #000; }
        .fallback { color: #9cc3ff; font-size: 12px; }
      </style>
      <ha-card>
        <div class="head">${title}</div>
        <div class="state">${this._loading ? "loading..." : `${this._filteredItems.length} items`}</div>
        ${this._error ? `<div class="error">${this._error}</div>` : ""}
        ${this._filteredItems.length ? `<ul>${listHtml}</ul>` : `<div class="empty">No detections in range.</div>`}
      </ha-card>
      ${modalHtml}
    `;

    this.shadowRoot.querySelectorAll("li.item").forEach((el) => {
      const id = el.getAttribute("data-id");
      const item = this._items.find((x) => x.id === id);
      if (!item) return;

      const thumb = el.querySelector("button.thumb");
      const refresh = el.querySelector("button.refresh");

      if (thumb) {
        thumb.addEventListener("click", (ev) => {
          ev.preventDefault();
          this._openFromThumbnail(item);
        });
      }
      if (refresh) {
        refresh.addEventListener("click", (ev) => {
          ev.preventDefault();
          this._refreshRecording(item, true);
        });
      }
    });

    this.shadowRoot.querySelectorAll("[data-close='1']").forEach((el) => {
      el.addEventListener("click", (ev) => {
        if (ev.target === el || ev.target?.getAttribute("data-close") === "1") {
          this._closeModal();
        }
      });
    });
  }
}

if (!customElements.get("reolink-feed-card")) {
  customElements.define("reolink-feed-card", ReolinkFeedCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "reolink-feed-card",
  name: "Reolink Feed Card",
  description: "Timeline of Reolink person/animal detections",
});
