class ReolinkFeedCard extends HTMLElement {
  constructor() {
    super();
    this._config = null;
    this._hass = null;
    this._items = [];
    this._filteredItems = [];
    this._error = null;
    this._loading = false;
    this._resolvingIds = new Set();
    this._rebuilding = false;
    this._page = 1;
    this._modal = { open: false, title: "", url: "", mime: "", kind: "video" };
    this.attachShadow({ mode: "open" });
  }

  setConfig(config) {
    this._config = {
      labels: ["person", "animal"],
      cameras: [],
      per_entity_changes: 400,
      page_size: 20,
      ...config,
    };
    this._render();
    this._loadItems();
  }

  static async getConfigElement() {
    return document.createElement("reolink-feed-card-editor");
  }

  static getStubConfig() {
    return {
      type: "custom:reolink-feed-card",
    };
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._items.length && !this._loading) {
      this._loadItems();
    }
  }

  getCardSize() {
    return 6;
  }

  _applyFilters() {
    const cameraFilter = new Set((this._config?.cameras || []).map((v) => String(v).toLowerCase()));
    this._filteredItems = this._items.filter((item) => {
      if (!cameraFilter.size) return true;
      return cameraFilter.has(String(item.camera_name || "").toLowerCase());
    });
    this._page = Math.min(this._page, this._totalPages());
    if (this._page < 1) this._page = 1;
  }

  _pageSize() {
    const raw = Number(this._config?.page_size ?? 20);
    if (!Number.isFinite(raw)) return 20;
    return Math.max(1, Math.min(100, Math.floor(raw)));
  }

  _totalPages() {
    return Math.max(1, Math.ceil(this._filteredItems.length / this._pageSize()));
  }

  _pagedItems() {
    const pageSize = this._pageSize();
    const page = Math.max(1, Math.min(this._page, this._totalPages()));
    const start = (page - 1) * pageSize;
    return this._filteredItems.slice(start, start + pageSize);
  }

  async _loadItems() {
    if (!this._hass || !this._config) {
      return;
    }
    this._loading = true;
    this._error = null;
    if (!this._modal?.open) {
      this._render();
    }
    try {
      const result = await this._hass.callWS({
        type: "reolink_feed/list",
        labels: this._config.labels,
      });
      this._items = result.items || [];
      this._applyFilters();
    } catch (err) {
      this._error = err?.message || String(err);
    } finally {
      this._loading = false;
      if (!this._modal?.open) {
        this._render();
      }
    }
  }

  async _rebuildFromHistory() {
    if (!this._hass || !this._config || this._rebuilding) {
      return;
    }

    this._rebuilding = true;
    this._error = null;
    this._render();
    try {
      const result = await this._hass.callWS({
        type: "reolink_feed/rebuild_from_history",
        per_entity_changes: this._config.per_entity_changes,
      });
      const itemCount = Number(result?.item_count || 0);
      const entityCount = Number(result?.entity_count || 0);
      this._showToast(`Rebuilt ${itemCount} items from ${entityCount} sensors`);
      await this._loadItems();
    } catch (err) {
      this._showToast(`Rebuild failed: ${err?.message || err}`);
      this._error = err?.message || String(err);
    } finally {
      this._rebuilding = false;
      this._render();
    }
  }

  async _refreshRecording(item, showToast = true, showSpinner = true) {
    if (!this._hass || !item?.id) return item?.recording || null;
    if (showSpinner) {
      this._resolvingIds.add(item.id);
      this._render();
    }
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
      if (showSpinner) {
        this._resolvingIds.delete(item.id);
        this._render();
      }
    }
  }

  async _openFromThumbnail(item) {
    if (!this._hass) return;
    const recording = await this._refreshRecording(item, false, false);
    if (!recording || recording.status !== "linked" || !recording.media_content_id) {
      if (item?.snapshot_url) {
        this._openModal(
          `${item.camera_name} · snapshot`,
          item.snapshot_url,
          "image/jpeg",
          "image"
        );
        return;
      }
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
      this._openModal(
        `${item.camera_name} · ${item.label}`,
        resolved.url,
        resolved.mime_type || "video/mp4",
        "video"
      );
    } catch (err) {
      this._showToast(`Open failed: ${err?.message || err}`);
    }
  }

  _openModal(title, url, mime, kind = "video") {
    this._modal = { open: true, title, url, mime, kind };
    this._render();
  }

  _closeModal() {
    this._modal = { open: false, title: "", url: "", mime: "", kind: "video" };
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

  _openMediaBrowserForRecording(item) {
    const mediaContentId = item?.recording?.media_content_id;
    if (!mediaContentId) {
      this._showToast("Recording not linked");
      return;
    }
    const target = this._mediaBrowserTarget(item, mediaContentId);
    const url = `/media-browser/browser/${encodeURIComponent(target)}`;
    window.open(url, "_blank", "noopener");
  }

  _mediaBrowserTarget(item, mediaContentId) {
    if (!mediaContentId.includes("FILE|")) {
      return mediaContentId;
    }

    const parts = mediaContentId.split("|");
    if (parts.length < 5) {
      return mediaContentId;
    }

    const [, configEntryId, channel, stream] = parts;
    const dt = new Date(item?.start_ts || Date.now());
    const year = dt.getFullYear();
    const month = dt.getMonth() + 1;
    const day = dt.getDate();
    const event = item?.label === "animal" ? "ANIMAL" : "PERSON";
    return `media-source://reolink/EVE|${configEntryId}|${channel}|${stream}|${year}|${month}|${day}|${event}`;
  }

  _formatTime(ts) {
    if (!ts) return "";
    return new Date(ts).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
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

  _labelIcon(label) {
    if (label === "animal") {
      return `
        <span class="label-icon animal" title="Animal" aria-label="Animal">
          <ha-icon icon="mdi:dog-side"></ha-icon>
        </span>
      `;
    }
    return `
      <span class="label-icon person" title="Person" aria-label="Person">
        <ha-icon icon="mdi:account"></ha-icon>
      </span>
    `;
  }

  _recordingIcon(recording, itemId) {
    const status = recording?.status || "pending";
    const linked = status === "linked";
    const icon = linked ? "mdi:video" : "mdi:video-off";
    const filename = this._recordingFilename(recording?.media_content_id);
    const title = linked ? (filename || "Recording linked") : "Recording not linked";
    return `<button class="recording-icon ${linked ? "linked" : "off"}" data-item-id="${itemId}" title="${title}" aria-label="${title}"><ha-icon icon="${icon}"></ha-icon></button>`;
  }

  _recordingFilename(mediaContentId) {
    if (!mediaContentId || typeof mediaContentId !== "string") return "";
    if (!mediaContentId.includes("FILE|")) return "";
    const parts = mediaContentId.split("|");
    if (parts.length < 5) return "";
    return parts[4] || "";
  }

  _render() {
    if (!this.shadowRoot || !this._config) {
      return;
    }

    const pagedItems = this._pagedItems();
    const totalPages = this._totalPages();
    const listHtml = pagedItems
      .map((item) => {
        const image = item.snapshot_url
          ? `<img src="${item.snapshot_url}" alt="${item.camera_name}" loading="lazy" />`
          : `<div class="placeholder">No snapshot</div>`;
        const resolving = this._resolvingIds.has(item.id) ? " resolving" : "";

        return `
          <li class="item" data-id="${item.id}">
            <button class="thumb" aria-label="Open recording preview">
              ${image}
              <span class="play-overlay" aria-hidden="true">
                <svg viewBox="0 0 24 24">
                  <path d="M8 5v14l11-7z"></path>
                </svg>
              </span>
            </button>
            <div class="meta">
              <div class="line1">
                <span class="camera">${item.camera_name}</span>
              </div>
              <div class="line2">At: ${this._formatTime(item.start_ts)}</div>
              <div class="line3">
                <span>Duration: ${this._formatDuration(item.duration_s)}</span>
                ${this._recordingIcon(item.recording, item.id)}
              </div>
            </div>
            <div class="right-col">
              ${this._labelIcon(item.label)}
              <button class="refresh${resolving}" aria-label="Refresh recording link" title="Refresh recording link">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M21 12a9 9 0 1 1-2.64-6.36"></path>
                  <polyline points="21 3 21 9 15 9"></polyline>
                </svg>
              </button>
            </div>
          </li>
        `;
      })
      .join("");
    const paginationHtml =
      this._filteredItems.length > this._pageSize()
        ? `
      <div class="pagination">
        <button class="page-nav" data-page-nav="prev" ${this._page <= 1 ? "disabled" : ""}>Previous</button>
        <span class="page-info">Page ${this._page} / ${totalPages}</span>
        <button class="page-nav" data-page-nav="next" ${this._page >= totalPages ? "disabled" : ""}>Next</button>
      </div>
      `
        : "";

    const modalHtml = this._modal.open
      ? `
      <div class="modal-backdrop" data-close="1">
        <div class="modal" role="dialog" aria-modal="true" aria-label="Recording preview">
          <div class="modal-head">
            <span>${this._modal.title}</span>
            <button class="close" data-close="1" aria-label="Close">✕</button>
          </div>
          <div class="modal-body">
            ${
              this._modal.kind === "image"
                ? `<img src="${this._modal.url}" alt="${this._modal.title}" />`
                : `<video controls autoplay playsinline src="${this._modal.url}"></video>`
            }
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
        .topbar { display: flex; justify-content: flex-end; align-items: center; gap: 10px; margin-bottom: 10px; }
        .actions { display: flex; align-items: center; gap: 8px; }
        button.rebuild { border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); border-radius: 8px; height: 30px; padding: 0 10px; cursor: pointer; font-size: 12px; }
        button.rebuild:hover { background: var(--secondary-background-color); }
        button.rebuild:disabled { opacity: 0.6; cursor: default; }
        button.refresh-feed { border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); border-radius: 8px; width: 30px; height: 30px; padding: 0; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; }
        button.refresh-feed:hover { background: var(--secondary-background-color); }
        button.refresh-feed svg { width: 15px; height: 15px; }
        button.refresh-feed.loading svg { animation: spin 1s linear infinite; }
        button.refresh-feed:disabled { opacity: 0.6; cursor: default; }
        .pagination { display: flex; justify-content: center; align-items: center; gap: 10px; margin-top: 10px; }
        .page-info { color: var(--secondary-text-color); font-size: 12px; min-width: 84px; text-align: center; }
        button.page-nav { border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); border-radius: 8px; height: 28px; padding: 0 10px; cursor: pointer; font-size: 12px; }
        button.page-nav:hover { background: var(--secondary-background-color); }
        button.page-nav:disabled { opacity: 0.6; cursor: default; }
        ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
        .item { display: grid; grid-template-columns: 1fr auto; grid-template-rows: auto auto; gap: 10px; align-items: stretch; padding: 8px; border-radius: 10px; background: rgba(255, 255, 255, 0.04); }
        .thumb { grid-column: 1 / span 2; position: relative; display: block; width: 100%; height: clamp(140px, 22vw, 190px); overflow: hidden; border-radius: 8px; background: #111; border: 1px solid var(--divider-color); padding: 0; cursor: pointer; line-height: 0; appearance: none; -webkit-appearance: none; }
        .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
        .play-overlay { position: absolute; inset: 0; display: grid; place-items: center; background: rgba(0, 0, 0, 0.18); opacity: 0; transition: opacity 120ms ease; pointer-events: none; will-change: opacity; }
        .thumb:hover .play-overlay, .thumb:focus-visible .play-overlay { opacity: 1; }
        .play-overlay svg { width: 22px; height: 22px; fill: #fff; }
        .placeholder { color: #ddd; font-size: 11px; padding: 8px; }
        .line1 { display: flex; gap: 8px; align-items: center; font-size: 13px; }
        .camera { font-weight: 600; }
        .right-col { display: flex; flex-direction: column; justify-content: space-between; align-items: flex-end; min-height: 52px; }
        .label-icon { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; }
        .label-icon ha-icon { --mdc-icon-size: 18px; color: #fff; }
        .line2, .line3 { color: var(--secondary-text-color); font-size: 12px; margin-top: 2px; }
        .line3 { display: flex; justify-content: space-between; }
        button.recording-icon { border: 0; background: transparent; padding: 0; margin: 0; display: inline-flex; align-items: center; justify-content: center; cursor: pointer; }
        button.recording-icon ha-icon { --mdc-icon-size: 16px; color: var(--secondary-text-color); opacity: 0.6; }
        button.recording-icon:hover ha-icon { opacity: 0.85; }
        button.refresh { border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); border-radius: 8px; width: 24px; height: 24px; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; padding: 0; }
        button.refresh:hover { background: var(--secondary-background-color); }
        button.refresh svg { width: 14px; height: 14px; }
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
        .modal img { width: 100%; max-height: 72vh; object-fit: contain; background: #000; }
        .fallback { color: #9cc3ff; font-size: 12px; }
      </style>
      <ha-card>
        <div class="topbar">
          <div class="actions">
            <button class="refresh-feed${this._loading ? " loading" : ""}" ${this._loading ? "disabled" : ""} aria-label="Refresh feed data" title="Refresh feed data">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M21 12a9 9 0 1 1-2.64-6.36"></path>
                <polyline points="21 3 21 9 15 9"></polyline>
              </svg>
            </button>
            <button class="rebuild" ${this._rebuilding ? "disabled" : ""} aria-label="Rebuild feed from history">
              ${this._rebuilding ? "Rebuilding..." : "Rebuild Feed"}
            </button>
          </div>
        </div>
        ${this._error ? `<div class="error">${this._error}</div>` : ""}
        ${this._filteredItems.length ? `<ul>${listHtml}</ul>${paginationHtml}` : `<div class="empty">No detections in range.</div>`}
      </ha-card>
      ${modalHtml}
    `;

    this.shadowRoot.querySelectorAll("li.item").forEach((el) => {
      const id = el.getAttribute("data-id");
      const item = this._items.find((x) => x.id === id);
      if (!item) return;

      const thumb = el.querySelector("button.thumb");
      const refresh = el.querySelector("button.refresh");
      const rec = el.querySelector("button.recording-icon");

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
      if (rec) {
        rec.addEventListener("click", (ev) => {
          ev.preventDefault();
          this._openMediaBrowserForRecording(item);
        });
      }
    });

    const rebuildButton = this.shadowRoot.querySelector("button.rebuild");
    rebuildButton?.addEventListener("click", (ev) => {
      ev.preventDefault();
      this._rebuildFromHistory();
    });
    const refreshFeedButton = this.shadowRoot.querySelector("button.refresh-feed");
    refreshFeedButton?.addEventListener("click", (ev) => {
      ev.preventDefault();
      this._loadItems();
    });

    this.shadowRoot.querySelectorAll("button.page-nav").forEach((el) => {
      el.addEventListener("click", (ev) => {
        ev.preventDefault();
        const action = el.getAttribute("data-page-nav");
        if (action === "prev" && this._page > 1) {
          this._page -= 1;
          this._render();
          return;
        }
        if (action === "next" && this._page < this._totalPages()) {
          this._page += 1;
          this._render();
        }
      });
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

class ReolinkFeedCardEditor extends HTMLElement {
  constructor() {
    super();
    this._config = {};
    this.attachShadow({ mode: "open" });
  }

  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  _emitConfig(next) {
    this._config = next;
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: next },
        bubbles: true,
        composed: true,
      })
    );
  }

  _onNumberChange(key, value, fallback) {
    const parsed = Number.parseInt(value, 10);
    const next = { ...this._config, [key]: Number.isFinite(parsed) ? parsed : fallback };
    this._emitConfig(next);
  }

  _onLabelToggle(label, checked) {
    const current = Array.isArray(this._config.labels) ? this._config.labels : ["person", "animal"];
    const labels = new Set(current);
    if (checked) labels.add(label);
    else labels.delete(label);
    const next = { ...this._config, labels: Array.from(labels) };
    this._emitConfig(next);
  }

  _render() {
    if (!this.shadowRoot) return;
    const pageSize = Number(this._config?.page_size ?? 20);
    const labels = new Set(Array.isArray(this._config?.labels) ? this._config.labels : ["person", "animal"]);
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .grid { display: grid; gap: 10px; }
        .field { display: grid; gap: 4px; }
        label { color: var(--primary-text-color); font-size: 13px; }
        input[type="text"], input[type="number"] {
          border: 1px solid var(--divider-color);
          background: var(--card-background-color);
          color: var(--primary-text-color);
          border-radius: 8px;
          padding: 8px;
          font-size: 13px;
        }
        .labels { display: flex; gap: 12px; align-items: center; }
        .labels label { display: flex; gap: 6px; align-items: center; font-size: 13px; }
      </style>
      <div class="grid">
        <div class="field">
          <label for="page_size">Page size</label>
          <input id="page_size" type="number" min="1" max="100" value="${pageSize}" />
        </div>
        <div class="field">
          <label>Labels</label>
          <div class="labels">
            <label><input id="label_person" type="checkbox" ${labels.has("person") ? "checked" : ""} />Person</label>
            <label><input id="label_animal" type="checkbox" ${labels.has("animal") ? "checked" : ""} />Animal</label>
          </div>
        </div>
      </div>
    `;

    this.shadowRoot.querySelector("#page_size")?.addEventListener("change", (ev) => {
      this._onNumberChange("page_size", ev.target.value, 20);
    });
    this.shadowRoot.querySelector("#label_person")?.addEventListener("change", (ev) => {
      this._onLabelToggle("person", ev.target.checked);
    });
    this.shadowRoot.querySelector("#label_animal")?.addEventListener("change", (ev) => {
      this._onLabelToggle("animal", ev.target.checked);
    });
  }
}

if (!customElements.get("reolink-feed-card-editor")) {
  customElements.define("reolink-feed-card-editor", ReolinkFeedCardEditor);
}
